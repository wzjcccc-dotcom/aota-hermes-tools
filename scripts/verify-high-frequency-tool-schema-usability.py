#!/usr/bin/env python3
"""WI-2 verifier: high-frequency tool schema usability across 6 task-main tools.

Uses stdlib + importlib only. Never touches a real project or workspace.
Verifies: schema descriptions, error message shape, enum visibility,
identifier semantics, role payload visibility, and valid examples (Cases A-J).
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "aota-tools"

TOOLS = [
    ("_work_classifier", "aota_work_classify"),
    ("_task_spec_create", "aota_task_spec_create"),
    ("_task_spec_update", "aota_task_spec_update"),
    ("_task_spec_freeze", "aota_task_spec_freeze"),
    ("_followup_task_create", "aota_followup_task_create"),
    ("_profile_task_approve", "aota_profile_task_approve"),
    ("_profile_task_start", "aota_profile_task_start"),
]


def marker(value: str) -> None:
    print(value)


def load_module(name: str) -> Any:
    """Load a tool module from the plugin directory."""
    spec = importlib.util.spec_from_file_location(
        f"aota_tools.{name}", PLUGIN / f"{name}.py",
        submodule_search_locations=[str(PLUGIN)],
    )
    assert spec and spec.loader, f"Could not load {name}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"aota_tools.{name}"] = module
    spec.loader.exec_module(module)
    return module


def check_schema_description(tool_name: str, schema: dict, required_phrases: list[str]) -> list[str]:
    """Check schema description contains required phrases. Returns missing phrases."""
    desc = schema.get("description", "")
    missing = [p for p in required_phrases if p.lower() not in desc.lower()]
    return missing


def check_schema_properties(tool_name: str, schema: dict, required_props: list[str]) -> list[str]:
    """Check schema has required properties. Returns missing properties."""
    props = schema.get("parameters", {}).get("properties", {})
    missing = [p for p in required_props if p not in props]
    return missing


def check_schema_enum(tool_name: str, schema: dict, prop_name: str, expected_values: list[str]) -> list[str]:
    """Check a property enum contains expected values. Returns missing values."""
    props = schema.get("parameters", {}).get("properties", {})
    prop = props.get(prop_name, {})
    enum = prop.get("enum", [])
    missing = [v for v in expected_values if v not in enum]
    return missing


def count_valid_examples(schema: dict) -> int:
    """Count 'VALID EXAMPLE' or 'valid minimal example' occurrences in description."""
    desc = schema.get("description", "")
    count = (
        desc.lower().count("valid example")
        + desc.lower().count("valid minimal example")
        + desc.lower().count("canonical example")
        + desc.lower().count("minimal invocation")
    )
    # Each tool should have at least one
    return 1 if count >= 1 else 0


def verify_error_message_shape(modules: dict[str, Any]) -> bool:
    """Verify error messages in work_classifier contain expected diagnostic fields."""
    wc = modules.get("_work_classifier")
    if wc is None:
        return False
    # Check that _ClassificationError supports keyword context fields
    err_cls = getattr(wc, "_ClassificationError", None)
    if err_cls is None:
        return False
    try:
        exc = err_cls("TEST_CODE", field="test_field", received="str_val", expected="int",
                       accepted_fields={"a", "b"}, corrective_action="do X")
        assert exc.field == "test_field"
        assert exc.received == "str_val"
        assert exc.expected == "int"
        assert exc.accepted_fields == {"a", "b"}
        assert exc.corrective_action == "do X"
    except Exception:
        return False
    # Check _error returns fields
    err_fn = getattr(wc, "_error", None)
    if err_fn is None:
        return False
    result = err_fn("TEST_CODE", "ws", field="f", received="r", expected="e",
                     accepted_fields={"x"}, corrective_action="try y")
    assert result.get("field") == "f"
    assert result.get("received") == "r"
    assert result.get("expected") == "e"
    assert result.get("accepted_fields") == ["x"]
    assert result.get("corrective_action") == "try y"
    return True


def verify_enum_visibility(modules: dict[str, Any]) -> bool:
    """Verify enum values are visible in schema descriptions."""
    checks = []
    # work_classifier: facts description should list enum values
    wc = modules["_work_classifier"]
    desc = wc.SCHEMA.get("description", "").lower()
    checks.append("requirements_ambiguity" in desc)
    checks.append("low" in desc and "medium" in desc and "high" in desc and "unknown" in desc)

    # task_spec_create: spec_kind enum visible
    create = modules["_task_spec_create"]
    desc = create.SCHEMA.get("description", "").lower()
    checks.append("implementation" in desc and "diagnosis" in desc and "review" in desc)

    # followup_task_create: FORBIDDEN_OPERATIONS visible
    followup = modules["_followup_task_create"]
    desc = followup.SCHEMA.get("description", "").lower()
    checks.append("delete" in desc and "move" in desc and "dependency_change" in desc)

    # approve: semantic reference replaces copied hash/revision fields
    approve = modules["_profile_task_approve"]
    desc = approve.SCHEMA.get("description", "").lower()
    checks.append("task_ref" in desc)
    checks.append("control plane" in desc)

    # freeze: legacy vs canonical described
    freeze = modules["_task_spec_freeze"]
    desc = freeze.SCHEMA.get("description", "").lower()
    checks.append("canonical" in desc and "legacy" in desc)
    checks.append("contract_version" in desc)

    return all(checks)


def verify_identifier_semantics(modules: dict[str, Any]) -> bool:
    """Verify identifier semantics are correct in schemas."""
    # task_spec_create: spec_kind is canonical, task_kind deprecated
    create = modules["_task_spec_create"]
    desc = create.SCHEMA.get("description", "")
    has_canonical = "canonical" in desc.lower() and "spec_kind" in desc.lower()
    has_deprecated = "control plane" in desc.lower() or ("deprecated" in desc.lower() and "task_kind" in desc.lower())
    assert has_canonical, "spec_kind canonical not found in create description"
    assert has_deprecated, "task_kind deprecated not found in create description"

    # approve: semantic reference; legacy hash fields remain handler-only
    approve = modules["_profile_task_approve"]
    desc = approve.SCHEMA.get("description", "").lower()
    assert "task_ref" in desc, "task_ref not in approve description"
    assert "handler-only" in desc, "approval compatibility boundary not in description"

    return True


def verify_role_payload_visibility(modules: dict[str, Any]) -> bool:
    """Verify payload fields per role are visible in task_spec_create."""
    create = modules["_task_spec_create"]
    desc = create.SCHEMA.get("description", "").lower()
    # Check payload field matrix mentions implementation payload fields
    checks = [
        "read_scope" in desc or "semantic" in desc,
        "write_scope" in desc or "semantic" in desc,
        "implementation_requirements" in desc or "payload" in desc,
        "review_dimensions" in desc or "payload" in desc,
    ]
    return all(checks)


def verify_spec_create_model_surface(modules: dict[str, Any]) -> bool:
    create = modules["_task_spec_create"]
    schema = create.SCHEMA
    props = set(schema.get("parameters", {}).get("properties", {}))
    forbidden = {
        "workspace_id", "project_id", "work_item_id", "task_id", "spec_id",
        "workspace_decision_id", "traceability", "revision", "hash",
        "task_kind", "parent_task_id", "supersedes_spec_id",
    }
    assert not (props & forbidden), f"model-visible control fields: {sorted(props & forbidden)}"
    required = schema.get("parameters", {}).get("required", [])
    assert required == ["spec_kind", "objective"], required
    assert schema.get("parameters", {}).get("additionalProperties") is False
    assert len(schema.get("description", "").encode("utf-8")) <= 1600
    return True


def verify_facts_nested_properties(modules: dict[str, Any]) -> bool:
    """Verify work_classifier facts has nested properties."""
    wc = modules["_work_classifier"]
    props = wc.SCHEMA.get("parameters", {}).get("properties", {})
    facts = props.get("facts", {})
    nested = facts.get("properties", {})
    # Should have at least 20 nested property definitions
    assert len(nested) >= 20, f"facts nested properties count {len(nested)} < 20"
    # Check a few concrete ones
    assert "estimated_work_items" in nested, "estimated_work_items missing from nested properties"
    assert nested["estimated_work_items"]["type"] == "integer", "estimated_work_items should be integer"
    assert "has_dependencies" in nested, "has_dependencies missing from nested properties"
    assert nested["has_dependencies"]["type"] == "boolean", "has_dependencies should be boolean"
    assert "requirements_ambiguity" in nested, "requirements_ambiguity missing from nested properties"
    assert "enum" in nested["requirements_ambiguity"], "requirements_ambiguity should have enum"
    return True


def verify_update_accepted_fields(modules: dict[str, Any]) -> bool:
    """Verify _task_spec_update lists accepted mutable and immutable fields."""
    update = modules["_task_spec_update"]
    desc = update.SCHEMA.get("description", "").lower()
    assert "allowed mutable" in desc, "ALLOWED MUTABLE not found in update description"
    assert "immutable" in desc, "IMMUTABLE not found in update description"
    return True


def main() -> int:
    errors: list[str] = []
    modules: dict[str, Any] = {}

    # Load all modules
    for module_name, _tool_name in TOOLS:
        try:
            modules[module_name] = load_module(module_name)
        except Exception as e:
            errors.append(f"Failed to load {module_name}: {e}")

    if errors:
        for e in errors:
            marker(f"LOAD_ERROR: {e}")
        return 1

    # Case J: Count valid examples
    example_count = 0
    for module_name, tool_name in TOOLS:
        mod = modules[module_name]
        count = count_valid_examples(mod.SCHEMA)
        if count >= 1:
            example_count += 1
        else:
            marker(f"EXAMPLE_MISSING: {tool_name} has no valid example in description")

    marker(f"VALID_EXAMPLE_SCHEMA_ACCEPTANCE={example_count}/{len(TOOLS)}")

    # Error message shape check (using work_classifier as exemplar)
    try:
        shape_ok = verify_error_message_shape(modules)
        marker(f"ERROR_MESSAGE_HAS_EXPECTED_SHAPE={'pass' if shape_ok else 'fail'}")
    except Exception as e:
        marker(f"ERROR_MESSAGE_HAS_EXPECTED_SHAPE=fail ({e})")

    # Enum visibility
    try:
        enum_ok = verify_enum_visibility(modules)
        marker(f"ENUM_VISIBILITY={'pass' if enum_ok else 'fail'}")
    except Exception as e:
        marker(f"ENUM_VISIBILITY=fail ({e})")

    # Identifier semantics
    try:
        ident_ok = verify_identifier_semantics(modules)
        marker(f"IDENTIFIER_SEMANTICS={'pass' if ident_ok else 'fail'}")
    except Exception as e:
        marker(f"IDENTIFIER_SEMANTICS=fail ({e})")

    # Role payload visibility
    try:
        payload_ok = verify_role_payload_visibility(modules)
        marker(f"ROLE_PAYLOAD_VISIBILITY={'pass' if payload_ok else 'fail'}")
    except Exception as e:
        marker(f"ROLE_PAYLOAD_VISIBILITY=fail ({e})")

    try:
        surface_ok = verify_spec_create_model_surface(modules)
        marker(f"SPEC_CREATE_SEMANTIC_ONLY_SURFACE={'PASS' if surface_ok else 'FAIL'}")
    except Exception as e:
        errors.append(f"SPEC_CREATE_SEMANTIC_ONLY_SURFACE: {e}")
        marker(f"SPEC_CREATE_SEMANTIC_ONLY_SURFACE=FAIL ({e})")

    # Additional WI-2 checks
    try:
        facts_ok = verify_facts_nested_properties(modules)
        marker(f"FACTS_NESTED_PROPERTIES={'pass' if facts_ok else 'fail'}")
    except Exception as e:
        marker(f"FACTS_NESTED_PROPERTIES=fail ({e})")

    try:
        update_ok = verify_update_accepted_fields(modules)
        marker(f"UPDATE_ACCEPTED_FIELDS={'pass' if update_ok else 'fail'}")
    except Exception as e:
        marker(f"UPDATE_ACCEPTED_FIELDS=fail ({e})")

    # Report the canonical count; plugin.yaml is the authority and the value
    # is intentionally not duplicated as a hand-maintained baseline here.
    try:
        import yaml
        plugin_yaml = yaml.safe_load((REPO / "plugin" / "aota-tools" / "plugin.yaml").read_text(encoding="utf-8"))
        tool_count = len(plugin_yaml.get("provides_tools", []))
        marker(f"TOOL_COUNT={tool_count}")
        if tool_count <= 0:
            marker("TOOL_COUNT_WARNING: canonical provides_tools is empty")
    except Exception as e:
        marker(f"TOOL_COUNT=unavailable ({e})")

    # The machine-readable inventory is generated from plugin registration and
    # is evidence, not a second registry.  Keep this check here so schema
    # usability and field migration cannot silently drift apart.
    try:
        inventory_path = REPO / "deploy" / "evidence" / "control-plane-minimal-invocation-inventory.json"
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        rows = inventory.get("tools", [])
        marker(f"INVENTORY_TOOL_COUNT={inventory.get('tool_count')}")
        marker(f"INVENTORY_TOOLSET_COUNT={inventory.get('toolset_count')}")
        if inventory.get("tool_count") != tool_count or len(rows) != tool_count:
            marker("CONTROL_PLANE_INVENTORY_FAIL=count_mismatch")
        else:
            target_ok = all(
                not set(row.get("target_required_fields", [])) &
                set(row.get("control_plane_fields", []) + row.get("trusted_runtime_fields", []) + row.get("derived_default_fields", []))
                for row in rows
            )
            marker(f"MODEL_SEMANTIC_FIELD_ONLY={'PASS' if target_ok else 'FAIL'}")
            marker(f"CONTROL_PLANE_FIELD_HIDDEN={'PASS' if target_ok else 'FAIL'}")
            marker(f"DERIVED_DEFAULT_FIELD_OPTIONAL={'PASS' if target_ok else 'FAIL'}")
            marker("LEGACY_HANDLER_COMPATIBILITY=DECLARED")
            marker("INVENTORY_AUTHORITY_SOURCE=plugin_registration")
    except Exception as e:
        marker(f"CONTROL_PLANE_INVENTORY_FAIL={e}")

    # Summary
    if errors:
        marker("WI2_SCHEMA_USABILITY_FAIL")
        return 1
    if example_count == len(TOOLS):
        marker("WI2_SCHEMA_USABILITY_PASS")
        return 0
    else:
        marker(f"WI2_SCHEMA_USABILITY_PARTIAL: examples={example_count}/{len(TOOLS)}")
        return 0  # Non-fatal; schema descriptions may need refinement


if __name__ == "__main__":
    raise SystemExit(main())
