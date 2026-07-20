"""aota_handoff_open — open exact handoff and return compact role card content (P8-D).

Reads handoff metadata + compact role card (CARD.json / DIAGNOSIS_CARD.json /
REVIEW_CARD.json / ARCHITECT_CARD.json / STEWARD_CARD.json). Does NOT auto-read full artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from ._handoff_common import (
    PROFILE_TASK_ROOT,
    find_handoff_path,
    get_handoff_filename,
    read_handoff,
    read_role_card_projection,
    validate_task_id,
    validate_handoff_id,
    validate_workspace_id,
)

TOOL_NAME = "aota_handoff_open"
TOOLSET_NAME = "aota_handoff"

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Open an exact durable handoff and return its metadata plus compact "
        "role card content (including STEWARD_CARD.json). "
        "Does NOT auto-open full report artifacts (RESULT.md / DIAGNOSIS.md / "
        "REVIEW.md / ARCHITECT_REVIEW.md / STEWARD_RESULT.md). If the role card is missing, returns card_missing=true and "
        "the handoff stays pending."
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
                "description": "Exact handoff ID to open (e.g. 'ho_20250101T120000_a1b2c3d4')",
            },
        },
        "required": ["workspace_id", "handoff_id"],
        "additionalProperties": False,
    },
}


def handle(args: dict, **_kwargs) -> str:
    """Handle aota_handoff_open tool invocation."""
    try:
        result = _do_open(args)
        return json.dumps(result, sort_keys=True)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, sort_keys=True)


def _do_open(args: dict) -> dict[str, Any]:
    """Core open logic: validate, locate, read, return."""
    workspace_id: str = args.get("workspace_id", "")
    handoff_id: str = args.get("handoff_id", "")

    # Validate inputs
    err = validate_workspace_id(workspace_id)
    if err:
        return {"status": "error", "error": f"invalid_workspace_id: {err}"}

    err = validate_handoff_id(handoff_id)
    if err:
        return {"status": "error", "error": f"invalid_handoff_id: {err}"}

    # Find handoff file
    handoff_path = find_handoff_path(workspace_id, handoff_id)
    if handoff_path is None:
        return {
            "status": "error",
            "error": f"handoff not found: {handoff_id}",
        }

    # Read handoff data
    handoff_data = read_handoff(handoff_path)

    # Verify workspace_id matches
    if handoff_data.get("workspace_id") != workspace_id:
        return {
            "status": "error",
            "error": "workspace_id mismatch in handoff data",
        }

    # Read role card from task directory
    task_id = handoff_data.get("task_id", "")
    role_artifact = handoff_data.get("role_artifact", {})
    if not isinstance(role_artifact, dict):
        role_artifact = {}
    card_name: Optional[str] = role_artifact.get("card_name")

    # Security: derive task_dir using known task_id (not from handoff content for path)
    task_dir = (
        PROFILE_TASK_ROOT / workspace_id / task_id
        if isinstance(task_id, str) and validate_task_id(task_id) is None
        else PROFILE_TASK_ROOT / workspace_id
    )

    card_content: Optional[str] = None
    card_missing: bool = True
    card_read_reason = "unrecognized_card"

    if isinstance(task_id, str) and validate_task_id(task_id) is None:
        card_content, card_missing, card_read_reason = read_role_card_projection(
            task_dir, handoff_data.get("profile"), card_name
        )

    # Build result (handoff metadata + card content)
    result: dict[str, Any] = {
        "status": "ok",
        "handoff_id": handoff_data.get("handoff_id"),
        "workspace_id": handoff_data.get("workspace_id"),
        "task_id": handoff_data.get("task_id"),
        "start_id": handoff_data.get("start_id"),
        "profile": handoff_data.get("profile"),
        "task_kind": handoff_data.get("task_kind"),
        "terminal_status": handoff_data.get("terminal_status"),
        "created_at": handoff_data.get("created_at"),
        "state": handoff_data.get("state"),
        "role_artifact": role_artifact,
        "lifecycle": handoff_data.get("lifecycle"),
        "subject_task_id": handoff_data.get("subject_task_id"),
        "needs_input_reason": handoff_data.get("needs_input_reason"),
        "card_missing": card_missing,
        "card_content": card_content,
        "card_read_reason": card_read_reason,
    }

    # P9: add decision metadata if a decision exists for this handoff
    try:
        from ._orchestration_common import get_decision_dir, read_decision
        import os as _os

        dec_dir = get_decision_dir(workspace_id)
        if dec_dir.is_dir():
            for _entry in _os.listdir(str(dec_dir)):
                if not _entry.startswith("DECISION.") or not _entry.endswith(".json"):
                    continue
                try:
                    _dec = read_decision(dec_dir / _entry)
                except (OSError, json.JSONDecodeError):
                    continue
                if _dec.get("handoff_id") == handoff_id:
                    result["decision_state"] = _dec.get("state")
                    result["awaiting_user"] = _dec.get("state") == "awaiting_user"
                    result["followup_task_id"] = _dec.get("followup", {}).get("task_id")
                    break
    except Exception:
        pass

    return result
