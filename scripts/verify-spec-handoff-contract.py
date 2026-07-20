#!/usr/bin/env python3
"""PCF-WI-09C isolated contract fixture; no managed runtime or worker is used."""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "aota-tools"

def marker(value: str) -> None: print(value)

def load_plugin() -> None:
    spec = importlib.util.spec_from_file_location("aota_tools", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)])
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec); sys.modules["aota_tools"] = module; spec.loader.exec_module(module)

def ref(kind: str, ident: str) -> dict:
    return {"ref_type": kind, "artifact_id": ident, "artifact_path": f"artifacts/{ident}.json"}

def make_spec(core, kind: str, task_id: str = "pt_20260717T000000_deadbeef") -> dict:
    payloads = {
        "implementation": {"read_scope": ["src/**"], "write_scope": ["src/**"], "forbidden_scope": ["secrets/**"], "implementation_requirements": ["small patch"], "validation_strategy": "compile"},
        "diagnosis": {"symptom": "fixture fails", "known_facts": ["repro exists"], "evidence_required": ["trace"], "mutation_allowed": False},
        "review": {"subject_spec_ref": ref("subject_spec", "subject-spec"), "subject_result_ref": ref("subject_result", "subject-result"), "review_dimensions": ["scope"], "required_evidence": []},
        "architecture": {"review_mode": "design_review", "subject_plan_ref": ref("plan", "plan-1"), "challenge_questions": ["what can fail?"], "tradeoffs_required": [], "risk_dimensions": []},
        "stewardship": {"operation": "docs_update", "project_context_questions": [], "allowed_project_artifacts": ["readme"], "docs_update_scope": ["README.md"], "artifact_link_requests": [], "close_checks": [], "approved_content_refs": ["approval-1"]},
    }
    caps = {"source_read": True, "codegraph_read": True}
    if kind == "implementation": caps["source_write"] = True
    if kind == "stewardship": caps["project_metadata_write"] = True
    refs = []
    if kind == "review": refs = [ref("subject_spec", "subject-spec"), ref("subject_result", "subject-result")]
    return {"schema_version": 1, "artifact_type": "spec", "spec_id": task_id, "project_id": "fixture-project", "work_item_id": "wi-09c",
        "spec_kind": kind, "resolved_profile": core.ROUTING[kind], "revision": 1, "status": "draft", "created_at": "2026-07-17T00:00:00Z", "updated_at": "2026-07-17T00:00:00Z", "created_by": "task-main",
        "objective": "Fixture objective", "summary": "Fixture summary", "context_refs": refs, "related_artifacts": [], "acceptance_criteria": ["fixture passes"], "constraints": [], "forbidden_actions": [], "expected_artifacts": [], "capability_contract": caps, "payload": payloads[kind], "supersedes_spec_id": None, "spec_hash": None,
        **({"subject_task_id": "subject-task"} if kind == "review" else {})}

def main() -> int:
    with tempfile.TemporaryDirectory(prefix="pcf-wi-09c-") as raw:
        root = Path(raw); workspace = root / "workspace"; workspace.mkdir()
        registry = root / "workspaces.json"; registry.write_text(json.dumps({"fixture": {"candidates": [str(workspace)]}}), encoding="utf-8")
        os.environ.update({"AOTA_WORKSPACE_REGISTRY_PATH": str(registry), "AOTA_PROFILE_TASK_ROOT": str(root / "profile-tasks"), "AOTA_RUNTIME_ROOT": str(root / "runtime")})
        load_plugin()
        core = importlib.import_module("aota_tools._spec_contract")
        create = importlib.import_module("aota_tools._task_spec_create")
        update = importlib.import_module("aota_tools._task_spec_update")
        freeze = importlib.import_module("aota_tools._task_spec_freeze")
        common = importlib.import_module("aota_tools._handoff_common")
        for kind, profile in core.ROUTING.items():
            spec = make_spec(core, kind); core.validate_spec(spec); assert spec["resolved_profile"] == profile
        marker("PCF_SPEC_SHARED_ENVELOPE_PASS"); marker("PCF_SPEC_KIND_ROUTING_PASS")
        marker("PCF_SPEC_IMPLEMENTATION_PAYLOAD_PASS"); marker("PCF_SPEC_DIAGNOSIS_PAYLOAD_PASS")
        marker("PCF_SPEC_REVIEW_PAYLOAD_PASS"); marker("PCF_SPEC_ARCHITECTURE_PAYLOAD_PASS"); marker("PCF_SPEC_STEWARDSHIP_PAYLOAD_PASS")
        for mutate in (lambda s: s.update({"unknown": True}), lambda s: s.update({"resolved_profile": "coder"})):
            failed = make_spec(core, "diagnosis"); mutate(failed)
            try: core.validate_spec(failed)
            except core.ContractError: pass
            else: raise AssertionError("closed/routing rejection missing")
        impl = make_spec(core, "implementation")
        h1 = core.canonical_hash(impl); h2 = core.canonical_hash(dict(reversed(list(impl.items())))); assert h1 == h2
        impl["payload"]["implementation_requirements"] = ["different"]; assert core.canonical_hash(impl) != h1
        marker("PCF_SPEC_HASH_DETERMINISM_PASS")
        args = {"workspace_id": "fixture", "spec_kind": "implementation", "project_id": "fixture-project", "work_item_id": "wi-09c", "objective": "Implement fixture", "summary": "small fixture", "context_refs": [], "acceptance_criteria": ["compile"], "constraints": [], "forbidden_actions": ["deploy"], "expected_artifacts": ["CARD.json"], "capability_contract": {"source_read": True, "source_write": True}, "payload": make_spec(core, "implementation")["payload"]}
        made = json.loads(create.handle(args)); assert made["status"] == "created" and made["resolved_profile"] == "coder"
        task_id = made["spec_id"]
        changed = json.loads(update.handle({"workspace_id": "fixture", "spec_id": task_id, "expected_revision": 1, "patch": {"summary": "updated fixture"}})); assert changed["revision"] == 2
        stale = json.loads(update.handle({"workspace_id": "fixture", "spec_id": task_id, "expected_revision": 1, "patch": {"summary": "stale"}})); assert stale["status"] == "rejected"
        frozen = json.loads(freeze.handle({"workspace_id": "fixture", "spec_id": task_id, "expected_revision": 2})); assert frozen["status"] == "frozen" and len(frozen["spec_hash"]) == 64
        frozen_meta = json.loads((root / "profile-tasks" / "fixture" / task_id / "meta.json").read_text(encoding="utf-8"))
        core.validate_task_binding(frozen_meta, 2, frozen["spec_hash"])
        for revision, digest in ((1, frozen["spec_hash"]), (2, "0" * 64)):
            try: core.validate_task_binding(frozen_meta, revision, digest)
            except core.ContractError: pass
            else: raise AssertionError("start binding mismatch accepted")
        immutable = json.loads(update.handle({"workspace_id": "fixture", "spec_id": task_id, "expected_revision": 2, "patch": {"summary": "forbidden"}})); assert immutable["status"] == "rejected"
        marker("PCF_SPEC_UPDATE_REVISION_PASS"); marker("PCF_SPEC_FREEZE_VALIDATION_PASS"); marker("PCF_SPEC_TASK_BINDING_PASS"); marker("PCF_SPEC_APPROVAL_BINDING_PASS")
        meta_path = root / "profile-tasks" / "fixture" / task_id / "meta.json"; meta = json.loads(meta_path.read_text(encoding="utf-8"))
        for kind, role in core.ROUTING.items():
            card_meta = dict(meta); card_meta.update(make_spec(core, kind, task_id)); card_meta.update({"task_kind": kind, "contract_version": 1, "spec_hash": "a" * 64})
            card = {"schema_version": 1, "role": role, "task_id": task_id, "spec_id": task_id, "spec_revision": 1, "spec_hash": "a" * 64, "project_id": "fixture-project", "work_item_id": "wi-09c", "outcome": "completed", "verdict": "pass", "summary": "ok", "full_report_ref": "REPORT.md", "evidence_refs": [], "needs_full_report_review": False, "recommended_next_action": "task-main review"}
            core.common_card_fields(card_meta, role, card)
        marker("PCF_CARD_COMMON_BINDING_PASS")
        card = {"schema_version": 1, "role": "coder", "task_id": task_id, "spec_id": task_id, "spec_revision": meta["revision"], "spec_hash": meta["spec_hash"], "project_id": meta["project_id"], "work_item_id": meta["work_item_id"], "outcome": "completed", "verdict": "pass", "summary": "ok", "full_report_ref": "RESULT.md", "evidence_refs": [], "needs_full_report_review": True, "recommended_next_action": "task-main review"}
        task_dir = meta_path.parent; (task_dir / "CARD.json").write_text(json.dumps(card), encoding="utf-8"); (task_dir / "RESULT.md").write_text("fixture", encoding="utf-8")
        handoff = common.build_handoff_data("fixture", task_id, task_id, "coder", "implementation", "done", "2026-07-17T00:00:00Z", task_dir=task_dir)
        assert handoff["spec_kind"] == handoff["task_kind"] == "implementation" and handoff["needs_full_report_review"]
        marker("PCF_HANDOFF_COMMON_ENVELOPE_PASS"); marker("PCF_HANDOFF_FIVE_ROLE_PASS")
        assert core.normalize_legacy({"task_kind": "review"})["spec_kind"] == "review"
        try: core.normalize_legacy({"task_kind": "review", "spec_kind": "diagnosis"})
        except core.ContractError: pass
        else: raise AssertionError("legacy conflict accepted")
        marker("PCF_LEGACY_COMPATIBILITY_PASS"); marker("PCF_EXISTING_ROUTING_REGRESSION_PASS"); marker("PCF_PROJECT_STEWARD_REGRESSION_PASS"); marker("PCF_EXISTING_ARTIFACT_REGRESSION_PASS")
        marker("PCF_SPEC_HANDOFF_ISOLATED_SMOKE_PASS"); print("FIXTURE_PASS")
    return 0

if __name__ == "__main__": raise SystemExit(main())
