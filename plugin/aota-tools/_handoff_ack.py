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


SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Acknowledge consumption of a durable handoff. "
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
            "workspace_id": {
                "type": "string",
                "description": "Registered workspace identifier (e.g. 'aota-runtime')",
            },
            "handoff_id": {
                "type": "string",
                "description": "Exact handoff ID to acknowledge",
            },
            "decision": {
                "type": "string",
                "description": (
                    "Orchestration decision for this handoff. "
                    "accepted=task completed normally and result is satisfactory; "
                    "needs_followup=minor issues need follow-up; "
                    "needs_user_input=user intervention required before proceeding; "
                    "review_required=formal review needed; "
                    "reopen_required=task should be re-opened; "
                    "no_action=handoff consumed but no further action needed"
                ),
                "enum": [
                    "accepted",
                    "needs_followup",
                    "needs_user_input",
                    "review_required",
                    "reopen_required",
                    "no_action",
                ],
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
        "required": ["workspace_id", "handoff_id", "decision"],
        "additionalProperties": False,
    },
}


def handle(args: dict, **_kwargs) -> str:
    """Handle aota_handoff_ack tool invocation."""
    try:
        result = _do_ack(args)
        return json.dumps(result, sort_keys=True)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, sort_keys=True)


def _do_ack(args: dict) -> dict[str, Any]:
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

    # Check if already acknowledged
    ack_dir = get_handoff_ack_dir(workspace_id)
    ack_path = ack_dir / get_handoff_filename(handoff_id)
    if ack_path.exists() and not pending_path.exists():
        # Handoff already acknowledged — check for existing ack artifact
        existing_ack = read_ack_artifact(workspace_id, handoff_id)
        if existing_ack is not None:
            existing_decision = existing_ack.get("decision", "")
            if existing_decision == decision:
                # Idempotent: same decision repeated → OK
                return {
                    "status": "ok",
                    "handoff_id": handoff_id,
                    "workspace_id": workspace_id,
                    "decision": decision,
                    "previous_decision": existing_decision,
                    "acknowledged": True,
                    "idempotent": True,
                }
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
        # Re-read handoff data
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

            # Move handoff from pending/ to acknowledged/
            moved_path = move_handoff_to_ack(workspace_id, handoff_id)
            if moved_path is None:
                return {
                    "status": "error",
                    "error": f"handoff {handoff_id} not found in pending directory",
                }

        # Check ack artifact again under lock
        existing_ack = read_ack_artifact(workspace_id, handoff_id)
        if existing_ack is not None:
            existing_decision = existing_ack.get("decision", "")
            if existing_decision == decision:
                # Idempotent — handoff was moved by another caller
                return {
                    "status": "ok",
                    "handoff_id": handoff_id,
                    "workspace_id": workspace_id,
                    "decision": decision,
                    "previous_decision": existing_decision,
                    "acknowledged": True,
                    "idempotent": True,
                }
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
        acknowledged_at = utc_now_iso()
        write_ack_artifact(
            workspace_id=workspace_id,
            handoff_id=handoff_id,
            decision=decision,
            note=note,
            acknowledged_at=acknowledged_at,
        )

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

    return {
        "status": "ok",
        "handoff_id": handoff_id,
        "workspace_id": workspace_id,
        "decision": decision,
        "acknowledged_at": acknowledged_at,
        "acknowledged": True,
        "idempotent": False,
    }
