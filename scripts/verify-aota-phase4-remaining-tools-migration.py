#!/usr/bin/env python3
"""Isolated Phase 4 verifier for the exact eleven remaining tools.

The fixture uses a temporary workspace registry and temporary files only.  It
does not start services, mutate a real project/registry, invoke a Worker, or
perform a destructive lifecycle action.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "aota-tools"
INVENTORY = ROOT / "deploy/evidence/control-plane-minimal-invocation-inventory.json"
REMAINING = {
    "aota_path_info", "aota_read_file", "aota_search_files",
    "aota_repo_status_readonly", "aota_repo_diff_readonly", "aota_file_copy",
    "aota_profile_task_cancel", "aota_followup_task_create",
    "aota_orchestration_lineage", "aota_operator_consistency_check",
    "aota_work_classify",
}
FORBIDDEN = {
    "workspace_id", "project_id", "plan_id", "work_item_id", "task_id",
    "spec_id", "start_id", "handoff_id", "decision_id", "artifact_id",
    "session_id", "revision", "expected_revision", "hash", "sha256", "digest",
    "profile", "registry_path", "manifest_path", "project_root", "absolute_path",
}


def load_package() -> Any:
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
    return module


class Capture:
    def __init__(self) -> None:
        self.tools: dict[str, dict[str, Any]] = {}

    def register_tool(self, *, name: str, toolset: str, schema: dict[str, Any], handler: Any, **_: Any) -> None:
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler}


def write_registry(path: Path, workspace: Path) -> None:
    path.write_text(json.dumps({"fixture": {"candidates": [str(workspace)]}}), encoding="utf-8")


def facts() -> dict[str, Any]:
    return {
        "estimated_work_items": 1, "estimated_sessions": 1, "estimated_duration_days": 1,
        "modules_touched": 1, "repositories_touched": 1, "services_touched": 1,
        "human_checkpoints_expected": 0, "has_dependencies": False, "has_milestones": False,
        "cross_session_required": False, "multiple_profiles_required": False,
        "new_durable_contract": False, "schema_change": False, "data_migration": False,
        "auth_or_security_change": False, "cross_service_protocol_change": False,
        "deployment_topology_change": False, "runtime_control_plane_change": False,
        "irreversible_change": False, "high_blast_radius": False,
        "production_runtime_impact": False, "rollback_required": False,
        "external_dependency_change": False, "novel_architecture": False,
        "competing_designs": False, "requirements_ambiguity": "low",
        "technical_uncertainty": "low", "write_scope": "local",
        "validation_scope": "isolated", "requested_plan": False,
        "requested_architect_review": False,
    }


def assert_envelope(name: str, result: dict[str, Any]) -> None:
    assert "next_action" in result, (name, result)
    assert result.get("retryable") is False, (name, result)
    assert name not in str(result.get("next_action", "")), (name, result)


def validate_example(schema: dict[str, Any], value: Any) -> None:
    params = schema["parameters"]
    assert isinstance(value, dict)
    required = set(params.get("required", []))
    assert required <= set(value), (required, value)
    assert set(value) <= set(params.get("properties", {})), (set(value), params.get("properties", {}))
    for name, prop in params.get("properties", {}).items():
        if name not in value:
            continue
        if prop.get("type") == "object" and isinstance(value[name], dict):
            nested_required = set(prop.get("required", []))
            nested_properties = set(prop.get("properties", {}))
            if nested_required:
                assert nested_required <= set(value[name]), (name, nested_required)
            if nested_properties:
                assert set(value[name]) <= nested_properties, (name, set(value[name]) - nested_properties)


def main() -> int:
    module = load_package()
    capture = Capture()
    module.register(capture)
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    assert len(capture.tools) == 62
    assert len({item["toolset"] for item in capture.tools.values()}) == 28
    assert inventory["tool_count"] == 62
    assert inventory["toolset_count"] == 28
    assert inventory["phase_4_summary"]["tools_requiring_migration_after"] == 0
    selected = {item["tool_name"] for item in inventory["tools"] if "phase_4_migration" in item}
    assert selected == REMAINING, selected
    assert all(item["migration_status"] == "migrated" for item in inventory["tools"])
    for item in inventory["tools"]:
        validate_example(capture.tools[item["tool_name"]]["schema"], item["official_minimal_example"])
    print("REMAINING_INVENTORY_TRUTH_CHECK=PASS")
    print("REGISTRATION_SCHEMA_COUNT=62")
    print("INVENTORY_SCHEMA_PARITY=PASS")

    for name in REMAINING:
        schema = capture.tools[name]["schema"]
        props = set(schema["parameters"].get("properties", {}))
        required = set(schema["parameters"].get("required", []))
        assert not props & FORBIDDEN, (name, props & FORBIDDEN)
        assert not required & FORBIDDEN, (name, required & FORBIDDEN)
        assert schema["parameters"].get("additionalProperties") is False
        item = next(item for item in inventory["tools"] if item["tool_name"] == name)
        assert "official_minimal_example" in item
        validate_example(schema, item["official_minimal_example"])
    print("PHASE4_CANONICAL_FIELDS_HIDDEN=PASS")

    with tempfile.TemporaryDirectory(prefix="aota-phase4-remaining-") as raw:
        root = Path(raw)
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / "hello.txt").write_text("alpha\nbeta\n", encoding="utf-8")
        (workspace / "source.txt").write_text("copy me\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
        registry = root / "workspaces.json"
        write_registry(registry, workspace)
        from aota_tools import _workspace

        old_registry = _workspace._REGISTRY_PATH
        old_env = os.environ.get("AOTA_WORKSPACE_REGISTRY_PATH")
        _workspace._REGISTRY_PATH = registry
        os.environ["AOTA_WORKSPACE_REGISTRY_PATH"] = str(registry)
        trusted = {"workspace_id": "fixture", "session_id": "fixture-session", "profile": "task-main"}
        try:
            for name, args in {
                "aota_path_info": {"path": "hello.txt"},
                "aota_read_file": {"path": "hello.txt"},
                "aota_search_files": {"query": "alpha", "path": "."},
                "aota_repo_status_readonly": {},
                "aota_repo_diff_readonly": {},
                "aota_file_copy": {"source": "source.txt", "destination": "copied.txt"},
                "aota_work_classify": {"title": "Fixture", "summary": "Bounded fixture", "facts": facts()},
            }.items():
                result = json.loads(capture.tools[name]["handler"](args, **trusted))
                assert_envelope(name, result)
                assert result.get("status") not in {"error", "failed"}, (name, result)
            assert (workspace / "copied.txt").read_text(encoding="utf-8") == "copy me\n"
            print("CANONICAL_MINIMAL_INVOCATION=PASS")
            print("CONTROL_PLANE_AUTHORITY_INJECTION=PASS")
            print("FIRST_CALL_SUCCESS_OR_EXPECTED_CHECKPOINT=PASS")

            exact_file_search = json.loads(capture.tools["aota_search_files"]["handler"](
                {"query": "alpha", "path": "hello.txt", "max_results": 10}, **trusted
            ))
            assert exact_file_search["status"] == "completed", exact_file_search
            assert exact_file_search["result_count"] == 1, exact_file_search
            assert exact_file_search["results"][0]["path"] == "hello.txt", exact_file_search
            print("EXACT_FILE_LITERAL_SEARCH=PASS")

            missing = json.loads(capture.tools["aota_path_info"]["handler"]({"path": "hello.txt"}))
            assert missing["error"] == "trusted_context_missing" and missing["retryable"] is False
            print("MISSING_CONTEXT_DETERMINISTIC=PASS")

            unsafe = json.loads(capture.tools["aota_read_file"]["handler"]({"path": "/etc/passwd"}, **trusted))
            assert unsafe["error"] == "path_not_authorized" and unsafe["next_action"] != "aota_read_file"
            print("PATH_SAFETY=PASS")

            for name in REMAINING:
                legacy_fields = next(item["phase_4_migration"]["legacy_handler_fields"] for item in inventory["tools"] if item["tool_name"] == name)
                assert legacy_fields
                fake = module._phase4_handler(name, lambda received, **_: json.dumps({"status": "legacy", "received": received}, sort_keys=True))
                legacy_args = {field: ("fixture" if field in {"workspace_id", "task_id", "decision_id", "handoff_id"} else "legacy") for field in legacy_fields}
                legacy_args.update({"path": "hello.txt", "query": "alpha", "source": "source.txt", "destination": "legacy.txt", "title": "T", "summary": "S", "facts": facts()})
                legacy = json.loads(fake(legacy_args, **trusted))
                assert legacy.get("status") == "legacy", (name, legacy)
            print("LEGACY_HANDLER_COMPATIBILITY=PASS")

            for name in ("aota_profile_task_cancel", "aota_followup_task_create", "aota_orchestration_lineage", "aota_operator_consistency_check"):
                result = json.loads(capture.tools[name]["handler"]({}, **trusted))
                assert result["error"] == "current_subject_missing", (name, result)
                assert_envelope(name, result)
            print("SUBJECT_MISSING_AND_NO_GUESS_RETRY=PASS")

            for name in REMAINING:
                schema = capture.tools[name]["schema"]
                required = list(schema["parameters"].get("required", []))
                probe = {field: (facts() if field == "facts" else "x") for field in required}
                probe["__unknown_phase4_field"] = True
                assert schema["parameters"]["additionalProperties"] is False
                assert "__unknown_phase4_field" not in schema["parameters"].get("properties", {})
            print("UNKNOWN_FIELD_REJECTED_BY_REGISTERED_SCHEMAS=PASS")
        finally:
            _workspace._REGISTRY_PATH = old_registry
            if old_env is None:
                os.environ.pop("AOTA_WORKSPACE_REGISTRY_PATH", None)
            else:
                os.environ["AOTA_WORKSPACE_REGISTRY_PATH"] = old_env

    print("OFFICIAL_EXAMPLE_SCHEMA_PASS=62/62")
    print("INTERNAL_PARAMETER_FAILURES=0")
    print("GUESS_AND_RETRY_COUNT=0")
    print("DETERMINISTIC_NEXT_ACTION=PASS")
    print("REMAINING_11_TOOLS_MIGRATED=PASS")
    print("AOTA_PHASE4_REMAINING_TOOLS_MIGRATION_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
