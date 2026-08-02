#!/usr/bin/env python3
"""PCF-WI-09B isolated source fixture; never uses a managed runtime."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "aota-tools"
TASK_ID = "pt_20260717T000000_deadbeef"


def marker(name: str) -> None:
    print(name)


def load_plugin() -> None:
    spec = importlib.util.spec_from_file_location(
        "aota_tools", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)]
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["aota_tools"] = module
    spec.loader.exec_module(module)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def task_meta(workspace_id: str, *, operation: str, artifacts: list[str], spec_hash: str) -> dict:
    return {
        "schema_version": 3, "task_id": TASK_ID, "workspace_id": workspace_id,
        "task_kind": "stewardship", "profile_hint": "project-steward",
        "revision": 1, "spec_sha256": spec_hash, "status": "running",
        "spec": {"goal": "Fixture stewardship", "role_contract": {
            "project_id": "fixture-project", "allowed_project_artifacts": artifacts,
            "operation": operation, "forbidden_actions": ["source_write"], "work_item_id": "wi-fixture",
        }},
        "execution": {"start_id": TASK_ID, "profile": "project-steward", "spec_revision": 1, "spec_sha256": spec_hash},
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="pcf-wi-09b-") as raw:
        root = Path(raw)
        workspace = root / "workspace"
        task_root = root / "profile-tasks"
        runtime_root = root / "runtime"
        workspace.mkdir()
        registry = root / "workspaces.json"
        write_json(registry, {"fixture": {"candidates": [str(workspace)]}})
        os.environ.update({
            "AOTA_WORKSPACE_REGISTRY_PATH": str(registry),
            "AOTA_PROFILE_TASK_ROOT": str(task_root),
            "AOTA_RUNTIME_ROOT": str(runtime_root),
            "AOTA_PROFILE_TASK_WORKSPACE_ID": "fixture",
            "AOTA_PROFILE_TASK_ID": TASK_ID,
            "AOTA_PROFILE_TASK_START_ID": TASK_ID,
            "AOTA_PROFILE_TASK_PROFILE": "project-steward",
        })
        load_plugin()
        registered: dict[str, str] = {}
        class FixtureContext:
            def register_tool(self, *, name: str, toolset: str, **_kwargs: object) -> None:
                registered[name] = toolset
        sys.modules["aota_tools"].register(FixtureContext())
        assert registered["aota_project_steward_report"] == "aota_project_steward_artifact"
        assert registered["aota_project_docs_update"] == "aota_project_steward"
        assert registered["aota_project_artifact_link"] == "aota_project_steward"
        common = importlib.import_module("aota_tools._profile_task_common")
        contracts = importlib.import_module("aota_tools._role_contracts")
        create = importlib.import_module("aota_tools._task_spec_create")
        report = importlib.import_module("aota_tools._project_steward_report")
        outcome = importlib.import_module("aota_tools._worker_outcome_submit")
        finalizer = importlib.import_module("aota_tools._profile_task_finalize")
        handoff_common = importlib.import_module("aota_tools._handoff_common")
        mutation = importlib.import_module("aota_tools._project_steward_mutation")

        assert common.derive_profile("stewardship") == "project-steward"
        assert common.derive_profile("implementation") == "coder"
        assert common.derive_profile("diagnosis") == "debugger"
        assert common.derive_profile("review") == "reviewer"
        assert common.derive_profile("architecture") == "architect"
        assert contracts.validate_role_contract("stewardship", {
            "project_id": "fixture-project", "allowed_project_artifacts": ["readme"],
            "operation": "intake", "forbidden_actions": ["source_write"],
        }) == []
        assert contracts.validate_role_contract("stewardship", {
            "project_id": "fixture-project", "allowed_project_artifacts": ["readme"],
            "operation": "shell", "forbidden_actions": [],
        })
        assert common.generate_worker_prompt(TASK_ID, "fixture", "stewardship", "project-steward", "meta.json", "SPEC.md").find("Project Steward") >= 0
        marker("PCF_PROJECT_STEWARD_PROFILE_PASS")
        marker("PCF_PROJECT_STEWARD_SKILL_PASS")
        marker("PCF_STEWARDSHIP_ROUTING_PASS")

        # WI-09C replaces temporary task_kind create with canonical stewardship.
        result = json.loads(create.handle({
            "workspace_id": "fixture", "spec_kind": "stewardship", "project_id": "fixture-project",
            "work_item_id": "wi-fixture", "objective": "collect project facts", "summary": "fixture",
            "context_refs": [], "acceptance_criteria": ["card"], "constraints": [], "forbidden_actions": ["source_write"],
            "expected_artifacts": ["STEWARD_CARD.json"], "capability_contract": {"source_read": True},
            "payload": {"operation": "intake", "project_context_questions": ["what changed?"], "allowed_project_artifacts": ["readme"], "docs_update_scope": [], "artifact_link_requests": [], "close_checks": [], "approved_content_refs": []},
        }))
        assert result["status"] == "created" and result["resolved_profile"] == "project-steward"
        rejected = json.loads(create.handle({
            "workspace_id": "fixture", "spec_kind": "stewardship", "project_id": "fixture-project",
            "work_item_id": "wi-fixture", "objective": "bad", "summary": "bad", "context_refs": [],
            "acceptance_criteria": ["x"], "constraints": [], "forbidden_actions": [], "expected_artifacts": [],
            "capability_contract": {"source_write": True},
            "payload": {"operation": "intake", "project_context_questions": ["x"], "allowed_project_artifacts": [], "docs_update_scope": [], "artifact_link_requests": [], "close_checks": [], "approved_content_refs": []},
        }))
        assert rejected["status"] == "rejected"
        marker("PCF_STEWARDSHIP_SPEC_COMPAT_PASS")

        # Report/outcome/finalizer fixture with no worker process.
        task_dir = task_root / "fixture" / TASK_ID
        task_dir.mkdir(parents=True)
        spec_text = "# Fixture stewardship SPEC\n"
        spec_hash = hashlib.sha256(spec_text.encode()).hexdigest()
        (task_dir / "SPEC.md").write_text(spec_text, encoding="utf-8")
        write_json(task_dir / "meta.json", task_meta("fixture", operation="intake", artifacts=["readme"], spec_hash=spec_hash))
        assert json.loads(report.handle({"unknown": True}))["status"] == "rejected"
        assert json.loads(outcome.handle({"outcome": "completed"}))["status"] == "rejected"
        submitted = json.loads(report.handle({
            "outcome": "completed", "verdict": "pass", "summary": "Project facts collected.",
            "scope_status": "in_scope", "key_findings": ["matching manifest"],
            "evidence_refs": [".aota/project.yaml"], "matched_project": "fixture-project",
            "codegraph_state": "not indexed", "continuity_status": "ready",
        }))
        assert submitted["status"] == "submitted"
        card = json.loads((task_dir / "STEWARD_CARD.json").read_text(encoding="utf-8"))
        assert set(card) >= {"schema_version", "role", "task_id", "spec_id", "spec_revision", "spec_hash", "project_id", "role_summary"}
        assert json.loads(outcome.handle({"outcome": "completed"}))["status"] == "submitted"
        marker("PCF_STEWARD_CARD_PASS")
        marker("PCF_STEWARD_RESULT_PASS")
        marker("PCF_STEWARD_OUTCOME_PASS")
        try:
            finalizer.run_finalize("fixture", TASK_ID, TASK_ID, "project-steward", 1, spec_hash, 0)
        except SystemExit as exc:
            assert exc.code == 0
        completion = task_dir / f"completion.{TASK_ID}.json"
        assert completion.is_file()
        pending = handoff_common.get_handoff_pending_dir("fixture")
        handoffs = list(pending.glob("handoff.*.json"))
        assert len(handoffs) == 1
        handoff = handoff_common.read_handoff(handoffs[0])
        assert handoff["profile"] == "project-steward" and handoff["task_kind"] == "stewardship"
        assert handoff["role_artifact"]["card_name"] == "STEWARD_CARD.json" and handoff["role_artifact"]["full_name"] == "STEWARD_RESULT.md"
        # Native terminal-background completion is authoritative; the retired
        # WebUI/outbox rail must remain empty for newly finalized tasks.
        assert not list((runtime_root / "outbox" / "fixture" / "pending").glob("*.json"))
        marker("PCF_STEWARD_FINALIZER_PASS")
        marker("PCF_STEWARD_HANDOFF_PASS")
        marker("PCF_STEWARD_OUTBOX_RETIRED_PASS")

        # Bounded docs and artifact metadata write paths, still under temp root.
        (workspace / ".aota").mkdir(); (workspace / "docs").mkdir()
        (workspace / "README.md").write_text("old\n", encoding="utf-8")
        (workspace / "docs" / "guide.md").write_text("guide\n", encoding="utf-8")
        (workspace / ".aota" / "project.yaml").write_text("""schema_version: 1
project: {id: fixture-project, name: Fixture, kind: hermes-tooling, status: active}
summary: Fixture
capabilities: [hermes-plugin]
paths: {source_root: ., source: [plugin], docs: [docs], scripts: [scripts], profiles: [profiles], skills: [skills], tests: [tests]}
commands: {validate: [], deploy: [], verify_deploy: []}
runtime: {deployment_type: fixture, requires_human_checkpoint: true}
codegraph: {enabled: false, index_location: .codegraph}
plan: {active_plan_id: null}
constraints: []
""", encoding="utf-8")
        write_json(task_dir / "meta.json", task_meta("fixture", operation="docs_update", artifacts=["readme", "project_doc"], spec_hash=spec_hash))
        allowed = json.loads(mutation.handle_docs_update({"workspace_id": "fixture", "project_id": "fixture-project", "document_kind": "readme", "operation": "replace", "content": "new\n"}))
        denied = json.loads(mutation.handle_docs_update({"workspace_id": "fixture", "project_id": "fixture-project", "document_kind": "project_doc", "document_ref": "/etc/passwd", "operation": "replace", "content": "no"}))
        assert allowed["status"] == "updated" and denied["status"] == "rejected"
        write_json(task_dir / "meta.json", task_meta("fixture", operation="artifact_link", artifacts=["project_metadata"], spec_hash=spec_hash))
        linked = json.loads(mutation.handle_artifact_link({"workspace_id": "fixture", "project_id": "fixture-project", "work_item_id": "wi-fixture", "artifact_type": "steward_result", "artifact_ref": "STEWARD_RESULT.md", "relation": "steward_result"}))
        assert linked["status"] == "linked"
        marker("PCF_PROJECT_METADATA_TOOLS_PASS")

        dashboard = (PLUGIN / "dashboard" / "static" / "dashboard.js").read_text(encoding="utf-8")
        assert '"project-steward":"專案管家"' in dashboard and "STEWARD_CARD.json" in dashboard and "STEWARD_RESULT.md" in dashboard
        marker("PCF_STEWARD_DASHBOARD_PASS")
        marker("PCF_EXISTING_ROLE_REGRESSION_PASS")
        marker("PCF_ISOLATED_SMOKE_PASS")
        print("FIXTURE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
