"""aota_handoff_ack — acknowledge consumption of a durable handoff (P8-D).

Moves handoff from pending/ to acknowledged/, creates ack artifact alongside.
Atomic, idempotent for same decision, rejects conflicting second ack.
Does NOT auto-dispatch, does NOT delete handoff history.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from ._handoff_common import (
    _HANDOFF_ID_RE,
    acquire_handoff_lock,
    find_handoff_path,
    get_handoff_ack_dir,
    get_ack_filename,
    get_handoff_filename,
    get_handoff_pending_dir,
    move_handoff_to_ack,
    read_ack_artifact,
    read_handoff,
    release_handoff_lock,
    utc_now_iso,
    validate_handoff_id,
    validate_workspace_id,
    write_ack_artifact,
)
from ._orchestration_common import get_decision_dir, read_decision
from ._completion_subject_resolver import (
    CompletionSubjectError,
    _result_error,
    resolve_current_completion_subject,
    resolved_context,
)
from ._session_active_spec_binding import trusted_session_context
from ._session_state_authority import (
    POINTER_KIND_ACTIVE_TASK,
    POINTER_KIND_CURRENT_COMPLETION,
    POINTER_KIND_CURRENT_DECISION,
    POINTER_KIND_CURRENT_HANDOFF,
    SessionStateError,
    consume_pointer,
)
from ._next_tool_contract import attach_next_tool_option

TOOL_NAME = "aota_handoff_ack"
TOOLSET_NAME = "aota_handoff"

# Valid ack decisions
_ACK_DECISIONS = frozenset(
    {
        "accepted",
        "needs_followup",
        "needs_user_input",
        "review_required",
        "reopen_required",
        "no_action",
    }
)

# Note length limit
_NOTE_MAX_LENGTH = 4000

# Timestamp helper reused from handoff_common
def utc_now_iso() -> str:
    """Return current UTC time in ISO 8601 format (Z suffix)."""
    import datetime

    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _matching_decision(
    workspace_id: str, handoff_id: str, decision: str
) -> dict[str, Any] | None:
    """Return the exact durable task-main decision consumed by this ack."""
    directory = get_decision_dir(workspace_id)
    if not directory.is_dir():
        return None
    try:
        for path in directory.glob("DECISION.*.json"):
            try:
                recorded = read_decision(path)
            except (OSError, json.JSONDecodeError):
                continue
            if recorded.get("handoff_id") == handoff_id and recorded.get("decision") == decision:
                return recorded
    except OSError:
        return None
    return None


def _closure_response(
    *,
    workspace_id: str,
    handoff_id: str,
    decision: str,
    handoff_data: dict[str, Any],
    decision_data: dict[str, Any] | None,
    ack_data: dict[str, Any],
    idempotent: bool,
) -> dict[str, Any]:
    """Build one bounded control-plane closure projection."""
    receipt_ref = (
        f"handoffs/{workspace_id}/acknowledged/{get_ack_filename(handoff_id)}"
    )
    return {
        "status": "ok",
        "handoff_id": handoff_id,
        "workspace_id": workspace_id,
        "decision": decision,
        "ack_id": f"ack:{handoff_id}",
        "task_id": ack_data.get("task_id") or handoff_data.get("task_id"),
        "start_id": ack_data.get("start_id") or handoff_data.get("start_id"),
        "decision_id": ack_data.get("decision_id") or (decision_data or {}).get("decision_id"),
        "terminal_status": ack_data.get("terminal_status") or handoff_data.get("terminal_status"),
        "terminal_outcome": ack_data.get("terminal_outcome") or handoff_data.get("outcome"),
        "acknowledged_at": ack_data.get("acknowledged_at"),
        "acknowledged": True,
        "idempotent": idempotent,
        "closure_state": ack_data.get("closure_state", "closed"),
        "closure_receipt": receipt_ref,
        "closure_receipt_ref": receipt_ref,
        "closed_pointers": ack_data.get("closed_pointers", {}),
        "closed_at": ack_data.get("closed_at") or ack_data.get("acknowledged_at"),
        "next_action": "closure_complete",
    }


SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Acknowledge the current decided durable handoff. Canonical invocation is {}: "
        "the control plane resolves handoff and decision identity from trusted "
        "completion/session/artifact bindings. "
        "Moves the handoff from pending/ to acknowledged/ and creates an ack "
        "artifact alongside it. Atomic and idempotent for the same decision; "
        "rejects conflicting second ack. "
        "Acknowledgment does NOT auto-dispatch any task, approve any result, "
        "or imply correctness — it only records that the orchestration layer "
        "consumed the completion signal."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ack_ref": {
                "type": "string",
                "description": "Semantic selector; omit for the current decided handoff.",
            },
            "note": {
                "type": "string",
                "description": (
                    "Optional bounded context note for the acknowledgment "
                    f"(max {_NOTE_MAX_LENGTH} characters)"
                ),
                "maxLength": _NOTE_MAX_LENGTH,
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}


def handle(args: dict, **_kwargs) -> str:
    """Handle aota_handoff_ack tool invocation."""
    try:
        trusted_context = trusted_session_context(_kwargs)
        if not {"workspace_id", "handoff_id", "decision"} & set(args):
            subject = resolve_current_completion_subject(
                args,
                _kwargs,
                ref=args.get("ack_ref", "current_decided_handoff"),
                require_decision=True,
            )
            decision = subject["decision"].get("decision", "")
            result = _do_ack(
                {
                    "workspace_id": subject["workspace_id"],
                    "handoff_id": subject["handoff_id"],
                    "decision": decision,
                    "note": args.get("note"),
                }
                , trusted_context=trusted_context
            )
            result.update(
                {
                    "operation_result": "handoff_acknowledged"
                    if result.get("acknowledged")
                    else "handoff_ack_failed",
                    "resolved_context": resolved_context(
                        subject, selector=args.get("ack_ref", "current_decided_handoff")
                    ),
                    "next_action": "closure_complete"
                    if result.get("acknowledged") and result.get("closure_state") == "closed"
                    else "read_terminal_closure"
                    if result.get("acknowledged")
                    else "stop_and_report_ack_failure",
                    "retryable": False,
                    "human_action_required": False,
                }
            )
            if result.get("acknowledged") and result.get("closure_state") == "closed":
                result["flow_disposition"] = "complete"
            elif result.get("acknowledged"):
                from ._profile_task_status import SCHEMA as profile_task_status_schema

                attach_next_tool_option(
                    result,
                    profile_task_status_schema,
                    arguments={"view": "closure"},
                    include=("view",),
                    reason="The handoff is acknowledged; read one aggregated terminal closure projection.",
                )
                result["flow_disposition"] = "continue"
            else:
                result["flow_disposition"] = "stop"
            return json.dumps(result, sort_keys=True)
        result = _do_ack(args, trusted_context=trusted_context)
        return json.dumps(result, sort_keys=True)
    except CompletionSubjectError as exc:
        return json.dumps(_result_error(exc, operation="handoff_ack"), sort_keys=True)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, sort_keys=True)


def _do_ack(args: dict, *, trusted_context=None) -> dict[str, Any]:
    """Core ack logic: validate, lock, move, write ack."""
    workspace_id: str = args.get("workspace_id", "")
    handoff_id: str = args.get("handoff_id", "")
    decision: str = args.get("decision", "")
    note: Optional[str] = args.get("note")

    # --- Validate inputs ---
    err = validate_workspace_id(workspace_id)
    if err:
        return {"status": "error", "error": f"invalid_workspace_id: {err}"}

    err = validate_handoff_id(handoff_id)
    if err:
        return {"status": "error", "error": f"invalid_handoff_id: {err}"}

    if decision not in _ACK_DECISIONS:
        return {
            "status": "error",
            "error": f"invalid decision: {decision!r}. Must be one of: {', '.join(sorted(_ACK_DECISIONS))}",
        }

    if note is not None and len(note) > _NOTE_MAX_LENGTH:
        return {
            "status": "error",
            "error": f"note exceeds {_NOTE_MAX_LENGTH} characters (got {len(note)})",
        }

    # --- Find handoff (must be in pending) ---
    pending_dir = get_handoff_pending_dir(workspace_id)
    pending_path = pending_dir / get_handoff_filename(handoff_id)
    handoff_path = find_handoff_path(workspace_id, handoff_id)

    if handoff_path is None:
        return {
            "status": "error",
            "error": f"handoff not found: {handoff_id}",
        }

    decision_data = _matching_decision(workspace_id, handoff_id, decision)
    if decision_data is None:
        return {
            "status": "error",
            "error": "decision_required_before_ack",
        }

    # Check if already acknowledged
    ack_dir = get_handoff_ack_dir(workspace_id)
    ack_path = ack_dir / get_handoff_filename(handoff_id)
    if ack_path.exists() and not pending_path.exists():
        # Handoff already acknowledged — check for existing ack artifact
        existing_ack = read_ack_artifact(workspace_id, handoff_id)
        if existing_ack is not None:
            existing_decision = existing_ack.get("decision", "")
            if existing_decision == decision:
                # A fully closed receipt is idempotent.  A receipt that was
                # durably acknowledged but not yet projected must continue
                # through pointer closure rather than falsely returning done.
                if existing_ack.get("closure_state", "closed") == "closed":
                    return _closure_response(
                        workspace_id=workspace_id,
                        handoff_id=handoff_id,
                        decision=decision,
                        handoff_data=read_handoff(handoff_path),
                        decision_data=decision_data,
                        ack_data=existing_ack,
                        idempotent=True,
                    )
            else:
                # Conflicting decision → reject
                return {
                    "status": "error",
                    "error": (
                        f"conflicting acknowledgment: handoff {handoff_id} "
                        f"already acknowledged with decision "
                        f"'{existing_decision}', cannot re-ack with "
                        f"'{decision}'"
                    ),
                }

    # --- Acquire per-handoff lock ---
    lock_fd: Optional[int] = None
    try:
        lock_fd = acquire_handoff_lock(workspace_id, handoff_id, timeout=5.0)
    except TimeoutError:
        return {
            "status": "error",
            "error": f"could not acquire lock for handoff: {handoff_id}",
        }
    except RuntimeError as e:
        return {"status": "error", "error": f"lock error: {str(e)}"}

    try:
        # --- Double-check under lock ---
        # Re-read handoff data.  Ack evidence is written before the handoff is
        # moved and before any session-state pointer is consumed.
        if pending_path.exists():
            try:
                handoff_data = read_handoff(pending_path)
            except (OSError, json.JSONDecodeError) as e:
                return {
                    "status": "error",
                    "error": f"failed to read handoff: {str(e)}",
                }

            if handoff_data.get("workspace_id") != workspace_id:
                return {
                    "status": "error",
                    "error": "workspace_id mismatch in handoff data",
                }
        else:
            try:
                handoff_data = read_handoff(ack_dir / get_handoff_filename(handoff_id))
            except (OSError, json.JSONDecodeError) as e:
                return {"status": "error", "error": f"failed to read handoff: {str(e)}"}

        # Check ack artifact again under lock
        existing_ack = read_ack_artifact(workspace_id, handoff_id)
        if existing_ack is not None:
            existing_decision = existing_ack.get("decision", "")
            if existing_decision == decision:
                if existing_ack.get("closure_state", "closed") == "closed":
                    # Idempotent — the complete closure was written by
                    # another caller.
                    return _closure_response(
                        workspace_id=workspace_id,
                        handoff_id=handoff_id,
                        decision=decision,
                        handoff_data=handoff_data,
                        decision_data=decision_data,
                        ack_data=existing_ack,
                        idempotent=True,
                    )
            else:
                return {
                    "status": "error",
                    "error": (
                        f"conflicting acknowledgment: handoff {handoff_id} "
                        f"already acknowledged with decision "
                        f"'{existing_decision}', cannot re-ack with "
                        f"'{decision}'"
                    ),
                }

        # Write ack artifact
        acknowledged_at = (existing_ack or {}).get("acknowledged_at") or utc_now_iso()
        write_ack_artifact(
            workspace_id=workspace_id,
            handoff_id=handoff_id,
            decision=decision,
            note=note if note is not None else (existing_ack or {}).get("note"),
            acknowledged_at=acknowledged_at,
            task_id=handoff_data.get("task_id", ""),
            start_id=handoff_data.get("start_id", ""),
            decision_id=(decision_data or {}).get("decision_id", ""),
            terminal_status=handoff_data.get("terminal_status", ""),
            terminal_outcome=handoff_data.get("outcome", ""),
            project_id=handoff_data.get("project_id", ""),
            origin_session_id=handoff_data.get("origin_session_id", ""),
            receipt_ref=handoff_data.get("receipt_ref", ""),
            closure_state="acknowledged",
            closure_history=(existing_ack or {}).get("closure_history", []),
        )

        # Only after the durable ack evidence exists may the visible handoff
        # move to acknowledged/.  A failed move leaves the evidence durable
        # and the current pointers untouched for deterministic retry.
        if pending_path.exists() and move_handoff_to_ack(workspace_id, handoff_id) is None:
            return {
                "status": "error",
                "error": f"handoff {handoff_id} not found in pending directory",
                "acknowledged": False,
                "closure_state": "acknowledged",
                "retryable": True,
            }

    finally:
        if lock_fd is not None:
            release_handoff_lock(lock_fd)

    # Update handoff data in acknowledged location to reflect state
    ack_handoff_path = ack_dir / get_handoff_filename(handoff_id)
    if ack_handoff_path.exists():
        try:
            handoff_data = read_handoff(ack_handoff_path)
            handoff_data["state"] = "acknowledged"
            # Re-write with updated state
            content = json.dumps(handoff_data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
            import tempfile as _tf
            import os as _os
            fd2, tmp2 = _tf.mkstemp(dir=str(ack_dir), prefix=f".{get_handoff_filename(handoff_id)}.tmp_")
            try:
                with _os.fdopen(fd2, "w", encoding="utf-8") as f:
                    f.write(content)
                    f.flush()
                    _os.fsync(f.fileno())
                _os.replace(tmp2, str(ack_handoff_path))
            except Exception:
                try:
                    _os.unlink(tmp2)
                except OSError:
                    pass
        except (OSError, json.JSONDecodeError):
            pass

    session_state_closure: dict[str, Any] = {"status": "not_attempted", "pointers": {}}
    if trusted_context is not None and trusted_context.usable_for_active_spec:
        project_id = handoff_data.get("project_id")
        closure = {}
        for kind in (
            POINTER_KIND_CURRENT_HANDOFF,
            POINTER_KIND_CURRENT_DECISION,
            POINTER_KIND_CURRENT_COMPLETION,
            POINTER_KIND_ACTIVE_TASK,
        ):
            try:
                closure[kind] = "closed" if consume_pointer(
                    kind, workspace_id, project_id, trusted_context.session_id,
                    consumed_at=acknowledged_at,
                ).get("state") == "consumed" else "unchanged"
            except SessionStateError as exc:
                if exc.code == "session_state_pointer_missing":
                    closure[kind] = "missing"
                else:
                    closure[kind] = {"status": "failed", "error": exc.code, "detail": exc.detail}
        session_state_closure = {"status": "closed", "pointers": closure}

    # Persist the final pointer projection as an append-only closure history on
    # the same durable ack record.  The first write above is the ordering gate;
    # this second atomic write makes post-ack status self-describing.
    final_ack = read_ack_artifact(workspace_id, handoff_id) or {
        "handoff_id": handoff_id,
        "workspace_id": workspace_id,
        "decision": decision,
        "acknowledged_at": acknowledged_at,
    }
    final_ack["closure_state"] = "closed"
    final_ack["closed_at"] = acknowledged_at
    final_ack["closed_pointers"] = session_state_closure.get("pointers", {})
    history = final_ack.get("closure_history")
    if not isinstance(history, list):
        history = []
    history.append({"state": "closed", "at": acknowledged_at})
    final_ack["closure_history"] = history
    final_ack["closure_receipt_ref"] = (
        f"handoffs/{workspace_id}/acknowledged/{get_ack_filename(handoff_id)}"
    )
    # Reuse the existing atomic writer contract and preserve all trusted fields.
    write_ack_artifact(
        workspace_id=workspace_id,
        handoff_id=handoff_id,
        decision=decision,
        note=final_ack.get("note"),
        acknowledged_at=final_ack.get("acknowledged_at", acknowledged_at),
        task_id=final_ack.get("task_id", handoff_data.get("task_id", "")),
        start_id=final_ack.get("start_id", handoff_data.get("start_id", "")),
        decision_id=final_ack.get("decision_id", (decision_data or {}).get("decision_id", "")),
        terminal_status=final_ack.get("terminal_status", handoff_data.get("terminal_status", "")),
        terminal_outcome=final_ack.get("terminal_outcome", handoff_data.get("outcome", "")),
        project_id=final_ack.get("project_id", handoff_data.get("project_id", "")),
        origin_session_id=final_ack.get("origin_session_id", handoff_data.get("origin_session_id", "")),
        receipt_ref=final_ack.get("receipt_ref", handoff_data.get("receipt_ref", "")),
        closure_receipt_ref=final_ack.get("closure_receipt_ref", ""),
        closure_state="closed",
        closed_at=acknowledged_at,
        closed_pointers=session_state_closure.get("pointers", {}),
        closure_history=final_ack.get("closure_history", []),
    )
    response = _closure_response(
        workspace_id=workspace_id,
        handoff_id=handoff_id,
        decision=decision,
        handoff_data=handoff_data,
        decision_data=decision_data,
        ack_data={**final_ack, "closure_state": "closed", "closed_at": acknowledged_at},
        idempotent=False,
    )
    response["session_state_closure"] = session_state_closure
    return response
