"""
P11-K 角色專屬 SPEC 契約的 schema engine。

定義所有角色欄位、驗證邏輯、渲染順序和 prompt 投影。

Design:
- task_kind 是 discriminator
- 每種 task_kind 有專屬欄位定義、必填欄位、禁止欄位
- 共用欄位（process_path, validation_tier, human_checkpoints）由 _task_spec_common.py 管理
- subject_spec_revision, subject_spec_sha256 由控制面讀取，不是模型填入
"""

from __future__ import annotations

from typing import Any, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SPEC_SCHEMA_VERSION = 2

PROCESS_PATHS = ("fast", "standard", "deep")
VALIDATION_TIERS = (0, 1, 2, 3, 4)
HUMAN_CHECKPOINT_VALUES = (
    "deploy", "reload", "restart", "docker", "host_write",
    "runtime_write", "migration", "destructive_file_operation",
    "secret_change", "live_worker",
)
FORBIDDEN_OPERATIONS = (
    "delete", "move", "dependency_change", "docker", "restart",
    "host_write", "runtime_artifact_write", "git_history_rewrite",
    "cross_workspace_write",
)
MUTATION_POLICIES = ("readonly", "isolated_reproduction_only")
CONFIDENCE_EXPECTATIONS = ("exploratory", "probable", "confirmed_required")
REVIEW_DIMENSIONS = (
    "spec_compliance", "scope_compliance", "correctness",
    "validation_adequacy", "regression_risk", "security",
    "maintainability", "artifact_consistency",
)
PREFLIGHT_DIMENSIONS = (
    "scope_clarity", "acceptance_testability", "validation_adequacy",
    "stop_conditions", "human_checkpoints", "destructive_operations",
    "compatibility", "evidence_requirements",
)
ARTIFACTS_UNDER_REVIEW = (
    "SPEC.md", "CARD.json", "RESULT.md", "git_diff",
    "validation_evidence", "worker_log",
)

# ---------------------------------------------------------------------------
# Error code constants
# ---------------------------------------------------------------------------

ERR_FIELD_REQUIRED = "ROLE_CONTRACT_FIELD_REQUIRED"
ERR_FIELD_FORBIDDEN = "ROLE_CONTRACT_FIELD_FORBIDDEN"
ERR_FIELD_INVALID_ENUM = "ROLE_CONTRACT_FIELD_INVALID_ENUM"
ERR_FIELD_INVALID_TYPE = "ROLE_CONTRACT_FIELD_INVALID_TYPE"
ERR_FIELD_TOO_MANY_ITEMS = "ROLE_CONTRACT_FIELD_TOO_MANY_ITEMS"
ERR_FIELD_ITEM_TOO_LONG = "ROLE_CONTRACT_FIELD_ITEM_TOO_LONG"
ERR_DICT_FIELD_REQUIRED = "ROLE_CONTRACT_DICT_FIELD_REQUIRED"
ERR_DICT_FIELD_INVALID_TYPE = "ROLE_CONTRACT_DICT_FIELD_INVALID_TYPE"
ERR_INT_TOO_SMALL = "ROLE_CONTRACT_INT_TOO_SMALL"

# ---------------------------------------------------------------------------
# Field definitions
# ---------------------------------------------------------------------------

IMPLEMENTATION_FIELDS: dict[str, dict[str, Any]] = {
    "required_changes": {
        "type": "list[str]",
        "required": True,
        "default": [],
        "max_items": 50,
        "max_item_len": 2000,
        "description": "List of required code changes to implement.",
    },
    "behavioral_invariants": {
        "type": "list[str]",
        "required": False,
        "default": [],
        "max_items": 30,
        "max_item_len": 2000,
        "description": "Behavioral invariants that must be preserved.",
    },
    "change_budget": {
        "type": "dict",
        "required": True,
        "default": {},
        "dict_fields": {
            "max_changed_files": {
                "type": "int",
                "required": True,
                "default": 1,
                "min_value": 1,
                "description": "Maximum number of files that can be changed.",
            },
            "allow_create": {
                "type": "bool",
                "required": False,
                "default": True,
                "description": "Allow creation of new files.",
            },
            "allow_delete": {
                "type": "bool",
                "required": False,
                "default": False,
                "description": "Allow deletion of files.",
            },
            "allow_move": {
                "type": "bool",
                "required": False,
                "default": False,
                "description": "Allow moving/renaming files.",
            },
            "allow_dependency_change": {
                "type": "bool",
                "required": False,
                "default": False,
                "description": "Allow dependency changes.",
            },
        },
        "description": "Budget constraints for changes.",
    },
    "allowed_validation_targets": {
        "type": "list[str]",
        "required": False,
        "default": [],
        "max_items": 20,
        "max_item_len": 500,
        "description": "Allowed validation targets.",
    },
    "forbidden_operations": {
        "type": "list[str]",
        "required": False,
        "default": [],
        "enum": FORBIDDEN_OPERATIONS,
        "max_items": 20,
        "description": "Operations that are explicitly forbidden.",
    },
    "checkpoint_conditions": {
        "type": "list[str]",
        "required": False,
        "default": [],
        "max_items": 20,
        "max_item_len": 2000,
        "description": "Conditions that trigger human checkpoints.",
    },
    "compatibility_requirements": {
        "type": "list[str]",
        "required": False,
        "default": [],
        "max_items": 20,
        "max_item_len": 2000,
        "description": "Compatibility requirements to maintain.",
    },
}

DIAGNOSIS_FIELDS: dict[str, dict[str, Any]] = {
    "observed_symptoms": {
        "type": "list[str]",
        "required": True,
        "default": [],
        "max_items": 50,
        "max_item_len": 2000,
        "description": "Observed symptoms of the issue.",
    },
    "reproduction_context": {
        "type": "list[str]",
        "required": False,
        "default": [],
        "max_items": 30,
        "max_item_len": 2000,
        "description": "Context needed to reproduce the issue.",
    },
    "suspected_components": {
        "type": "list[str]",
        "required": False,
        "default": [],
        "max_items": 30,
        "max_item_len": 500,
        "description": "Suspected components causing the issue.",
    },
    "diagnostic_questions": {
        "type": "list[str]",
        "required": True,
        "default": [],
        "max_items": 30,
        "max_item_len": 2000,
        "description": "Questions to answer during diagnosis.",
    },
    "initial_hypotheses": {
        "type": "list[str]",
        "required": False,
        "default": [],
        "max_items": 20,
        "max_item_len": 2000,
        "description": "Initial hypotheses about root cause.",
    },
    "evidence_plan": {
        "type": "list[str]",
        "required": False,
        "default": [],
        "max_items": 30,
        "max_item_len": 2000,
        "description": "Plan for gathering evidence.",
    },
    "mutation_policy": {
        "type": "str",
        "required": False,
        "default": "readonly",
        "enum": MUTATION_POLICIES,
        "description": "Policy on source mutation during diagnosis.",
    },
    "confidence_expectation": {
        "type": "str",
        "required": False,
        "default": "probable",
        "enum": CONFIDENCE_EXPECTATIONS,
        "description": "Expected confidence level for diagnosis.",
    },
}

REVIEW_FIELDS: dict[str, dict[str, Any]] = {
    "artifacts_under_review": {
        "type": "list[str]",
        "required": True,
        "default": [],
        "enum": ARTIFACTS_UNDER_REVIEW,
        "max_items": 10,
        "description": "Artifacts to be reviewed.",
    },
    "review_dimensions": {
        "type": "list[str]",
        "required": True,
        "default": [],
        "enum": REVIEW_DIMENSIONS,
        "max_items": 10,
        "description": "Dimensions along which to review.",
    },
    "acceptance_mapping_required": {
        "type": "bool",
        "required": False,
        "default": False,
        "description": "Whether acceptance mapping is required.",
    },
    "verdict_rules": {
        "type": "list[str]",
        "required": False,
        "default": [],
        "max_items": 20,
        "max_item_len": 2000,
        "description": "Rules for reaching a verdict.",
    },
    "inconclusive_conditions": {
        "type": "list[str]",
        "required": False,
        "default": [],
        "max_items": 20,
        "max_item_len": 2000,
        "description": "Conditions under which review is inconclusive.",
    },
    "independence_requirements": {
        "type": "list[str]",
        "required": False,
        "default": [
            "reviewer does not modify workspace",
            "reviewer does not use coder RESULT as sole evidence",
            "reviewer must compare actual diff",
            "reviewer does not fix issues for coder",
        ],
        "max_items": 20,
        "max_item_len": 2000,
        "description": "Independence requirements for the reviewer.",
    },
}

ARCHITECTURE_COMMON_FIELDS: dict[str, dict[str, Any]] = {
    "review_questions": {
        "type": "list[str]",
        "required": True,
        "default": [],
        "max_items": 30,
        "max_item_len": 2000,
        "description": "Questions for design review.",
    },
    "constraints": {
        "type": "list[str]",
        "required": False,
        "default": [],
        "max_items": 30,
        "max_item_len": 2000,
        "description": "Design constraints.",
    },
    "risk_focus": {
        "type": "list[str]",
        "required": False,
        "default": [],
        "max_items": 20,
        "max_item_len": 2000,
        "description": "Risk areas to focus on.",
    },
    "gate_criteria": {
        "type": "list[str]",
        "required": True,
        "default": [],
        "max_items": 30,
        "max_item_len": 2000,
        "description": "Criteria that must be met to pass the gate.",
    },
}

ARCHITECTURE_DESIGN_REVIEW_FIELDS: dict[str, dict[str, Any]] = {
    "problem_statement": {
        "type": "list[str]",
        "required": True,
        "default": [],
        "max_items": 20,
        "max_item_len": 4000,
        "description": "Problem statement for the design.",
    },
    "proposed_design": {
        "type": "list[str]",
        "required": True,
        "default": [],
        "max_items": 50,
        "max_item_len": 4000,
        "description": "Description of the proposed design.",
    },
    "alternatives_considered": {
        "type": "list[str]",
        "required": False,
        "default": [],
        "max_items": 20,
        "max_item_len": 4000,
        "description": "Alternative designs considered.",
    },
    "blast_radius": {
        "type": "list[str]",
        "required": False,
        "default": [],
        "max_items": 20,
        "max_item_len": 2000,
        "description": "Blast radius assessment.",
    },
    "rollback_strategy": {
        "type": "list[str]",
        "required": False,
        "default": [],
        "max_items": 20,
        "max_item_len": 2000,
        "description": "Rollback strategy.",
    },
    "compatibility_strategy": {
        "type": "list[str]",
        "required": False,
        "default": [],
        "max_items": 20,
        "max_item_len": 2000,
        "description": "Compatibility strategy.",
    },
    "unresolved_decisions": {
        "type": "list[str]",
        "required": False,
        "default": [],
        "max_items": 20,
        "max_item_len": 2000,
        "description": "Unresolved design decisions.",
    },
    "validation_strategy": {
        "type": "list[str]",
        "required": False,
        "default": [],
        "max_items": 20,
        "max_item_len": 2000,
        "description": "Strategy for validating the design.",
    },
}

ARCHITECTURE_SPEC_PREFLIGHT_FIELDS: dict[str, dict[str, Any]] = {
    "preflight_dimensions": {
        "type": "list[str]",
        "required": True,
        "default": [],
        "enum": PREFLIGHT_DIMENSIONS,
        "max_items": 10,
        "description": "Dimensions to check during spec preflight.",
    },
}

# ---------------------------------------------------------------------------
# Kind-specific metadata
# ---------------------------------------------------------------------------

KIND_FIELDS_MAP: dict[str, dict[str, dict[str, Any]]] = {
    "implementation": IMPLEMENTATION_FIELDS,
    "diagnosis": DIAGNOSIS_FIELDS,
    "review": REVIEW_FIELDS,
    "architecture": ARCHITECTURE_COMMON_FIELDS,
}

ARCHITECTURE_MODE_FIELDS: dict[str, dict[str, dict[str, Any]]] = {
    "design_review": ARCHITECTURE_DESIGN_REVIEW_FIELDS,
    "spec_preflight": ARCHITECTURE_SPEC_PREFLIGHT_FIELDS,
}

# Render order per task_kind
RENDER_ORDER: dict[str, list[str]] = {
    "implementation": [
        "required_changes",
        "behavioral_invariants",
        "change_budget",
        "allowed_validation_targets",
        "compatibility_requirements",
        "forbidden_operations",
        "checkpoint_conditions",
    ],
    "diagnosis": [
        "observed_symptoms",
        "reproduction_context",
        "suspected_components",
        "diagnostic_questions",
        "initial_hypotheses",
        "evidence_plan",
        "mutation_policy",
        "confidence_expectation",
    ],
    "review": [
        "artifacts_under_review",
        "review_dimensions",
        "acceptance_mapping_required",
        "verdict_rules",
        "inconclusive_conditions",
        "independence_requirements",
    ],
}

ARCHITECTURE_RENDER_ORDER: dict[str, list[str]] = {
    "design_review": [
        "problem_statement",
        "constraints",
        "proposed_design",
        "alternatives_considered",
        "blast_radius",
        "rollback_strategy",
        "compatibility_strategy",
        "unresolved_decisions",
        "validation_strategy",
        "review_questions",
        "gate_criteria",
        "risk_focus",
    ],
    "spec_preflight": [
        "preflight_dimensions",
        "constraints",
        "risk_focus",
        "review_questions",
        "gate_criteria",
    ],
}

# Prompt projection per task_kind
PROMPT_PROJECTION: dict[str, list[str]] = {
    "implementation": [
        "required_changes",
        "behavioral_invariants",
        "change_budget",
        "forbidden_operations",
        "checkpoint_conditions",
        "allowed_validation_targets",
        "compatibility_requirements",
    ],
    "diagnosis": [
        "observed_symptoms",
        "reproduction_context",
        "suspected_components",
        "diagnostic_questions",
        "initial_hypotheses",
        "evidence_plan",
        "mutation_policy",
        "confidence_expectation",
    ],
    "review": [
        "artifacts_under_review",
        "review_dimensions",
        "acceptance_mapping_required",
        "inconclusive_conditions",
        "independence_requirements",
    ],
    "architecture": [
        "review_questions",
        "gate_criteria",
        "constraints",
        "risk_focus",
    ],
}

ARCHITECTURE_MODE_PROMPT_PROJECTION: dict[str, list[str]] = {
    "design_review": [
        "problem_statement",
        "proposed_design",
        "alternatives_considered",
        "blast_radius",
        "rollback_strategy",
        "compatibility_strategy",
        "unresolved_decisions",
        "validation_strategy",
    ],
    "spec_preflight": [
        "preflight_dimensions",
    ],
}

# ---------------------------------------------------------------------------
# Public API functions
# ---------------------------------------------------------------------------


def get_fields_for_kind(
    task_kind: str,
    architecture_mode: Optional[str] = None,
) -> dict[str, dict[str, Any]]:
    """Return the complete field definitions for the given task_kind.

    For architecture, common fields are merged with mode-specific fields.
    For non-architecture kinds, only the kind-specific fields are returned.
    """
    if task_kind not in KIND_FIELDS_MAP:
        return {}

    base = dict(KIND_FIELDS_MAP[task_kind])

    if task_kind == "architecture" and architecture_mode is not None:
        mode_fields = ARCHITECTURE_MODE_FIELDS.get(architecture_mode, {})
        base.update(mode_fields)

    return base


def get_all_known_fields() -> dict[str, set[str]]:
    """Return {task_kind: set(field_names)} mapping for forbidden field detection."""
    result: dict[str, set[str]] = {}

    for kind in KIND_FIELDS_MAP:
        result[kind] = set(KIND_FIELDS_MAP[kind].keys())

    # Add architecture mode fields separately so they can be detected
    # as valid fields only when the matching mode is active
    for mode, fields in ARCHITECTURE_MODE_FIELDS.items():
        result[f"architecture__{mode}"] = set(fields.keys())

    return result


def get_required_fields(
    task_kind: str,
    architecture_mode: Optional[str] = None,
) -> list[str]:
    """Return the list of required field names for the given task_kind."""
    fields = get_fields_for_kind(task_kind, architecture_mode)
    return [name for name, spec in fields.items() if spec.get("required")]


def get_render_order(
    task_kind: str,
    architecture_mode: Optional[str] = None,
) -> list[str]:
    """Return the SPEC.md render order for role-specific fields."""
    if task_kind == "architecture":
        if architecture_mode in ARCHITECTURE_RENDER_ORDER:
            return list(ARCHITECTURE_RENDER_ORDER[architecture_mode])
        return []
    return list(RENDER_ORDER.get(task_kind, []))


def get_prompt_projection(
    task_kind: str,
    architecture_mode: Optional[str] = None,
) -> list[str]:
    """Return the list of field names to project into the worker prompt."""
    base = list(PROMPT_PROJECTION.get(task_kind, []))

    if task_kind == "architecture" and architecture_mode is not None:
        mode_fields = ARCHITECTURE_MODE_PROMPT_PROJECTION.get(architecture_mode, [])
        base.extend(mode_fields)

    return base


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _check_type(value: Any, expected_type: str) -> bool:
    """Check if value matches the expected type string."""
    type_map = {
        "str": str,
        "list[str]": list,
        "bool": bool,
        "int": int,
        "dict": dict,
    }
    py_type = type_map.get(expected_type)
    if py_type is None:
        return False
    return isinstance(value, py_type)


def _check_enum(value: Any, enum_values: tuple[Any, ...]) -> bool:
    """Check if a value is in the allowed enum values."""
    return value in enum_values


def _build_error(
    code: str,
    task_kind: str,
    field: str,
    reason: str,
    expected: Optional[str] = None,
    received: Optional[str] = None,
) -> dict[str, Any]:
    """Build a validation error dict."""
    error: dict[str, Any] = {
        "code": code,
        "task_kind": task_kind,
        "field": field,
        "reason": reason,
    }
    if expected is not None:
        error["expected"] = expected
    if received is not None:
        error["received"] = received
    return error


def _validate_field_list(
    value: Any,
    field_spec: dict[str, Any],
    task_kind: str,
    field_name: str,
    errors: list[dict[str, Any]],
) -> None:
    """Validate a list[str] field."""
    if not isinstance(value, list):
        errors.append(
            _build_error(
                ERR_FIELD_INVALID_TYPE,
                task_kind,
                field_name,
                f"Expected list, got {type(value).__name__}",
                expected="list",
                received=type(value).__name__,
            )
        )
        return

    max_items = field_spec.get("max_items")
    if max_items is not None and len(value) > max_items:
        errors.append(
            _build_error(
                ERR_FIELD_TOO_MANY_ITEMS,
                task_kind,
                field_name,
                f"Too many items: {len(value)} (max {max_items})",
                expected=str(max_items),
                received=str(len(value)),
            )
        )

    max_item_len = field_spec.get("max_item_len")
    if max_item_len is not None:
        for i, item in enumerate(value):
            if isinstance(item, str) and len(item) > max_item_len:
                errors.append(
                    _build_error(
                        ERR_FIELD_ITEM_TOO_LONG,
                        task_kind,
                        field_name,
                        f"Item {i} too long: {len(item)} chars (max {max_item_len})",
                        expected=str(max_item_len),
                        received=str(len(item)),
                    )
                )

    enum_values = field_spec.get("enum")
    if enum_values is not None:
        for item in value:
            if not _check_enum(item, enum_values):
                errors.append(
                    _build_error(
                        ERR_FIELD_INVALID_ENUM,
                        task_kind,
                        field_name,
                        f"Invalid enum value: {item!r}",
                        expected=", ".join(str(e) for e in enum_values),
                        received=str(item),
                    )
                )


def _validate_field_str(
    value: Any,
    field_spec: dict[str, Any],
    task_kind: str,
    field_name: str,
    errors: list[dict[str, Any]],
) -> None:
    """Validate a str field."""
    if not isinstance(value, str):
        errors.append(
            _build_error(
                ERR_FIELD_INVALID_TYPE,
                task_kind,
                field_name,
                f"Expected str, got {type(value).__name__}",
                expected="str",
                received=type(value).__name__,
            )
        )
        return

    enum_values = field_spec.get("enum")
    if enum_values is not None and not _check_enum(value, enum_values):
        errors.append(
            _build_error(
                ERR_FIELD_INVALID_ENUM,
                task_kind,
                field_name,
                f"Invalid enum value: {value!r}",
                expected=", ".join(str(e) for e in enum_values),
                received=str(value),
            )
        )


def _validate_field_bool(
    value: Any,
    field_spec: dict[str, Any],
    task_kind: str,
    field_name: str,
    errors: list[dict[str, Any]],
) -> None:
    """Validate a bool field."""
    if not isinstance(value, bool):
        errors.append(
            _build_error(
                ERR_FIELD_INVALID_TYPE,
                task_kind,
                field_name,
                f"Expected bool, got {type(value).__name__}",
                expected="bool",
                received=type(value).__name__,
            )
        )


def _validate_field_int(
    value: Any,
    field_spec: dict[str, Any],
    task_kind: str,
    field_name: str,
    errors: list[dict[str, Any]],
) -> None:
    """Validate an int field."""
    if not isinstance(value, int) or isinstance(value, bool):
        errors.append(
            _build_error(
                ERR_FIELD_INVALID_TYPE,
                task_kind,
                field_name,
                f"Expected int, got {type(value).__name__}",
                expected="int",
                received=type(value).__name__,
            )
        )
        return

    min_value = field_spec.get("min_value")
    if min_value is not None and value < min_value:
        errors.append(
            _build_error(
                ERR_INT_TOO_SMALL,
                task_kind,
                field_name,
                f"Value too small: {value} (min {min_value})",
                expected=str(min_value),
                received=str(value),
            )
        )


def _validate_field_dict(
    value: Any,
    field_spec: dict[str, Any],
    task_kind: str,
    field_name: str,
    errors: list[dict[str, Any]],
) -> None:
    """Validate a dict field with sub-fields."""
    if not isinstance(value, dict):
        errors.append(
            _build_error(
                ERR_FIELD_INVALID_TYPE,
                task_kind,
                field_name,
                f"Expected dict, got {type(value).__name__}",
                expected="dict",
                received=type(value).__name__,
            )
        )
        return

    dict_fields = field_spec.get("dict_fields", {})
    for sub_field_name, sub_spec in dict_fields.items():
        sub_value = value.get(sub_field_name)

        if sub_spec.get("required"):
            if sub_value is None:
                errors.append(
                    _build_error(
                        ERR_DICT_FIELD_REQUIRED,
                        task_kind,
                        f"{field_name}.{sub_field_name}",
                        f"Required dict field missing: {field_name}.{sub_field_name}",
                        expected="value present",
                        received="None",
                    )
                )
                continue

        if sub_value is not None:
            sub_type = sub_spec.get("type", "")
            if sub_type == "int":
                _validate_field_int(
                    sub_value, sub_spec, task_kind,
                    f"{field_name}.{sub_field_name}", errors,
                )
            elif sub_type == "bool":
                _validate_field_bool(
                    sub_value, sub_spec, task_kind,
                    f"{field_name}.{sub_field_name}", errors,
                )
            elif sub_type == "str":
                _validate_field_str(
                    sub_value, sub_spec, task_kind,
                    f"{field_name}.{sub_field_name}", errors,
                )


# ---------------------------------------------------------------------------
# Validation functions
# ---------------------------------------------------------------------------


def validate_role_contract(
    task_kind: str,
    role_contract: dict[str, Any],
    architecture_mode: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Validate a role_contract dict for the given task_kind.

    Returns a list of error dicts. An empty list means validation passed.
    """
    errors: list[dict[str, Any]] = []

    # Get the valid fields for this kind
    valid_fields = get_fields_for_kind(task_kind, architecture_mode)

    # Collect all known fields across all kinds for forbidden detection
    all_known = get_all_known_fields()
    # Build a flat set of all field names across all kinds and modes
    all_known_flat: set[str] = set()
    for fields_set in all_known.values():
        all_known_flat.update(fields_set)

    # Determine which mode-specific fields are forbidden for this kind
    architecture_mode_forbidden: set[str] = set()
    if task_kind == "architecture":
        if architecture_mode == "design_review":
            architecture_mode_forbidden = set(
                ARCHITECTURE_SPEC_PREFLIGHT_FIELDS.keys()
            )
        elif architecture_mode == "spec_preflight":
            architecture_mode_forbidden = set(
                ARCHITECTURE_DESIGN_REVIEW_FIELDS.keys()
            )

    # Check each key in the role_contract
    for key in role_contract:
        if key in valid_fields:
            # Field is valid for this kind - validate type, enum, list constraints
            field_spec = valid_fields[key]
            value = role_contract[key]
            field_type = field_spec.get("type", "")

            if value is None:
                # None values are handled by the required check later
                continue

            if field_type == "list[str]":
                _validate_field_list(value, field_spec, task_kind, key, errors)
            elif field_type == "str":
                _validate_field_str(value, field_spec, task_kind, key, errors)
            elif field_type == "bool":
                _validate_field_bool(value, field_spec, task_kind, key, errors)
            elif field_type == "int":
                _validate_field_int(value, field_spec, task_kind, key, errors)
            elif field_type == "dict":
                _validate_field_dict(value, field_spec, task_kind, key, errors)

        elif key in architecture_mode_forbidden:
            # This field is from the other architecture mode - forbidden
            errors.append(
                _build_error(
                    ERR_FIELD_FORBIDDEN,
                    task_kind,
                    key,
                    f"Field is specific to architecture mode other than {architecture_mode!r}",
                    expected="not present",
                    received=f"present in {task_kind} with mode {architecture_mode!r}",
                )
            )

        elif key in all_known_flat:
            # Field belongs to another kind - forbidden
            errors.append(
                _build_error(
                    ERR_FIELD_FORBIDDEN,
                    task_kind,
                    key,
                    f"Field is not valid for task_kind={task_kind!r}",
                    expected="a valid field for this task_kind",
                    received=key,
                )
            )
        else:
            # Unknown field - also forbidden
            errors.append(
                _build_error(
                    ERR_FIELD_FORBIDDEN,
                    task_kind,
                    key,
                    f"Unknown field: {key!r}",
                    expected="a known field name",
                    received=key,
                )
            )

    # Check all required fields are present and non-empty
    for field_name, field_spec in valid_fields.items():
        if not field_spec.get("required"):
            continue

        value = role_contract.get(field_name)
        field_type = field_spec.get("type", "")

        if value is None:
            errors.append(
                _build_error(
                    ERR_FIELD_REQUIRED,
                    task_kind,
                    field_name,
                    f"Required field missing or None: {field_name}",
                    expected="non-null value",
                    received="None",
                )
            )
            continue

        if field_type == "list[str]" and len(value) == 0:
            errors.append(
                _build_error(
                    ERR_FIELD_REQUIRED,
                    task_kind,
                    field_name,
                    f"Required list field is empty: {field_name}",
                    expected="non-empty list",
                    received="empty list",
                )
            )

        if field_type == "dict" and len(value) == 0:
            errors.append(
                _build_error(
                    ERR_FIELD_REQUIRED,
                    task_kind,
                    field_name,
                    f"Required dict field is empty: {field_name}",
                    expected="non-empty dict",
                    received="empty dict",
                )
            )

        if field_type == "str" and value == "":
            errors.append(
                _build_error(
                    ERR_FIELD_REQUIRED,
                    task_kind,
                    field_name,
                    f"Required string field is empty: {field_name}",
                    expected="non-empty string",
                    received="empty string",
                )
            )

    return errors


def apply_defaults(
    task_kind: str,
    role_contract: dict[str, Any],
    architecture_mode: Optional[str] = None,
) -> dict[str, Any]:
    """Apply default values for missing optional fields.

    Does NOT overwrite existing values. Returns a complete role_contract.
    """
    fields = get_fields_for_kind(task_kind, architecture_mode)
    result = dict(role_contract)

    for field_name, field_spec in fields.items():
        if field_name not in result:
            result[field_name] = field_spec.get("default")

    return result


# ---------------------------------------------------------------------------
# Semantic validators
# ---------------------------------------------------------------------------


def validate_implementation_semantics(
    role_contract: dict[str, Any],
) -> list[dict[str, Any]]:
    """Implementation-specific semantic validation.

    Returns a list of warning/error dicts.
    """
    errors: list[dict[str, Any]] = []

    # required_changes non-empty is already checked by validate_role_contract
    # change_budget.max_changed_files >= 1
    change_budget = role_contract.get("change_budget", {})
    if isinstance(change_budget, dict):
        max_changed = change_budget.get("max_changed_files", 1)
        if not isinstance(max_changed, int) or max_changed < 1:
            errors.append(
                _build_error(
                    ERR_INT_TOO_SMALL,
                    "implementation",
                    "change_budget.max_changed_files",
                    "max_changed_files must be >= 1",
                    expected=">= 1",
                    received=str(max_changed),
                )
            )

    # allow_delete=False => forbidden_operations should include "delete" (warning)
    allow_delete = change_budget.get("allow_delete", False) if isinstance(change_budget, dict) else False
    if not allow_delete:
        forbidden = role_contract.get("forbidden_operations", [])
        if isinstance(forbidden, list) and "delete" not in forbidden:
            errors.append(
                {
                    "code": "ROLE_CONTRACT_SEMANTIC_WARNING",
                    "task_kind": "implementation",
                    "field": "forbidden_operations",
                    "reason": (
                        "allow_delete is False but 'delete' is not in "
                        "forbidden_operations; recommended to add it"
                    ),
                }
            )

    return errors


def validate_diagnosis_semantics(
    role_contract: dict[str, Any],
) -> list[dict[str, Any]]:
    """Diagnosis-specific semantic validation.

    Returns a list of error dicts.
    """
    errors: list[dict[str, Any]] = []

    # mutation_policy must be "readonly" (debugger has no write capability)
    mutation_policy = role_contract.get("mutation_policy", "readonly")
    if mutation_policy != "readonly":
        errors.append(
            _build_error(
                ERR_FIELD_INVALID_ENUM,
                "diagnosis",
                "mutation_policy",
                "debugger has no write capability; mutation_policy must be 'readonly'",
                expected="readonly",
                received=str(mutation_policy),
            )
        )

    return errors


def validate_review_semantics(
    role_contract: dict[str, Any],
) -> list[dict[str, Any]]:
    """Review-specific semantic validation.

    Returns a list of error dicts.
    """
    # write_scope empty and subject_task_id required are validated
    # by _task_spec_common, not duplicated here.
    return []


def validate_architecture_semantics(
    role_contract: dict[str, Any],
    architecture_mode: Optional[str],
) -> list[dict[str, Any]]:
    """Architecture-specific semantic validation.

    Returns a list of error dicts.
    """
    errors: list[dict[str, Any]] = []

    if architecture_mode is None:
        errors.append(
            _build_error(
                ERR_FIELD_REQUIRED,
                "architecture",
                "architecture_mode",
                "architecture_mode is required for architecture task_kind",
                expected="design_review or spec_preflight",
                received="None",
            )
        )
        return errors

    if architecture_mode not in ("design_review", "spec_preflight"):
        errors.append(
            _build_error(
                ERR_FIELD_INVALID_ENUM,
                "architecture",
                "architecture_mode",
                f"Invalid architecture_mode: {architecture_mode!r}",
                expected="design_review, spec_preflight",
                received=str(architecture_mode),
            )
        )
        return errors

    if architecture_mode == "design_review":
        problem_statement = role_contract.get("problem_statement", [])
        proposed_design = role_contract.get("proposed_design", [])
        review_questions = role_contract.get("review_questions", [])

        if not isinstance(problem_statement, list) or len(problem_statement) == 0:
            errors.append(
                _build_error(
                    ERR_FIELD_REQUIRED,
                    "architecture",
                    "problem_statement",
                    "problem_statement must be non-empty for design_review",
                    expected="non-empty list",
                    received=str(problem_statement),
                )
            )

        if not isinstance(proposed_design, list) or len(proposed_design) == 0:
            errors.append(
                _build_error(
                    ERR_FIELD_REQUIRED,
                    "architecture",
                    "proposed_design",
                    "proposed_design must be non-empty for design_review",
                    expected="non-empty list",
                    received=str(proposed_design),
                )
            )

        if not isinstance(review_questions, list) or len(review_questions) == 0:
            errors.append(
                _build_error(
                    ERR_FIELD_REQUIRED,
                    "architecture",
                    "review_questions",
                    "review_questions must be non-empty for design_review",
                    expected="non-empty list",
                    received=str(review_questions),
                )
            )

    elif architecture_mode == "spec_preflight":
        preflight_dimensions = role_contract.get("preflight_dimensions", [])

        if not isinstance(preflight_dimensions, list) or len(preflight_dimensions) == 0:
            errors.append(
                _build_error(
                    ERR_FIELD_REQUIRED,
                    "architecture",
                    "preflight_dimensions",
                    "preflight_dimensions must be non-empty for spec_preflight",
                    expected="non-empty list",
                    received=str(preflight_dimensions),
                )
            )

    return errors

# End of _role_contracts.py
