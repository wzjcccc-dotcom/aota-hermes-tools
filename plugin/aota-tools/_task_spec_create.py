"""aota_task_spec_create — create a bounded AOTA task specification artifact.

This tool only creates a draft SPEC; it does not approve or start execution.
Use it before any profile execution task.
"""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path

from ._task_spec_common import (
    PROFILE_TASK_ROOT,
    TASK_KINDS,
    TASK_KIND_PROFILE_HINT,
    RISK_LEVELS,
    STATUS_DRAFT,
    HUMAN_CHECKPOINT_POLICY_MAP,
    SOURCE_MUTATION_POLICY_MAP,
    generate_task_id,
    utc_now_iso,
    compute_sha256,
    validate_scope_expressions,
    validate_semantic_rules,
    check_scope_overlap,
    validate_field_limits,
    render_spec_md,
    build_spec_dict,
    build_meta,
    atomic_write,
    write_json,
    validate_task_reference,
    create_exclusive_task_dir,
    get_task_dir,
    load_meta,
    WorkspaceError,
    SPEC_SCHEMA_VERSION_CURRENT,
    validate_process_path,
    validate_validation_tier,
    validate_human_checkpoints,
    validate_role_contract_for_task,
    role_contract_errors_to_message,
    apply_defaults,
)
from ._spec_traceability import build_trusted_snapshot, validate_traceability_input
from ._spec_contract import ContractError, ROUTING, SPEC_KINDS, validate_spec, PAYLOAD_FIELDS, REF_TYPES
from ._session_active_spec_binding import trusted_session_context
from ._trusted_runtime_context import missing_context_result
from ._reference_resolver import ReferenceError, resolve_current_plan
from ._next_tool_contract import attach_next_tool_option
from ._session_state_authority import (
    SessionStateError,
    STANDALONE_PROJECT_SENTINEL,
    consume_current_work_classification_pointer,
    read_current_work_classification_pointer,
    write_current_draft_spec_pointer,
)

TOOL_NAME = "aota_task_spec_create"
TOOLSET_NAME = "aota_task_spec"

# ---------------------------------------------------------------------------
# WI-2: build canonical SCHEMA description
# ---------------------------------------------------------------------------

_REF_TYPE_LIST = sorted(REF_TYPES)
_SPEC_KINDS_LIST = list(SPEC_KINDS)

def _build_payload_field_matrix() -> str:
    lines = ["Canonical payload fields per spec_kind (closed field matrix):"]
    for kind in _SPEC_KINDS_LIST:
        fields = sorted(PAYLOAD_FIELDS.get(kind, frozenset()))
        lines.append(f"  {kind}: {fields}")
    return "\n".join(lines)

_PAYLOAD_FIELD_MATRIX = _build_payload_field_matrix()

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Use to create a bounded AOTA task specification artifact. "
        "This tool only creates a draft SPEC; it does not approve or start "
        "execution. Use it before any profile execution task.\n\n"
        "CANONICAL CREATE: Use spec_kind (not task_kind). task_kind is a "
        "deprecated compatibility alias and will be REJECTED on new writes. "
        "Accepted spec_kind values: " + ", ".join(_SPEC_KINDS_LIST) + ".\n\n"
        "CONTEXT_REFS ARTIFACT REF: Each context_refs entry is an object with fields:\n"
        '  ref_type (required): one of ' + ", ".join(_REF_TYPE_LIST) + "\n"
        "  artifact_id (required): bounded logical id string\n"
        "  artifact_path (optional): workspace-relative path, no .. or backslash\n"
        "  revision (optional): integer >= 1\n"
        "  hash (optional): 64-char hex string\n\n"
        "WORKSPACE_DECISION_ID: Accepts only a workspace-selection authority decision "
        "(created by task-main). Not a plan decision, review decision, or user input. "
        "When provided, workspace_context is derived from that decision.\n\n"
        "REVIEW SPEC: context_refs MUST include at least one ref with "
        "ref_type=subject_spec. subject_task_id is a required top-level parameter "
        "for review SPECs.\n\n"
        + _PAYLOAD_FIELD_MATRIX + "\n\n"
        "Role-specific contract (role_contract object):\n"
        "- implementation: required_changes (list[str], required), change_budget (dict with max_changed_files, allow_create, allow_delete, allow_move, allow_dependency_change), behavioral_invariants, allowed_validation_targets, forbidden_operations, checkpoint_conditions, compatibility_requirements\n"
        "- diagnosis: observed_symptoms (list[str], required), diagnostic_questions (list[str], required), reproduction_context, suspected_components, initial_hypotheses, evidence_plan, mutation_policy (readonly|isolated_reproduction_only), confidence_expectation (exploratory|probable|confirmed_required)\n"
        "- review: artifacts_under_review (list[str], required), review_dimensions (list[str], required), acceptance_mapping_required (bool), verdict_rules, inconclusive_conditions, independence_requirements\n"
        "- architecture: review_questions (list[str], required), gate_criteria (list[str], required), constraints, risk_focus + design_review: problem_statement (required), proposed_design (required), alternatives_considered, blast_radius, rollback_strategy, compatibility_strategy, unresolved_decisions, validation_strategy + spec_preflight: preflight_dimensions (required)\n"
        "- stewardship (temporary WI-09B compatibility): project_id, allowed_project_artifacts, operation, forbidden_actions; write_scope must be empty\n"
        "Forbidden fields from other task kinds will be rejected.\n\n"
        "VALID EXAMPLES:\n"
        "1. Implementation SPEC (canonical):\n"
        "  {workspace_id: my-ws, spec_kind: implementation, project_id: my-proj, "
        "work_item_id: WI-01, objective: Add feature X, summary: Implement X, "
        "acceptance_criteria: [test passes], constraints: [no deploy], "
        "capability_contract: {source_read: true, source_write: true}, "
        "payload: {read_scope: [src/**], write_scope: [src/**], "
        "forbidden_scope: [src/secret/**], implementation_requirements: [add function], "
        "validation_commands: [python_module_compile], validation_strategy: Tier 1, "
        "runtime_actions: {}}}\n"
        "2. Review SPEC (canonical):\n"
        "  {workspace_id: my-ws, spec_kind: review, project_id: my-proj, "
        "work_item_id: WI-02, objective: Review implementation, summary: Review WI-01, "
        "subject_task_id: pt_20260721T120000_abcdef01, "
        "context_refs: [{ref_type: subject_spec, artifact_id: pt_20260721T120000_abcdef01}], "
        "acceptance_criteria: [spec compliance verified], "
        "capability_contract: {source_read: true}, "
        "payload: {subject_spec_ref: {ref_type: subject_spec, artifact_id: pt_20260721T120000_abcdef01}, "
        "subject_result_ref: {ref_type: subject_result, artifact_id: pt_20260721T120000_abcdef01}, "
        "review_dimensions: [spec_compliance, correctness]}}\n"
        "3. INVALID: task_kind is rejected on canonical create:\n"
        "  {workspace_id: my-ws, task_kind: implementation, ...} "
        "-> error: task_kind is a deprecated read compatibility alias; create requires spec_kind\n"
        "4. INVALID: context_refs artifact ref as string:\n"
        "  {context_refs: [plan-123]} "
        "-> error: artifact ref must be an object with ref_type, artifact_id fields"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {
                "type": "string",
                "description": "Registered workspace identifier (e.g. 'aota-runtime')",
            },
            "task_kind": {
                "type": "string",
                "enum": list(TASK_KINDS),
                "description": "DEPRECATED compatibility alias. Use spec_kind instead. task_kind is rejected on new canonical writes; only accepted for legacy read compatibility.",
            },
            "title": {
                "type": "string",
                "description": "Short human-readable title (max 200 characters)",
            },
            "goal": {
                "type": "string",
                "description": "Detailed goal description (max 8000 characters)",
            },
            "risk_level": {
                "type": "string",
                "enum": list(RISK_LEVELS),
                "description": "Assessed risk level: low, medium, or high",
            },
            "known_inputs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Known inputs / context references",
                "default": [],
            },
            "read_scope": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Glob patterns defining read-accessible paths",
            },
            "write_scope": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Glob patterns defining write-accessible paths",
                "default": [],
            },
            "forbidden_scope": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Glob patterns explicitly forbidden",
                "default": [],
            },
            "acceptance_criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Measurable acceptance criteria",
            },
            "validation_policy": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Validation / review policy items",
                "default": [],
            },
            "stop_conditions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Conditions that stop task execution",
            },
            "evidence_required": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Required evidence items for completion",
            },
            "subject_task_id": {
                "type": "string",
                "description": "Subject task ID (required for review and architecture tasks)",
            },
            "parent_task_id": {
                "type": "string",
                "description": "Parent task ID for task hierarchy",
            },
            "architecture_mode": {
                "type": "string",
                "enum": ["design_review", "spec_preflight"],
                "description": "Architecture review mode (required for architecture task kind)",
            },
            "process_path": {
                "type": "string",
                "enum": ["fast", "standard", "deep"],
                "description": "Process path: fast (low-risk, local, reversible), standard (multi-file, medium risk), deep (control-plane, security, data, cross-service, high-risk). Default: standard",
            },
            "validation_tier": {
                "type": "integer",
                "enum": [0, 1, 2, 3, 4],
                "description": "Validation tier: 0 (Static), 1 (Local Smoke), 2 (Integration), 3 (Runtime), 4 (Live E2E). Default: 0",
            },
            "human_checkpoints": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Human checkpoint triggers (e.g. deploy, reload, restart, docker, host_write, runtime_write, migration, destructive_file_operation, secret_change, live_worker)",
                "default": [],
            },
            "role_contract": {
                "type": "object",
                "description": "Role-specific contract fields. Required fields and allowed fields depend on spec_kind/task_kind. See tool description for per-kind schema. Forbidden fields from other task kinds will be rejected.",
                "default": {},
            },
            "traceability": {
                "type": "object",
                "description": "Optional strict source reference: {mode: standalone} or {mode: plan_linked, plan_id, milestone_id, work_item_id, architect_review_id?}. Trusted revision/SHA fields are never accepted.",
            },
        },
        "required": [
            "workspace_id",
            "task_kind",
            "title",
            "goal",
            "risk_level",
            "read_scope",
            "acceptance_criteria",
            "stop_conditions",
            "evidence_required",
        ],
        "additionalProperties": False,
    },
}

# WI-09C canonical input surface.  The legacy fields above remain documented
# for migration readers, but a canonical creation is selected by spec_kind.
SCHEMA["parameters"]["properties"].update({
    "spec_kind": {"type": "string", "enum": list(SPEC_KINDS), "description": "Canonical SPEC kind. Required for new writes. Accepted values: " + ", ".join(_SPEC_KINDS_LIST) + ". task_kind is a deprecated alias and will be rejected on canonical create."},
    "project_id": {"type": "string", "description": "Canonical project identifier."},
    "work_item_id": {"type": "string", "description": "Work item identifier for traceability."},
    "objective": {"type": "string", "description": "Concise objective statement."},
    "summary": {"type": "string", "description": "Bounded summary of the task."},
    "context_refs": {"type": "array", "items": {"type": "object"}, "description": "Artifact references. Each item is an object with: ref_type (required, one of " + ", ".join(_REF_TYPE_LIST) + "), artifact_id (required), artifact_path (optional), revision (optional int >= 1), hash (optional 64-char hex). Strings are rejected."},
    "related_artifacts": {"type": "array", "items": {"type": "object"}, "description": "Related artifact references (same shape as context_refs)."},
    "constraints": {"type": "array", "items": {"type": "string"}, "description": "Bounded constraints."},
    "forbidden_actions": {"type": "array", "items": {"type": "string"}, "description": "Explicitly forbidden actions."},
    "expected_artifacts": {"type": "array", "items": {"type": "string"}, "description": "Expected output artifacts."},
    "capability_contract": {"type": "object", "description": "Requested capabilities (boolean flags)."},
    "payload": {"type": "object", "description": "Closed per-kind payload. Allowed fields per spec_kind:\n" + _PAYLOAD_FIELD_MATRIX},
    "supersedes_spec_id": {"type": "string", "description": "ID of the SPEC this one supersedes."},
    "workspace_decision_id": {"type": "string", "description": "Workspace-selection authority decision ID (from task-main). Only workspace-selection decisions are accepted; not plan/review/user decisions."},
})
SCHEMA["description"] += " New writes use canonical spec_kind; task_kind is a deprecated compatibility alias that is rejected on create."
# Canonical model surface: semantic content only.  The handler retains the
# older explicit fields for trusted/internal compatibility, but they are not
# published to the model and therefore cannot be copied or guessed by it.
_CONTROL_PLANE_MODEL_FIELDS = {
    "workspace_id", "project_id", "work_item_id", "workspace_decision_id",
    "parent_task_id", "subject_task_id", "supersedes_spec_id", "task_kind",
    "process_path", "validation_tier", "human_checkpoints", "traceability",
}
for _field in _CONTROL_PLANE_MODEL_FIELDS:
    SCHEMA["parameters"]["properties"].pop(_field, None)
SCHEMA["parameters"]["properties"].update({
    "read_scope": {"type": "array", "items": {"type": "string"}, "description": "Semantic read scope for the requested operation."},
    "write_scope": {"type": "array", "items": {"type": "string"}, "description": "Semantic write scope; empty for read-only SPECs."},
    "subject_ref": {
        "type": "string",
        "enum": ["current_work_classification", "current_plan"],
        "description": "Semantic review subject. The control plane resolves the internal artifact identity. P0 architecture defaults to current_work_classification.",
    },
})
SCHEMA["parameters"]["required"] = ["spec_kind", "objective"]
SCHEMA["description"] = (
    "Create a draft SPEC from semantic intent. The control plane derives workspace, "
    "project/work-item identity, Profile routing, validation tier, process path, "
    "approval policy, revision, and hashes from trusted context and canonical artifacts. "
    "For P0 standalone diagnosis, the minimal invocation is "
    "{spec_kind: diagnosis, objective: ..., read_scope: [...], write_scope: []}. "
    "Do not send workspace/project/work-item IDs, traceability, revisions, hashes, "
    "paths, or approval bindings; those are control-plane-owned. "
    "For architecture, provide subject_ref or allow P0 to use the trusted current_work_classification; "
    "never provide an artifact ID. P1/P2 or subject-bound work requires a uniquely resolvable current Plan/subject; "
    "ambiguity is fail-closed and asks for a human choice. Supported SPEC kinds are "
    "implementation, diagnosis, review, architecture, and stewardship. Payload fields "
    "remain closed by kind, including implementation_requirements, review_dimensions, "
    "symptom, and evidence_required. The handler still accepts legacy explicit "
    "control-plane fields for trusted migration callers only."
)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

_LEGACY_CONTROL_FIELDS = frozenset({
    "project_id", "work_item_id", "traceability",
    "task_id", "spec_id", "workspace_decision_id",
})


def _is_semantic_spec_create(args: dict) -> bool:
    return "spec_kind" in args and not any(key in args for key in _LEGACY_CONTROL_FIELDS)


def _classification_context_result(code: str, *, detail: str = "") -> str:
    next_actions = {
        "classification_context_missing": "run_work_intake_and_classification",
        "classification_context_ambiguous": "repair_session_classification_authority",
        "classification_context_session_mismatch": "run_work_intake_and_classification",
        "classification_context_boundary_mismatch": "repair_session_classification_authority",
        "classification_context_consumed": "run_work_intake_and_classification",
    }
    result = {
        "status": "rejected",
        "operation_result": "spec_create",
        "error": code,
        "retryable": False,
        "same_call_retryable": False,
        "flow_disposition": "await_human" if code == "classification_context_ambiguous" else "stop",
        "human_action_required": code == "classification_context_ambiguous",
        "next_action": next_actions.get(code, "run_work_intake_and_classification"),
    }
    if detail:
        result["detail"] = detail
    return json.dumps(result, sort_keys=True)


def _failure_fingerprint(error: str, args: dict) -> str:
    encoded = json.dumps(
        {"error": error, "arguments": args},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "ff_" + hashlib.sha256(encoded).hexdigest()[:24]


def _repairable_semantic_failure(error: str, args: dict) -> str | None:
    repair_fields: list[str] = []
    if error == "architecture review_mode invalid":
        repair_fields = ["payload.review_mode"]
    elif "challenge_questions" in error:
        repair_fields = ["payload.challenge_questions"]
    elif error.startswith("payload has unknown or cross-role field"):
        repair_fields = ["payload"]
    elif error.startswith("invalid spec_kind"):
        repair_fields = ["spec_kind"]
    elif error == "objective is required":
        repair_fields = ["objective"]
    if not repair_fields:
        return None
    result: dict[str, object] = {
        "status": "rejected",
        "operation_result": "spec_create",
        "error": error,
        "retryable": True,
        "same_call_retryable": False,
        "retry_scope": "changed_arguments_only",
        "flow_disposition": "continue",
        "human_action_required": False,
        "next_action": "repair_semantic_arguments",
        "repairable_fields": repair_fields,
        "failure_fingerprint": _failure_fingerprint(error, args),
    }
    attach_next_tool_option(
        result,
        SCHEMA,
        include=("subject_ref", "read_scope", "write_scope", "payload"),
        reason="Repair only the reported semantic fields; do not repeat identical arguments.",
    )
    return json.dumps(result, sort_keys=True)


def _resolve_semantic_classification(args: dict, trusted_context):
    if not trusted_context.usable_for_active_spec or not trusted_context.workspace_id:
        return None, _classification_context_result("classification_context_missing")
    expected_project = trusted_context.project_id or None
    try:
        binding = read_current_work_classification_pointer(
            trusted_context,
            project_id=expected_project,
        )
        # P0 has a fixed standalone partition even when the host also knows a
        # current project for unrelated work.
        if binding is None and expected_project:
            binding = read_current_work_classification_pointer(
                trusted_context,
                project_id=STANDALONE_PROJECT_SENTINEL,
            )
    except SessionStateError as exc:
        code = {
            "session_state_pointer_ambiguous": "classification_context_ambiguous",
            "trusted_session_context_missing": "classification_context_missing",
            "session_state_binding_mismatch": "classification_context_session_mismatch",
        }.get(exc.code, exc.code if exc.code.startswith("classification_context_") else "classification_context_ambiguous")
        return None, _classification_context_result(code, detail=exc.detail)
    if binding is None:
        return None, _classification_context_result("classification_context_missing")
    if binding.get("session_id") != trusted_context.session_id:
        return None, _classification_context_result("classification_context_session_mismatch")
    if binding.get("workspace_id") != trusted_context.workspace_id:
        return None, _classification_context_result("classification_context_boundary_mismatch")
    if binding.get("state") != "active":
        return None, _classification_context_result("classification_context_consumed")
    classification = binding.get("classification")
    if classification == "P0":
        if binding.get("project_id") != STANDALONE_PROJECT_SENTINEL:
            return None, _classification_context_result("classification_context_boundary_mismatch", detail="P0 binding is not standalone")
        return {**args, "_classification_binding": binding}, None
    if classification not in {"P1", "P2"}:
        return None, _classification_context_result("classification_context_ambiguous", detail="classification_invalid")
    try:
        plan = resolve_current_plan(args["workspace_id"], require_active_work_item=True)
    except ReferenceError as exc:
        raise WorkspaceError(exc.detail or exc.code) from exc
    context = plan.get("workspace_context") if isinstance(plan.get("workspace_context"), dict) else {}
    project_id = context.get("project_id")
    work_item_id = plan.get("active_work_item_id")
    if not project_id or not work_item_id:
        raise WorkspaceError("active_work_item_missing")
    if binding.get("project_id") != project_id:
        return None, _classification_context_result("classification_context_boundary_mismatch")
    traceability = {
        "mode": "plan_linked",
        "plan_id": plan.get("plan_id"),
        "milestone_id": plan.get("active_milestone_id"),
        "work_item_id": work_item_id,
    }
    enriched = {
        **args,
        "workspace_id": trusted_context.workspace_id,
        "project_id": project_id,
        "work_item_id": work_item_id,
        "traceability": traceability,
        "_classification_binding": binding,
    }
    return enriched, None

def handle(args: dict, **kwargs) -> str:
    semantic_create = False
    try:
        trusted_context = trusted_session_context(kwargs)
        semantic_create = isinstance(args, dict) and _is_semantic_spec_create(args)
        if semantic_create:
            if not trusted_context.workspace_id:
                return _classification_context_result("classification_context_missing")
            args = {**args, "workspace_id": trusted_context.workspace_id}
            resolved, rejection = _resolve_semantic_classification(args, trusted_context)
            if rejection is not None:
                return rejection
            args = resolved
        if "spec_kind" in args and not args.get("workspace_id"):
            if not trusted_context.workspace_id:
                return json.dumps(missing_context_result(operation="task_spec_create"), sort_keys=True)
            args = {**args, "workspace_id": trusted_context.workspace_id}
        if "spec_kind" in args and "_classification_binding" not in args and not any(args.get(key) for key in ("project_id", "work_item_id", "traceability")):
            # Diagnosis may remain P0 standalone when no current Plan exists;
            # all other canonical kinds require a uniquely resolvable active
            # Work Item. Ambiguity is never resolved by timestamp/order.
            try:
                plan = resolve_current_plan(args["workspace_id"], require_active_work_item=args["spec_kind"] != "diagnosis")
            except ReferenceError as exc:
                if exc.code == "reference_missing" and args["spec_kind"] == "diagnosis":
                    plan = None
                else:
                    raise WorkspaceError(f"{exc.code}: {exc.detail}") from exc
            if plan is not None:
                context = plan.get("workspace_context") if isinstance(plan.get("workspace_context"), dict) else {}
                project_id = context.get("project_id")
                work_item_id = plan.get("active_work_item_id")
                if not project_id or not work_item_id:
                    raise WorkspaceError("current_plan_missing: current Plan has no project/active Work Item binding")
                args = {**args, "project_id": project_id, "work_item_id": work_item_id}
        return _do_create(args, trusted_context=trusted_context)
    except ReferenceError as e:
        return json.dumps({"status": "rejected", "operation_result": "spec_create", "error": e.code, "detail": e.detail, "choices": e.choices, "retryable": False, "same_call_retryable": False, "flow_disposition": "await_human" if e.choices else "stop", "human_action_required": bool(e.choices), "next_action": "select_governance_subject" if e.choices else "stop_and_report_spec_create_failure"}, sort_keys=True)
    except WorkspaceError as e:
        if semantic_create:
            repair = _repairable_semantic_failure(str(e), args)
            if repair is not None:
                return repair
        return json.dumps(
            {"status": "rejected", "operation_result": "spec_create", "error": str(e), "retryable": False, "same_call_retryable": False, "flow_disposition": "await_human" if "ambiguous" in str(e) else "stop", "human_action_required": "ambiguous" in str(e),
             "next_action": "select_governance_subject" if "ambiguous" in str(e) else "stop_and_report_spec_create_failure"}, sort_keys=True
        )
    except Exception as e:
        return json.dumps(
            {"status": "failed", "operation_result": "spec_create", "error": str(e), "retryable": False, "same_call_retryable": False, "flow_disposition": "stop", "human_action_required": False,
             "next_action": "stop_and_report_spec_create_failure"}, sort_keys=True
        )


def _do_create(args: dict, *, trusted_context=None) -> str:
    # WI-09C canonical input.  ``task_kind`` remains readable only through the
    # legacy adapter; new writes never accept it as their discriminator.
    if "spec_kind" in args:
        if "task_kind" in args or "target_profile" in args or "resolved_profile" in args:
            raise WorkspaceError("spec_kind is canonical; caller profile/task_kind override is rejected")
        return _do_create_contract(args, trusted_context=trusted_context)
    if "task_kind" in args:
        raise WorkspaceError("task_kind is a deprecated read compatibility alias; create requires spec_kind. Use spec_kind with one of: " + ", ".join(_SPEC_KINDS_LIST))
    # ------------------------------------------------------------------
    # Extract parameters
    # ------------------------------------------------------------------
    workspace_id: str = args.get("workspace_id", "")
    task_kind: str = args.get("task_kind", "")
    title: str = args.get("title", "")
    goal: str = args.get("goal", "")
    risk_level: str = args.get("risk_level", "")
    known_inputs: list[str] = args.get("known_inputs") or []
    read_scope: list[str] = args.get("read_scope", [])
    write_scope: list[str] = args.get("write_scope") or []
    forbidden_scope: list[str] = args.get("forbidden_scope") or []
    acceptance_criteria: list[str] = args.get("acceptance_criteria", [])
    validation_policy: list[str] = args.get("validation_policy") or []
    stop_conditions: list[str] = args.get("stop_conditions", [])
    evidence_required: list[str] = args.get("evidence_required", [])
    subject_task_id: str | None = args.get("subject_task_id")
    parent_task_id: str | None = args.get("parent_task_id")
    architecture_mode: str | None = args.get("architecture_mode")
    process_path: str | None = args.get("process_path")
    validation_tier: int | None = args.get("validation_tier")
    human_checkpoints: list[str] = args.get("human_checkpoints") or []
    role_contract: dict | None = args.get("role_contract") or {}
    traceability_input: dict | None = args.get("traceability")

    # ------------------------------------------------------------------
    # 1. Validate scope expressions
    # ------------------------------------------------------------------
    all_scope_lists = {
        "read_scope": read_scope,
        "write_scope": write_scope,
        "forbidden_scope": forbidden_scope,
    }
    for key, items in all_scope_lists.items():
        err = validate_scope_expressions(items)
        if err:
            raise WorkspaceError(f"{key}: {err}")

    # ------------------------------------------------------------------
    # 2. Validate semantic rules per task_kind
    # ------------------------------------------------------------------
    err = validate_semantic_rules(task_kind, write_scope, subject_task_id, architecture_mode)
    if err:
        raise WorkspaceError(err)

    # ------------------------------------------------------------------
    # 2b. Scope overlap check
    # ------------------------------------------------------------------
    err = check_scope_overlap(write_scope, forbidden_scope)
    if err:
        raise WorkspaceError(err)

    # ------------------------------------------------------------------
    # 2c. P11-K: Validate shared new fields
    # ------------------------------------------------------------------
    err = validate_process_path(process_path)
    if err:
        raise WorkspaceError(err)
    err = validate_validation_tier(validation_tier)
    if err:
        raise WorkspaceError(err)
    err = validate_human_checkpoints(human_checkpoints)
    if err:
        raise WorkspaceError(err)

    # ------------------------------------------------------------------
    # 2d. P11-K: Validate role contract
    # ------------------------------------------------------------------
    role_errors = validate_role_contract_for_task(task_kind, role_contract, architecture_mode)
    if role_errors:
        msg = role_contract_errors_to_message(role_errors)
        raise WorkspaceError(f"role_contract_validation_failed: {msg}")

    # Apply defaults to role_contract
    if role_contract is None:
        role_contract = {}
    role_contract = apply_defaults(task_kind, role_contract, architecture_mode)

    # ------------------------------------------------------------------
    # 3. Resolve workspace
    # ------------------------------------------------------------------
    from ._workspace import resolve_workspace

    workspace_root = resolve_workspace(workspace_id)

    # ------------------------------------------------------------------
    # 3a. Validate the optional source reference against the trusted Plan.
    # ------------------------------------------------------------------
    source_traceability = None
    parsed_traceability = validate_traceability_input(traceability_input)
    if parsed_traceability is not None:
        source_traceability = build_trusted_snapshot(workspace_id, parsed_traceability)

    # ------------------------------------------------------------------
    # 4. Validate field limits
    # ------------------------------------------------------------------
    err = validate_field_limits(
        title,
        goal,
        known_inputs,
        read_scope,
        write_scope,
        forbidden_scope,
        acceptance_criteria,
        validation_policy,
        stop_conditions,
        evidence_required,
    )
    if err:
        raise WorkspaceError(err)

    # ------------------------------------------------------------------
    # 5. Validate reference tasks
    # ------------------------------------------------------------------
    if subject_task_id:
        validate_task_reference(
            workspace_id, subject_task_id, "subject_task_id"
        )
    if parent_task_id:
        validate_task_reference(
            workspace_id, parent_task_id, "parent_task_id"
        )

    # P11-J.1-A: For spec_preflight, read subject SPEC revision/hash
    subject_spec_revision: int | None = None
    subject_spec_sha256: str | None = None
    if task_kind == "architecture" and architecture_mode == "spec_preflight" and subject_task_id:
        subject_dir = get_task_dir(workspace_id, subject_task_id)
        subject_meta = load_meta(subject_dir)
        subject_spec_revision = subject_meta.get("revision")
        subject_spec_sha256 = subject_meta.get("spec_sha256")
        if subject_spec_revision is None or not subject_spec_sha256:
            raise WorkspaceError(
                f"subject_spec_unreadable: cannot read revision/hash from subject task '{subject_task_id}'"
            )

    # P11-K: Review tasks also bind subject SPEC revision/hash
    if task_kind == "review" and subject_task_id:
        subject_dir = get_task_dir(workspace_id, subject_task_id)
        subject_meta = load_meta(subject_dir)
        subject_spec_revision = subject_meta.get("revision")
        subject_spec_sha256 = subject_meta.get("spec_sha256")
        if subject_spec_revision is None or not subject_spec_sha256:
            raise WorkspaceError(
                f"subject_spec_unreadable: cannot read revision/hash from subject task '{subject_task_id}'"
            )

    # ------------------------------------------------------------------
    # 6. Generate task_id + exclusive mkdir
    # ------------------------------------------------------------------
    task_id, task_dir = create_exclusive_task_dir(workspace_id)

    profile_hint = TASK_KIND_PROFILE_HINT[task_kind]
    revision = 1
    status = STATUS_DRAFT
    now = utc_now_iso()

    # ------------------------------------------------------------------
    # 7. Render SPEC.md
    # ------------------------------------------------------------------
    spec_md = render_spec_md(
        task_id=task_id,
        workspace_id=workspace_id,
        task_kind=task_kind,
        profile_hint=profile_hint,
        risk_level=risk_level,
        revision=revision,
        status=status,
        goal=goal,
        known_inputs=known_inputs,
        read_scope=read_scope,
        write_scope=write_scope,
        forbidden_scope=forbidden_scope,
        acceptance_criteria=acceptance_criteria,
        validation_policy=validation_policy,
        stop_conditions=stop_conditions,
        evidence_required=evidence_required,
        subject_task_id=subject_task_id,
        parent_task_id=parent_task_id,
        architecture_mode=architecture_mode,
        subject_spec_revision=subject_spec_revision,
        subject_spec_sha256=subject_spec_sha256,
        process_path=process_path,
        validation_tier=validation_tier,
        human_checkpoints=human_checkpoints,
        role_contract=role_contract,
        source_traceability=source_traceability,
        spec_schema_version=SPEC_SCHEMA_VERSION_CURRENT,
    )

    spec_md_bytes = len(spec_md.encode("utf-8"))
    max_bytes = 65536
    if spec_md_bytes > max_bytes:
        # Clean up directory
        _cleanup_dir(task_dir)
        raise WorkspaceError(
            f"SPEC.md exceeds {max_bytes} bytes (got {spec_md_bytes})"
        )

    # ------------------------------------------------------------------
    # 8. Compute SHA-256
    # ------------------------------------------------------------------
    spec_sha256 = compute_sha256(spec_md)

    # ------------------------------------------------------------------
    # 9. Build meta.json
    # ------------------------------------------------------------------
    spec_dict = build_spec_dict(
        title=title,
        goal=goal,
        known_inputs=known_inputs,
        read_scope=read_scope,
        write_scope=write_scope,
        forbidden_scope=forbidden_scope,
        acceptance_criteria=acceptance_criteria,
        validation_policy=validation_policy,
        stop_conditions=stop_conditions,
        evidence_required=evidence_required,
        process_path=process_path,
        validation_tier=validation_tier,
        human_checkpoints=human_checkpoints,
        role_contract=role_contract,
        source_traceability=source_traceability,
    )

    human_checkpoint_policy = HUMAN_CHECKPOINT_POLICY_MAP[task_kind]
    source_mutation_policy = SOURCE_MUTATION_POLICY_MAP[task_kind]

    meta = build_meta(
        task_id=task_id,
        workspace_id=workspace_id,
        workspace_root_at_creation=str(workspace_root),
        task_kind=task_kind,
        profile_hint=profile_hint,
        risk_level=risk_level,
        revision=revision,
        created_at=now,
        updated_at=now,
        subject_task_id=subject_task_id,
        parent_task_id=parent_task_id,
        human_checkpoint_policy=human_checkpoint_policy,
        source_mutation_policy=source_mutation_policy,
        spec_sha256=spec_sha256,
        spec=spec_dict,
        architecture_mode=architecture_mode,
        subject_spec_revision=subject_spec_revision,
        subject_spec_sha256=subject_spec_sha256,
        spec_schema_version=SPEC_SCHEMA_VERSION_CURRENT,
    )

    # ------------------------------------------------------------------
    # 10. Atomic write both files
    # ------------------------------------------------------------------
    try:
        spec_path = task_dir / "SPEC.md"
        meta_path = task_dir / "meta.json"
        atomic_write(spec_path, spec_md)
        write_json(meta_path, meta)
    except Exception:
        _cleanup_dir(task_dir)
        raise

    # ------------------------------------------------------------------
    # 11. Return compact output
    # ------------------------------------------------------------------
    return json.dumps(
        {
            "status": "created",
            "task_id": task_id,
            "workspace_id": workspace_id,
            "task_kind": task_kind,
            "profile_hint": profile_hint,
            "risk_level": risk_level,
            "revision": revision,
            "spec_path": str(spec_path),
            "meta_path": str(meta_path),
            "draft_spec_sha256": spec_sha256,
            "spec_sha256": spec_sha256,
            "human_checkpoint_policy": human_checkpoint_policy,
            "approval_status": "required" if task_kind == "implementation" else "not_required",
            "spec_schema_version": SPEC_SCHEMA_VERSION_CURRENT,
            "error": None,
        },
        sort_keys=True,
    )


def _do_create_contract(args: dict, *, trusted_context=None) -> str:
    from ._workspace import resolve_workspace
    workspace_id = args.get("workspace_id", "")
    spec_kind = args.get("spec_kind")
    classification_binding = args.get("_classification_binding")
    classification = classification_binding.get("classification") if isinstance(classification_binding, dict) else None
    if spec_kind not in SPEC_KINDS:
        raise WorkspaceError("invalid spec_kind: must be one of " + ", ".join(_SPEC_KINDS_LIST) + ", received=" + str(spec_kind) + ". Use spec_kind (not task_kind) for canonical creates.")
    subject_task_id = args.get("subject_task_id")
    if subject_task_id == "":
        subject_task_id = None
    if subject_task_id is not None and not isinstance(subject_task_id, str):
        raise WorkspaceError("subject_task_id must be a string")
    if spec_kind == "review" and not subject_task_id:
        raise WorkspaceError("review_missing_subject: review task requires subject_task_id; also ensure context_refs includes a ref with ref_type=subject_spec")
    if subject_task_id:
        validate_task_reference(
            workspace_id,
            subject_task_id,
            "subject_task_id",
            expected_project_id=args.get("project_id"),
        )
    workspace_root = resolve_workspace(workspace_id)
    task_id, task_dir = create_exclusive_task_dir(workspace_id)
    now = utc_now_iso()
    objective = args.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        raise WorkspaceError("objective is required")
    summary = args.get("summary") or objective
    acceptance_criteria = args.get("acceptance_criteria") or [objective]
    payload = dict(args.get("payload") or {})
    if spec_kind == "diagnosis":
        read_scope = args.get("read_scope") or []
        payload.setdefault("symptom", objective)
        payload.setdefault("known_facts", list(read_scope))
        payload.setdefault("evidence_required", list(read_scope) or [objective])
        payload.setdefault("mutation_allowed", False)
    if spec_kind == "architecture":
        payload.setdefault("review_mode", "design_review")
        payload.setdefault("challenge_questions", [objective])
        payload.setdefault("tradeoffs_required", [])
        payload.setdefault("risk_dimensions", [])

        subject_artifact = next((
            payload.get(field)
            for field in ("subject_plan_ref", "subject_spec_ref", "subject_work_classification_ref")
            if isinstance(payload.get(field), dict)
        ), None)
        if subject_artifact is None:
            subject_ref = args.get("subject_ref") or "current_work_classification"
            if subject_ref == "current_work_classification":
                if not isinstance(classification_binding, dict) or not classification_binding.get("artifact_digest"):
                    _cleanup_dir(task_dir)
                    raise WorkspaceError("architecture_subject_missing: current work classification is unavailable")
                subject_artifact = {
                    "ref_type": "work_classification",
                    "artifact_id": classification_binding["artifact_digest"],
                }
                payload["subject_work_classification_ref"] = subject_artifact
            elif subject_ref == "current_plan":
                try:
                    plan = resolve_current_plan(workspace_id)
                except ReferenceError as exc:
                    _cleanup_dir(task_dir)
                    raise WorkspaceError(f"{exc.code}: {exc.detail}") from exc
                subject_artifact = {"ref_type": "plan", "artifact_id": plan["plan_id"]}
                payload["subject_plan_ref"] = subject_artifact
            else:
                _cleanup_dir(task_dir)
                raise WorkspaceError("architecture_subject_invalid: use a supported semantic subject_ref")
        context_refs = list(args.get("context_refs") or [])
        if subject_artifact not in context_refs:
            context_refs.append(subject_artifact)
        args = {**args, "context_refs": context_refs}

    # P0 standalone identity is generated by the control plane.  It is not an
    # administrative Plan or a model-invented Work Item ID.  Plan-bound callers
    # must continue to provide/resolve the canonical project/work-item path.
    project_id = args.get("project_id")
    work_item_id = args.get("work_item_id")
    if classification == "P0":
        project_id = f"standalone:{workspace_id}"
        work_item_id = f"standalone:{task_id}"
    elif classification in {"P1", "P2"} and (not project_id or not work_item_id):
        _cleanup_dir(task_dir)
        raise WorkspaceError("plan_bound_spec_requires_project_and_work_item_authority")
    elif not project_id and not work_item_id and not args.get("traceability"):
        project_id = f"standalone:{workspace_id}"
        work_item_id = f"standalone:{task_id}"
    if bool(project_id) != bool(work_item_id):
        _cleanup_dir(task_dir)
        raise WorkspaceError("plan_bound_spec_requires_project_and_work_item_authority")
    workspace_context = None
    if args.get("workspace_decision_id") is not None:
        from ._workspace_context import context_from_selection
        workspace_context = context_from_selection(workspace_id, project_id, args["workspace_decision_id"])
    source_traceability = None
    try:
        validated_traceability = validate_traceability_input(
            args.get("traceability"),
            required=classification in {"P1", "P2"},
        )
        if validated_traceability is not None:
            source_traceability = build_trusted_snapshot(workspace_id, validated_traceability)
    except Exception:
        _cleanup_dir(task_dir)
        raise
    spec = {
        "schema_version": 1, "artifact_type": "spec", "spec_id": task_id,
        "project_id": project_id, "work_item_id": work_item_id,
        "spec_kind": spec_kind, "resolved_profile": ROUTING[spec_kind], "revision": 1,
        "status": "draft", "created_at": now, "updated_at": now, "created_by": "task-main",
        "objective": objective, "summary": summary,
        "context_refs": args.get("context_refs", []), "related_artifacts": args.get("related_artifacts", []),
        "acceptance_criteria": acceptance_criteria, "constraints": args.get("constraints", []),
        "forbidden_actions": args.get("forbidden_actions", []), "expected_artifacts": args.get("expected_artifacts", []),
        "capability_contract": args.get("capability_contract", {}), "payload": payload,
        "supersedes_spec_id": args.get("supersedes_spec_id"), "spec_hash": None, "workspace_context": workspace_context,
    }
    if subject_task_id:
        spec["subject_task_id"] = subject_task_id
    try:
        validate_spec(spec)
    except ContractError as exc:
        _cleanup_dir(task_dir)
        raise WorkspaceError(str(exc)) from exc
    spec_md = json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    file_hash = compute_sha256(spec_md)
    meta = dict(spec)
    meta.update({
        "contract_version": 1, "task_id": task_id, "workspace_id": workspace_id,
        "workspace_root_at_creation": str(workspace_root), "task_kind": spec_kind,  # deprecated alias
        "profile_hint": ROUTING[spec_kind], "spec": spec, "spec_sha256": file_hash,
        "spec_path": "SPEC.md", "frozen_revision": None,
    })
    if source_traceability is not None:
        meta["source_traceability"] = source_traceability
    if isinstance(classification_binding, dict):
        meta["classification_binding"] = {
            "pointer_kind": classification_binding.get("pointer_kind"),
            "classification": classification_binding.get("classification"),
            "authority": classification_binding.get("authority"),
            "execution_depth": classification_binding.get("execution_depth"),
            "classifier_result_digest": classification_binding.get("classifier_result_digest"),
            "artifact_digest": classification_binding.get("artifact_digest"),
            "session_id": classification_binding.get("session_id"),
            "workspace_id": classification_binding.get("workspace_id"),
            "project_id": classification_binding.get("project_id"),
            "binding_status": "used",
        }
    try:
        atomic_write(task_dir / "SPEC.md", spec_md)
        write_json(task_dir / "meta.json", meta)
    except Exception:
        _cleanup_dir(task_dir)
        raise
    result = {"status": "created", "operation_result": "spec_created", "task_id": task_id, "spec_id": task_id,
        "spec_kind": spec_kind, "task_kind": spec_kind, "resolved_profile": ROUTING[spec_kind],
        "revision": 1, "spec_hash": None, "draft_spec_sha256": file_hash, "spec_sha256": file_hash,
        "approval_status": "required" if spec_kind == "implementation" else "not_required",
        "error": None, "resolved_context": {"binding": "current_work_item" if not str(project_id).startswith("standalone:") else "standalone_p0", "profile": ROUTING[spec_kind]}, "next_action": "freeze_current_spec", "retryable": False, "same_call_retryable": False, "flow_disposition": "continue", "human_action_required": False}
    from ._task_spec_freeze import SCHEMA as freeze_schema
    attach_next_tool_option(
        result,
        freeze_schema,
        arguments={"spec_ref": "current_draft_spec"},
        reason="The draft is bound to the current session and is ready to freeze.",
    )
    if classification_binding is not None:
        result.update({
            "classification": classification,
            "architect_gate": classification_binding.get("authority"),
            "execution_depth": classification_binding.get("execution_depth"),
            "classification_binding": {
                "status": "pending_consume",
                "pointer_kind": classification_binding.get("pointer_kind"),
                "artifact_digest": classification_binding.get("artifact_digest"),
                "classifier_result_digest": classification_binding.get("classifier_result_digest"),
                "used": True,
            },
            "classification_used": True,
        })
    try:
        if trusted_context is None or not trusted_context.usable_for_active_spec:
            result["current_draft_binding"] = {
                "status": "failed", "error": "trusted_session_context_missing",
                "retryable": False, "next_action": "stop_and_report_runtime_context_missing",
            }
            result["current_draft_ready"] = False
            result["next_action"] = "stop_and_report_runtime_context_missing"
            result["error"] = "trusted_session_context_missing"
            if classification_binding is not None:
                result["status"] = "failed"
                result["classification_binding"].update({
                    "status": "active",
                    "consumed": False,
                })
        else:
            pointer, pointer_path = write_current_draft_spec_pointer(trusted_context, meta)
            result["current_draft_binding"] = {
                "status": "written", "pointer_kind": pointer["pointer_kind"],
                "pointer_path": str(pointer_path), "artifact_digest": pointer["artifact_digest"],
            }
            result["current_draft_ready"] = True
            if classification_binding is not None:
                try:
                    consumed = consume_current_work_classification_pointer(
                        trusted_context,
                        classification_binding,
                        consumed_by="aota_task_spec_create",
                        consumed_spec_id=task_id,
                    )
                except SessionStateError as exc:
                    result["status"] = "failed"
                    result["error"] = exc.code
                    result["next_action"] = "repair_session_classification_authority"
                    result["classification_binding"].update({
                        "status": "active",
                        "consumed": False,
                        "consume_error": exc.code,
                    })
                else:
                    result["classification_binding"].update({
                        "status": "consumed",
                        "consumed": True,
                        "consumed_by": consumed.get("consumed_by"),
                        "consumed_spec_id": consumed.get("consumed_spec_id"),
                        "consumed_at": consumed.get("consumed_at"),
                    })
    except SessionStateError as exc:
        result["current_draft_binding"] = {
            "status": "failed", "error": exc.code, "detail": exc.detail,
            "retryable": False, "next_action": "stop_and_report_control_plane_inconsistency",
        }
        result["current_draft_ready"] = False
        result["next_action"] = "stop_and_report_control_plane_inconsistency"
        result["error"] = exc.code
        if classification_binding is not None:
            result["status"] = "failed"
            result["classification_binding"].update({
                "status": "active",
                "consumed": False,
                "consume_error": exc.code,
            })
    if result.get("error"):
        result["flow_disposition"] = "stop"
        result.pop("allowed_next_tool", None)
        result.pop("allowed_next_arguments", None)
        result.pop("allowed_next_tool_schema", None)
        result.pop("next_options", None)
    return json.dumps(result, sort_keys=True)


def _cleanup_dir(task_dir: Path) -> None:
    """Remove the task directory (and its contents) on failure."""
    import shutil

    try:
        if task_dir.exists():
            shutil.rmtree(str(task_dir))
    except Exception:
        pass
