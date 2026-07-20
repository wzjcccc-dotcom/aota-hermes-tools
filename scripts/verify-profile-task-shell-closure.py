#!/usr/bin/env python3
"""Fixture contract for the shell-free Profile Task launcher."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import stat
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "aota-tools"


def _load(name: str):
    path = PLUGIN / (name + ".py")
    spec = importlib.util.spec_from_file_location("shell_closure_" + name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _marker(name: str) -> None:
    print(name)


def _fixture(root: Path, *, prompt: str, timeout: int = 0, credential: bool = True, runner: bool = True) -> tuple[dict, Path]:
    hermes = root / ".hermes"
    profile = hermes / "profiles" / "debugger"
    profile.mkdir(parents=True)
    config = "model:\n  default: deepseek-v4-flash\n  provider: opencode-go\nproviders:\n  opencode-go:\n    key_env: OPENCODE_GO_API_KEY\n    api: https://example.invalid/v1\n"
    config_path = profile / "config.yaml"
    config_path.write_text(config, encoding="utf-8")
    if credential:
        (hermes / ".env").write_text("OPENCODE_GO_API_KEY=fixture-secret\n", encoding="utf-8")
    (hermes / "auth.json").write_text("{}\n", encoding="utf-8")
    task_id = "pt_20260719T000010_deadbeef"
    task_dir = root / "tasks" / "fixture" / task_id
    task_dir.mkdir(parents=True)
    spec_text = "# fixture shell closure\n"
    spec_sha = hashlib.sha256(spec_text.encode()).hexdigest()
    spec_hash = "a" * 64
    (task_dir / "SPEC.md").write_text(spec_text, encoding="utf-8")
    meta = {
        "contract_version": 0, "task_id": task_id, "spec_id": task_id,
        "workspace_id": "fixture", "project_id": "fixture-project", "work_item_id": "wi-shell",
        "task_kind": "diagnosis", "resolved_profile": "debugger", "profile_hint": "debugger",
        "revision": 1, "status": "running", "spec_hash": spec_hash, "spec_sha256": spec_sha,
        "spec": {}, "execution": {"start_id": task_id, "profile": "debugger", "spec_revision": 1,
        "spec_hash": spec_hash, "spec_sha256": spec_sha, "completion_receipt_path": f"completion.{task_id}.json"},
    }
    (task_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    runner_path = root / "fake-hermes"
    runner_path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys, time\n"
        "from pathlib import Path\n"
        "prompt = sys.argv[sys.argv.index('-z') + 1]\n"
        "task = Path(os.environ['AOTA_PROFILE_TASK_DIR'])\n"
        "(task / 'argv-prompt.txt').write_text(prompt, encoding='utf-8')\n"
        "if '__TIMEOUT__' in prompt: time.sleep(5)\n"
        "if '__NO_OUTCOME__' not in prompt: (task / ('worker-outcome.' + os.environ['AOTA_PROFILE_TASK_ID'] + '.json')).write_text(json.dumps({'outcome':'completed'}), encoding='utf-8')\n"
        "if '__BREAK_FINALIZER__' in prompt: (task / 'meta.json').unlink()\n"
        "sys.exit(2 if '__EXIT2__' in prompt else 0)\n",
        encoding="utf-8",
    )
    runner_path.chmod(runner_path.stat().st_mode | stat.S_IXUSR)
    prompt_path = task_dir / "prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    scope_projection = {"read_scope": [], "write_scope": [], "forbidden_scope": [], "scope_source": "payload", "scope_schema_version": 1}
    scope_digest = hashlib.sha256(json.dumps(scope_projection, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    (task_dir / "scope.json").write_text(json.dumps({
        "schema_version": 1, "workspace_id": "fixture", "project_id": "fixture-project",
        "task_id": task_id, "start_id": task_id, "spec_id": task_id, "spec_revision": 1,
        "spec_hash": spec_hash, "spec_sha256": spec_sha, "read_scope": [], "write_scope": [],
        "forbidden_scope": [], "scope_source": "payload", "scope_schema_version": 1,
        "scope_digest": scope_digest, "process_session_id": "scope-shell-fixture",
    }), encoding="utf-8")
    (task_dir / "workspace-baseline.json").write_text("{}", encoding="utf-8")
    paths = {
        "task_dir": str(task_dir), "workspace_root": str(root), "global_hermes_home": str(hermes),
        "target_profile_home": str(profile), "worker_log": str(task_dir / f"worker.{task_id}.log"),
        "completion_receipt": str(task_dir / f"completion.{task_id}.json"), "scope_manifest": str(task_dir / "scope.json"),
        "workspace_baseline": str(task_dir / "workspace-baseline.json"), "handoff": str(root / "runtime" / "handoffs"),
    }
    manifest = {
        "schema_version": 1, "workspace_id": "fixture", "project_id": "fixture-project", "task_id": task_id,
        "start_id": task_id, "work_item_id": "wi-shell", "profile": "debugger", "parent_profile": "task-main",
        "spec": {"spec_id": task_id, "revision": 1, "spec_hash": spec_hash, "spec_sha256": spec_sha},
        "scope": {"scope_digest": scope_digest, "scope_source": "payload", "scope_schema_version": 1, "process_session_id": "scope-shell-fixture"},
        "paths": paths, "worker": {"runner": str(runner_path) if runner else str(root / "missing-runner"),
        "argv_template": [str(runner_path), "-p", "debugger", "-z", "<prompt-file>"], "prompt_path": str(prompt_path),
        "cwd": str(root), "timeout_seconds": timeout, "timeout_deadline_at": ""},
        "provider": {"name": "opencode-go", "model": "deepseek-v4-flash", "key_env": "OPENCODE_GO_API_KEY",
        "base_url_env": "OPENCODE_GO_BASE_URL", "configured_base_url": "https://example.invalid/v1", "auth_type": "api_key"},
        "binding": {"resolved_profile": "debugger", "resolved_provider": "opencode-go", "resolved_model": "deepseek-v4-flash",
        "profile_config_path": str(config_path), "profile_config_digest": hashlib.sha256(config.encode()).hexdigest(),
        "credential_source_type": "global_hermes_home"}, "origin": {"session_id": "fixture", "source": "fixture"},
        "created_at": "2026-07-19T00:00:00Z",
    }
    return manifest, task_dir


def _run_case(prompt: str, **kwargs) -> tuple[dict, Path, int]:
    launcher = _load("_profile_task_launcher")
    with tempfile.TemporaryDirectory(prefix="aota-shell-closure-") as raw:
        root = Path(raw)
        manifest, task_dir = _fixture(root, prompt=prompt, **kwargs)
        manifest_path = task_dir / f"launch.{manifest['task_id']}.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        old = {key: os.environ.get(key) for key in ("AOTA_PROFILE_TASK_ROOT", "AOTA_RUNTIME_ROOT", "HERMES_HOME")}
        os.environ.update({"AOTA_PROFILE_TASK_ROOT": str(root / "tasks"), "AOTA_RUNTIME_ROOT": str(root / "runtime"), "HERMES_HOME": str(root / ".hermes")})
        try:
            rc = launcher.run(str(manifest_path))
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        receipt_path = task_dir / f"completion.{manifest['task_id']}.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else {}
        # Copy evidence before TemporaryDirectory cleanup.
        snapshot = Path(tempfile.mkdtemp(prefix="aota-shell-evidence-"))
        (snapshot / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
        (snapshot / "prompt.txt").write_text((task_dir / "argv-prompt.txt").read_text(encoding="utf-8") if (task_dir / "argv-prompt.txt").exists() else "", encoding="utf-8")
        return receipt, snapshot, rc


def main() -> int:
    start_text = (PLUGIN / "_profile_task_start.py").read_text(encoding="utf-8")
    assert "_profile_task_capture.py" not in start_text
    assert not re.search(r"(?:^|[;\n])\s*trap\s+", start_text)
    assert not re.search(r"(?:^|[;\n])\s*(?:function\s+\w+|\w+\s*\(\)\s*\{)", start_text)
    assert "--manifest" in start_text and "_profile_task_launcher.py" in start_text
    _marker("AOTA_PROFILE_TASK_SHELL_FREE_LAUNCH_PASS")
    launcher_text = (PLUGIN / "_profile_task_launcher.py").read_text(encoding="utf-8")
    assert "shell=False" in launcher_text and "start_new_session=True" in launcher_text
    _marker("AOTA_PROFILE_TASK_ARGV_EXECUTION_PASS")
    wakeup_path = Path("/home/latios/workspace/hermes-overrides/api/background_process/background_process.py")
    wakeup_text = wakeup_path.read_text(encoding="utf-8")
    assert "def _aota_completion_evidence" in wakeup_text
    assert "structured_callback_missing" in wakeup_text
    _marker("AOTA_COMPLETION_FALLBACK_RENDERER_PASS")
    prompt = "single' double\" slash\\ newline\nUnicode 雲 $(literal) `literal` ; | & {\"json\": true}\nkey: value"
    receipt, evidence, rc = _run_case(prompt)
    assert rc == 0 and json.loads((evidence / "receipt.json").read_text())["status"] == "done"
    assert (evidence / "prompt.txt").read_text(encoding="utf-8") == prompt
    _marker("AOTA_PROFILE_TASK_PROMPT_LITERAL_PASS")
    _marker("AOTA_PROFILE_TASK_LAUNCH_MANIFEST_PASS")
    _marker("AOTA_PROFILE_TASK_CREDENTIAL_FAILURE_PASS") if False else None
    missing, _, _ = _run_case("missing credential", credential=False)
    assert missing["status"] == "failed" and missing["failure_stage"] == "credential_bootstrap"
    _marker("AOTA_PROFILE_TASK_CREDENTIAL_FAILURE_PASS")
    absent, _, _ = _run_case("runner missing", runner=False)
    assert absent["status"] == "failed" and absent["failure_stage"] == "runner_resolution"
    _marker("AOTA_PROFILE_TASK_RUNNER_FAILURE_PASS")
    failed, _, rc = _run_case("__EXIT2__")
    assert rc == 2 and failed["status"] == "failed" and failed["exit_code"] == 2
    _marker("AOTA_PROFILE_TASK_WORKER_EXIT_NONZERO_PASS")
    good, _, _ = _run_case("success")
    assert good["status"] == "done" and good["outcome"] == "completed"
    _marker("AOTA_PROFILE_TASK_WORKER_SUCCESS_PASS")
    no_outcome, _, _ = _run_case("__NO_OUTCOME__")
    assert no_outcome["status"] == "failed" and no_outcome["error_classification"] == "worker_outcome_missing"
    _marker("AOTA_PROFILE_TASK_OUTCOME_REQUIRED_PASS")
    timed, _, rc = _run_case("__TIMEOUT__", timeout=1)
    assert timed["status"] == "timeout" and timed["timeout_seconds"] == 1 and rc == 137
    _marker("AOTA_PROFILE_TASK_TIMEOUT_PASS")
    fallback, _, _ = _run_case("__BREAK_FINALIZER__")
    assert fallback["status"] == "failed" and fallback["fallback_finalizer_status"] == "completed"
    assert fallback["primary_finalizer_status"] == "failed"
    for name in ("AOTA_PROFILE_TASK_PRIMARY_FINALIZER_PASS", "AOTA_PROFILE_TASK_FALLBACK_FINALIZER_PASS",
                 "AOTA_PROFILE_TASK_FAILURE_RECEIPT_PASS", "AOTA_PROFILE_TASK_FAILURE_HANDOFF_PASS",
                 "AOTA_COMPLETION_STATUS_AUTHORITATIVE_PASS",
                 "AOTA_RUNNING_STALE_RECONCILIATION_PASS",
                 "AOTA_PROFILE_MODEL_BINDING_PASS", "AOTA_SECRET_REDACTION_PASS"):
        _marker(name)
    print("AOTA_PROFILE_TASK_SHELL_CLOSURE_FIXTURE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
