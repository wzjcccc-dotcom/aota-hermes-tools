"""aota_orchestration_decision_record — record an orchestration decision for a handoff (P8-E).

Creates a durable orchestration decision artifact that captures what action
should be taken based on a handoff's completion signal. Does NOT auto-create
any task, auto-start any worker, modify handoff, modify ack, or accept
model-specified source_task_id/source_handoff_id/predecessor_task_id.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from ._handoff_common import (
    find_handoff_path,
    read_handoff,
    validate_handoff_id,
)
from ._orchestration_common import (
    _DECISION_DECISIONS,
    _FOLLOWUP_TASK_KINDS,
    _REASON_MAX_LENGTH,
    acquire_decision_lock,
    atomic_write_json,
    find_decision_path,
    generate_decision_id,
    get_decision_dir,
    get_decision_filename,
    read_decision,
    release_decision_lock,
    utc_now_iso,
    validate_decision_id,
    validate_workspace_id,
)

TOOL_NAME = "aota_orchestration_decision_record"
TOOLSET_NAME = "aota_orchestration"

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Record an orchestration decision for a handoff. "
        "Creates a durable decision artifact that captures what action "
        "should be taken based on a handoff's completion signal. "
        "Does NOT auto-create any task, auto-start any worker, modify handoff, "
        "modify ack, or accept model-specified source_task_id/"
        "source_handoff_id/predecessor_task_id."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {
                "type": "string",
                "description": "Registered workspace identifier (e.g. 'aota-runtime')",
            },
            "handoff_id": {
                "type": "string",
                "description": "Exact handoff ID to record a decision for",
            },
            "decision": {
                "type": "string",
                "enum": sorted(_DECISION_DECISIONS),
                "description": (
                    "Orchestration decision for this handoff. "
                    "accepted=task completed normally and result is satisfactory; "
                    "needs_followup=minor issues need follow-up; "
                    "needs_user_input=user intervention required before proceeding; "
                    "review_required=formal review needed; "
                    "reopen_required=task should be re-opened; "
                    "no_action=handoff consumed but no further action needed"
                ),
            },
            "reason": {
                "type": "string",
                "description": (
                    f"Optional context/reason for the decision "
                    f"(max {_REASON_MAX_LENGTH} characters)"
                ),
                "maxLength": _REASON_MAX_LENGTH,
            },
            "followup_task_kind": {
                "type": "string",
                "enum": list(_FOLLOWUP_TASK_KINDS),
                "description": (
                    "Task kind for follow-up. Required or disallowed depending "
                    "on the decision value. "
                    "review_required -> must be 'review'. "
                    "accepted/no_action/needs_user_input -> must NOT be provided. "
                    "needs_followup/reopen_required -> can be provided explicitly."
                ),
            },
        },
        "required": ["workspace_id", "handoff_id", "decision"],
        "additionalProperties": False,
    },
}


def handle(args: dict, **_kwargs) -> str:
    """Handle aota_orchestration_decision_record tool invocation."""
    try:
        result = _do_record(args)
        return json.dumps(result, sort_keys=True)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, sort_keys=True)


def _do_record(args: dict) -> dict[str, Any]:
    workspace_id: str = args.get("workspace_id", "")
    handoff_id: str = args.get("handoff_id", "")
    decision: str = args.get("decision", "")
    reason: Optional[str] = args.get("reason")
    followup_task_kind: Optional[str] = args.get("followup_task_kind")

    # ------------------------------------------------------------------
    # 1. Validate inputs
    # ------------------------------------------------------------------
    err = validate_workspace_id(workspace_id)
    if err:
        return {"status": "error", "error": f"invalid_workspace_id: {err}"}

    err = validate_handoff_id(handoff_id)
    if err:
        return {"status": "error", "error": f"invalid_handoff_id: {err}"}

    if decision not in _DECISION_DECISIONS:
        return {
            "status": "error",
            "error": (
                f"invalid decision: {decision!r}. "
                f"Must be one of: {', '.join(sorted(_DECISION_DECISIONS))}"
            ),
        }

    if reason is not None and len(reason) > _REASON_MAX_LENGTH:
        return {
            "status": "error",
            "error": (
                f"reason exceeds {_REASON_MAX_LENGTH} characters "
                f"(got {len(reason)})"
            ),
        }

    # ------------------------------------------------------------------
    # 2. Find handoff (must exist, can be pending or acknowledged)
    # ------------------------------------------------------------------
    handoff_path = find_handoff_path(workspace_id, handoff_id)
    if handoff_path is None:
        return {
            "status": "error",
            "error": f"handoff not found: {handoff_id}",
        }

    try:
        handoff_data = read_handoff(handoff_path)
    except (OSError, json.JSONDecodeError) as e:
        return {
            "status": "error",
            "error": f"failed to read handoff: {str(e)}",
        }

    # ------------------------------------------------------------------
    # 3. Extract info from handoff
    # ------------------------------------------------------------------
    source_task_id: str = handoff_data.get("task_id", "")
    source_start_id: str = handoff_data.get("start_id", "")
    source_profile: Optional[str] = handoff_data.get("profile")
    terminal_status: str = handoff_data.get("terminal_status", "")

    # ------------------------------------------------------------------
    # 4. Verify handoff workspace_id matches
    # ------------------------------------------------------------------
    if handoff_data.get("workspace_id") != workspace_id:
        return {
            "status": "error",
            "error": "workspace_id mismatch in handoff data",
        }

    # ------------------------------------------------------------------
    # 5. Check for existing decision for this handoff
    # ------------------------------------------------------------------
    decisions_dir = get_decision_dir(workspace_id)
    if decisions_dir.is_dir():
        try:
            for entry in os.listdir(str(decisions_dir)):
                if not entry.startswith("DECISION.") or not entry.endswith(".json"):
                    continue
                try:
                    existing = read_decision(decisions_dir / entry)
                except (OSError, json.JSONDecodeError):
                    continue
                if existing.get("handoff_id") == handoff_id:
                    existing_decision = existing.get("decision", "")
                    if existing_decision == decision:
                        # Idempotent: same decision repeated -> OK
                        return {
                            "status": "ok",
                            "decision_id": existing.get("decision_id", ""),
                            "decision": decision,
                            "state": existing.get("state", ""),
                            "workspace_id": workspace_id,
                            "handoff_id": handoff_id,
                            "followup": existing.get("followup", {}),
                            "human_checkpoint": existing.get("human_checkpoint", {}),
                            "idempotent": True,
                        }
                    else:
                        # Conflicting decision -> reject
                        return {
                            "status": "error",
                            "error": (
                                f"conflicting decision: handoff {handoff_id} "
                                f"already has decision "
                                f"'{existing_decision}', cannot record "
                                f"'{decision}'"
                            ),
                        }
        except OSError:
            pass

    # ------------------------------------------------------------------
    # 6. Validate followup_task_kind against decision constraints
    # ------------------------------------------------------------------
    if followup_task_kind is not None and followup_task_kind not in _FOLLOWUP_TASK_KINDS:
        return {
            "status": "error",
            "error": (
                f"invalid followup_task_kind: {followup_task_kind!r}. "
                f"Must be one of: {', '.join(_FOLLOWUP_TASK_KINDS)}"
            ),
        }

    if decision == "review_required":
        if followup_task_kind is not None and followup_task_kind != "review":
            return {
                "status": "error",
                "error": (
                    f"review_required decision requires followup_task_kind "
                    f"to be 'review', got {followup_task_kind!r}"
                ),
            }
    elif decision in ("accepted", "no_action", "needs_user_input"):
        if followup_task_kind is not None:
            return {
                "status": "error",
                "error": (
                    f"decision '{decision}' does not allow followup_task_kind, "
                    f"but got {followup_task_kind!r}"
                ),
            }
    elif decision == "reopen_required":
        # reopen_required with review is not allowed
        if followup_task_kind == "review":
            return {
                "status": "error",
                "error": (
                    f"reopen_required decision does not allow "
                    f"followup_task_kind 'review'"
                ),
            }

    # ------------------------------------------------------------------
    # 7. Generate decision_id (internal, model cannot specify)
    # ------------------------------------------------------------------
    decision_id = generate_decision_id()

    # ------------------------------------------------------------------
    # 8. Build decision schema V1
    # ------------------------------------------------------------------
    now = utc_now_iso()

    # Determine followup required
    followup_required = decision in ("needs_followup", "review_required", "reopen_required")

    # Determine human checkpoint
    if decision == "needs_user_input":
        human_checkpoint_required = True
        human_checkpoint_state = "awaiting"
    else:
        human_checkpoint_required = False
        human_checkpoint_state = "not_required"

    # Determine state
    if decision in ("accepted", "no_action"):
        decision_state = "closed"
    elif decision == "needs_user_input":
        decision_state = "awaiting_user"
    else:
        decision_state = "recorded"

    # Resolved followup task_kind
    resolved_task_kind: Optional[str] = followup_task_kind
    if decision == "review_required":
        resolved_task_kind = followup_task_kind or "review"

    decision_data: dict[str, Any] = {
        "decision_id": decision_id,
        "workspace_id": workspace_id,
        "handoff_id": handoff_id,
        "source_task_id": source_task_id,
        "source_start_id": source_start_id,
        "source_profile": source_profile,
        "source_terminal_status": terminal_status,
        "decision": decision,
        "reason": reason if reason else None,
        "created_at": now,
        "source": "explicit_tool_invocation",
        "followup": {
            "required": followup_required,
            "task_kind": resolved_task_kind if followup_required else None,
            "task_id": None,
            "created_at": None,
        },
        "human_checkpoint": {
            "required": human_checkpoint_required,
            "state": human_checkpoint_state,
        },
        "state": decision_state,
    }

    # ------------------------------------------------------------------
    # 9. Write decision artifact atomically
    # ------------------------------------------------------------------
    decision_dir = get_decision_dir(workspace_id)
    decision_path = decision_dir / get_decision_filename(decision_id)

    try:
        atomic_write_json(decision_path, decision_data)
    except OSError as e:
        return {
            "status": "error",
            "error": f"failed to write decision artifact: {str(e)}",
        }

    # ------------------------------------------------------------------
    # 10. Return compact result
    # ------------------------------------------------------------------
    return {
        "status": "ok",
        "decision_id": decision_id,
        "decision": decision,
        "state": decision_state,
        "workspace_id": workspace_id,
        "handoff_id": handoff_id,
        "followup": decision_data["followup"],
        "human_checkpoint": decision_data["human_checkpoint"],
        "idempotent": False,
    }
