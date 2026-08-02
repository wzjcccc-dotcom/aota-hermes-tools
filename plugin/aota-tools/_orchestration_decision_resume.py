"""aota_orchestration_decision_resume — resume an awaiting_user decision with user input (P9).

Transitions a needs_user_input decision from awaiting_user to ready_for_followup
by recording user input summary and the intended followup task kind.
Does NOT create any task, update SPEC, approve, start worker, modify source
task, modify handoff, or generate a new decision.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from ._orchestration_common import (
    _DECISION_DECISIONS,
    _FOLLOWUP_TASK_KINDS,
    _USER_INPUT_SUMMARY_MAX_LENGTH,
    acquire_decision_lock,
    atomic_write_json,
    find_decision_path,
    read_decision,
    release_decision_lock,
    utc_now_iso,
    validate_decision_id,
    validate_workspace_id,
)
from ._completion_subject_resolver import (
    CompletionSubjectError,
    _result_error,
    resolve_current_completion_subject,
    resolved_context,
)

TOOL_NAME = "aota_orchestration_decision_resume"
TOOLSET_NAME = "aota_orchestration"

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Resume a needs_user_input orchestration decision from awaiting_user "
        "to ready_for_followup by recording user input summary and the "
        "intended followup task kind. "
        "Does NOT create any task, update SPEC, approve, start worker, "
        "modify source task, modify handoff, or generate a new decision."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "decision_ref": {
                "type": "string",
                "description": "Semantic selector; omit for the current awaiting-user decision.",
            },
            "user_input_summary": {
                "type": "string",
                "description": (
                    f"Summary of user input (max {_USER_INPUT_SUMMARY_MAX_LENGTH} "
                    f"characters). Do NOT include secrets or tokens."
                ),
                "maxLength": _USER_INPUT_SUMMARY_MAX_LENGTH,
            },
            "followup_task_kind": {
                "type": "string",
                "enum": list(_FOLLOWUP_TASK_KINDS),
                "description": (
                    "Task kind for the intended follow-up after this resume. "
                    "Must match the followup_task_kind used when creating "
                    "the follow-up task later."
                ),
            },
        },
        "required": ["user_input_summary", "followup_task_kind"],
        "additionalProperties": False,
    },
}


def handle(args: dict, **_kwargs) -> str:
    """Handle aota_orchestration_decision_resume tool invocation."""
    try:
        if not {"workspace_id", "decision_id"} & set(args):
            subject = resolve_current_completion_subject(
                args,
                _kwargs,
                ref=args.get("decision_ref", "current_awaiting_user_decision"),
                require_decision=True,
            )
            normalized = dict(args)
            normalized.update({
                "workspace_id": subject["workspace_id"],
                "decision_id": subject["decision"]["decision_id"],
            })
            result = _do_resume(normalized)
            result.update({
                "operation_result": "decision_resumed",
                "resolved_context": resolved_context(subject, selector=args.get("decision_ref", "current_awaiting_user_decision")),
                "retryable": False,
                "human_action_required": False,
                "next_action": "continue_orchestration",
            })
            return json.dumps(result, sort_keys=True)
        result = _do_resume(args)
        return json.dumps(result, sort_keys=True)
    except CompletionSubjectError as exc:
        return json.dumps(_result_error(exc, operation="decision_resume"), sort_keys=True)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, sort_keys=True)


def _do_resume(args: dict) -> dict[str, Any]:
    workspace_id: str = args.get("workspace_id", "")
    decision_id: str = args.get("decision_id", "")
    user_input_summary: str = args.get("user_input_summary", "")
    followup_task_kind: str = args.get("followup_task_kind", "")

    # ------------------------------------------------------------------
    # 1. Validate inputs
    # ------------------------------------------------------------------
    err = validate_workspace_id(workspace_id)
    if err:
        return {"status": "error", "error": f"invalid_workspace_id: {err}"}

    err = validate_decision_id(decision_id)
    if err:
        return {"status": "error", "error": f"invalid_decision_id: {err}"}

    if not user_input_summary:
        return {"status": "error", "error": "user_input_summary is required"}

    if len(user_input_summary) > _USER_INPUT_SUMMARY_MAX_LENGTH:
        return {
            "status": "error",
            "error": (
                f"user_input_summary exceeds {_USER_INPUT_SUMMARY_MAX_LENGTH} "
                f"characters (got {len(user_input_summary)})"
            ),
        }

    if followup_task_kind not in _FOLLOWUP_TASK_KINDS:
        return {
            "status": "error",
            "error": (
                f"invalid followup_task_kind: {followup_task_kind!r}. "
                f"Must be one of: {', '.join(_FOLLOWUP_TASK_KINDS)}"
            ),
        }

    # ------------------------------------------------------------------
    # 2. Find decision artifact
    # ------------------------------------------------------------------
    decision_path = find_decision_path(workspace_id, decision_id)
    if decision_path is None:
        return {
            "status": "error",
            "error": f"decision not found: {decision_id}",
        }

    # ------------------------------------------------------------------
    # 3. Acquire lock
    # ------------------------------------------------------------------
    lock_fd: Optional[int] = None
    try:
        lock_fd = acquire_decision_lock(workspace_id, decision_id, timeout=5.0)
    except TimeoutError:
        return {
            "status": "error",
            "error": f"could not acquire lock for decision: {decision_id}",
        }
    except RuntimeError as e:
        return {"status": "error", "error": f"lock error: {str(e)}"}

    try:
        # ------------------------------------------------------------------
        # 4. Read decision under lock
        # ------------------------------------------------------------------
        try:
            decision = read_decision(decision_path)
        except (OSError, json.JSONDecodeError) as e:
            return {
                "status": "error",
                "error": f"failed to read decision: {str(e)}",
            }

        # ------------------------------------------------------------------
        # 5. Verify decision type and check for existing resume (idempotency)
        # ------------------------------------------------------------------
        decision_value = decision.get("decision", "")
        decision_state = decision.get("state", "")

        if decision_value != "needs_user_input":
            return {
                "status": "error",
                "error": (
                    f"decision '{decision_id}' has decision={decision_value!r}, "
                    f"expected 'needs_user_input'. Only needs_user_input decisions "
                    f"can be resumed."
                ),
            }

        # Check existing resume FIRST (before state check, for idempotency)
        existing_resume = decision.get("resume")
        if existing_resume is not None:
            # Already resumed — check terminal states first
            if decision_state == "followup_created":
                return {
                    "status": "error",
                    "error": (
                        f"decision '{decision_id}' is in state followup_created, "
                        f"follow-up already created. Cannot resume."
                    ),
                }
            if decision_state == "closed":
                return {
                    "status": "error",
                    "error": (
                        f"decision '{decision_id}' is closed. Cannot resume."
                    ),
                }
            # Check idempotency
            if (
                existing_resume.get("user_input_summary") == user_input_summary
                and existing_resume.get("followup_task_kind") == followup_task_kind
            ):
                # Idempotent: same summary + same task_kind
                return {
                    "status": "ok",
                    "decision_id": decision_id,
                    "decision": decision_value,
                    "state": "ready_for_followup",
                    "workspace_id": workspace_id,
                    "idempotent": True,
                }
            else:
                return {
                    "status": "error",
                    "error": (
                        f"conflicting resume: decision '{decision_id}' already "
                        f"has a resume. Different user_input_summary or "
                        f"followup_task_kind provided."
                    ),
                }

        # No existing resume — now check state
        if decision_state != "awaiting_user":
            return {
                "status": "error",
                "error": (
                    f"decision '{decision_id}' has state={decision_state!r}, "
                    f"expected 'awaiting_user'. Only awaiting_user decisions "
                    f"can be resumed."
                ),
            }

        if decision_state == "followup_created":
            return {
                "status": "error",
                "error": (
                    f"decision '{decision_id}' is in state followup_created, "
                    f"follow-up already created. Cannot resume."
                ),
            }

        if decision_state == "closed":
            return {
                "status": "error",
                "error": (
                    f"decision '{decision_id}' is closed. Cannot resume."
                ),
            }

        # ------------------------------------------------------------------
        # 6. Check if follow-up already created (via followup.task_id)
        # ------------------------------------------------------------------
        followup = decision.get("followup", {})
        existing_task_id = followup.get("task_id")
        if existing_task_id is not None:
            return {
                "status": "error",
                "error": (
                    f"decision '{decision_id}' already has a follow-up task "
                    f"created (task_id={existing_task_id}). Cannot resume."
                ),
            }

        # ------------------------------------------------------------------
        # 7. Build resume schema V1 extension
        # ------------------------------------------------------------------
        now = utc_now_iso()
        resume_data: dict[str, Any] = {
            "resumed": True,
            "resumed_at": now,
            "user_input_summary": user_input_summary,
            "followup_task_kind": followup_task_kind,
            "source": "explicit_tool_invocation",
        }

        # Update decision with resume data and state
        decision["resume"] = resume_data
        decision["state"] = "ready_for_followup"

        # ------------------------------------------------------------------
        # 8. Write decision artifact atomically
        # ------------------------------------------------------------------
        try:
            atomic_write_json(decision_path, decision)
        except OSError as e:
            return {
                "status": "error",
                "error": f"failed to write decision artifact: {str(e)}",
            }

    finally:
        if lock_fd is not None:
            release_decision_lock(lock_fd)

    # ------------------------------------------------------------------
    # 10. Return result
    # ------------------------------------------------------------------
    return {
        "status": "ok",
        "decision_id": decision_id,
        "decision": decision_value,
        "state": "ready_for_followup",
        "workspace_id": workspace_id,
        "idempotent": False,
    }
