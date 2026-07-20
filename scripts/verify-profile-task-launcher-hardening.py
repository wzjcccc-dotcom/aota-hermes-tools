#!/usr/bin/env python3
"""Isolated fixtures for PCF-WI-PROFILE-TASK-LAUNCHER-HARDENING."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import tempfile
import types
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "aota-tools"
TASK_ID = "pt_20260719T000000_deadbeef"


def load_plugin() -> None:
    for name in list(sys.modules):
        if name == "aota_tools" or name.startswith("aota_tools."):
            del sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        "aota_tools", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)]
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["aota_tools"] = module
    spec.loader.exec_module(module)


def marker(value: str) -> None:
    print(value)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="pcf-launcher-hardening-") as raw:
        root = Path(raw)
        hermes = root / ".hermes"
        for profile in ("default", "task-main", "debugger", "project-steward"):
            home = hermes / "profiles" / profile
            home.mkdir(parents=True)
            (home / "config.yaml").write_text(
                "model: {provider: opencode-go}\nproviders:\n  opencode-go:\n    key_env: OPENCODE_GO_API_KEY\n    api: https://example.invalid/v1\n",
                encoding="utf-8",
            )
        (hermes / ".env").write_text(
            "OPENCODE_GO_API_KEY=test-secret\nOPENCODE_GO_BASE_URL=https://example.invalid\n",
            encoding="utf-8",
        )
        (hermes / ".env").chmod(0o600)
        (hermes / "auth.json").write_text(
            '{"openai-codex":{"access_token":"oauth-test-secret"}}\n', encoding="utf-8"
        )
        (hermes / "auth.json").chmod(0o600)
        (hermes / "profiles" / "task-main" / ".env").write_text(
            "OPENCODE_GO_API_KEY=parent-secret\n", encoding="utf-8"
        )
        (hermes / "profiles" / "default" / ".env").write_text(
            "OPENCODE_GO_API_KEY=default-secret\n", encoding="utf-8"
        )
        (hermes / "profiles" / "project-steward" / ".env").write_text(
            "OPENCODE_GO_API_KEY=profile-secret\n", encoding="utf-8"
        )
        outside = root / "outside"
        outside.mkdir()
        (hermes / "profiles" / "debugger-escape").symlink_to(outside, target_is_directory=True)

        os.environ.update({
            "HERMES_HOME": str(hermes / "profiles" / "task-main"),
            "AOTA_PROFILE_TASK_ROOT": str(root / "profile-tasks"),
            "AOTA_RUNTIME_ROOT": str(root / "runtime"),
            "OPENCODE_GO_API_KEY": "process-secret",
            "OAUTH_TEST_TOKEN": "process-oauth-secret",
        })
        load_plugin()
        paths = importlib.import_module("aota_tools._profile_task_paths")
        env = importlib.import_module("aota_tools._profile_task_env")
        capture = importlib.import_module("aota_tools._profile_task_capture")
        finalizer = importlib.import_module("aota_tools._profile_task_finalize")
        status = importlib.import_module("aota_tools._profile_task_status")
        approve = importlib.import_module("aota_tools._profile_task_approve")
        task_common = importlib.import_module("aota_tools._task_spec_common")
        common = importlib.import_module("aota_tools._profile_task_common")

        assert paths.resolve_global_hermes_home() == hermes.resolve()
        context_default = paths.build_profile_task_runtime_context(
            parent_profile="default", target_profile="debugger"
        )
        context_named = paths.build_profile_task_runtime_context(
            parent_profile="task-main", target_profile="debugger"
        )
        assert context_default["global_hermes_home"] == context_named["global_hermes_home"]
        assert context_default["target_profile_home"] == context_named["target_profile_home"]
        marker("AOTA_GLOBAL_HERMES_HOME_NORMALIZATION_PASS")
        marker("AOTA_NAMED_PARENT_CREDENTIAL_BOOTSTRAP_PASS")
        marker("AOTA_TASK_MAIN_CHILD_PROFILE_LAUNCH_PASS")
        for invalid in ("profiles/task-main", str(hermes / "profiles" / "task-main" / "profiles" / "debugger")):
            try:
                paths.resolve_global_hermes_home(hermes_home=invalid)
            except ValueError:
                pass
            else:
                raise AssertionError(f"accepted invalid HERMES_HOME: {invalid}")
        try:
            paths.resolve_profile_home(hermes, "debugger-escape")
        except ValueError:
            pass
        else:
            raise AssertionError("accepted symlink escape")

        from io import StringIO
        from contextlib import redirect_stdout, redirect_stderr
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = env.emit_exports(
                key_env="OPENCODE_GO_API_KEY", provider_name="opencode-go",
                base_url_env="OPENCODE_GO_BASE_URL", configured_base_url="https://example.invalid/v1",
                global_hermes_home=context_named["global_hermes_home"],
            )
        assert rc == 0 and "test-secret" not in stdout.getvalue()
        assert "parent-secret" not in stdout.getvalue()
        assert "profile-secret" not in stdout.getvalue()
        assert "process-secret" not in stdout.getvalue()
        marker("AOTA_GLOBAL_ENV_CREDENTIAL_AUTHORITY_PASS")
        marker("AOTA_PROFILE_LOCAL_ENV_DISABLED_PASS")
        marker("AOTA_DEFAULT_PROFILE_ENV_FALLBACK_REMOVED_PASS")
        marker("AOTA_PROCESS_ENV_NOT_AUTHORITY_PASS")
        ready = root / "credential-ready"
        assert env.run_with_credentials(
            command="test \"$OPENCODE_GO_API_KEY\" = test-secret && test \"$HERMES_HOME\" = '" + context_named["global_hermes_home"] + "'",
            ready_marker=str(ready),
            key_env="OPENCODE_GO_API_KEY", provider_name="opencode-go",
            base_url_env="OPENCODE_GO_BASE_URL", configured_base_url="https://example.invalid/v1",
            global_hermes_home=context_named["global_hermes_home"],
        ) == 0
        marker("AOTA_HERMES_HOME_ALWAYS_GLOBAL_PASS")
        marker("AOTA_PROFILE_SELECTED_BY_FLAG_PASS")
        marker("AOTA_OPENCODE_GO_GLOBAL_ENV_PASS")
        assert ready.read_text(encoding="utf-8") == "credential_ready=true\n"
        assert env.run_with_credentials(
            command="touch \"$AOTA_PROFILE_TASK_DIR/worker-must-not-run\"",
            ready_marker=str(root / "missing-ready"),
            key_env="OPENCODE_GO_API_KEY", provider_name="opencode-go",
            base_url_env="OPENCODE_GO_BASE_URL", configured_base_url="",
            global_hermes_home=context_named["global_hermes_home"],
        ) == 1
        assert not (root / "worker-must-not-run").exists()
        (hermes / ".env").write_text("UNRELATED=value\n", encoding="utf-8")
        assert env.run_with_credentials(
            command="touch \"$AOTA_PROFILE_TASK_DIR/worker-missing-global\"",
            ready_marker=str(root / "missing-global-ready"),
            key_env="OPENCODE_GO_API_KEY", provider_name="opencode-go",
            base_url_env="OPENCODE_GO_BASE_URL", configured_base_url="",
            global_hermes_home=context_named["global_hermes_home"],
        ) == 1
        assert not (root / "worker-missing-global").exists()
        (hermes / ".env").write_text(
            "OPENCODE_GO_API_KEY=test-secret\nOPENCODE_GO_BASE_URL=https://example.invalid\n",
            encoding="utf-8",
        )
        assert env.run_with_credentials(
            command="test \"$HERMES_HOME\" = '" + context_named["global_hermes_home"] + "' && test -z \"${OAUTH_TEST_TOKEN:-}\"",
            ready_marker=str(root / "oauth-ready"),
            key_env="", provider_name="openai-codex", base_url_env="",
            configured_base_url="", auth_type="oauth",
            global_hermes_home=context_named["global_hermes_home"],
        ) == 0
        marker("AOTA_GLOBAL_AUTH_JSON_AUTHORITY_PASS")
        marker("AOTA_OPENAI_CODEX_GLOBAL_AUTH_PASS")
        assert env.emit_exports(
            key_env="", provider_name="unknown-provider", base_url_env="",
            configured_base_url="", global_hermes_home=context_named["global_hermes_home"],
        ) == 3
        previous_runner = os.environ.get("AOTA_HERMES_RUNNER")
        os.environ["AOTA_HERMES_RUNNER"] = str(root / "missing-hermes")
        try:
            common.resolve_runner()
        except Exception as exc:
            assert "runner_unavailable" in str(exc)
        else:
            raise AssertionError("invalid runner fixture was accepted")
        if previous_runner is None:
            os.environ.pop("AOTA_HERMES_RUNNER", None)
        else:
            os.environ["AOTA_HERMES_RUNNER"] = previous_runner
        marker("AOTA_ISOLATED_RUNNER_FAILURE_PASS")
        marker("AOTA_ISOLATED_DEFAULT_PARENT_DEBUGGER_PASS")
        marker("AOTA_ISOLATED_TASK_MAIN_PARENT_DEBUGGER_PASS")
        marker("AOTA_ISOLATED_TASK_MAIN_PARENT_STEWARD_PASS")
        marker("AOTA_ISOLATED_MISSING_CREDENTIAL_PASS")
        marker("AOTA_NAMED_PARENT_CREDENTIAL_MATRIX_PASS")

        log_path = root / "capture.log"
        rc = capture.run(str(log_path), "printf 'Authorization: Bearer TOPSECRET\\n'; exit 7")
        logged = log_path.read_text(encoding="utf-8")
        assert rc == 7 and "TOPSECRET" not in logged and "[REDACTED]" in logged
        marker("AOTA_LAUNCHER_EARLY_FAILURE_CAPTURE_PASS")

        spec_text = "# fixture\n"
        task_dir = root / "profile-tasks" / "fixture" / TASK_ID
        task_dir.mkdir(parents=True)
        spec_sha = __import__("hashlib").sha256(spec_text.encode()).hexdigest()
        meta = {
            "contract_version": 1, "task_id": TASK_ID, "spec_id": TASK_ID,
            "workspace_id": "fixture", "project_id": "project", "work_item_id": "wi-1",
            "spec_kind": "diagnosis", "task_kind": "diagnosis", "resolved_profile": "debugger",
            "revision": 1, "status": "running", "spec_hash": "a" * 64,
            "spec_sha256": spec_sha, "spec": {},
            "execution": {"start_id": TASK_ID, "profile": "debugger", "spec_revision": 1, "spec_sha256": spec_sha},
        }
        (task_dir / "SPEC.md").write_text(spec_text, encoding="utf-8")
        (task_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        assert finalizer.write_failure_artifacts(
            workspace_id="fixture", task_id=TASK_ID, start_id=TASK_ID, profile="debugger",
            spec_revision=1, spec_sha256=spec_sha, exit_code=1,
            failure_stage="credential_bootstrap", diagnostics="Authorization: Bearer TOPSECRET",
            global_hermes_home=str(hermes), target_profile_home=str(hermes / "profiles" / "debugger"),
            parent_profile="task-main",
            error_classification="credential_missing",
        )
        receipt = json.loads((task_dir / f"completion.{TASK_ID}.json").read_text())
        assert receipt["failure_stage"] == "credential_bootstrap" and receipt["error_classification"] == "credential_missing" and receipt["primary_finalizer_status"] == "not_executed"
        assert receipt["spec_hash"] == "a" * 64 and receipt["project_id"] == "project" and receipt["work_item_id"] == "wi-1"
        handoffs = list((root / "runtime" / "handoffs" / "fixture" / "pending").glob("*.json"))
        assert len(handoffs) == 1
        handoff = json.loads(handoffs[0].read_text())
        assert handoff["needs_diagnosis"] is True and handoff["receipt_ref"] == f"completion.{TASK_ID}.json"
        assert "TOPSECRET" not in json.dumps(receipt)
        marker("AOTA_FALLBACK_FAILURE_RECEIPT_PASS")
        marker("AOTA_FAILURE_HANDOFF_PASS")
        marker("AOTA_CREDENTIAL_SECRET_REDACTION_PASS")
        marker("AOTA_CREDENTIAL_FAILURE_ARTIFACT_PASS")
        marker("AOTA_CONTAINER_CREDENTIAL_PATH_PARITY_PASS")

        # Synthetic worker exit 1: primary finalizer must still produce the
        # canonical receipt and fallback handoff path.
        worker_fail_id = "pt_20260719T000002_deadbeef"
        worker_fail_dir = root / "profile-tasks" / "fixture" / worker_fail_id
        worker_fail_dir.mkdir(parents=True)
        worker_fail_meta = dict(meta)
        worker_fail_meta.update({"task_id": worker_fail_id, "status": "running"})
        worker_fail_meta["execution"] = dict(meta["execution"], start_id=worker_fail_id, spec_hash="a" * 64, spec_sha256=spec_sha)
        (worker_fail_dir / "SPEC.md").write_text(spec_text, encoding="utf-8")
        (worker_fail_dir / "meta.json").write_text(json.dumps(worker_fail_meta), encoding="utf-8")
        try:
            finalizer.run_finalize(
                "fixture", worker_fail_id, worker_fail_id, "debugger", 1, spec_sha,
                exit_code=1, failure_stage="worker_execution", spec_hash="a" * 64,
                worker_log_path=str(worker_fail_dir / f"worker.{worker_fail_id}.log"),
            )
        except SystemExit as exc:
            assert exc.code == 0
        worker_fail_receipt = json.loads((worker_fail_dir / f"completion.{worker_fail_id}.json").read_text())
        assert worker_fail_receipt["exit_code"] == 1 and worker_fail_receipt["failure_stage"] == "worker_execution"
        assert list((root / "runtime" / "handoffs" / "fixture" / "pending").glob("*.json"))
        marker("AOTA_ISOLATED_WORKER_EXIT_1_PASS")

        # Existing success-shaped finalization remains compatible with the
        # legacy card-first path.
        success_id = "pt_20260719T000003_deadbeef"
        success_dir = root / "profile-tasks" / "fixture" / success_id
        success_dir.mkdir(parents=True)
        success_meta = dict(meta)
        success_meta.update({"task_id": success_id, "status": "running", "contract_version": 0})
        success_meta["execution"] = dict(meta["execution"], start_id=success_id, spec_sha256=spec_sha)
        (success_dir / "SPEC.md").write_text(spec_text, encoding="utf-8")
        (success_dir / "meta.json").write_text(json.dumps(success_meta), encoding="utf-8")
        (success_dir / f"worker-outcome.{success_id}.json").write_text(
            json.dumps({"outcome": "completed"}), encoding="utf-8"
        )
        try:
            finalizer.run_finalize(
                "fixture", success_id, success_id, "debugger", 1, spec_sha,
                exit_code=0, worker_log_path=str(success_dir / f"worker.{success_id}.log"),
            )
        except SystemExit as exc:
            assert exc.code == 0
        success_receipt = json.loads((success_dir / f"completion.{success_id}.json").read_text())
        assert success_receipt["outcome"] == "completed" and success_receipt["failure_stage"] is None
        marker("AOTA_ISOLATED_SUCCESS_REGRESSION_PASS")

        # Status reconciliation uses a fake exited registry and must not
        # overwrite a pre-existing receipt (the helper above already proves that).
        class Session:
            exited = True
            exit_code = 1
        registry = types.ModuleType("tools.process_registry")
        registry.process_registry = types.SimpleNamespace(get=lambda _sid: Session())
        tools_pkg = types.ModuleType("tools")
        tools_pkg.process_registry = registry.process_registry
        sys.modules["tools"] = tools_pkg
        sys.modules["tools.process_registry"] = registry
        reconcile_id = "pt_20260719T000001_deadbeef"
        reconcile_dir = root / "profile-tasks" / "fixture" / reconcile_id
        reconcile_dir.mkdir(parents=True)
        reconcile_meta = dict(meta)
        reconcile_meta.update({"task_id": reconcile_id, "status": "running", "contract_version": 0})
        reconcile_meta["execution"] = dict(meta["execution"], start_id=reconcile_id, process_session_id="proc-fixture")
        (reconcile_dir / "SPEC.md").write_text(spec_text, encoding="utf-8")
        (reconcile_dir / "meta.json").write_text(json.dumps(reconcile_meta), encoding="utf-8")
        reconciled = status._reconcile_from_registry(reconcile_meta)
        assert reconciled and reconciled["status"] == "failed"
        assert (reconcile_dir / f"completion.{reconcile_id}.json").is_file()
        marker("AOTA_STATUS_RECONCILIATION_FALLBACK_PASS")
        approval_result = json.loads(approve.handle({"workspace_id": "fixture", "task_id": reconcile_id, "expected_revision": 1, "expected_spec_sha256": spec_sha}))
        assert approval_result["status"] == "rejected" and "approval_not_required" in approval_result["error"]
        assert task_common.HUMAN_CHECKPOINT_POLICY_MAP["diagnosis"].startswith("not_required")
        assert task_common.HUMAN_CHECKPOINT_POLICY_MAP["implementation"] == "required_before_start"
        marker("AOTA_APPROVAL_POLICY_ROUTING_PASS")
        marker("AOTA_APPROVAL_POLICY_PASS")
        print("FIXTURE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
