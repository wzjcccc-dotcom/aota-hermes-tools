"""Deterministic, read-only AOTA work intake classifier (PF-WI-04)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Mapping

from ._handoff_common import validate_workspace_id
from ._workspace import WorkspaceError, resolve_workspace

TOOL_NAME = "aota_work_classify"
TOOLSET_NAME = "aota_work_intake"
_MAX_TITLE = 200
_MAX_SUMMARY = 4000
_MAX_RATIONALE = 500

_INT_BOUNDS = {
    "estimated_work_items": (1, 100), "estimated_sessions": (1, 100),
    "estimated_duration_days": (0, 3650), "modules_touched": (1, 1000),
    "repositories_touched": (1, 100), "services_touched": (0, 100),
    "human_checkpoints_expected": (0, 100),
}
_BOOL_FACTS = frozenset({
    "has_dependencies", "has_milestones", "cross_session_required",
    "multiple_profiles_required", "new_durable_contract", "schema_change",
    "data_migration", "auth_or_security_change", "cross_service_protocol_change",
    "deployment_topology_change", "runtime_control_plane_change", "irreversible_change",
    "high_blast_radius", "production_runtime_impact", "rollback_required",
    "external_dependency_change", "novel_architecture", "competing_designs",
    "requested_plan", "requested_architect_review",
})
_ENUM_FACTS = {
    "requirements_ambiguity": frozenset({"low", "medium", "high", "unknown"}),
    "technical_uncertainty": frozenset({"low", "medium", "high", "unknown"}),
    "write_scope": frozenset({"none", "local", "multi_module", "cross_repository", "runtime", "infrastructure"}),
    "validation_scope": frozenset({"none", "syntax", "isolated", "integration", "live", "end_to_end"}),
}
FACT_FIELDS = frozenset(_INT_BOUNDS) | _BOOL_FACTS | frozenset(_ENUM_FACTS)
_PLANNING_DEPTHS = ("P0", "P1", "P2")
_ARCHITECT_GATES = ("A0", "A1", "A2")
_DELIVERY_PATHS = ("fast", "standard", "deep")

# ---------------------------------------------------------------------------
# WI-2: build dynamic SCHEMA description from canonical constants
# ---------------------------------------------------------------------------

def _build_fact_field_list() -> str:
    """Build a compact field-name/type/bounds/enum listing from canonical constants."""
    int_items = sorted(_INT_BOUNDS)
    bool_items = sorted(_BOOL_FACTS)
    enum_items = sorted(_ENUM_FACTS)
    lines = ["Accepted fields (31 total):"]
    lines.append("  int fields (with bounds):")
    for field in int_items:
        lo, hi = _INT_BOUNDS[field]
        lines.append(f"    {field}: int [{lo}..{hi}]")
    lines.append("  bool fields:")
    for field in bool_items:
        lines.append(f"    {field}: bool")
    lines.append("  enum fields (with accepted values):")
    for field in enum_items:
        values = sorted(_ENUM_FACTS[field])
        lines.append(f"    {field}: str, accepted={values}")
    return "\n".join(lines)

_FACT_FIELD_LIST_TEXT = _build_fact_field_list()

_FACTS_NESTED_PROPERTIES: dict[str, Any] = {}
for _field in sorted(_INT_BOUNDS):
    _lo, _hi = _INT_BOUNDS[_field]
    _FACTS_NESTED_PROPERTIES[_field] = {"type": "integer", "minimum": _lo, "maximum": _hi, "description": f"int [{_lo}..{_hi}]"}
for _field in sorted(_BOOL_FACTS):
    _FACTS_NESTED_PROPERTIES[_field] = {"type": "boolean", "description": "bool"}
for _field in sorted(_ENUM_FACTS):
    _FACTS_NESTED_PROPERTIES[_field] = {"type": "string", "enum": sorted(_ENUM_FACTS[_field]), "description": f"enum: {sorted(_ENUM_FACTS[_field])}"}

QUESTION_CODES = {
    "estimated_work_items": "CONFIRM_WORK_ITEM_COUNT",
    "estimated_sessions": "CONFIRM_CROSS_SESSION",
    "cross_session_required": "CONFIRM_CROSS_SESSION",
    "new_durable_contract": "CONFIRM_DURABLE_CONTRACT",
    "schema_change": "CONFIRM_SCHEMA_CHANGE",
    "auth_or_security_change": "CONFIRM_SECURITY_CHANGE",
    "data_migration": "CONFIRM_MIGRATION",
    "deployment_topology_change": "CONFIRM_DEPLOYMENT_CHANGE",
    "high_blast_radius": "CONFIRM_BLAST_RADIUS",
    "production_runtime_impact": "CONFIRM_BLAST_RADIUS",
    "irreversible_change": "CONFIRM_IRREVERSIBILITY",
    "novel_architecture": "CONFIRM_ARCHITECTURAL_NOVELTY",
}

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Classify bounded work-intake facts into independent planning depth, "
        "architect gate, and delivery path. Deterministic and read-only: it "
        "does not create Plans, SPECs, tasks, artifacts, or follow-up actions.\n\n"
        "FACTS FIELD MATRIX (31 total, all required):\n"
        + _FACT_FIELD_LIST_TEXT +
        "\n\nValid minimal example:\n"
        '  {"workspace_id": "my-workspace", "title": "Add feature X", "summary": "Implement feature X in module Y", '
        '"facts": {"estimated_work_items": 1, "estimated_sessions": 1, "estimated_duration_days": 2, '
        '"modules_touched": 2, "repositories_touched": 1, "services_touched": 1, '
        '"human_checkpoints_expected": 0, '
        '"has_dependencies": false, "has_milestones": false, "cross_session_required": false, '
        '"multiple_profiles_required": false, "new_durable_contract": false, "schema_change": false, '
        '"data_migration": false, "auth_or_security_change": false, "cross_service_protocol_change": false, '
        '"deployment_topology_change": false, "runtime_control_plane_change": false, '
        '"irreversible_change": false, "high_blast_radius": false, "production_runtime_impact": false, '
        '"rollback_required": false, "external_dependency_change": false, "novel_architecture": false, '
        '"competing_designs": false, "requirements_ambiguity": "low", "technical_uncertainty": "low", '
        '"write_scope": "local", "validation_scope": "isolated", "requested_plan": false, '
        '"requested_architect_review": false}}'
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {"type": "string", "description": "Registered workspace identifier; only verified, never read."},
            "title": {"type": "string", "description": "Bounded work title (max 200 characters)."},
            "summary": {"type": "string", "description": "Bounded work summary (max 4000 characters)."},
            "facts": {"type": "object", "description": "Strict bounded intake-facts object with exactly 31 required fields. See tool description for full field matrix. Unknown keys are rejected with accepted_fields list.", "properties": _FACTS_NESTED_PROPERTIES, "additionalProperties": False},
            "override": {"type": "object", "description": "Optional escalation-only caller instruction; rationale is required and is not authorization."},
        },
        "required": ["workspace_id", "title", "summary", "facts"],
        "additionalProperties": False,
    },
}


class _ClassificationError(Exception):
    def __init__(self, code: str, *, field: str | None = None, received: Any = None,
                 expected: Any = None, accepted_fields: Any = None,
                 corrective_action: str | None = None) -> None:
        self.code = code
        self.field = field
        self.received = received
        self.expected = expected
        self.accepted_fields = accepted_fields
        self.corrective_action = corrective_action
        super().__init__(code)


def _error(code: str, workspace_id: str | None = None, *, field: str | None = None,
           received: Any = None, expected: Any = None, accepted_fields: Any = None,
           corrective_action: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"classification_status": "error", "error_code": code}
    if isinstance(workspace_id, str) and workspace_id:
        result["workspace_id"] = workspace_id
    if field is not None:
        result["field"] = str(field)
    if received is not None:
        result["received"] = str(received) if not isinstance(received, (list, dict)) else received
    if expected is not None:
        result["expected"] = str(expected) if not isinstance(expected, (list, dict)) else expected
    if accepted_fields is not None:
        result["accepted_fields"] = sorted(accepted_fields) if isinstance(accepted_fields, (set, frozenset, list)) else accepted_fields
    if corrective_action is not None:
        result["corrective_action"] = corrective_action
    return result


def _text(value: Any, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise _ClassificationError("CLASSIFICATION_INPUT_INVALID")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise _ClassificationError("CLASSIFICATION_INPUT_INVALID")
    return value


def _validate_facts(value: Any) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(value, Mapping):
        raise _ClassificationError("CLASSIFICATION_INPUT_INVALID")
    unknown = set(value) - FACT_FIELDS
    if unknown:
        raise _ClassificationError(
            "CLASSIFICATION_FACT_UNKNOWN",
            field=sorted(unknown)[0] if len(unknown) == 1 else list(sorted(unknown))[:5],
            accepted_fields=FACT_FIELDS,
            corrective_action="remove unknown fields; use only accepted fields listed in the tool description",
        )
    missing = sorted(FACT_FIELDS - set(value))
    if missing:
        return None, missing
    facts = dict(value)
    for name, (minimum, maximum) in _INT_BOUNDS.items():
        item = facts[name]
        if not isinstance(item, int) or isinstance(item, bool):
            raise _ClassificationError(
                "CLASSIFICATION_FACT_TYPE_INVALID",
                field=name, received=type(item).__name__, expected="int",
                corrective_action=f"provide an integer value for '{name}' within [{minimum}..{maximum}]",
            )
        if not minimum <= item <= maximum:
            raise _ClassificationError(
                "CLASSIFICATION_FACT_VALUE_INVALID",
                field=name, received=item, expected=f"int [{minimum}..{maximum}]",
                corrective_action=f"value {item} is outside allowed range [{minimum}..{maximum}] for '{name}'",
            )
    for name in _BOOL_FACTS:
        if not isinstance(facts[name], bool):
            raise _ClassificationError(
                "CLASSIFICATION_FACT_TYPE_INVALID",
                field=name, received=type(facts[name]).__name__, expected="bool",
                corrective_action=f"provide a boolean (true/false) value for '{name}'",
            )
    for name, allowed in _ENUM_FACTS.items():
        if not isinstance(facts[name], str):
            raise _ClassificationError(
                "CLASSIFICATION_FACT_TYPE_INVALID",
                field=name, received=type(facts[name]).__name__, expected="str",
                corrective_action=f"provide a string value from {sorted(allowed)} for '{name}'",
            )
        if facts[name] not in allowed:
            raise _ClassificationError(
                "CLASSIFICATION_FACT_VALUE_INVALID",
                field=name, received=facts[name], expected=sorted(allowed),
                corrective_action=f"value '{facts[name]}' is not accepted; use one of {sorted(allowed)}",
            )
    if facts["requirements_ambiguity"] == "unknown" or facts["technical_uncertainty"] == "unknown":
        missing = [name for name in ("requirements_ambiguity", "technical_uncertainty") if facts[name] == "unknown"]
        return None, missing
    return facts, []


def _needs_input(workspace_id: str, missing: list[str]) -> dict[str, Any]:
    missing = sorted(missing)[:len(FACT_FIELDS)]
    questions = [{"fact": fact, "question_code": QUESTION_CODES.get(fact, "CONFIRM_CLASSIFICATION_FACT")} for fact in missing]
    return {
        "classification_status": "needs_input", "workspace_id": workspace_id,
        "missing_facts": missing, "questions": questions,
        "recommended_next_step": "request_missing_facts", "override_applied": False,
    }


def _append_when(items: list[str], condition: bool, code: str) -> None:
    if condition:
        items.append(code)


def _classify_planning(f: Mapping[str, Any]) -> tuple[str, list[str], list[str]]:
    hard: list[str] = []
    _append_when(hard, f["data_migration"], "PLAN_P2_MIGRATION")
    _append_when(hard, f["runtime_control_plane_change"], "PLAN_P2_CONTROL_PLANE")
    _append_when(hard, f["deployment_topology_change"], "PLAN_P2_DEPLOYMENT_TOPOLOGY")
    _append_when(hard, f["new_durable_contract"] and f["has_dependencies"], "PLAN_P2_DURABLE_CONTRACT_WITH_DEPENDENCIES")
    _append_when(hard, f["repositories_touched"] >= 2, "PLAN_P2_CROSS_REPOSITORY")
    _append_when(hard, f["services_touched"] >= 2, "PLAN_P2_CROSS_SERVICE")

    _append_when(hard, f["multiple_profiles_required"] and f["estimated_work_items"] >= 3, "PLAN_P2_MULTIPLE_PROFILES")
    _append_when(hard, f["has_milestones"], "PLAN_P2_MULTIPLE_MILESTONES")
    _append_when(hard, f["estimated_work_items"] >= 5, "PLAN_P2_MANY_WORK_ITEMS")
    _append_when(hard, f["estimated_sessions"] >= 4, "PLAN_P2_MULTIPLE_SESSIONS")
    _append_when(hard, f["human_checkpoints_expected"] >= 2, "PLAN_P2_MULTIPLE_HUMAN_CHECKPOINTS")
    _append_when(hard, f["requested_plan"] and f["estimated_work_items"] >= 3, "PLAN_P2_USER_REQUESTED")
    if hard:
        return "P2", hard, []
    advisory: list[str] = []
    _append_when(advisory, f["estimated_work_items"] >= 2, "PLAN_P1_MULTIPLE_WORK_ITEMS")
    _append_when(advisory, f["estimated_sessions"] >= 2, "PLAN_P1_MULTIPLE_SESSIONS")
    _append_when(advisory, f["has_dependencies"], "PLAN_P1_DEPENDENCIES")
    _append_when(advisory, f["cross_session_required"], "PLAN_P1_CROSS_SESSION")
    _append_when(advisory, f["modules_touched"] >= 3, "PLAN_P1_MULTI_MODULE")
    _append_when(advisory, f["multiple_profiles_required"], "PLAN_P1_MULTIPLE_PROFILES")
    _append_when(advisory, f["human_checkpoints_expected"] >= 1, "PLAN_P1_HUMAN_CHECKPOINT")
    _append_when(advisory, f["requested_plan"], "PLAN_P1_USER_REQUESTED")
    p0 = (f["estimated_work_items"] == 1 and f["estimated_sessions"] == 1 and not f["cross_session_required"] and not f["has_dependencies"] and not f["has_milestones"] and f["repositories_touched"] == 1 and f["services_touched"] <= 1 and not f["requested_plan"])
    if p0:
        return "P0", ["PLAN_P0_SINGLE_BOUNDED_TASK"], []
    return "P1", advisory or ["PLAN_P1_COMPLEXITY_BOUNDARY"], []


def _classify_architect(f: Mapping[str, Any]) -> tuple[str, list[str], list[str]]:
    hard: list[str] = []
    _append_when(hard, f["auth_or_security_change"], "ARCH_A2_SECURITY")
    _append_when(hard, f["data_migration"], "ARCH_A2_MIGRATION")
    _append_when(hard, f["irreversible_change"], "ARCH_A2_IRREVERSIBLE_CHANGE")
    _append_when(hard, f["schema_change"] and f["irreversible_change"], "ARCH_A2_IRREVERSIBLE_SCHEMA")
    _append_when(hard, f["runtime_control_plane_change"], "ARCH_A2_CONTROL_PLANE")
    _append_when(hard, f["deployment_topology_change"], "ARCH_A2_DEPLOYMENT_TOPOLOGY")
    _append_when(hard, f["cross_service_protocol_change"], "ARCH_A2_CROSS_SERVICE_PROTOCOL")
    _append_when(hard, f["high_blast_radius"], "ARCH_A2_HIGH_BLAST_RADIUS")
    _append_when(hard, f["production_runtime_impact"] and f["rollback_required"], "ARCH_A2_PRODUCTION_ROLLBACK")
    _append_when(hard, f["novel_architecture"] and f["competing_designs"], "ARCH_A2_NOVEL_COMPETING_DESIGNS")
    _append_when(hard, f["requirements_ambiguity"] == "high", "ARCH_A2_HIGH_REQUIREMENT_AMBIGUITY")
    _append_when(hard, f["technical_uncertainty"] == "high" and f["write_scope"] in {"runtime", "infrastructure", "cross_repository"}, "ARCH_A2_HIGH_TECHNICAL_UNCERTAINTY")
    _append_when(hard, f["requested_architect_review"], "ARCH_A2_USER_REQUESTED")
    if hard:
        return "A2", hard, []
    advisory: list[str] = []
    _append_when(advisory, f["new_durable_contract"], "ARCH_A1_DURABLE_CONTRACT")
    _append_when(advisory, f["schema_change"], "ARCH_A1_SCHEMA_CHANGE")
    _append_when(advisory, f["external_dependency_change"], "ARCH_A1_EXTERNAL_DEPENDENCY")
    _append_when(advisory, f["novel_architecture"], "ARCH_A1_NOVEL_ARCHITECTURE")
    _append_when(advisory, f["competing_designs"], "ARCH_A1_COMPETING_DESIGNS")
    _append_when(advisory, f["requirements_ambiguity"] == "medium", "ARCH_A1_MEDIUM_REQUIREMENT_AMBIGUITY")
    _append_when(advisory, f["technical_uncertainty"] == "medium", "ARCH_A1_MEDIUM_TECHNICAL_UNCERTAINTY")
    _append_when(advisory, f["repositories_touched"] >= 2, "ARCH_A1_CROSS_REPOSITORY")
    _append_when(advisory, f["services_touched"] >= 2, "ARCH_A1_CROSS_SERVICE")
    _append_when(advisory, f["write_scope"] in {"cross_repository", "runtime", "infrastructure"}, "ARCH_A1_RUNTIME_WRITE_SCOPE")
    _append_when(advisory, f["validation_scope"] in {"integration", "live", "end_to_end"}, "ARCH_A1_HIGHER_VALIDATION_SCOPE")
    return ("A1", advisory, []) if advisory else ("A0", ["ARCH_A0_NO_TRIGGER"], [])


def _derive_delivery(planning: str, architect: str, f: Mapping[str, Any]) -> tuple[str, list[str]]:
    deep: list[str] = []
    _append_when(deep, f["auth_or_security_change"], "PATH_DEEP_SECURITY")
    _append_when(deep, f["data_migration"], "PATH_DEEP_MIGRATION")
    _append_when(deep, f["irreversible_change"], "PATH_DEEP_IRREVERSIBLE")
    _append_when(deep, f["runtime_control_plane_change"], "PATH_DEEP_CONTROL_PLANE")
    _append_when(deep, f["deployment_topology_change"], "PATH_DEEP_DEPLOYMENT")
    _append_when(deep, f["high_blast_radius"], "PATH_DEEP_HIGH_BLAST_RADIUS")
    _append_when(deep, f["validation_scope"] == "end_to_end", "PATH_DEEP_E2E")
    _append_when(deep, architect == "A2" and not deep, "PATH_DEEP_A2")
    if deep:
        return "deep", deep
    if planning == "P0" and architect == "A0" and f["write_scope"] in {"none", "local"} and f["validation_scope"] in {"none", "syntax", "isolated"} and not f["production_runtime_impact"] and f["human_checkpoints_expected"] == 0:
        return "fast", ["PATH_FAST_P0_A0_LOCAL"]
    return "standard", ["PATH_STANDARD_DEFAULT"]


def _validate_override(value: Any, planning: str, architect: str, delivery: str) -> tuple[str, str, str, bool]:
    if value is None:
        return planning, architect, delivery, False
    if not isinstance(value, Mapping) or not value or set(value) - {"planning_depth", "architect_gate", "delivery_path", "rationale"}:
        raise _ClassificationError("CLASSIFICATION_OVERRIDE_INVALID")
    if "rationale" not in value:
        raise _ClassificationError("CLASSIFICATION_OVERRIDE_INVALID")
    if not any(field in value for field in ("planning_depth", "architect_gate", "delivery_path")):
        raise _ClassificationError("CLASSIFICATION_OVERRIDE_INVALID")
    _text(value["rationale"], _MAX_RATIONALE)
    requested = (value.get("planning_depth", planning), value.get("architect_gate", architect), value.get("delivery_path", delivery))
    if requested[0] not in _PLANNING_DEPTHS or requested[1] not in _ARCHITECT_GATES or requested[2] not in _DELIVERY_PATHS:
        raise _ClassificationError("CLASSIFICATION_OVERRIDE_INVALID")
    for selected, baseline, allowed in zip(
        requested, (planning, architect, delivery),
        (_PLANNING_DEPTHS, _ARCHITECT_GATES, _DELIVERY_PATHS),
    ):
        if allowed.index(selected) < allowed.index(baseline):
            raise _ClassificationError("CLASSIFICATION_OVERRIDE_DENIED")
    if requested[1] == "A2" and requested[2] != "deep":
        raise _ClassificationError("CLASSIFICATION_OVERRIDE_INVALID")
    if requested[0] != "P0" and requested[2] == "fast":
        raise _ClassificationError("CLASSIFICATION_OVERRIDE_INVALID")
    return requested[0], requested[1], requested[2], True


def _classify(args: Any) -> dict[str, Any]:
    if not isinstance(args, Mapping) or set(args) - {"workspace_id", "title", "summary", "facts", "override"} or not {"workspace_id", "title", "summary", "facts"}.issubset(args):
        raise _ClassificationError("CLASSIFICATION_INPUT_INVALID")
    workspace_id = args["workspace_id"]
    if not isinstance(workspace_id, str) or validate_workspace_id(workspace_id):
        raise _ClassificationError("WORKSPACE_NOT_FOUND")
    try:
        resolve_workspace(workspace_id)
    except WorkspaceError as exc:
        raise _ClassificationError("WORKSPACE_NOT_FOUND") from exc
    _text(args["title"], _MAX_TITLE)
    _text(args["summary"], _MAX_SUMMARY)
    facts, missing = _validate_facts(args["facts"])
    if facts is None:
        return _needs_input(workspace_id, missing)
    planning, planning_reasons, planning_advisory = _classify_planning(facts)
    architect, architect_reasons, architect_advisory = _classify_architect(facts)
    delivery, delivery_reasons = _derive_delivery(planning, architect, facts)
    planning, architect, delivery, override_applied = _validate_override(args.get("override"), planning, architect, delivery)
    if override_applied:
        planning_reasons = ["HUMAN_OVERRIDE", *planning_reasons]
        architect_reasons = ["HUMAN_OVERRIDE", *architect_reasons]
        delivery_reasons = ["HUMAN_OVERRIDE", *delivery_reasons]
    return {
        "classification_status": "classified", "workspace_id": workspace_id,
        "planning_depth": planning, "architect_gate": architect, "delivery_path": delivery,
        "planning_dominant_reason": planning_reasons[0], "architect_dominant_reason": architect_reasons[0], "delivery_dominant_reason": delivery_reasons[0],
        "planning_reasons": planning_reasons, "architect_reasons": architect_reasons, "delivery_reasons": delivery_reasons,
        "hard_triggers": ((planning_reasons if planning == "P2" else []) + (architect_reasons if architect == "A2" else [])),
        "advisory_triggers": [*planning_advisory, *architect_advisory],
        "plan_required": planning in {"P1", "P2"}, "architect_review_required": architect == "A2", "architect_review_recommended": architect == "A1",
        "architect_action": {"A0": "none", "A1": "optional_review", "A2": "required_review"}[architect],
        "recommended_next_step": {"P0": "direct_spec", "P1": "draft_lightweight_plan", "P2": "draft_full_plan"}[planning],
        "override_applied": override_applied,
    }


def handle(args: dict, **_kwargs: Any) -> str:
    workspace_id = args.get("workspace_id") if isinstance(args, dict) else None
    try:
        return json.dumps(_classify(args), ensure_ascii=False, sort_keys=True)
    except _ClassificationError as exc:
        return json.dumps(_error(
            exc.code,
            workspace_id if isinstance(workspace_id, str) else None,
            field=exc.field,
            received=exc.received,
            expected=exc.expected,
            accepted_fields=exc.accepted_fields,
            corrective_action=exc.corrective_action,
        ), ensure_ascii=False, sort_keys=True)
    except Exception:
        return json.dumps(_error("CLASSIFICATION_INTERNAL_ERROR", workspace_id if isinstance(workspace_id, str) else None), ensure_ascii=False, sort_keys=True)


def run_isolated_smoke() -> dict[str, str]:
    """Run deterministic, temporary-workspace fixtures without source artifacts."""
    from . import _workspace
    base = {
        "estimated_work_items": 1, "estimated_sessions": 1, "estimated_duration_days": 1,
        "modules_touched": 1, "repositories_touched": 1, "services_touched": 1,
        "has_dependencies": False, "has_milestones": False, "cross_session_required": False,
        "multiple_profiles_required": False, "human_checkpoints_expected": 0,
        "new_durable_contract": False, "schema_change": False, "data_migration": False,
        "auth_or_security_change": False, "cross_service_protocol_change": False,
        "deployment_topology_change": False, "runtime_control_plane_change": False,
        "irreversible_change": False, "high_blast_radius": False, "production_runtime_impact": False,
        "rollback_required": False, "external_dependency_change": False, "novel_architecture": False,
        "competing_designs": False, "requirements_ambiguity": "low", "technical_uncertainty": "low",
        "write_scope": "local", "validation_scope": "isolated", "requested_plan": False,
        "requested_architect_review": False,
    }
    prior_registry = _workspace._REGISTRY_PATH
    with tempfile.TemporaryDirectory() as raw:
        root, registry = Path(raw) / "workspace", Path(raw) / "workspaces.json"
        root.mkdir(); registry.write_text(json.dumps({"fixture": {"candidates": [str(root)]}}), encoding="utf-8")
        _workspace._REGISTRY_PATH = registry
        try:
            before_mtime_ns = root.stat().st_mtime_ns
            def run(**changes: Any) -> dict[str, Any]:
                facts = {**base, **changes}
                return json.loads(handle({"workspace_id": "fixture", "title": "Fixture", "summary": "Deterministic fixture", "facts": facts}))
            assert (run()["planning_depth"], run()["architect_gate"], run()["delivery_path"]) == ("P0", "A0", "fast")
            assert (run(estimated_work_items=2, has_dependencies=True)["planning_depth"], run(estimated_work_items=2, has_dependencies=True)["architect_gate"], run(estimated_work_items=2, has_dependencies=True)["delivery_path"]) == ("P1", "A0", "standard")
            assert (run(estimated_work_items=5)["planning_depth"], run(estimated_work_items=5)["architect_gate"], run(estimated_work_items=5)["delivery_path"]) == ("P2", "A0", "standard")
            assert (run(auth_or_security_change=True)["planning_depth"], run(auth_or_security_change=True)["architect_gate"], run(auth_or_security_change=True)["delivery_path"]) == ("P0", "A2", "deep")
            assert (run(irreversible_change=True)["planning_depth"], run(irreversible_change=True)["architect_gate"], run(irreversible_change=True)["delivery_path"]) == ("P0", "A2", "deep")
            assert (run(new_durable_contract=True)["planning_depth"], run(new_durable_contract=True)["architect_gate"], run(new_durable_contract=True)["delivery_path"]) == ("P0", "A1", "standard")
            assert (run(new_durable_contract=True, has_dependencies=True, estimated_work_items=3)["planning_depth"], run(new_durable_contract=True, has_dependencies=True, estimated_work_items=3)["architect_gate"]) == ("P2", "A1")
            assert (run(data_migration=True)["planning_depth"], run(data_migration=True)["architect_gate"], run(data_migration=True)["delivery_path"]) == ("P2", "A2", "deep")
            assert run(requirements_ambiguity="medium")["architect_gate"] == "A1"
            assert (run(requirements_ambiguity="high")["architect_gate"], run(requirements_ambiguity="high")["delivery_path"]) == ("A2", "deep")
            assert run(requested_plan=True)["planning_depth"] == "P1"
            assert (run(requested_architect_review=True)["architect_gate"], run(requested_architect_review=True)["delivery_path"]) == ("A2", "deep")
            assert run(validation_scope="integration")["architect_gate"] == "A1"
            assert run(validation_scope="end_to_end")["delivery_path"] == "deep"
            expected = run(estimated_work_items=2); assert expected == run(estimated_work_items=2)
            missing = json.loads(handle({"workspace_id": "fixture", "title": "Fixture", "summary": "x", "facts": {}})); assert missing["classification_status"] == "needs_input"
            unknown = json.loads(handle({"workspace_id": "fixture", "title": "Fixture", "summary": "x", "facts": {**base, "unknown": True}})); assert unknown["error_code"] == "CLASSIFICATION_FACT_UNKNOWN"
            wrong = json.loads(handle({"workspace_id": "fixture", "title": "Fixture", "summary": "x", "facts": {**base, "estimated_work_items": True}})); assert wrong["error_code"] == "CLASSIFICATION_FACT_TYPE_INVALID"
            overridden = json.loads(handle({"workspace_id": "fixture", "title": "Fixture", "summary": "x", "facts": base, "override": {"planning_depth": "P1", "architect_gate": "A1", "delivery_path": "standard", "rationale": "More review"}})); assert overridden["override_applied"] and overridden["planning_depth"] == "P1"
            denied = json.loads(handle({"workspace_id": "fixture", "title": "Fixture", "summary": "x", "facts": {**base, "auth_or_security_change": True}, "override": {"architect_gate": "A0", "rationale": "No"}})); assert denied["error_code"] == "CLASSIFICATION_OVERRIDE_DENIED"
            assert not list(root.iterdir()) and root.stat().st_mtime_ns == before_mtime_ns
        finally:
            _workspace._REGISTRY_PATH = prior_registry
    return {"status": "PASS", "read_only": "READ_ONLY_CONFIRMED=yes", "fixtures": "PASS"}
