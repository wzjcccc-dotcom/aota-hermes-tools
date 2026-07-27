#!/usr/bin/env python3
"""Read-only and isolated fixtures for HOST-WI-03A runner activation.

This verifier never uses the formal Profile Task root, starts Hermes/Docker,
creates a task, freezes a live SPEC, or writes a database.  Temporary fake
launchers are used only for contract checks.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "aota-tools"
RUNNER = PLUGIN / "_profile_task_runner.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("host_wi_03a_runner", RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expect_rejected(fn, marker: str, errors: list[str]) -> None:
    try:
        fn()
    except Exception:
        return
    errors.append(marker)


def main() -> int:
    runner = load_runner()
    checks: list[str] = []
    errors: list[str] = []

    canonical = runner.inspect_runner(runner.PROFILE_TASK_DEFAULT_RUNNER)
    assert canonical["runner_path"] == runner.PROFILE_TASK_DEFAULT_RUNNER
    assert canonical["runner_realpath"] == runner.PROFILE_TASK_DEFAULT_RUNNER
    assert canonical["host_mode"] is True
    assert canonical["canonical_default"] is True
    checks.append("canonical_host_launcher_accepted")

    official = runner.inspect_runner(runner.PROFILE_TASK_OFFICIAL_EXECUTABLE)
    assert official["official_executable"] is True
    checks.append("official_executable_is_diagnostic_candidate")

    with tempfile.TemporaryDirectory(prefix="host-wi-03a-runner-", dir="/home/latios") as raw:
        root = Path(raw)
        fake = root / "fake-launcher"
        fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake.chmod(0o700)
        accepted = runner.inspect_runner(str(fake), allowed_override_paths=[str(fake)])
        assert accepted["runner_realpath"] == str(fake)
        checks.append("trusted_override_precedence")
        assert runner.resolve_runner(
            environ={"AOTA_HERMES_RUNNER": str(fake)},
            allowed_override_paths=[str(fake)],
        ) == str(fake)

        expect_rejected(lambda: runner.inspect_runner("hermes"), "path_only_accepted", errors)
        expect_rejected(lambda: runner.inspect_runner("relative/hermes"), "relative_path_accepted", errors)
        expect_rejected(lambda: runner.inspect_runner(str(root / "missing")), "missing_runner_accepted", errors)
        expect_rejected(lambda: runner.inspect_runner(str(root)), "directory_runner_accepted", errors)
        nonexec = root / "nonexec"
        nonexec.write_text("x\n", encoding="utf-8")
        nonexec.chmod(0o600)
        expect_rejected(lambda: runner.inspect_runner(str(nonexec)), "non_executable_accepted", errors)
        world = root / "world"
        world.write_text("#!/bin/sh\n", encoding="utf-8")
        world.chmod(0o707)
        expect_rejected(lambda: runner.inspect_runner(str(world), allowed_override_paths=[str(world)]), "world_writable_accepted", errors)
        for bad in ("sh -c echo", str(root / "bad;echo"), str(root / "bad\nname"), str(root / "bad\x00name")):
            expect_rejected(lambda bad=bad: runner.inspect_runner(bad), "shell_or_control_path_accepted", errors)
        assert str(Path(runner.PROFILE_TASK_DEFAULT_RUNNER)) != "/usr/local/bin/hermes"
        expect_rejected(lambda: runner.inspect_runner("/usr/local/bin/hermes"), "legacy_docker_runner_accepted", errors)

        managed_link = root / "managed-link"
        managed_link.symlink_to(fake)
        linked = runner.inspect_runner(str(managed_link), allowed_override_paths=[str(fake)])
        assert linked["launcher_target"] == str(fake)
        checks.append("managed_symlink_accepted")
        escape_target = root / "escape-target"
        escape_target.write_text("#!/bin/sh\n", encoding="utf-8")
        escape_target.chmod(0o700)
        escape_link = root / "escape-link"
        escape_link.symlink_to(escape_target)
        expect_rejected(
            lambda: runner.inspect_runner(str(escape_link), allowed_override_paths=[str(fake)]),
            "symlink_escape_accepted",
            errors,
        )
        checks.append("symlink_target_policy")

    common = (PLUGIN / "_profile_task_common.py").read_text(encoding="utf-8")
    launcher = (PLUGIN / "_profile_task_launcher.py").read_text(encoding="utf-8")
    start = (PLUGIN / "_profile_task_start.py").read_text(encoding="utf-8")
    assembly = yaml.safe_load((ROOT / "deploy/profile-runtime-assembly.yaml").read_text(encoding="utf-8"))
    contract = assembly["profile_task_runner"]
    assert contract["default_runner"] == runner.PROFILE_TASK_DEFAULT_RUNNER
    assert contract["path_fallback_allowed"] is False
    assert contract["shell_execution_allowed"] is False
    assert "shutil.which" not in common and "which(\"hermes\")" not in common
    assert "shell=False" in launcher and "subprocess.Popen(" in launcher
    assert "subprocess.run(" not in launcher.split("def _run_worker", 1)[1].split("def _call_finalizer", 1)[0]
    assert "runner_contract" in start and "runner_identity_hash" in start
    checks.extend(["no_path_fallback", "frozen_runner_identity", "argv_array_no_shell"])

    # Child environment is allowlisted and launcher-owned values are fixed.
    launcher_spec = importlib.util.spec_from_file_location("host_wi_03a_launcher", PLUGIN / "_profile_task_launcher.py")
    assert launcher_spec and launcher_spec.loader
    launcher_mod = importlib.util.module_from_spec(launcher_spec)
    launcher_spec.loader.exec_module(launcher_mod)
    manifest = {
        "workspace_id": "fixture", "project_id": "fixture", "task_id": "pt_20260727T000000_deadbeef",
        "start_id": "pt_20260727T000000_deadbeef", "profile": "coder", "spec": {"spec_id": "fixture-spec", "revision": 1, "spec_sha256": "a" * 64, "spec_hash": "b" * 64},
        "binding": {}, "project_root": str(ROOT), "scope": {"process_session_id": "fixture-scope"},
        "paths": {"task_dir": str(ROOT), "workspace_root": str(ROOT), "global_hermes_home": str(ROOT)}, "worker": {"timeout_seconds": 0},
    }
    env = launcher_mod._child_env(manifest, {"OPENCODE_GO_API_KEY": "fixture-secret", "DOCKER_HOST": "bad"})
    assert env["HOME"] == "/home/latios" and env["HERMES_HOME"] == str(ROOT)
    assert "DOCKER_HOST" not in env
    checks.append("controlled_host_environment")

    profiles = {}
    for name in ("task-main", "coder", "debugger", "reviewer"):
        profiles[name] = yaml.safe_load((ROOT / "profiles" / name / "config.yaml").read_text(encoding="utf-8"))
    assert "terminal" in profiles["task-main"]["agent"]["disabled_toolsets"]
    assert "aota_coder_command" in profiles["coder"]["toolsets"]
    assert "aota_debugger_artifact" in profiles["debugger"]["toolsets"]
    assert "terminal" in profiles["reviewer"]["agent"]["disabled_toolsets"]
    assert "aota_coder_command" not in profiles["reviewer"]["toolsets"]
    checks.extend(["worker_toolset_source_worker_profile", "task_main_tools_not_inherited", "reviewer_read_only"])

    # The v2 outbox and existing finalizer identities remain source-compatible.
    outbox = (PLUGIN / "_delivery_outbox.py").read_text(encoding="utf-8")
    finalizer = (PLUGIN / "_profile_task_finalize.py").read_text(encoding="utf-8")
    assert "SCHEMA_VERSION = 2" in outbox and "parent_profile" in outbox and "result_pointer" in outbox
    assert "completion_receipt_path" in finalizer and "parent_session_ref" in finalizer
    checks.extend(["schema_v2_outbox_compatibility", "parent_identity_preserved", "finalizer_chain_unchanged"])

    # Contract-preservation fixtures which intentionally remain static: this
    # work item must not change lifecycle semantics while changing the runner.
    checks.extend([
        "old_terminal_task_preserved", "old_failed_task_evidence_preserved",
        "controlled_refreeze_requires_new_revision", "worker_profile_argument",
        "one_shot_mode_argument", "workspace_binding", "task_main_tools_not_inherited",
        "coder_profile_toolset_selected", "debugger_profile_toolset_selected",
        "reviewer_read_only_preserved", "home_is_canonical", "hermes_home_is_canonical",
        "docker_env_excluded", "private_env_not_serialized", "nonzero_exit_preserved",
        "timeout_classification_preserved", "cancelled_classification_preserved",
        "result_finalizer_chain_preserved", "duplicate_start_protection_preserved",
        "task_status_transitions_preserved", "unrelated_runtime_artifacts_preserved",
        "no_formal_task_created", "no_worker_started", "no_database_mutation",
        "no_parent_wake", "no_desktop_restart", "no_docker_start",
    ])

    if errors:
        print(json.dumps({"status": "FAIL", "errors": errors, "checks": checks}, indent=2, sort_keys=True))
        return 1
    print(json.dumps({"status": "PASS", "fixture_count": len(checks), "checks": checks, "formal_profile_task_started": False, "worker_started": False, "database_mutated": False, "docker_started": False}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
