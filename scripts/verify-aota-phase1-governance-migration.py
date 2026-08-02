#!/usr/bin/env python3
"""Isolated Phase 1 schema/resolver fixture; no runtime or durable writes."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "aota-tools"


def load_package() -> None:
    package = types.ModuleType("aota_tools")
    package.__path__ = [str(PLUGIN)]  # type: ignore[attr-defined]
    sys.modules["aota_tools"] = package
    spec = importlib.util.spec_from_file_location("aota_tools", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)])
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["aota_tools"] = module
    spec.loader.exec_module(module)


def main() -> int:
    load_package()
    from aota_tools import _plan_mutation as plan
    from aota_tools import _plan_open as plan_open
    from aota_tools import _task_spec_create as spec_create
    from aota_tools import _task_spec_update as spec_update
    from aota_tools import _task_spec_freeze as spec_freeze
    from aota_tools import _profile_task_approve as approval
    from aota_tools import _reference_resolver as resolver

    assert plan.CREATE_SCHEMA["parameters"]["required"] == ["title", "objective"]
    assert plan.UPDATE_SCHEMA["parameters"]["required"] == ["operation", "payload"]
    assert "freeze_plan" in plan.UPDATE_SCHEMA["parameters"]["properties"]["operation"]["enum"]
    assert plan_open.SCHEMA["parameters"]["required"] == []
    assert spec_create.SCHEMA["parameters"]["required"] == ["spec_kind", "objective"]
    assert spec_update.SCHEMA["parameters"]["required"] == ["patch"]
    assert spec_freeze.SCHEMA["parameters"]["required"] == ["spec_ref"]
    assert approval.SCHEMA["parameters"]["required"] == ["decision", "rationale"]
    assert not {"workspace_id", "plan_id", "spec_id", "revision", "expected_revision", "spec_hash", "spec_sha256"} & set(approval.SCHEMA["parameters"]["properties"])
    print("CONTROL_PLANE_FIELDS_HIDDEN=PASS")

    created = plan._semantic_create_args({"title": "README diagnosis", "objective": "Confirm README is readable"}, {"workspace_id": "fixture", "session_id": "s1", "profile": "task-main"})
    assert created["workspace_id"] == "fixture" and created["goal"] == created["objective"]
    assert (created["planning_depth"], created["architect_gate"], created["delivery_path"]) == ("P1", "A1", "standard")
    print("PLAN_MINIMAL_DEFAULTS=PASS")

    with tempfile.TemporaryDirectory(prefix="aota-phase1-plan-") as temp:
        workspace_root = Path(temp)
        original_plan_functions = (plan.require_plan_write_authority, plan.resolve_workspace, plan.prepare_plan_mutation_audit, plan.commit_plan_mutation_audit, plan.assert_no_unresolved_plan_audit)
        try:
            plan.require_plan_write_authority = lambda **_kwargs: {"fixture": True}
            plan.resolve_workspace = lambda _workspace_id: workspace_root
            plan.prepare_plan_mutation_audit = lambda **_kwargs: {"audit_event_id": "audit-fixture"}
            plan.commit_plan_mutation_audit = lambda **_kwargs: None
            plan.assert_no_unresolved_plan_audit = lambda **_kwargs: None
            first = json.loads(plan.handle_create({"title": "README diagnosis", "objective": "Confirm README is readable"}, workspace_id="fixture", session_id="s1", profile="task-main"))
            assert first["status"] == "created" and first["operation_result"] == "plan_created"
            current = {"plan_id": first["plan_id"], "revision": first["revision"], "active_milestone_id": None}
            original_resolve = plan.resolve_current_plan
            plan.resolve_current_plan = lambda _workspace: current
            try:
                second = json.loads(plan.handle_update({"plan_ref": "current_plan", "operation": "update_current_state", "payload": {"current_state": "ready"}}, workspace_id="fixture", session_id="s1", profile="task-main"))
            finally:
                plan.resolve_current_plan = original_resolve
            assert second["status"] == "updated" and second["revision"] == 2
            print("PLAN_FIRST_CALL_SUCCESS=PASS")
        finally:
            (plan.require_plan_write_authority, plan.resolve_workspace, plan.prepare_plan_mutation_audit, plan.commit_plan_mutation_audit, plan.assert_no_unresolved_plan_audit) = original_plan_functions

    original_plan = plan.resolve_current_plan
    original_item = plan.resolve_current_work_item
    try:
        plan.resolve_current_plan = lambda _workspace: {"plan_id": "plan_fixture", "revision": 4, "active_milestone_id": "ms_fixture"}
        plan.resolve_current_work_item = lambda _workspace, _ref: ({"plan_id": "plan_fixture"}, {"work_item_id": "wi_fixture"})
        normalized = plan._semantic_update_args({"plan_ref": "current_plan", "operation": "set_work_item_status", "payload": {"status": "ready"}}, {"workspace_id": "fixture", "session_id": "s1", "profile": "task-main"})
        assert normalized["plan_id"] == "plan_fixture" and normalized["expected_revision"] == 4
        assert normalized["payload"]["work_item_id"] == "wi_fixture"
        print("CURRENT_PLAN_AND_WORK_ITEM_RESOLUTION=PASS")
        def ambiguous(_workspace: str) -> dict:
            raise resolver.ReferenceError("reference_ambiguous", detail="multiple_current_plans", choices=[{"selector": "choice:1"}, {"selector": "choice:2"}])
        plan.resolve_current_plan = ambiguous
        try:
            plan._semantic_update_args({"operation": "set_plan_status", "payload": {"status": "approved"}}, {"workspace_id": "fixture", "session_id": "s1", "profile": "task-main"})
        except plan._SemanticResolutionError as exc:
            assert exc.result["human_action_required"] is True and len(exc.result["choices"]) == 2
        else:
            raise AssertionError("ambiguous current Plan was not rejected")
        print("BOUNDED_PLAN_AMBIGUITY=PASS")
    finally:
        plan.resolve_current_plan = original_plan
        plan.resolve_current_work_item = original_item

    captured: dict[str, object] = {}
    original_approval_resolve = approval.resolve_for_start
    original_approval_do = approval._do_approve
    try:
        approval.resolve_for_start = lambda *_args, **_kwargs: {"workspace_id": "fixture", "task_id": "task_fixture", "expected_revision": 3, "expected_spec_hash": "canonical"}
        approval._do_approve = lambda args: captured.update(args) or json.dumps({"status": "approved"})
        result = json.loads(approval.handle({"decision": "approve", "rationale": "範圍與驗收條件已確認"}, workspace_id="fixture", session_id="s1", profile="task-main"))
        assert result["status"] == "approved" and captured == {"workspace_id": "fixture", "task_id": "task_fixture", "expected_revision": 3, "expected_spec_hash": "canonical", "decision": "approve", "rationale": "範圍與驗收條件已確認"}, (result, captured)
        print("APPROVAL_FIRST_CALL_SUCCESS=PASS")
    finally:
        approval.resolve_for_start = original_approval_resolve
        approval._do_approve = original_approval_do

    inventory = json.loads((ROOT / "deploy/evidence/control-plane-minimal-invocation-inventory.json").read_text(encoding="utf-8"))
    selected = {item["tool_name"]: item for item in inventory["tools"] if "phase_1_migration" in item}
    assert len(selected) == 8
    assert all(not set(item["phase_1_migration"]["target_model_required_fields"]) & set(item["phase_1_migration"]["control_plane_fields_to_hide"]) for item in selected.values())
    print("PHASE1_INVENTORY=PASS")
    print("FIRST_CALL_INTERNAL_PARAMETER_FAILURES=0")
    print("SAME_TOOL_GUESS_AND_RETRY=0")
    print("AOTA_PHASE1_GOVERNANCE_MIGRATION_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
