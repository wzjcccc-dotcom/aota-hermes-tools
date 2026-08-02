"""aota_handoff_list — list pending handoffs for a workspace (P8-D).

Returns compact list of pending handoffs sorted oldest-first.
No embedded full card content, no arbitrary path traversal.
Includes decision metadata for each handoff if an orchestration decision exists.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ._handoff_common import (
    list_pending_handoffs,
    validate_task_id,
    validate_workspace_id,
)
from ._completion_observation import (
    NEXT_ACTION_WAIT_FOR_COMPLETION_DELIVERY,
    completion_observation_context,
)
from ._session_active_spec_binding import trusted_session_context
from ._task_spec_common import get_task_dir, load_meta
from ._completion_subject_resolver import (
    CompletionSubjectError,
    _result_error,
    resolve_current_completion_subject,
)

TOOL_NAME = "aota_handoff_list"
TOOLSET_NAME = "aota_handoff"

SCHEMA = {
    "name": TOOL_NAME,
        "description": (
            "List pending durable handoffs for a workspace, sorted oldest-first. "
            "Returns compact metadata only — no full card content. "
            "Use aota_handoff_open to read full card content for a specific handoff. "
            "For an active wakeup-capable task, task-main must wait for completion "
            "delivery; task_id filtering is guarded and cannot be used as a polling "
            "bypass."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum number of handoffs to return (default 10, max 50)",
                "default": 10,
                "minimum": 1,
                "maximum": 50,
            },
            "terminal_status": {
                "type": "string",
                "description": "Optional filter by terminal_status (done|failed|needs_input|cancelled)",
                "enum": ["done", "failed", "needs_input", "cancelled"],
            },
            "profile": {
                "type": "string",
                "description": "Optional filter by profile (coder|debugger|reviewer)",
                "enum": ["coder", "debugger", "reviewer"],
            },
            "handoff_ref": {
                "type": "string",
                "description": "Semantic selector for general operator browsing; current completion opens directly.",
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}


def handle(args: dict, **_kwargs) -> str:
    """Handle aota_handoff_list tool invocation."""
    try:
        if "workspace_id" not in args:
            from ._session_active_spec_binding import trusted_session_context

            context = trusted_session_context(_kwargs)
            workspace_id = context.workspace_id
            if not workspace_id:
                return json.dumps(
                    {
                        "status": "rejected",
                        "operation_result": "handoff_list",
                        "error": "trusted_session_context_missing",
                        "retryable": False,
                        "human_action_required": False,
                        "next_action": "stop_and_report_runtime_context_missing",
                    },
                    sort_keys=True,
                )
            try:
                subject = resolve_current_completion_subject(args, _kwargs)
            except CompletionSubjectError as exc:
                if exc.code != "completion_subject_missing":
                    return json.dumps(_result_error(exc, operation="handoff_list"), sort_keys=True)
            else:
                return json.dumps(
                    {
                        "status": "rejected",
                        "operation_result": "handoff_list",
                        "error": "current_completion_subject_available",
                        "resolved_context": {
                            "selector": "current_completion",
                            "title": subject.get("title", "completion"),
                            "task_kind": subject.get("task_kind", ""),
                            "profile": subject.get("profile", ""),
                        },
                        "next_action": "open_current_handoff",
                        "retryable": False,
                        "human_action_required": False,
                    },
                    sort_keys=True,
                )
            args = dict(args)
            args["workspace_id"] = workspace_id
        context = trusted_session_context(_kwargs)
        result = _do_list(
            args,
            observer_session_id=context.session_id,
            observer_principal=context.principal,
        )
        return json.dumps(result, sort_keys=True)
    except CompletionSubjectError as exc:
        return json.dumps(_result_error(exc, operation="handoff_list"), sort_keys=True)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, sort_keys=True)


def _enrich_with_decision_metadata(
    workspace_id: str, handoffs: list[dict[str, Any]]
) -> None:
    """Add P9 decision metadata (awaiting_user, followup_task_id) to handoff entries.

    Scans decisions dir and matches by handoff_id. Mutates handoffs in-place.
    """
    from ._orchestration_common import get_decision_dir, read_decision

    dec_dir = get_decision_dir(workspace_id)
    if not dec_dir.is_dir():
        return

    # Build a dict: handoff_id -> decision_data
    decision_by_handoff: dict[str, dict[str, Any]] = {}
    try:
        for entry in os.listdir(str(dec_dir)):
            if not entry.startswith("DECISION.") or not entry.endswith(".json"):
                continue
            try:
                dec_data = read_decision(dec_dir / entry)
            except (OSError, json.JSONDecodeError):
                continue
            hid = dec_data.get("handoff_id", "")
            if hid:
                decision_by_handoff[hid] = dec_data
    except OSError:
        return

    for h in handoffs:
        hid = h.get("handoff_id", "")
        if hid in decision_by_handoff:
            dec = decision_by_handoff[hid]
            h["decision_state"] = dec.get("state")
            h["awaiting_user"] = dec.get("state") == "awaiting_user"
            h["followup_task_id"] = dec.get("followup", {}).get("task_id")


def _do_list(
    args: dict,
    *,
    observer_session_id: str = "",
    observer_principal: str = "",
) -> dict[str, Any]:
    """Core list logic: validate, scan, return."""
    workspace_id: str = args.get("workspace_id", "")
    limit: int = args.get("limit", 10)
    terminal_status: str | None = args.get("terminal_status")
    profile: str | None = args.get("profile")
    task_id: str | None = args.get("task_id")
    observation_purpose: str | None = args.get("observation_purpose")

    # Validate workspace_id
    err = validate_workspace_id(workspace_id)
    if err:
        return {"status": "error", "error": f"invalid_workspace_id: {err}"}

    if task_id:
        err = validate_task_id(task_id)
        if err:
            return {"status": "error", "error": f"invalid_task_id: {err}"}
        if observation_purpose not in (None, "completion"):
            return {"status": "error", "error": "invalid_observation_purpose"}
        task_dir = get_task_dir(workspace_id, task_id)
        if task_dir.is_dir():
            meta = load_meta(task_dir)
            observation = completion_observation_context(meta, task_id)
            if observation["active"]:
                if not observation["recovery_due"]:
                    return {
                        "status": "rejected",
                        "error": "completion_delivery_pending",
                        "task_id": task_id,
                        "workspace_id": workspace_id,
                        "next_action": NEXT_ACTION_WAIT_FOR_COMPLETION_DELIVERY,
                        "recovery_allowed_after": observation["recovery_allowed_after"],
                    }
                if observation["recovery_consumed"]:
                    return {
                        "status": "rejected",
                        "error": "recovery_already_consumed",
                        "task_id": task_id,
                        "workspace_id": workspace_id,
                        "next_action": NEXT_ACTION_WAIT_FOR_COMPLETION_DELIVERY,
                    }
                # A missing-delivery recovery must use the status retrieval
                # surface, which returns receipt, process, outcome, and handoff
                # evidence in one bounded response.  Listing cannot consume it.
                return {
                    "status": "rejected",
                    "error": "completion_recovery_requires_status",
                    "task_id": task_id,
                    "workspace_id": workspace_id,
                    "next_action": "perform_one_bounded_recovery",
                    "recovery_allowed_after": observation["recovery_allowed_after"],
                }

    # Enforce limit bounds
    effective_limit = max(1, min(limit, 50))

    # Scan pending handoffs
    handoffs = list_pending_handoffs(
        workspace_id,
        limit=effective_limit,
        terminal_status=terminal_status,
        profile=profile,
        task_id=task_id,
    )

    # P9: enrich with decision metadata
    _enrich_with_decision_metadata(workspace_id, handoffs)

    return {
        "status": "ok",
        "workspace_id": workspace_id,
        "count": len(handoffs),
        "handoffs": handoffs,
    }
