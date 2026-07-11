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
    validate_workspace_id,
)

TOOL_NAME = "aota_handoff_list"
TOOLSET_NAME = "aota_handoff"

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "List pending durable handoffs for a workspace, sorted oldest-first. "
        "Returns compact metadata only — no full card content. "
        "Use aota_handoff_open to read full card content for a specific handoff."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {
                "type": "string",
                "description": "Registered workspace identifier (e.g. 'aota-runtime')",
            },
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
        },
        "required": ["workspace_id"],
        "additionalProperties": False,
    },
}


def handle(args: dict, **_kwargs) -> str:
    """Handle aota_handoff_list tool invocation."""
    try:
        result = _do_list(args)
        return json.dumps(result, sort_keys=True)
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


def _do_list(args: dict) -> dict[str, Any]:
    """Core list logic: validate, scan, return."""
    workspace_id: str = args.get("workspace_id", "")
    limit: int = args.get("limit", 10)
    terminal_status: str | None = args.get("terminal_status")
    profile: str | None = args.get("profile")

    # Validate workspace_id
    err = validate_workspace_id(workspace_id)
    if err:
        return {"status": "error", "error": f"invalid_workspace_id: {err}"}

    # Enforce limit bounds
    effective_limit = max(1, min(limit, 50))

    # Scan pending handoffs
    handoffs = list_pending_handoffs(
        workspace_id,
        limit=effective_limit,
        terminal_status=terminal_status,
        profile=profile,
    )

    # P9: enrich with decision metadata
    _enrich_with_decision_metadata(workspace_id, handoffs)

    return {
        "status": "ok",
        "workspace_id": workspace_id,
        "count": len(handoffs),
        "handoffs": handoffs,
    }
