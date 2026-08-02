#!/usr/bin/env python3
"""Isolated Phase 3 artifact/project semantic-invocation verifier.

This verifier uses a temporary workspace registry and project only.  It never
touches the host registry, initializes a real project, refreshes a real
registry, or performs a lifecycle mutation.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "aota-tools"
FORBIDDEN = {"workspace_id", "project_id", "project_root", "artifact_id", "artifact_path", "content_location", "manifest_path", "registry_path", "revision", "hash", "digest", "session_id", "task_id", "spec_id", "start_id"}


def load_package() -> None:
    package = types.ModuleType("aota_tools")
    package.__path__ = [str(PLUGIN)]  # type: ignore[attr-defined]
    sys.modules["aota_tools"] = package
    spec = importlib.util.spec_from_file_location("aota_tools", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)])
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["aota_tools"] = module
    spec.loader.exec_module(module)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fixture_workspace(root: Path) -> tuple[Path, Path]:
    workspace = root / "workspace"
    project = workspace / "fixture-project"
    manifest = project / ".aota" / "project.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("""schema_version: 1
project: {id: fixture-project, name: Fixture, kind: tooling, status: active}
summary: bounded fixture
capabilities: [hermes-plugin]
paths: {source_root: src, source: [src], docs: [docs], scripts: [scripts], profiles: [], skills: [], tests: []}
commands: {validate: [], deploy: [], verify_deploy: []}
runtime: {deployment_type: source, requires_human_checkpoint: true}
codegraph: {enabled: false, index_location: .codegraph}
plan: {active_plan_id: null}
constraints: []
""", encoding="utf-8")
    (project / "src").mkdir(parents=True)
    (project / "docs").mkdir()
    (project / "scripts").mkdir()
    write_json(project / ".aota" / "observed.json", {
        "schema_version": 1, "project_id": "fixture-project", "observed_at": "2026-07-28T00:00:00Z",
        "filesystem": {"root_exists": True, "manifest_exists": True},
        "git": {"available": False, "branch": "", "head": "", "dirty": False},
        "codegraph": {"configured": False, "index_present": False, "runtime_verified": False},
        "plan": {"active_plan_id": None, "revision": None, "sha256": None},
    })
    import hashlib
    registry = workspace / ".aota" / "registry" / "projects.json"
    registry.parent.mkdir(parents=True)
    write_json(registry, {"schema_version": 1, "workspace_id": "fixture", "registry_revision": 1, "generated_at": "2026-07-28T00:00:00Z", "source_fingerprint": "0" * 64, "project_count": 1, "projects": [{"project_id": "fixture-project", "root": "fixture-project", "manifest_path": "fixture-project/.aota/project.yaml", "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(), "name": "Fixture", "kind": "tooling", "status": "active", "summary": "bounded fixture", "capabilities": ["hermes-plugin"], "keywords": [], "relevant_paths": {}, "commands": {}, "active_plan_id": None, "constraints": [], "warnings": []}], "invalid_projects": [], "duplicate_project_ids": [], "scan_warnings": []})
    workspace_registry = root / "workspaces.json"
    write_json(workspace_registry, {"fixture": {"candidates": [str(workspace)]}})
    return workspace, workspace_registry


def main() -> int:
    load_package()
    from aota_tools import _phase3_control_plane as p3
    from aota_tools import _workspace

    selected = set(p3.PHASE3_SCHEMAS)
    assert len(selected) == 22, len(selected)
    for name, schema in p3.PHASE3_SCHEMAS.items():
        props = set(schema["parameters"]["properties"])
        required = set(schema["parameters"].get("required", []))
        assert not props & FORBIDDEN, (name, props & FORBIDDEN)
        assert not required & FORBIDDEN, (name, required & FORBIDDEN)
    print("PHASE3_CONTROL_PLANE_FIELDS_HIDDEN=PASS")

    with tempfile.TemporaryDirectory(prefix="aota-phase3-artifact-project-") as raw:
        root = Path(raw)
        workspace, registry = fixture_workspace(root)
        old_registry = _workspace._REGISTRY_PATH
        old_env = os.environ.get("AOTA_WORKSPACE_REGISTRY_PATH")
        old_project = os.environ.get("AOTA_TRUSTED_PROJECT_ID")
        _workspace._REGISTRY_PATH = registry
        os.environ["AOTA_WORKSPACE_REGISTRY_PATH"] = str(registry)
        os.environ["AOTA_TRUSTED_PROJECT_ID"] = "fixture-project"
        kwargs = {"workspace_id": "fixture", "session_id": "fixture-session", "profile": "task-main"}
        try:
            project = p3.resolve_current_project(kwargs=kwargs)
            assert project["project_id"] == "fixture-project"
            print("CURRENT_PROJECT_RESOLUTION=PASS")

            opened = p3.resolve_current_artifact("current_project_declaration", kwargs=kwargs)
            assert opened["artifact_kind"] == "project_declaration" and opened["sha256"]
            print("ARTIFACT_OPEN_FIRST_CALL_SUCCESS=PASS")

            link = p3.phase3_handler("aota_project_artifact_link", lambda args, **_kwargs: json.dumps({"status": "linked", "received": args}, sort_keys=True))
            linked = json.loads(link({"artifact_ref": "current_project_declaration", "target_ref": "current_project", "relation": "evidence_for"}, **kwargs))
            assert linked["operation_result"] == "artifact_linked" and linked["binding_verified"] is True
            print("ARTIFACT_LINK_FIRST_CALL_SUCCESS=PASS")

            register = p3.phase3_handler("aota_project_registry_refresh", lambda args, **_kwargs: json.dumps({"status": "refreshed"}, sort_keys=True))
            registered_once = json.loads(register({"operation": "register_current_project"}, **kwargs))
            registered_twice = json.loads(register({"operation": "register_current_project"}, **kwargs))
            assert registered_once["operation_result"] == "project_registered" and registered_once["registry_exact_match"] == 1
            assert registered_twice["operation_result"] == registered_once["operation_result"]
            print("REGISTRATION_IDEMPOTENCY=PASS")

            project_registry = workspace / ".aota" / "registry" / "projects.json"
            project_registry.unlink()
            refresh_before_registration = json.loads(register({"operation": "refresh_current_project_registry"}, **kwargs))
            assert refresh_before_registration["error"] == "project_registration_required" and refresh_before_registration["retryable"] is False
            assert refresh_before_registration["flow_disposition"] == "continue"
            assert refresh_before_registration["allowed_next_tool"] == "aota_project_registry_refresh"
            assert refresh_before_registration["allowed_next_arguments"] == {"operation": "register_current_project"}
            bootstrapped = json.loads(register({"operation": "register_current_project"}, **kwargs))
            assert bootstrapped["operation_result"] == "project_registered" and bootstrapped["resolved_context"]["project_id"] == "fixture-project"
            assert bootstrapped["flow_disposition"] == "continue"
            assert bootstrapped["allowed_next_tool"] == "aota_project_open"
            assert bootstrapped["allowed_next_arguments"] == {"project_ref": "current_project", "include_observed": True}
            assert bootstrapped["route_contract"]["route_id"] == "raw_current_project_setup_v1"
            assert bootstrapped["route_contract"]["step"] == 1
            assert bootstrapped["route_contract"]["max_domain_calls"] == 2
            assert project_registry.is_file() and p3.resolve_current_project(kwargs=kwargs)["project_id"] == "fixture-project"
            print("REGISTRATION_BOOTSTRAP_WITHOUT_EXISTING_REGISTRY=PASS")
            print("PROJECT_REGISTRATION_FLOW_CONTRACT=PASS")

            observed = p3.phase3_handler("aota_project_prepare", lambda args, **_kwargs: json.dumps({"status": "legacy"}, sort_keys=True))
            observed_result = json.loads(observed({"operation": "refresh_current_project_observed_state"}, **kwargs))
            assert observed_result["operation_result"] == "observed_state_refreshed" and observed_result["codegraph_available"] is False
            print("OBSERVED_STATE_CONTROL_PLANE_GENERATED=PASS")

            lifecycle = json.loads(observed({"operation": "request_project_lifecycle_transition", "action": "archive", "rationale": "fixture"}, **kwargs))
            assert lifecycle["operation_result"] == "lifecycle_checkpoint_required" and lifecycle["human_action_required"] is True
            print("LIFECYCLE_OPERATOR_CHECKPOINT=PASS")

            steward = p3.phase3_handler("aota_project_steward_report", lambda args, **_kwargs: json.dumps({"status": "legacy"}, sort_keys=True))
            steward_result = json.loads(steward({"operation": "reconcile_current_project", "rationale": "fixture"}, **kwargs))
            assert steward_result["operation_result"] == "stewardship_reconciled" and "reconciliation_findings" in steward_result
            print("STEWARDSHIP_FACTS_FINDINGS_MUTATIONS_SEPARATED=PASS")

            initialize = p3.phase3_handler("aota_project_initialize_core", lambda args, **_kwargs: json.dumps({"status": "legacy"}, sort_keys=True))
            init_result = json.loads(initialize({"project_name": "Fixture New Project", "summary": "source fixture"}, **kwargs))
            assert init_result["operation_result"] == "project_initialization_planned" and init_result["real_mutation_performed"] is False
            print("PROJECT_INITIALIZATION_DERIVED_FIELDS=PASS")

            try:
                p3.resolve_current_artifact("current_lifecycle_evidence", kwargs=kwargs)
            except p3.Phase3ResolutionError as exc:
                assert exc.code == "artifact_missing"
            else:
                raise AssertionError("missing artifact did not fail closed")
            print("ARTIFACT_MISSING=PASS")

            (workspace / "fixture-project" / ".aota" / "lifecycle.json").write_text("{}", encoding="utf-8")
            (workspace / "fixture-project" / ".aota" / "lifecycle-evidence.json").write_text("{}", encoding="utf-8")
            try:
                p3.resolve_current_artifact("current_lifecycle_evidence", kwargs=kwargs)
            except p3.Phase3ResolutionError as exc:
                assert exc.code == "artifact_ambiguous"
            else:
                raise AssertionError("ambiguous artifact used fallback")
            print("ARTIFACT_AMBIGUITY=PASS")

            outside = root / "outside.txt"; outside.write_text("x", encoding="utf-8")
            try:
                p3._safe_target(workspace / "fixture-project" / ".." / ".." / "outside.txt", workspace)
            except p3.Phase3ResolutionError as exc:
                assert exc.code == "path_escape"
            else:
                raise AssertionError("path traversal accepted")
            link = workspace / "fixture-project" / ".aota" / "escape.json"
            try:
                link.symlink_to(outside)
                try:
                    p3._safe_target(link, workspace / "fixture-project")
                except p3.Phase3ResolutionError as exc:
                    assert exc.code == "symlink_escape"
                else:
                    raise AssertionError("symlink escape accepted")
            finally:
                if link.is_symlink(): link.unlink()
            print("PATH_TRAVERSAL_AND_SYMLINK_ESCAPE=PASS")

            legacy = p3.phase3_handler("aota_project_scan", lambda args, **_kwargs: json.dumps({"status": "legacy", "received": args}, sort_keys=True))
            legacy_result = json.loads(legacy({"workspace_id": "fixture", "path": "fixture-project"}, **kwargs))
            assert legacy_result["status"] == "legacy"
            print("LEGACY_HANDLER_COMPATIBILITY=PASS")
        finally:
            _workspace._REGISTRY_PATH = old_registry
            if old_env is None: os.environ.pop("AOTA_WORKSPACE_REGISTRY_PATH", None)
            else: os.environ["AOTA_WORKSPACE_REGISTRY_PATH"] = old_env
            if old_project is None: os.environ.pop("AOTA_TRUSTED_PROJECT_ID", None)
            else: os.environ["AOTA_TRUSTED_PROJECT_ID"] = old_project

    inventory = json.loads((ROOT / "deploy/evidence/control-plane-minimal-invocation-inventory.json").read_text(encoding="utf-8"))
    phase3 = [item for item in inventory["tools"] if "phase_3_migration" in item]
    assert len(phase3) == 22, len(phase3)
    assert all(item["phase_3_migration"]["migration_status"] == "migrated_this_phase" for item in phase3)
    print("PHASE3_INVENTORY=PASS")
    print("FIRST_CALL_INTERNAL_PARAMETER_FAILURES=0")
    print("GUESS_AND_RETRY_COUNT=0")
    print("AOTA_PHASE3_ARTIFACT_PROJECT_MIGRATION_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
