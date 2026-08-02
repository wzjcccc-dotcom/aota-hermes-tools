"""Phase 4 model-facing projections for the remaining eleven tools.

This module is deliberately an adapter, not a second implementation.  The
existing handlers remain authoritative for filesystem, Git, task cancellation,
orchestration, operator, and intake behavior.  The adapter only removes
control-plane inputs from the canonical schema, resolves the trusted current
subject, injects the legacy handler envelope, and adds a bounded response
contract.  Calls containing the old explicit fields are delegated unchanged.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Callable, Mapping

from ._completion_subject_resolver import (
    CompletionSubjectError,
    resolve_current_completion_subject,
    resolved_context,
)
from ._reference_resolver import ReferenceError, resolve as resolve_reference
from ._trusted_runtime_context import get_trusted_runtime_context
from ._workspace import WorkspaceError

from ._file_copy import SCHEMA as _file_copy_schema
from ._followup_task_create import SCHEMA as _followup_schema
from ._operator_consistency_check import SCHEMA as _operator_schema
from ._orchestration_lineage import SCHEMA as _lineage_schema
from ._path_info import SCHEMA as _path_info_schema
from ._profile_task_cancel import SCHEMA as _cancel_schema
from ._read_file import SCHEMA as _read_file_schema
from ._repo_diff import SCHEMA as _repo_diff_schema
from ._repo_status import SCHEMA as _repo_status_schema
from ._search_files import SCHEMA as _search_schema
from ._work_classifier import SCHEMA as _work_classify_schema


class Phase4ResolutionError(ValueError):
    """Bounded, deterministic model-facing resolution failure."""

    def __init__(self, code: str, *, detail: str = "", choices: list[dict[str, Any]] | None = None) -> None:
        super().__init__(code if not detail else f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.choices = choices or []


def _schema(
    source: Mapping[str, Any],
    *,
    remove: set[str],
    required: list[str],
    add: dict[str, Any] | None = None,
    description: str,
) -> dict[str, Any]:
    projected = copy.deepcopy(dict(source))
    params = projected.setdefault("parameters", {})
    props = dict(params.get("properties", {}))
    for field in remove:
        props.pop(field, None)
    if add:
        props.update(copy.deepcopy(add))
    params["properties"] = props
    params["required"] = list(required)
    params["additionalProperties"] = False
    projected["description"] = description
    return projected


_TARGET = {
    "type": "string",
    "description": "Semantic current subject; omit to use the trusted current subject.",
}

PHASE4_SCHEMAS: dict[str, dict[str, Any]] = {
    "aota_path_info": _schema(
        _path_info_schema, remove={"workspace_id"}, required=["path"],
        description="Inspect one human-readable workspace-relative path in the trusted current workspace. The control plane supplies workspace scope and validates containment.",
    ),
    "aota_read_file": _schema(
        _read_file_schema, remove={"workspace_id"}, required=["path"],
        description="Read one bounded human-readable workspace-relative text file in the trusted current workspace. Use this as the first domain call when the request asks for file contents or a known line range. For a named literal, enum, or schema probe inside a known file, use exactly one exact-file aota_search_files call instead and read only one narrow matching range if its preview is insufficient. The control plane supplies workspace scope and validates containment.",
    ),
    "aota_search_files": _schema(
        _search_schema, remove={"workspace_id"}, required=["query"],
        description="Run exactly one bounded literal search in the trusted current workspace for a named literal, enum, or schema probe. Scope path to the exact known file when supplied; use a bounded directory only when the file location is unknown. Do not repeat the same probe or use search merely to verify an exact file before reading its contents. The control plane supplies the authorized root and bounds.",
    ),
    "aota_repo_status_readonly": _schema(
        _repo_status_schema, remove={"workspace_id"}, required=[], add={"target": _TARGET},
        description="Return bounded Git status for the trusted current repository. Omit target for the current repository; no Git identity or path is required.",
    ),
    "aota_repo_diff_readonly": _schema(
        _repo_diff_schema, remove={"workspace_id"}, required=[], add={"target": _TARGET},
        description="Return bounded Git diff evidence for the trusted current repository. Provide only view/scope preferences; revisions and paths are control-plane facts.",
    ),
    "aota_file_copy": _schema(
        _file_copy_schema, remove={"workspace_id"}, required=["source", "destination"],
        description="Copy a human-readable relative source to a human-readable relative destination in the trusted workspace. The control plane validates containment and injects workspace scope.",
    ),
    "aota_profile_task_cancel": _schema(
        _cancel_schema, remove={"workspace_id", "task_id"}, required=[], add={"target": {**_TARGET, "description": "Semantic cancellation target; currently only current_task is supported."}},
        description="Request cancellation of the trusted current running Profile Task. Do not provide task IDs; the control plane resolves the exact task and preserves the existing cancellation/checkpoint contract.",
    ),
    "aota_followup_task_create": _schema(
        _followup_schema, remove={"workspace_id", "decision_id"}, required=["title", "goal", "risk_level", "read_scope", "acceptance_criteria", "stop_conditions", "evidence_required"], add={"decision_ref": _TARGET},
        description="Create a bounded follow-up from the trusted current orchestration decision. Provide task intent and human decisions only; lineage IDs and workspace scope are resolved by the control plane.",
    ),
    "aota_orchestration_lineage": _schema(
        _lineage_schema, remove={"workspace_id", "task_id", "decision_id"}, required=[], add={"subject_ref": {**_TARGET, "description": "Semantic lineage subject: current_task or current_decision."}},
        description="Traverse bounded lineage from the trusted current task or decision. Do not provide task or decision IDs; the control plane resolves the subject.",
    ),
    "aota_operator_consistency_check": _schema(
        _operator_schema, remove={"workspace_id", "task_id", "handoff_id", "decision_id"}, required=[], add={"subject_ref": {**_TARGET, "description": "Semantic audit subject: current_task, current_handoff, or current_decision."}},
        description="Run the read-only consistency audit for the trusted current task, handoff, or decision. Internal identifiers are resolved by the control plane.",
    ),
    "aota_work_classify": _schema(
        _work_classify_schema, remove={"workspace_id"}, required=["title", "summary", "facts"],
        description="Classify bounded work-intake facts. Provide work semantics and human decisions; the control plane supplies and validates workspace scope.",
    ),
}

_LEGACY_FIELDS = {
    "aota_path_info": {"workspace_id"},
    "aota_read_file": {"workspace_id"},
    "aota_search_files": {"workspace_id"},
    "aota_repo_status_readonly": {"workspace_id"},
    "aota_repo_diff_readonly": {"workspace_id"},
    "aota_file_copy": {"workspace_id"},
    "aota_profile_task_cancel": {"workspace_id", "task_id"},
    "aota_followup_task_create": {"workspace_id", "decision_id"},
    "aota_orchestration_lineage": {"workspace_id", "task_id", "decision_id"},
    "aota_operator_consistency_check": {"workspace_id", "task_id", "handoff_id", "decision_id"},
    "aota_work_classify": {"workspace_id"},
}

_READ_TOOLS = {
    "aota_path_info", "aota_read_file", "aota_search_files",
    "aota_repo_status_readonly", "aota_repo_diff_readonly",
}


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True)


def _workspace(kwargs: Mapping[str, Any]) -> str:
    context = get_trusted_runtime_context(kwargs)
    if not context.workspace_id:
        raise Phase4ResolutionError("trusted_context_missing")
    return context.workspace_id


def _subject_error(exc: Exception, operation: str) -> dict[str, Any]:
    code = getattr(exc, "code", "authority_conflict")
    choices = list(getattr(exc, "choices", []) or [])
    if code in {"reference_ambiguous", "completion_subject_ambiguous"} or choices:
        public_code, next_action, human = "current_subject_ambiguous", "select_current_subject", True
    elif code in {"reference_missing", "completion_subject_missing", "decision_subject_missing"}:
        public_code, next_action, human = "current_subject_missing", "select_current_subject", False
    elif code in {"trusted_session_context_missing", "trusted_context_missing"}:
        public_code, next_action, human = "trusted_context_missing", "stop_and_report_runtime_context_missing", False
    elif code in {"path_escape", "symlink_escape", "path_not_authorized"}:
        public_code, next_action, human = "path_not_authorized", "stop_and_report_path_authority_failure", False
    elif code in {"unsupported_semantic_reference", "reference_invalid", "completion_subject_reference_invalid"}:
        public_code, next_action, human = "unsupported_semantic_reference", "select_supported_semantic_reference", False
    else:
        public_code, next_action, human = "authority_conflict", "stop_and_report_control_plane_error", False
    result: dict[str, Any] = {
        "status": "rejected",
        "operation_result": operation,
        "error": public_code,
        "resolved_context": {},
        "retryable": False,
        "human_action_required": human,
        "next_action": next_action,
    }
    if operation in {item.removeprefix("aota_") for item in _READ_TOOLS}:
        result.update({"resolved_scope": {}, "result_count": 0, "results": [], "truncated": False})
    if choices:
        result["choices"] = choices[:10]
    return result


def _operation_result(name: str) -> str:
    return name.removeprefix("aota_")


def _normalise(name: str, raw: str, *, scope: str | None = None) -> str:
    try:
        result = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return _json({
            "status": "failed", "operation_result": _operation_result(name),
            "error": "authority_conflict", "detail": str(raw)[:200],
            "retryable": False, "human_action_required": False,
            "next_action": "stop_and_report_control_plane_error",
        })
    if not isinstance(result, dict):
        return _json({"status": "failed", "operation_result": _operation_result(name), "error": "authority_conflict", "retryable": False, "human_action_required": False, "next_action": "stop_and_report_control_plane_error"})
    failed = result.get("status") in {"error", "failed", "rejected", "unknown_workspace", "task_not_found", "task_not_cancellable", "invalid_task_id"} or "error" in result or str(result.get("classification_status", "")) == "error"
    result.setdefault("status", "rejected" if failed else "completed")
    result.setdefault("operation_result", _operation_result(name))
    result.setdefault("resolved_context", {"binding": scope or "trusted_current_subject"})
    result.setdefault("retryable", False)
    result.setdefault("human_action_required", False)
    if result.get("status") == "task_not_found":
        result["error"] = "current_subject_missing"
        result["next_action"] = "select_current_subject"
        result["human_action_required"] = False
    if "error" in result and result.get("error_code") is None:
        error = str(result.get("error", ""))
        if any(token in error.lower() for token in ("absolute path", "escapes workspace", "symlink", "path is", "path:")):
            result["error"] = "path_not_authorized"
            result["next_action"] = "stop_and_report_path_authority_failure"
        else:
            result["error"] = result.get("error") or "authority_conflict"
    if "next_action" not in result:
        if result.get("classification_status") == "needs_input":
            result["next_action"] = "request_missing_facts"
        elif result.get("recommended_next_step"):
            result["next_action"] = result["recommended_next_step"]
        else:
            result["next_action"] = "none" if not failed else "stop_and_report_control_plane_error"
    if name in _READ_TOOLS:
        result.setdefault("resolved_scope", {"binding": "trusted_current_workspace"})
        if name == "aota_search_files":
            result.setdefault("results", [])
            result.setdefault("result_count", len(result["results"]))
            result.setdefault("truncated", False)
        else:
            result.setdefault("results", [] if failed else [result.get("path", result.get("mode", "current_repository"))])
            result.setdefault("result_count", len(result["results"]))
            result.setdefault("truncated", False)
    return _json(result)


def _trusted_task_id(kwargs: Mapping[str, Any]) -> str:
    context = get_trusted_runtime_context(kwargs)
    task_id = str(context.execution_context.get("task_id", "") or "")
    if task_id:
        from ._task_spec_common import get_task_dir, load_meta
        task_dir = get_task_dir(context.workspace_id, task_id)
        if not task_dir.is_dir():
            raise Phase4ResolutionError("reference_missing", detail="trusted_current_task_not_found")
        try:
            meta = load_meta(task_dir)
        except Exception as exc:
            raise Phase4ResolutionError("authority_conflict", detail="trusted_current_task_unreadable") from exc
        if meta.get("task_id") not in {None, "", task_id}:
            raise Phase4ResolutionError("authority_conflict", detail="trusted_current_task_binding_mismatch")
        return task_id
    workspace = _workspace(kwargs)
    try:
        binding = resolve_reference(workspace, "active_profile_task")
    except ReferenceError as exc:
        raise Phase4ResolutionError(exc.code, detail=str(exc)) from exc
    task_ref = binding.get("task_ref", {})
    task_id = task_ref.get("task_id") if isinstance(task_ref, Mapping) else None
    if not isinstance(task_id, str) or not task_id:
        raise Phase4ResolutionError("reference_missing", detail="current_task_missing")
    return task_id


def _completion_id(kwargs: Mapping[str, Any], ref: str, field: str) -> tuple[str, dict[str, Any]]:
    try:
        subject = resolve_current_completion_subject({}, kwargs, ref=ref, require_decision=field == "decision_id")
    except CompletionSubjectError as exc:
        raise exc
    value = subject.get(field)
    if field == "decision_id" and isinstance(subject.get("decision"), Mapping):
        value = subject["decision"].get("decision_id")
    if not isinstance(value, str) or not value:
        raise Phase4ResolutionError("reference_missing", detail=f"{field}_missing")
    return value, subject


def _canonical_args(name: str, args: Mapping[str, Any], kwargs: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    workspace = _workspace(kwargs)
    normalized = dict(args)
    normalized["workspace_id"] = workspace
    context = {"binding": "trusted_current_workspace"}
    if name in {"aota_repo_status_readonly", "aota_repo_diff_readonly"}:
        target = args.get("target", "current_repository")
        if target not in {None, "", "current_repository"}:
            raise Phase4ResolutionError("unsupported_semantic_reference", detail="repository_target")
    if name == "aota_profile_task_cancel":
        target = args.get("target", "current_task")
        if target not in {None, "", "current_task"}:
            raise Phase4ResolutionError("unsupported_semantic_reference", detail="cancel_target")
        normalized["task_id"] = _trusted_task_id(kwargs)
        context = {"binding": "current_task"}
    elif name == "aota_followup_task_create":
        ref = str(args.get("decision_ref", "current_decided_decision"))
        decision_id, subject = _completion_id(kwargs, ref, "decision_id")
        normalized["decision_id"] = decision_id
        context = resolved_context(subject, selector=ref)
    elif name in {"aota_orchestration_lineage", "aota_operator_consistency_check"}:
        ref = str(args.get("subject_ref", "current_task"))
        if ref == "current_task":
            normalized["task_id"] = _trusted_task_id(kwargs)
            context = {"binding": "current_task"}
        elif ref == "current_decision":
            decision_id, subject = _completion_id(kwargs, "current_decided_decision", "decision_id")
            normalized["decision_id"] = decision_id
            context = resolved_context(subject, selector=ref)
        elif ref == "current_handoff" and name == "aota_operator_consistency_check":
            handoff_id, subject = _completion_id(kwargs, "current_decided_handoff", "handoff_id")
            normalized["handoff_id"] = handoff_id
            context = resolved_context(subject, selector=ref)
        else:
            raise Phase4ResolutionError("unsupported_semantic_reference", detail=ref)
        normalized.pop("subject_ref", None)
    normalized.pop("target", None)
    normalized.pop("decision_ref", None)
    return normalized, context


def phase4_handler(name: str, legacy_handler: Callable[..., str]) -> Callable[..., str]:
    """Build one canonical projection while retaining explicit legacy calls."""
    legacy_fields = _LEGACY_FIELDS[name]

    def handle(args: dict[str, Any], **kwargs: Any) -> str:
        if legacy_fields & set(args):
            return legacy_handler(args, **kwargs)
        operation = _operation_result(name)
        try:
            normalized, context = _canonical_args(name, args, kwargs)
            return _normalise(name, legacy_handler(normalized, **kwargs), scope=context.get("binding", "trusted_current_subject"))
        except (Phase4ResolutionError, CompletionSubjectError, ReferenceError, WorkspaceError) as exc:
            return _json(_subject_error(exc, operation))
        except Exception as exc:
            return _json({
                "status": "failed", "operation_result": operation,
                "error": "authority_conflict", "detail": str(exc)[:200],
                "retryable": False, "human_action_required": False,
                "next_action": "stop_and_report_control_plane_error",
            })

    return handle


__all__ = ["PHASE4_SCHEMAS", "phase4_handler", "Phase4ResolutionError"]
