#!/usr/bin/env python3
"""Isolated source E2E for registered workspace routing; no real worker starts."""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "aota-tools"
FIXTURE = REPO / "fixtures" / "project-continuity" / "valid" / ".aota" / "project.yaml"


def load() -> None:
    pkg = types.ModuleType("aota_tools"); pkg.__path__ = [str(PLUGIN)]; sys.modules["aota_tools"] = pkg


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="workspace-context-") as raw:
        root = Path(raw); workspace = root / "tools"; manifest = workspace / ".aota" / "project.yaml"
        manifest.parent.mkdir(parents=True); manifest.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
        registry = root / "workspaces.json"; registry.write_text(json.dumps({"fixture-tools": {"candidates": [str(workspace)], "status": "active", "summary": "fixture"}}), encoding="utf-8")
        os.environ.update({"AOTA_WORKSPACE_REGISTRY_PATH": str(registry), "AOTA_RUNTIME_ROOT": str(root / "runtime"), "AOTA_PROFILE_TASK_ROOT": str(root / "tasks")})
        load()
        context = importlib.import_module("aota_tools._workspace_context")
        selection = importlib.import_module("aota_tools._workspace_selection_record")
        contract = importlib.import_module("aota_tools._spec_contract")
        plan = importlib.import_module("aota_tools._plan_common")
        handoffs = importlib.import_module("aota_tools._handoff_common")
        listed = context.list_workspaces({"max_items": 5}); assert listed["status"] == "ok" and listed["workspaces"][0]["workspace_id"] == "fixture-tools"
        opened = context.open_workspace({"workspace_id": "fixture-tools"}); assert opened["status"] == "ok" and opened["projects"][0]["project_id"] == "fixture-project"
        print("AOTA_WORKSPACE_LIST_SOURCE_PASS"); print("AOTA_WORKSPACE_OPEN_SOURCE_PASS")
        selected = json.loads(selection.handle({"workspace_id": "fixture-tools", "project_id": "fixture-project", "source_type": "user_supplied", "relationship": "existing"}))
        assert selected["status"] == "recorded"; frozen = selected["workspace_context"]
        assert json.loads(selection.handle({"workspace_id": "fixture-tools", "project_id": "fixture-project", "source_type": "user_supplied", "relationship": "existing"}, profile="project-steward"))["error_code"] == "workspace_selection_authority_denied"
        assert context.context_from_selection("fixture-tools", "fixture-project", selected["decision_id"])["decision_id"] == selected["decision_id"]
        print("AOTA_TASK_MAIN_WORKSPACE_SELECTION_PASS"); print("AOTA_USER_SUPPLIED_WORKSPACE_VALIDATION_PASS"); print("AOTA_WORKSPACE_SELECTION_AUTHORITY_PASS")
        bound_plan = plan.create_plan(title="fixture", goal="routing", workspace_context={"workspace_id": "fixture-tools", "project_id": "fixture-project", "decision_id": selected["decision_id"], "relationship": "existing"})
        assert bound_plan["workspace_context"]["decision_id"] == selected["decision_id"]
        spec = {"schema_version": 1, "artifact_type": "spec", "spec_id": "pt_20260718T000000_deadbeef", "project_id": "fixture-project", "work_item_id": "wi_fixture", "spec_kind": "implementation", "resolved_profile": "coder", "revision": 1, "status": "frozen", "created_at": "2026-07-18T00:00:00Z", "updated_at": "2026-07-18T00:00:00Z", "created_by": "task-main", "objective": "fixture", "summary": "fixture", "context_refs": [], "related_artifacts": [], "acceptance_criteria": ["pass"], "constraints": [], "forbidden_actions": [], "expected_artifacts": [], "capability_contract": {"source_read": True, "source_write": True}, "payload": {"read_scope": ["src/**"], "write_scope": ["src/**"], "forbidden_scope": ["x/**"], "implementation_requirements": ["fixture"], "validation_strategy": "compile", "runtime_actions": {}}, "supersedes_spec_id": None, "workspace_context": frozen, "spec_hash": None, "frozen_at": "2026-07-18T00:00:00Z"}
        spec["spec_hash"] = contract.canonical_hash(spec); contract.validate_spec(spec, frozen=True)
        task_dir = Path(os.environ["AOTA_PROFILE_TASK_ROOT"]) / "fixture-tools" / spec["spec_id"]; task_dir.mkdir(parents=True)
        meta = dict(spec); meta.update({"contract_version": 1, "task_id": spec["spec_id"], "workspace_id": "fixture-tools", "spec": spec})
        card = contract.apply_common_card(meta, "coder", {"outcome": "completed", "verdict": "pass", "summary": "ok", "evidence_refs": [], "needs_full_report_review": False, "recommended_next_action": "task-main review"}, "RESULT.md")
        (task_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8"); (task_dir / "CARD.json").write_text(json.dumps(card), encoding="utf-8"); (task_dir / "RESULT.md").write_text("ok", encoding="utf-8")
        handoff = handoffs.build_handoff_data("fixture-tools", spec["spec_id"], spec["spec_id"], "coder", "implementation", "done", "2026-07-18T00:00:00Z", task_dir=task_dir)
        assert handoff["binding"] == card["binding"] and handoff["binding"]["workspace_decision_id"] == selected["decision_id"]
        tampered = dict(card); tampered["binding"] = dict(card["binding"], project_id="other")
        try: contract.common_card_fields(meta, "coder", tampered)
        except contract.ContractError: pass
        else: raise AssertionError("card tampering accepted")
        print("AOTA_PLAN_WORKSPACE_CONTEXT_PASS"); print("AOTA_SPEC_FROZEN_WORKSPACE_CONTEXT_PASS"); print("AOTA_PROFILE_TASK_CONTEXT_INJECTION_PASS"); print("AOTA_CARD_HANDOFF_BINDING_PASS"); print("AOTA_CONTEXT_TAMPER_REJECT_PASS")
        print("AOTA_WORKSPACE_CONTEXT_ROUTING_FIXTURE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
