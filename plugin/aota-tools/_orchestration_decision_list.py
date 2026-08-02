"""aota_orchestration_decision_list — list orchestration decisions for a workspace (P9).

Returns compact list of orchestration decisions sorted oldest-first.
Supports optional state filter. Defaults to listing active decisions only
(awaiting_user, ready_for_followup, recorded). Does NOT list closed by default.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from ._orchestration_common import (
    get_decision_dir,
    read_decision,
    validate_workspace_id,
)
from ._session_active_spec_binding import trusted_session_context

TOOL_NAME = "aota_orchestration_decision_list"
TOOLSET_NAME = "aota_orchestration"

_ACTIVE_STATES = frozenset({"awaiting_user", "ready_for_followup", "recorded"})
_FILTER_STATES = frozenset(
    {"awaiting_user", "ready_for_followup", "followup_created", "closed", "recorded"}
)

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "List orchestration decisions in the trusted current operator scope. "
        "Returns compact metadata only — no full artifact, reason, or task SPEC. "
        "By default only lists active decisions (awaiting_user, ready_for_followup, "
        "recorded). Does NOT list closed decisions by default. "
        "Use state filter to narrow results."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "state": {
                "type": "string",
                "enum": list(sorted(_FILTER_STATES)),
                "description": (
                    "Optional filter by decision state. "
                    "One of: awaiting_user, ready_for_followup, followup_created, "
                    "closed, recorded"
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of decisions to return (default 10, max 50)",
                "default": 10,
                "minimum": 1,
                "maximum": 50,
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}


def handle(args: dict, **_kwargs) -> str:
    """Handle aota_orchestration_decision_list tool invocation."""
    try:
        if "workspace_id" not in args:
            context = trusted_session_context(_kwargs)
            if not context.workspace_id:
                return json.dumps({
                    "status": "rejected",
                    "operation_result": "decision_list",
                    "error": "trusted_session_context_missing",
                    "retryable": False,
                    "human_action_required": False,
                    "next_action": "stop_and_report_runtime_context_missing",
                }, sort_keys=True)
            args = dict(args)
            args["workspace_id"] = context.workspace_id
        result = _do_list(args)
        result.setdefault("operation_result", "decision_list")
        result.setdefault("retryable", False)
        result.setdefault("human_action_required", False)
        result.setdefault("next_action", "continue_operator_review")
        return json.dumps(result, sort_keys=True)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, sort_keys=True)


def _do_list(args: dict) -> dict[str, Any]:
    workspace_id: str = args.get("workspace_id", "")
    state_filter: Optional[str] = args.get("state")
    limit: int = args.get("limit", 10)

    # ------------------------------------------------------------------
    # 1. Validate workspace_id
    # ------------------------------------------------------------------
    err = validate_workspace_id(workspace_id)
    if err:
        return {"status": "error", "error": f"invalid_workspace_id: {err}"}

    # Enforce limit bounds
    effective_limit = max(1, min(limit, 50))

    # Validate state filter if provided
    if state_filter is not None and state_filter not in _FILTER_STATES:
        return {
            "status": "error",
            "error": (
                f"invalid state filter: {state_filter!r}. "
                f"Must be one of: {', '.join(sorted(_FILTER_STATES))}"
            ),
        }

    # ------------------------------------------------------------------
    # 2. Scan decisions directory
    # ------------------------------------------------------------------
    decisions_dir = get_decision_dir(workspace_id)
    if not decisions_dir.is_dir():
        return {
            "status": "ok",
            "workspace_id": workspace_id,
            "count": 0,
            "decisions": [],
        }

    collected: list[dict[str, Any]] = []

    try:
        for entry in os.listdir(str(decisions_dir)):
            if not entry.startswith("DECISION.") or not entry.endswith(".json"):
                continue

            try:
                dec = read_decision(decisions_dir / entry)
            except (OSError, json.JSONDecodeError):
                continue

            decision_state = dec.get("state", "")
            decision_value = dec.get("decision", "")

            # Apply default filter: only active states unless state filter given
            if state_filter is not None:
                if decision_state != state_filter:
                    continue
            else:
                # Default: only list active decisions
                if decision_state not in _ACTIVE_STATES:
                    continue

            # Build compact output
            followup = dec.get("followup", {})
            resume = dec.get("resume")

            compact: dict[str, Any] = {
                "decision_id": dec.get("decision_id", ""),
                "handoff_id": dec.get("handoff_id", ""),
                "source_task_id": dec.get("source_task_id", ""),
                "source_profile": dec.get("source_profile", ""),
                "decision": decision_value,
                "state": decision_state,
                "followup_required": followup.get("required", False),
                "followup_task_kind": followup.get("task_kind"),
                "followup_task_id": followup.get("task_id"),
                "human_checkpoint_state": dec.get("human_checkpoint", {}).get("state"),
                "created_at": dec.get("created_at", ""),
            }

            if resume and resume.get("resumed_at"):
                compact["resumed_at"] = resume["resumed_at"]

            collected.append(compact)

    except OSError:
        pass

    # ------------------------------------------------------------------
    # 3. Sort oldest-first by created_at
    # ------------------------------------------------------------------
    collected.sort(key=lambda d: d.get("created_at", ""))

    # ------------------------------------------------------------------
    # 4. Apply limit
    # ------------------------------------------------------------------
    decisions = collected[:effective_limit]

    return {
        "status": "ok",
        "workspace_id": workspace_id,
        "count": len(decisions),
        "decisions": decisions,
    }
