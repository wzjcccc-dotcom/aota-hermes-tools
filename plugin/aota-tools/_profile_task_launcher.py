"""Structured, shell-free AOTA Profile Task launcher.

The terminal background rail invokes this file with one small command.  This
module owns the child process lifecycle; dynamic task data never crosses a
shell parser.  The launch manifest is written by ``_profile_task_start`` and
is treated as immutable here.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import importlib.util
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping


MAX_LOG_BYTES = 5 * 1024 * 1024
MAX_LINE_BYTES = 8192
_OVERRIDE_KEYS = {
    "HERMES_INFERENCE_PROVIDER", "HERMES_TUI_PROVIDER", "HERMES_INFERENCE_MODEL",
    "HERMES_MODEL", "HERMES_PROFILE", "HERMES_PROVIDER", "MODEL", "PROVIDER",
    "OLLAMA_MODEL", "OLLAMA_PROVIDER",
}
_DOCKER_ENV_PREFIXES = ("DOCKER_", "COMPOSE_")
_TASK_ID_PREFIX = "pt_"


def _load_sibling(name: str):
    path = Path(__file__).resolve().with_name(name + ".py")
    spec = importlib.util.spec_from_file_location("aota_launcher_" + name, path)
    if not spec or not spec.loader:
        raise RuntimeError("launcher_internal: sibling_module_unavailable=" + name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _redact(text: str) -> str:
    import re
    text = re.sub(r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(bearer\s+)[^\s]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(api[_-]?key|token|secret|password)\s*([:=])\s*([^\s,;]+)", r"\1\2[REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED]", text)
    return text


class DurableLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
            path.write_bytes(path.read_bytes()[-MAX_LOG_BYTES:])
        self._size = min(path.stat().st_size if path.exists() else 0, MAX_LOG_BYTES)
        self._file = path.open("a", encoding="utf-8")

    def write(self, message: str, *, stage: str = "") -> None:
        prefix = f"stage={stage} " if stage else ""
        rendered = _redact(prefix + str(message))
        lines = rendered.splitlines() or [""]
        for line in lines:
            raw = line.encode("utf-8", errors="replace")[:MAX_LINE_BYTES]
            if len(line.encode("utf-8", errors="replace")) > MAX_LINE_BYTES:
                raw += b"...[LINE_TRUNCATED]"
            raw += b"\n"
            if self._size >= MAX_LOG_BYTES:
                return
            raw = raw[: MAX_LOG_BYTES - self._size]
            self._file.buffer.write(raw)
            self._file.flush()
            self._size += len(raw)

    def close(self) -> None:
        self._file.close()


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("manifest_not_object")
    return value


def _safe_path(value: object, field: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"binding_invalid:{field}")
    return Path(value).resolve(strict=False)


def _validate_manifest(manifest: Mapping[str, Any], runner_inspector=None) -> dict[str, Any]:
    if manifest.get("schema_version") != 1:
        raise ValueError("manifest_schema_unsupported")
    for field in ("workspace_id", "project_id", "task_id", "start_id", "profile", "parent_profile", "work_item_id"):
        if not isinstance(manifest.get(field), str) or not manifest[field]:
            raise ValueError("binding_invalid:" + field)
    if not str(manifest["task_id"]).startswith(_TASK_ID_PREFIX) or manifest["start_id"] != manifest["task_id"]:
        raise ValueError("binding_invalid:task_id")
    paths = manifest.get("paths")
    worker = manifest.get("worker")
    provider = manifest.get("provider")
    spec = manifest.get("spec")
    binding = manifest.get("binding")
    scope = manifest.get("scope")
    if not all(isinstance(item, dict) for item in (paths, worker, provider, spec, binding, scope)):
        raise ValueError("manifest_sections_missing")
    for field in ("task_dir", "workspace_root", "global_hermes_home", "target_profile_home", "worker_log", "completion_receipt", "scope_manifest", "workspace_baseline", "handoff"):
        _safe_path(paths.get(field), "paths." + field)
    if not isinstance(worker.get("runner"), str) or not worker["runner"]:
        raise ValueError("binding_invalid:worker.runner")
    runner_module = _load_sibling("_profile_task_runner")
    inspect = runner_inspector or runner_module.inspect_runner
    observed_runner = inspect(worker["runner"])
    frozen_runner = manifest.get("runner_contract")
    if frozen_runner is not None:
        if not isinstance(frozen_runner, dict):
            raise ValueError("binding_invalid:runner_contract")
        for key in ("runner_path", "runner_realpath", "runner_identity_hash", "runner_contract_version"):
            if frozen_runner.get(key) != observed_runner.get(key):
                raise ValueError("runner_contract_mismatch:" + key)
        if frozen_runner.get("host_mode") is not True:
            raise ValueError("runner_contract_mismatch:host_mode")
    if not isinstance(worker.get("prompt_path"), str) or not worker["prompt_path"]:
        raise ValueError("binding_invalid:worker.prompt_path")
    if not isinstance(provider.get("name"), str) or not isinstance(provider.get("model"), str):
        raise ValueError("binding_invalid:provider")
    if not isinstance(binding.get("profile_config_digest"), str) or len(binding["profile_config_digest"]) != 64:
        raise ValueError("binding_invalid:profile_config_digest")
    if not isinstance(scope.get("scope_digest"), str) or len(scope["scope_digest"]) != 64:
        raise ValueError("scope_binding_mismatch:scope_digest")
    if not isinstance(scope.get("process_session_id"), str) or not scope["process_session_id"]:
        raise ValueError("scope_binding_mismatch:process_session_id")
    return dict(manifest)


def _scope_binding(manifest: Mapping[str, Any]) -> None:
    """Recheck the immutable scope projection at the worker boundary."""
    helper = _load_sibling("_task_spec_scope")
    scope_path = _safe_path(manifest["paths"]["scope_manifest"], "paths.scope_manifest")
    try:
        scope = json.loads(scope_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("scope_binding_mismatch:scope_manifest_unreadable") from exc
    if not isinstance(scope, dict):
        raise RuntimeError("scope_binding_mismatch:scope_manifest_shape")
    digest = helper.compute_scope_digest(scope)
    expected = manifest["scope"]
    for key in ("workspace_id", "task_id", "start_id"):
        if scope.get(key) != manifest.get(key):
            raise RuntimeError("scope_binding_mismatch:" + key)
    if scope.get("spec_id") != manifest["spec"]["spec_id"] or scope.get("spec_revision") != manifest["spec"]["revision"] or scope.get("spec_hash") != manifest["spec"]["spec_hash"] or scope.get("spec_sha256") != manifest["spec"]["spec_sha256"]:
        raise RuntimeError("scope_binding_mismatch:spec")
    if scope.get("scope_digest") != expected["scope_digest"] or digest != expected["scope_digest"]:
        raise RuntimeError("scope_binding_mismatch:digest")
    if scope.get("scope_source") != expected.get("scope_source") or scope.get("process_session_id") != expected.get("process_session_id"):
        raise RuntimeError("scope_binding_mismatch:metadata")


def _profile_binding(manifest: Mapping[str, Any]) -> None:
    binding = manifest["binding"]
    config_path = _safe_path(binding.get("profile_config_path"), "binding.profile_config_path")
    raw = config_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != binding["profile_config_digest"]:
        raise RuntimeError("profile_launch_binding_mismatch:profile_config_digest")
    try:
        import yaml
        config = yaml.safe_load(raw.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("profile_launch_binding_mismatch:profile_config_unreadable") from exc
    model_block = config.get("model") if isinstance(config, dict) else None
    provider = model_block.get("provider") if isinstance(model_block, dict) else None
    model = model_block.get("default") if isinstance(model_block, dict) else None
    if provider != manifest["provider"]["name"] or model != manifest["provider"]["model"]:
        raise RuntimeError("profile_launch_binding_mismatch:provider_model")
    if manifest["profile"] != manifest["binding"]["resolved_profile"]:
        raise RuntimeError("profile_launch_binding_mismatch:profile")


def _child_env(manifest: Mapping[str, Any], credentials: Mapping[str, str]) -> dict[str, str]:
    allowed = {
        "PATH", "HOME", "TERM", "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "LANGUAGE",
        "NO_COLOR", "AOTA_RUNTIME_ROOT", "AOTA_PROFILE_TASK_ROOT", "AOTA_CANONICAL_WORKSPACE_ROOT",
        "AOTA_WORKSPACE_REGISTRY_PATH", "AOTA_WEBUI_ATTACHMENT_ROOT", "AOTA_WEBUI_ATTACHMENT_REF_ROOT",
    }
    child = {key: value for key, value in os.environ.items() if key in allowed}
    child["HOME"] = "/home/latios"
    child["HERMES_HOME"] = str(manifest["paths"]["global_hermes_home"])
    child.update({
        key: value
        for key, value in credentials.items()
        if key not in _OVERRIDE_KEYS and not any(key.startswith(prefix) for prefix in _DOCKER_ENV_PREFIXES)
    })
    binding = manifest["binding"]
    paths = manifest["paths"]
    markers = {
        "AOTA_PROFILE_TASK_ROOT": str(Path(paths["task_dir"]).resolve().parents[1]),
        "AOTA_PROFILE_TASK_DIR": str(paths["task_dir"]),
        "AOTA_PROFILE_TASK_WORKSPACE_ID": str(manifest["workspace_id"]),
        "AOTA_PROFILE_TASK_ID": str(manifest["task_id"]),
        "AOTA_PROFILE_TASK_START_ID": str(manifest["start_id"]),
        "AOTA_PROFILE_TASK_PROFILE": str(manifest["profile"]),
        "AOTA_PROFILE_TASK_SPEC_REVISION": str(manifest["spec"]["revision"]),
        "AOTA_PROFILE_TASK_SPEC_SHA256": str(manifest["spec"]["spec_sha256"]),
        "AOTA_PROFILE_TASK_PROCESS_SESSION_ID": str(manifest["scope"]["process_session_id"]),
        "AOTA_PROFILE_TASK_TIMEOUT_SECONDS": str(manifest["worker"].get("timeout_seconds") or ""),
        "AOTA_PROFILE_TASK_TIMEOUT_DEADLINE_AT": str(manifest["worker"].get("timeout_deadline_at") or ""),
        "AOTA_WORKSPACE_ID": str(manifest["workspace_id"]),
        "AOTA_PROJECT_ID": str(manifest["project_id"]),
        "AOTA_WORKSPACE_ROOT": str(paths["workspace_root"]),
        "AOTA_PROJECT_ROOT": str(manifest.get("project_root") or paths["workspace_root"]),
        "AOTA_SPEC_ID": str(manifest["spec"]["spec_id"]),
        "AOTA_SPEC_REVISION": str(manifest["spec"]["revision"]),
        "AOTA_SPEC_HASH": str(manifest["spec"]["spec_hash"]),
    }
    child.update(markers)
    for key in _OVERRIDE_KEYS:
        child.pop(key, None)
    return child


def _terminate_group(process: subprocess.Popen, log: DurableLog) -> bool:
    triggered = False
    try:
        os.killpg(process.pid, signal.SIGTERM)
        triggered = True
        log.write("SIGTERM sent to worker process group", stage="timeout")
    except (ProcessLookupError, OSError) as exc:
        log.write(type(exc).__name__, stage="timeout")
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
            log.write("SIGKILL sent to worker process group", stage="timeout")
        except (ProcessLookupError, OSError) as exc:
            log.write(type(exc).__name__, stage="timeout")
        process.wait()
    return triggered


def _run_worker(argv: list[str], env: Mapping[str, str], cwd: Path, log: DurableLog, timeout: int | None) -> tuple[int, bool]:
    log.write("worker argv prepared runner/profile/-z prompt; prompt omitted", stage="worker_spawn")
    process = subprocess.Popen(
        argv,
        shell=False,
        cwd=str(cwd),
        env=dict(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        text=False,
    )
    log.write(f"pid={process.pid}", stage="worker_spawn")
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    deadline = time.monotonic() + timeout if timeout else None
    timed_out = False
    while selector.get_map():
        remaining = max(0.0, deadline - time.monotonic()) if deadline else None
        if remaining == 0.0 and process.poll() is None:
            timed_out = _terminate_group(process, log)
        for key, _ in selector.select(0.2 if remaining is None else min(0.2, remaining)):
            data = key.fileobj.readline()
            if not data:
                selector.unregister(key.fileobj)
                key.fileobj.close()
                continue
            line = data[:MAX_LINE_BYTES].decode("utf-8", errors="replace").rstrip("\n")
            log.write(line, stage="worker_execution:" + str(key.data))
    return_code = process.wait()
    log.write(f"exit_code={return_code} timeout_triggered={str(timed_out).lower()}", stage="worker_exit")
    return return_code, timed_out


def _call_finalizer(manifest: Mapping[str, Any], *, exit_code: int, stage: str, log_path: Path, mode: str, error_classification: str = "") -> tuple[bool, str]:
    finalizer = _load_sibling("_profile_task_finalize")
    os.environ["AOTA_PROFILE_TASK_ROOT"] = str(Path(manifest["paths"]["task_dir"]).resolve().parents[1])
    runtime_root = os.environ.get("AOTA_RUNTIME_ROOT")
    if runtime_root:
        os.environ["AOTA_RUNTIME_ROOT"] = runtime_root
    try:
        finalizer.run_finalize(
            workspace_id=manifest["workspace_id"], task_id=manifest["task_id"], start_id=manifest["start_id"],
            profile=manifest["profile"], spec_revision=int(manifest["spec"]["revision"]),
            spec_sha256=manifest["spec"]["spec_sha256"], spec_hash=manifest["spec"]["spec_hash"],
            exit_code=exit_code, workspace_root=str(manifest["paths"]["workspace_root"]),
            timeout_seconds=int(manifest["worker"].get("timeout_seconds") or 0), worker_log_path=str(log_path),
            failure_stage=stage, finalizer_mode=mode,
            command_summary=f"profile-task launcher profile={manifest['profile']} task_id={manifest['task_id']}",
            global_hermes_home=str(manifest["paths"]["global_hermes_home"]),
            target_profile_home=str(manifest["paths"]["target_profile_home"]),
            parent_profile=str(manifest["parent_profile"]), error_classification=error_classification,
        )
    except SystemExit as exc:
        return exc.code in (None, 0), f"SystemExit:{exc.code}"
    except Exception as exc:
        return False, f"{type(exc).__name__}:{exc}"
    return True, ""


def _fallback(manifest: Mapping[str, Any], *, exit_code: int, stage: str, classification: str, log: DurableLog, primary_status: str) -> bool:
    log.write(f"exit_code={exit_code} primary_finalizer_status={primary_status}", stage="fallback_finalizer")
    try:
        finalizer = _load_sibling("_profile_task_finalize")
        result = finalizer.write_failure_artifacts(
            workspace_id=manifest["workspace_id"], task_id=manifest["task_id"], start_id=manifest["start_id"],
            profile=manifest["profile"], spec_revision=int(manifest["spec"]["revision"]),
            spec_sha256=manifest["spec"]["spec_sha256"], spec_hash=manifest["spec"]["spec_hash"],
            exit_code=exit_code, failure_stage=stage, error_classification=classification,
            diagnostics=f"failure_stage={stage} error_classification={classification}",
            command_summary=f"profile-task launcher task_id={manifest['task_id']}",
            global_hermes_home=str(manifest["paths"]["global_hermes_home"]),
            target_profile_home=str(manifest["paths"]["target_profile_home"]),
            parent_profile=str(manifest["parent_profile"]), finalizer_mode="fallback",
            primary_finalizer_status=primary_status,
            replace_success_on_primary_failure=primary_status == "failed",
        )
        log.write(f"completed={str(bool(result)).lower()}", stage="fallback_finalizer")
        return bool(result)
    except Exception as exc:
        log.write(f"{type(exc).__name__}:{exc}", stage="fallback_finalizer")
        return False


def run(manifest_path: str, *, runner_inspector=None) -> int:
    path = Path(manifest_path).resolve(strict=False)
    inferred = path.name.removeprefix("launch.").removesuffix(".json") or "unknown"
    log = DurableLog(path.parent / f"worker.{inferred}.log")
    log.write("launcher entered", stage="manifest_load")
    manifest: dict[str, Any] | None = None
    try:
        loaded_manifest = _load_json(path)
        manifest = loaded_manifest
        manifest = _validate_manifest(loaded_manifest, runner_inspector=runner_inspector)
        log.write("manifest loaded and binding shape validated", stage="binding_validation")
        _scope_binding(manifest)
        log.write("scope/spec/task binding and digest verified", stage="binding_validation")
        _profile_binding(manifest)
        log.write("profile config digest/provider/model verified", stage="binding_validation")
        env_mod = _load_sibling("_profile_task_env")
        credentials = env_mod.resolve_global_provider_credentials(
            global_hermes_home=manifest["paths"]["global_hermes_home"], provider_name=manifest["provider"]["name"],
            key_env=manifest["provider"].get("key_env", ""), base_url_env=manifest["provider"].get("base_url_env", ""),
            configured_base_url=manifest["provider"].get("configured_base_url", ""), provider_auth_type=manifest["provider"].get("auth_type", ""),
        )
        child_env = _child_env(manifest, credentials.get("child_env", {}))
        log.write(f"provider={manifest['provider']['name']} auth_type={credentials['auth_type']} source={credentials['credential_source']} key_present={str(credentials['key_present']).lower()}", stage="credential_bootstrap")
        runner = Path(manifest["worker"]["runner"])
        runner_module = _load_sibling("_profile_task_runner")
        runner_identity = (runner_inspector or runner_module.inspect_runner)(str(runner))
        log.write(
            "runner identity verified contract_version="
            + str(runner_identity["runner_contract_version"]),
            stage="runner_resolution",
        )
        prompt_path = _safe_path(manifest["worker"]["prompt_path"], "worker.prompt_path")
        prompt = prompt_path.read_text(encoding="utf-8")
        argv = [str(runner), "-p", str(manifest["profile"]), "-z", prompt]
        timeout = int(manifest["worker"].get("timeout_seconds") or 0) or None
        exit_code, timed_out = _run_worker(argv, child_env, _safe_path(manifest["paths"]["workspace_root"], "paths.workspace_root"), log, timeout)
        stage = "timeout" if timed_out else ("worker_execution" if exit_code else "")
        classification = "timeout" if timed_out else ("worker_failed" if exit_code else "")
        final_exit_code = 137 if timed_out else exit_code
        ok, detail = _call_finalizer(manifest, exit_code=final_exit_code, stage=stage, log_path=log.path, mode="primary", error_classification=classification)
        log.write(detail or "primary finalizer completed", stage="primary_finalizer")
        if not ok:
            _fallback(manifest, exit_code=final_exit_code or 1, stage="primary_finalizer", classification="primary_finalizer_failed", log=log, primary_status="failed")
        return final_exit_code if final_exit_code else (0 if ok else 1)
    except Exception as exc:
        if manifest is not None:
            message = str(exc)
            if "credential" in message:
                stage = "credential_bootstrap"
                if "credential_provider_unsupported" in message:
                    classification = "credential_provider_unsupported"
                elif "credential_authority_invalid" in message:
                    classification = "credential_authority_invalid"
                elif "credential_missing" in message or "missing" in message:
                    classification = "credential_missing"
                else:
                    classification = "credential_bootstrap"
            elif "runner" in message:
                stage, classification = "runner_resolution", "runner_resolution"
            elif "scope_binding_mismatch" in message:
                stage, classification = "binding_validation", "scope_binding_mismatch"
            elif "profile_launch_binding" in message:
                stage, classification = "binding_validation", "profile_launch_binding_mismatch"
            elif "manifest" in message or "binding" in message:
                stage, classification = "binding_validation", "binding_invalid"
            else:
                stage, classification = "launcher_internal", "launcher_internal"
            log.write(f"{type(exc).__name__}:{message}", stage=stage)
            _fallback(manifest, exit_code=1, stage=stage, classification=classification, log=log, primary_status="not_executed")
        else:
            log.write(f"{type(exc).__name__}:{exc}", stage="manifest_load")
        return 1
    finally:
        log.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    return run(parser.parse_args().manifest)


if __name__ == "__main__":
    raise SystemExit(main())
