"""Semantic P0 dispatch facade for the common SPEC -> Worker round trip.

The existing SPEC create, freeze, and Profile Task start handlers remain the
durable authorities.  This module is a small model-facing adapter that
checks the trusted P0 classification first, translates semantic fields into
the canonical SPEC contract, and invokes those handlers in one control-plane
transaction.  P1/P2 callers continue to use the existing gated chain.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from ._next_tool_contract import minimal_next_tool_schema
from ._session_active_spec_binding import trusted_session_context
from ._session_state_authority import (
    STANDALONE_PROJECT_SENTINEL,
    SessionStateError,
    read_current_work_classification_pointer,
)
from ._task_spec_create import handle as _create_spec
from ._task_spec_freeze import handle as _freeze_spec
from ._profile_task_start import handle as _start_profile_task


TOOL_NAME = "aota_profile_task_dispatch"
TOOLSET_NAME = "aota_profile_task"

_VALIDATION_FIELDS = {
    "commands",
    "strategy",
    "evidence_required",
    "review_dimensions",
    "risk_dimensions",
    "review_mode",
    "operation",
}

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Dispatch one P0 standalone semantic SPEC to its fixed Profile Worker. "
        "The control plane derives workspace/project identity, Profile routing, "
        "revision, hashes, freeze, and start state; do not provide IDs, paths, "
        "Profile names, tool names, or hashes. This facade is P0-only. P1/P2 "
        "must retain their Plan/Work Item/approval-gated SPEC chain. On success "
        "it returns only the completion wait contract; the native completion "
        "event supplies the CARD and semantic RESULT reference."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "spec_kind": {
                "type": "string",
                "enum": ["implementation", "diagnosis", "review", "architecture", "stewardship"],
                "description": "Semantic task kind; Profile routing is fixed by the control plane.",
            },
            "objective": {
                "type": "string",
                "description": "Bounded objective stated in semantic terms.",
            },
            "acceptance_criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional measurable acceptance criteria.",
            },
            "read_scope": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Semantic read scope; paths are validated by the canonical SPEC contract.",
            },
            "write_scope": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Semantic write scope; empty for read-only work.",
            },
            "forbidden_scope": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Semantic forbidden scope.",
            },
            "requirements": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Role-neutral requirements mapped into the fixed role contract.",
            },
            "constraints": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Bounded semantic constraints.",
            },
            "subject_ref": {
                "type": "string",
                "enum": ["current_work_classification", "current_plan"],
                "description": "Semantic subject selector; no artifact ID is accepted.",
            },
            "validation": {
                "type": "object",
                "description": (
                    "Optional bounded validation intent. Allowed keys are commands, strategy, "
                    "evidence_required, review_dimensions, risk_dimensions, review_mode, operation."
                ),
                "properties": {
                    "commands": {"type": "array", "items": {"type": "string"}},
                    "strategy": {"type": "string"},
                    "evidence_required": {"type": "array", "items": {"type": "string"}},
                    "review_dimensions": {"type": "array", "items": {"type": "string"}},
                    "risk_dimensions": {"type": "array", "items": {"type": "string"}},
                    "review_mode": {"type": "string", "enum": ["design_review", "spec_preflight"]},
                    "operation": {"type": "string", "enum": ["intake", "context_prepare", "relationship_resolve", "docs_update", "artifact_link", "close"]},
                },
                "additionalProperties": False,
            },
        },
        "required": ["spec_kind", "objective"],
        "additionalProperties": False,
    },
}


def _decode(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {"status": "failed", "error": "control_plane_response_invalid"}
    return value if isinstance(value, dict) else {"status": "failed", "error": "control_plane_response_invalid"}


def _public_result(
    result: Mapping[str, Any],
    *,
    stage: str,
    default_next_action: str,
) -> dict[str, Any]:
    """Project a handler result without exposing durable IDs or hashes."""
    status = str(result.get("status") or "failed")
    projected: dict[str, Any] = {
        "status": status,
        "operation_result": TOOL_NAME,
        "dispatch_stage": stage,
        "retryable": bool(result.get("retryable", False)),
        "same_call_retryable": False,
        "flow_disposition": result.get("flow_disposition", "stop"),
        "human_action_required": bool(result.get("human_action_required", False)),
        "next_action": result.get("next_action") or default_next_action,
    }
    for key in (
        "error",
        "detail",
        "retry_scope",
        "repairable_fields",
        "failure_fingerprint",
        "completion_transport",
        "completion_delivery_expected",
        "recovery_allowed_after",
        "approval_status",
        "resolved_profile",
        "classification",
        "allowed_next_tool",
        "allowed_next_arguments",
        "allowed_next_tool_schema",
        "next_options",
    ):
        if key in result and result[key] not in (None, ""):
            projected[key] = result[key]
    return projected


def _classification_for_p0(context: Any) -> dict[str, Any] | None:
    if not context.usable_for_active_spec or not context.workspace_id:
        raise SessionStateError("trusted_session_context_missing")
    project_id = context.project_id or None
    binding = read_current_work_classification_pointer(context, project_id=project_id)
    if binding is None and project_id:
        binding = read_current_work_classification_pointer(
            context, project_id=STANDALONE_PROJECT_SENTINEL
        )
    if binding is None:
        raise SessionStateError("classification_context_missing")
    if binding.get("session_id") != context.session_id:
        raise SessionStateError("classification_context_session_mismatch")
    if binding.get("state") != "active":
        raise SessionStateError("classification_context_consumed")
    return binding


def _classification_failure(exc: BaseException) -> dict[str, Any]:
    code = getattr(exc, "code", str(exc) or "classification_context_missing")
    code = {
        "session_state_pointer_ambiguous": "classification_context_ambiguous",
        "session_state_binding_mismatch": "classification_context_session_mismatch",
    }.get(code, code)
    ambiguous = code == "classification_context_ambiguous"
    return {
        "status": "rejected",
        "operation_result": TOOL_NAME,
        "dispatch_stage": "classification",
        "error": code,
        "retryable": False,
        "same_call_retryable": False,
        "flow_disposition": "await_human" if ambiguous else "stop",
        "human_action_required": ambiguous,
        "next_action": "repair_session_classification_authority" if ambiguous else "run_work_intake_and_classification",
    }


def _semantic_create_args(args: Mapping[str, Any]) -> dict[str, Any]:
    kind = args.get("spec_kind")
    objective = args.get("objective")
    if not isinstance(kind, str) or not isinstance(objective, str) or not objective.strip():
        raise ValueError("spec_kind_and_objective_required")
    if args.get("subject_ref") == "current_plan":
        raise ValueError("p0_subject_ref_invalid")
    validation = args.get("validation") or {}
    if not isinstance(validation, dict) or set(validation) - _VALIDATION_FIELDS:
        raise ValueError("validation_fields_invalid")
    requirements = args.get("requirements") or []
    if not isinstance(requirements, list) or any(not isinstance(item, str) for item in requirements):
        raise ValueError("requirements_invalid")
    for field in ("acceptance_criteria", "read_scope", "write_scope", "forbidden_scope", "constraints"):
        value = args.get(field) or []
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"{field}_invalid")
    payload: dict[str, Any] = {}
    if kind == "implementation":
        payload["read_scope"] = list(args.get("read_scope") or [])
        payload["write_scope"] = list(args.get("write_scope") or [])
        payload["forbidden_scope"] = list(args.get("forbidden_scope") or [])
        payload["implementation_requirements"] = requirements or [objective]
        payload["validation_commands"] = list(validation.get("commands") or [])
        payload["validation_strategy"] = validation.get("strategy") or "bounded fixture or compile validation"
        payload["runtime_actions"] = {}
    elif kind == "diagnosis":
        payload["symptom"] = objective
        payload["known_facts"] = list(args.get("read_scope") or [])
        payload["evidence_required"] = list(validation.get("evidence_required") or requirements or args.get("read_scope") or [objective])
        payload["mutation_allowed"] = False
    elif kind == "architecture":
        payload["review_mode"] = validation.get("review_mode") or "design_review"
        payload["challenge_questions"] = requirements or [objective]
        payload["tradeoffs_required"] = []
        payload["risk_dimensions"] = list(validation.get("risk_dimensions") or [])
    elif kind == "review":
        payload["review_dimensions"] = list(validation.get("review_dimensions") or requirements or ["spec_compliance"])
        payload["required_evidence"] = list(validation.get("evidence_required") or [])
    elif kind == "stewardship":
        payload["operation"] = validation.get("operation") or "intake"
        payload["project_context_questions"] = requirements or [objective]
        payload["allowed_project_artifacts"] = []
    return {
        "spec_kind": kind,
        "objective": objective,
        "acceptance_criteria": list(args.get("acceptance_criteria") or []),
        "read_scope": list(args.get("read_scope") or []),
        "write_scope": list(args.get("write_scope") or []),
        "forbidden_actions": list(args.get("forbidden_scope") or []),
        "constraints": list(args.get("constraints") or []),
        "subject_ref": args.get("subject_ref"),
        "payload": payload,
    }


def handle(args: dict, **kwargs: Any) -> str:
    """Run the P0 semantic dispatch transaction and return a compact result."""
    if not isinstance(args, dict):
        return json.dumps({"status": "rejected", "error": "arguments_invalid", "retryable": False}, sort_keys=True)
    context = trusted_session_context(kwargs)
    try:
        binding = _classification_for_p0(context)
    except (SessionStateError, ValueError) as exc:
        return json.dumps(_classification_failure(exc), sort_keys=True)
    if binding.get("classification") != "P0":
        return json.dumps({
            "status": "rejected",
            "operation_result": TOOL_NAME,
            "dispatch_stage": "classification",
            "error": "p0_dispatch_requires_p0_classification",
            "retryable": False,
            "same_call_retryable": False,
            "flow_disposition": "continue",
            "human_action_required": False,
            "next_action": "use_semantic_spec_chain",
            "allowed_next_tool": "aota_task_spec_create",
            "allowed_next_tool_schema": minimal_next_tool_schema(
                {"parameters": {"type": "object", "properties": {"spec_kind": {"type": "string"}, "objective": {"type": "string"}}}},
                include=("spec_kind", "objective"),
            ),
        }, sort_keys=True)
    try:
        create_args = _semantic_create_args(args)
    except ValueError as exc:
        return json.dumps({
            "status": "rejected", "operation_result": TOOL_NAME,
            "dispatch_stage": "semantic_validation", "error": str(exc),
            "retryable": True, "same_call_retryable": False,
            "retry_scope": "changed_arguments_only", "flow_disposition": "continue",
            "human_action_required": False, "next_action": "repair_semantic_arguments",
        }, sort_keys=True)

    created = _decode(_create_spec(create_args, **kwargs))
    if created.get("status") != "created" or created.get("error"):
        return json.dumps(_public_result(created, stage="spec_create", default_next_action="repair_semantic_arguments"), sort_keys=True)
    frozen = _decode(_freeze_spec({"spec_ref": "current_draft_spec"}, **kwargs))
    if frozen.get("status") != "frozen" or frozen.get("error"):
        return json.dumps(_public_result(frozen, stage="spec_freeze", default_next_action="stop_and_report_freeze_failure"), sort_keys=True)
    started = _decode(_start_profile_task({"task_ref": "active_frozen_spec"}, **kwargs))
    return json.dumps(_public_result(started, stage="worker_start", default_next_action="stop_and_report_profile_task_start_failure"), sort_keys=True)


__all__ = ["TOOL_NAME", "TOOLSET_NAME", "SCHEMA", "handle"]
