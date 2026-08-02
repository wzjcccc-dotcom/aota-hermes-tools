#!/usr/bin/env python3
"""Zero-LLM fixtures for the semantic P0 dispatch facade."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "aota-tools"


def load_package() -> None:
    package = types.ModuleType("aota_tools")
    package.__path__ = [str(PLUGIN)]  # type: ignore[attr-defined]
    sys.modules["aota_tools"] = package
    spec = importlib.util.spec_from_file_location(
        "aota_tools", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)]
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["aota_tools"] = module
    spec.loader.exec_module(module)


def main() -> int:
    load_package()
    dispatch = importlib.import_module("aota_tools._profile_task_dispatch")
    context = SimpleNamespace(
        usable_for_active_spec=True,
        workspace_id="fixture",
        project_id="",
        session_id="session-p0",
    )
    calls: list[tuple[str, dict]] = []
    original = (
        dispatch.trusted_session_context,
        dispatch.read_current_work_classification_pointer,
        dispatch._create_spec,
        dispatch._freeze_spec,
        dispatch._start_profile_task,
    )
    try:
        dispatch.trusted_session_context = lambda _kwargs: context
        dispatch.read_current_work_classification_pointer = lambda *_args, **_kwargs: {
            "classification": "P0", "state": "active", "session_id": "session-p0",
        }

        def create(args: dict, **_kwargs: object) -> str:
            calls.append(("create", dict(args)))
            return json.dumps({"status": "created", "task_id": "internal-task", "error": None})

        def freeze(args: dict, **_kwargs: object) -> str:
            calls.append(("freeze", dict(args)))
            return json.dumps({"status": "frozen", "error": None})

        def start(args: dict, **_kwargs: object) -> str:
            calls.append(("start", dict(args)))
            return json.dumps({
                "status": "running", "task_id": "internal-task", "workspace_id": "fixture",
                "profile": "architect", "process_session_id": "internal-process",
                "completion_transport": "terminal_background",
                "completion_delivery_expected": False,
                "next_action": "wait_for_completion_delivery",
                "recovery_allowed_after": "2030-01-01T00:00:00Z",
            })

        dispatch._create_spec = create
        dispatch._freeze_spec = freeze
        dispatch._start_profile_task = start
        result = json.loads(dispatch.handle({
            "spec_kind": "architecture",
            "objective": "Read one exact README path.",
            "read_scope": ["README.md"],
            "requirements": ["Report the architecture implication."],
            "constraints": ["read-only"],
        }, session_id="session-p0", profile="task-main"))
        assert result["status"] == "running", result
        assert result["operation_result"] == "aota_profile_task_dispatch", result
        assert result["dispatch_stage"] == "worker_start", result
        assert result["next_action"] == "wait_for_completion_delivery", result
        assert "task_id" not in result and "process_session_id" not in result, result
        assert [name for name, _ in calls] == ["create", "freeze", "start"], calls
        assert calls[0][1]["spec_kind"] == "architecture", calls
        assert "workspace_id" not in calls[0][1], calls
        assert calls[1][1] == {"spec_ref": "current_draft_spec"}, calls
        assert calls[2][1] == {"task_ref": "active_frozen_spec"}, calls
        print("P0_DISPATCH_SINGLE_ROUND_TRIP=PASS")

        dispatch.read_current_work_classification_pointer = lambda *_args, **_kwargs: {
            "classification": "P1", "state": "active", "session_id": "session-p0",
        }
        calls.clear()
        p1 = json.loads(dispatch.handle({"spec_kind": "diagnosis", "objective": "Diagnose"}, session_id="session-p0", profile="task-main"))
        assert p1["error"] == "p0_dispatch_requires_p0_classification", p1
        assert p1["next_action"] == "use_semantic_spec_chain", p1
        assert not calls, calls
        print("P1_P2_OLD_CHAIN_PRESERVED=PASS")

        dispatch.read_current_work_classification_pointer = lambda *_args, **_kwargs: {
            "classification": "P0", "state": "active", "session_id": "session-p0",
        }
        dispatch._create_spec = lambda *_args, **_kwargs: json.dumps({
            "status": "rejected", "error": "semantic_invalid", "retryable": True,
            "same_call_retryable": False, "retry_scope": "changed_arguments_only",
            "flow_disposition": "continue", "next_action": "repair_semantic_arguments",
        })
        repaired = json.loads(dispatch.handle({"spec_kind": "architecture", "objective": "Bad"}, session_id="session-p0", profile="task-main"))
        assert repaired["dispatch_stage"] == "spec_create", repaired
        assert repaired["retryable"] is True and repaired["same_call_retryable"] is False, repaired
        print("SCOPED_REPAIR_NO_SAME_CALL_RETRY=PASS")

        properties = dispatch.SCHEMA["parameters"]["properties"]
        forbidden = {"workspace_id", "project_id", "task_id", "spec_id", "revision", "hash", "profile", "tool"}
        assert not forbidden & set(properties), forbidden & set(properties)
        assert dispatch.SCHEMA["parameters"]["required"] == ["spec_kind", "objective"]
        print("P0_DISPATCH_MODEL_SCHEMA_MINIMAL=PASS")
    finally:
        (
            dispatch.trusted_session_context,
            dispatch.read_current_work_classification_pointer,
            dispatch._create_spec,
            dispatch._freeze_spec,
            dispatch._start_profile_task,
        ) = original
    print("AOTA_P0_DISPATCH_FACADE_FIXTURE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
