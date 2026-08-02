#!/usr/bin/env python3
"""Verify trusted runtime normalization against the Hermes dispatch shapes."""
from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "aota-tools"


def load_context():
    package = types.ModuleType("aota_tools")
    package.__path__ = [str(PLUGIN)]  # type: ignore[attr-defined]
    sys.modules["aota_tools"] = package
    spec = importlib.util.spec_from_file_location("aota_tools._trusted_runtime_context", PLUGIN / "_trusted_runtime_context.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    mod = load_context()
    names = ("AOTA_TRUSTED_PRINCIPAL", "AOTA_TRUSTED_WORKSPACE_ID", "AOTA_PROFILE_TASK_ID", "AOTA_PROFILE_TASK_START_ID", "AOTA_PROFILE_TASK_PROFILE", "AOTA_ORIGIN_SESSION_ID", "AOTA_PARENT_SESSION_ID")
    before = {name: os.environ.get(name) for name in names}
    try:
        os.environ.update({"AOTA_TRUSTED_PRINCIPAL": "task-main", "AOTA_TRUSTED_WORKSPACE_ID": "fixture-workspace"})
        desktop = mod.get_trusted_runtime_context({"session_id": "desktop-session", "task_id": "turn-task"})
        check(desktop.usable_orchestrator_session and desktop.session_id == "desktop-session", "desktop registry shape")
        print("DESKTOP_CONTEXT=PASS")

        cli = mod.get_trusted_runtime_context({"session_key": "cli-session"})
        check(cli.usable_orchestrator_session and cli.session_id == "cli-session", "cli session-key alias")
        print("CLI_CONTEXT=PASS")

        os.environ["AOTA_ORIGIN_SESSION_ID"] = "origin-session"
        os.environ["AOTA_PARENT_SESSION_ID"] = "parent-session"
        webui = mod.get_trusted_runtime_context({"conversation_id": "webui-session"})
        check(webui.usable_orchestrator_session and webui.origin_session_id == "origin-session" and webui.parent_session_id == "parent-session", "webui lineage shape")
        print("WEBUI_CONTEXT=PASS")

        worker_env = {"AOTA_PROFILE_TASK_ID": "pt_worker", "AOTA_PROFILE_TASK_START_ID": "start_worker", "AOTA_PROFILE_TASK_PROFILE": "debugger"}
        os.environ.update(worker_env)
        worker = mod.get_trusted_runtime_context({"session_id": "worker-session"})
        check(worker.worker_context and not worker.usable_orchestrator_session, "worker principal separation")
        print("WORKER_DENIED=PASS")

        for name in worker_env:
            os.environ.pop(name, None)
        missing = mod.get_trusted_runtime_context({})
        check(not missing.usable_orchestrator_session, "missing context fail closed")
        result = mod.missing_context_result(operation="freeze")
        check(result["error"] == "trusted_session_context_missing" and result["retryable"] is False and result["next_action"] == "stop_and_report_runtime_context_missing", "missing context result")
        print("MISSING_CONTEXT=PASS")
    finally:
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    print("TRUSTED_RUNTIME_CONTEXT_FIXTURE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
