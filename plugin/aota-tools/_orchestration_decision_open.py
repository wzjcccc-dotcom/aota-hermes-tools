"""aota_orchestration_decision_open — open an exact orchestration decision with full metadata (P9).

Returns the full decision artifact plus source binding, handoff state, and
task compact status. Does NOT auto-open role full artifact, follow-up SPEC,
or repo diff. Does NOT create follow-up, ack handoff, or start worker.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from ._handoff_common import (
    find_handoff_path,
    read_handoff,
    PROFILE_TASK_ROOT,
)
from ._orchestration_common import (
    find_decision_path,
    get_decision_dir,
    read_decision,
    validate_decision_id,
    validate_workspace_id,
)
from ._task_spec_common import get_task_dir, load_meta

TOOL_NAME = "aota_orchestration_decision_open"
TOOLSET_NAME = "aota_orchestration"

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Open an exact orchestration decision and return its full metadata "
        "including source binding, decision, reason, state, resume metadata, "
        "followup metadata, human checkpoint, source handoff state, and source "
        "task compact status. "
        "Does NOT auto-open role full artifact, follow-up SPEC, or repo diff. "
        "Does NOT create follow-up, ack handoff, or start worker."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {
                "type": "string",
                "description": "Registered workspace identifier (e.g. 'aota-runtime')",
            },
            "decision_id": {
                "type": "string",
                "description": "Exact orchestration decision ID to open",
            },
        },
        "required": ["workspace_id", "decision_id"],
        "additionalProperties": False,
    },
}


def handle(args: dict, **_kwargs) -> str:
    """Handle aota_orchestration_decision_open tool invocation."""
    try:
        result = _do_open(args)
        return json.dumps(result, sort_keys=True)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, sort_keys=True)


def _do_open(args: dict) -> dict[str, Any]:
    workspace_id: str = args.get("workspace_id", "")
    decision_id: str = args.get("decision_id", "")

    # ------------------------------------------------------------------
    # 1. Validate inputs
    # ------------------------------------------------------------------
    err = validate_workspace_id(workspace_id)
    if err:
        return {"status": "error", "error": f"invalid_workspace_id: {err}"}

    err = validate_decision_id(decision_id)
    if err:
        return {"status": "error", "error": f"invalid_decision_id: {err}"}

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
    # 3. Read decision
    # ------------------------------------------------------------------
    try:
        decision = read_decision(decision_path)
    except (OSError, json.JSONDecodeError) as e:
        return {
            "status": "error",
            "error": f"failed to read decision artifact: {str(e)}",
        }

    # ------------------------------------------------------------------
    # 4. Source binding
    # ------------------------------------------------------------------
    source_binding: dict[str, Any] = {
        "source_task_id": decision.get("source_task_id", ""),
        "source_start_id": decision.get("source_start_id", ""),
        "source_profile": decision.get("source_profile", ""),
        "source_terminal_status": decision.get("source_terminal_status", ""),
    }

    # ------------------------------------------------------------------
    # 5. Decision, reason, state
    # ------------------------------------------------------------------
    decision_value: str = decision.get("decision", "")
    reason: Optional[str] = decision.get("reason")
    state: str = decision.get("state", "")

    # ------------------------------------------------------------------
    # 6. Resume metadata (P9)
    # ------------------------------------------------------------------
    resume: Optional[dict[str, Any]] = decision.get("resume")

    # ------------------------------------------------------------------
    # 7. Followup metadata
    # ------------------------------------------------------------------
    followup: dict[str, Any] = decision.get("followup", {})

    # ------------------------------------------------------------------
    # 8. Human checkpoint
    # ------------------------------------------------------------------
    human_checkpoint: dict[str, Any] = decision.get("human_checkpoint", {})

    # ------------------------------------------------------------------
    # 9. Source handoff state (read handoff artifact if exists)
    # ------------------------------------------------------------------
    handoff_id: str = decision.get("handoff_id", "")
    source_handoff: Optional[dict[str, Any]] = None
    if handoff_id:
        handoff_path = find_handoff_path(workspace_id, handoff_id)
        if handoff_path is not None:
            try:
                handoff_data = read_handoff(handoff_path)
                source_handoff = {
                    "handoff_id": handoff_data.get("handoff_id"),
                    "state": handoff_data.get("state"),
                    "terminal_status": handoff_data.get("terminal_status"),
                    "profile": handoff_data.get("profile"),
                    "task_kind": handoff_data.get("task_kind"),
                    "created_at": handoff_data.get("created_at"),
                    "subject_task_id": handoff_data.get("subject_task_id"),
                    "task_id": handoff_data.get("task_id"),
                }
            except (OSError, json.JSONDecodeError):
                source_handoff = {"missing": True, "error": "failed to read handoff artifact"}
        else:
            source_handoff = {"missing": True, "error": "handoff artifact not found"}

    # ------------------------------------------------------------------
    # 10. Source task compact status (read task meta.json if exists)
    # ------------------------------------------------------------------
    source_task_id: str = decision.get("source_task_id", "")
    source_task: Optional[dict[str, Any]] = None
    if source_task_id:
        task_dir = get_task_dir(workspace_id, source_task_id)
        if task_dir.is_dir():
            try:
                meta = load_meta(task_dir)
                source_task = {
                    "task_id": meta.get("task_id", source_task_id),
                    "status": meta.get("status", ""),
                    "task_kind": meta.get("task_kind", ""),
                    "profile_hint": meta.get("profile_hint", ""),
                }
            except (OSError, json.JSONDecodeError):
                source_task = {"missing": True, "error": "failed to read task meta.json"}
        else:
            source_task = {"missing": True, "error": "task directory not found"}

    # ------------------------------------------------------------------
    # 11. Build result
    # ------------------------------------------------------------------
    result: dict[str, Any] = {
        "status": "ok",
        "decision_id": decision_id,
        "workspace_id": workspace_id,
        "source_binding": source_binding,
        "decision": decision_value,
        "reason": reason,
        "state": state,
        "resume": resume,
        "followup": followup,
        "human_checkpoint": human_checkpoint,
        "handoff_id": handoff_id,
        "source_handoff": source_handoff,
        "source_task": source_task,
    }

    return result
