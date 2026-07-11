"""aota_operator_inbox_list — list operator inbox items for a workspace (P10).

Read-only. Scans handoffs, decisions, tasks to build a unified inbox.
No file mutations.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from ._operator_common import (
    validate_workspace_id,
    scan_tasks_by_status,
    get_handoff_for_task,
    get_decision_for_handoff,
    get_decision_for_source_task,
    get_approval,
    is_approval_valid,
    build_handoff_item_id,
    build_decision_item_id,
    build_task_item_id,
    build_approval_item_id,
    ITEM_TYPE_PRIORITY,
    TERMINAL_STATUSES,
    MAX_SCAN_TASKS,
    MAX_ITEMS,
)
from ._handoff_common import (
    get_handoff_pending_dir,
    read_handoff,
    _HANDOFF_ID_RE,
)
from ._orchestration_common import (
    get_decision_dir,
    read_decision,
)
from ._task_spec_common import (
    STATUS_DRAFT,
    get_task_dir,
    load_meta,
    read_json,
)

TOOL_NAME = "aota_operator_inbox_list"
TOOLSET_NAME = "aota_operator"

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "List operator inbox items for a workspace. "
        "Returns unified compact inbox with items sorted by priority "
        "(lower first) and then by created_at. "
        "Supports filtering by item_type, priority_max, consistency_status. "
        "Read-only: no file mutations."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {
                "type": "string",
                "description": "Registered workspace identifier",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum items to return (default 20, max 100)",
                "default": 20,
                "minimum": 1,
                "maximum": 100,
            },
            "item_type": {
                "type": "string",
                "enum": [
                    "pending_handoff",
                    "awaiting_user",
                    "ready_for_followup",
                    "draft_requires_approval",
                    "approved_ready_to_start",
                    "draft_ready_to_start",
                    "needs_input_task_without_decision",
                    "failed_task_unconsumed",
                    "cancelled_task_unconsumed",
                    "timeout_task_unconsumed",
                    "broken_lineage",
                    "artifact_gap",
                    "running_task",
                ],
                "description": "Optional filter by item type",
            },
            "priority_max": {
                "type": "integer",
                "description": "Only show items with priority <= this value (lower = more urgent)",
                "minimum": 1,
                "maximum": 100,
            },
            "consistency_status": {
                "type": "string",
                "enum": ["ok", "warning", "broken"],
                "description": "Optional filter by consistency check status",
            },
            "include_informational": {
                "type": "boolean",
                "description": "Include informational items (running_task) in results (default: false)",
                "default": False,
            },
        },
        "required": ["workspace_id"],
        "additionalProperties": False,
    },
}

_ACTIVE_TASK_STATUSES = frozenset(
    {"draft", "running", "needs_input", "failed", "cancelled", "timeout"}
)
_DRAFT_OR_TERMINAL = frozenset(
    {"draft", "done", "failed", "needs_input", "cancelled", "timeout", "scope_violation"}
)
_NEEDS_INPUT_STATUSES = frozenset({"needs_input"})
_FAILED_STATUSES = frozenset({"failed"})
_CANCELLED_STATUSES = frozenset({"cancelled"})
_TIMEOUT_STATUSES = frozenset({"timeout"})
_RUNNING_STATUSES = frozenset({"running"})
_INFORMATIONAL_PRIORITY = ITEM_TYPE_PRIORITY.get("running_task", 999)

_ACTIVE_DECISION_STATES = frozenset(
    {"awaiting_user", "ready_for_followup", "recorded", "followup_created"}
)


def handle(args: dict, **_kwargs) -> str:
    """Handle aota_operator_inbox_list tool invocation."""
    try:
        result = _do_list(args)
        return json.dumps(result, sort_keys=True)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, sort_keys=True)


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def _do_list(args: dict) -> dict[str, Any]:
    workspace_id: str = args.get("workspace_id", "")
    limit: int = args.get("limit", 20)
    item_type_filter: Optional[str] = args.get("item_type")
    priority_max: Optional[int] = args.get("priority_max")
    consistency_filter: Optional[str] = args.get("consistency_status")
    include_informational: bool = args.get("include_informational", False)

    # Validate
    err = validate_workspace_id(workspace_id)
    if err:
        return {"status": "error", "error": f"invalid_workspace_id: {err}"}

    effective_limit = max(1, min(limit, MAX_ITEMS))

    # Collect all items
    all_items: list[dict[str, Any]] = []
    scanned_counts: dict[str, int] = {
        "task_dirs_scanned": 0,
        "handoffs_scanned": 0,
        "decisions_scanned": 0,
    }

    # -----------------------------------------------------------------------
    # A. Scan pending handoffs
    # -----------------------------------------------------------------------
    pending_dir = get_handoff_pending_dir(workspace_id)
    if pending_dir.is_dir():
        try:
            for entry in os.listdir(str(pending_dir)):
                if not entry.startswith("handoff.") or not entry.endswith(".json"):
                    continue
                handoff_id = entry[len("handoff."):-len(".json")]
                if not _HANDOFF_ID_RE.match(handoff_id):
                    continue
                scanned_counts["handoffs_scanned"] += 1
                try:
                    hdata = read_handoff(pending_dir / entry)
                except (OSError, json.JSONDecodeError):
                    continue

                created_at = hdata.get("created_at", "")
                task_id = hdata.get("task_id", "")
                profile = hdata.get("profile", "")
                task_kind = hdata.get("task_kind", "")
                item = {
                    "item_id": build_handoff_item_id(handoff_id),
                    "item_type": "pending_handoff",
                    "priority": ITEM_TYPE_PRIORITY.get("pending_handoff", 50),
                    "created_at": created_at,
                    "summary": f"Pending handoff: {profile}/{task_kind} for task {task_id}",
                    "handoff_id": handoff_id,
                    "task_id": task_id,
                    "profile": profile,
                    "task_kind": task_kind,
                    "terminal_status": hdata.get("terminal_status", ""),
                    "consistency_status": "ok",
                    "recommended_action": {
                        "tool": "aota_handoff_open",
                        "hint": f"Open handoff {handoff_id} to review completion",
                    },
                }
                all_items.append(item)
        except OSError:
            pass

    # -----------------------------------------------------------------------
    # B. Scan decisions (active states only)
    # -----------------------------------------------------------------------
    dec_dir = get_decision_dir(workspace_id)
    if dec_dir.is_dir():
        try:
            for entry in os.listdir(str(dec_dir)):
                if not entry.startswith("DECISION.") or not entry.endswith(".json"):
                    continue
                scanned_counts["decisions_scanned"] += 1
                try:
                    ddata = read_decision(dec_dir / entry)
                except (OSError, json.JSONDecodeError):
                    continue

                state = ddata.get("state", "")
                if state not in _ACTIVE_DECISION_STATES:
                    continue

                decision_id = ddata.get("decision_id", "")
                handoff_id = ddata.get("handoff_id", "")
                source_task_id = ddata.get("source_task_id", "")
                created_at = ddata.get("created_at", "")
                decision_value = ddata.get("decision", "")
                followup = ddata.get("followup", {})

                # awaiting_user
                if state == "awaiting_user":
                    item = {
                        "item_id": build_decision_item_id(decision_id),
                        "item_type": "awaiting_user",
                        "priority": ITEM_TYPE_PRIORITY.get("awaiting_user", 30),
                        "created_at": created_at,
                        "summary": f"Awaiting user input: decision {decision_value} on source task {source_task_id}",
                        "decision_id": decision_id,
                        "handoff_id": handoff_id,
                        "source_task_id": source_task_id,
                        "decision": decision_value,
                        "consistency_status": "ok",
                        "recommended_action": {
                            "tool": "aota_orchestration_decision_open",
                            "hint": f"Open decision {decision_id} and provide user input",
                        },
                    }
                    all_items.append(item)

                # ready_for_followup with no followup task yet
                if state == "ready_for_followup" and followup.get("task_id") is None:
                    item = {
                        "item_id": build_decision_item_id(decision_id),
                        "item_type": "ready_for_followup",
                        "priority": ITEM_TYPE_PRIORITY.get("ready_for_followup", 60),
                        "created_at": created_at,
                        "summary": f"Ready for follow-up: {followup.get('task_kind', 'unknown')} on source {source_task_id}",
                        "decision_id": decision_id,
                        "handoff_id": handoff_id,
                        "source_task_id": source_task_id,
                        "followup_task_kind": followup.get("task_kind"),
                        "consistency_status": "ok",
                        "recommended_action": {
                            "tool": "aota_followup_task_create",
                            "hint": f"Create {followup.get('task_kind', '')} follow-up task",
                        },
                    }
                    all_items.append(item)
        except OSError:
            pass

    # -----------------------------------------------------------------------
    # C. Scan tasks (draft, running, needs_input, failed, cancelled)
    # -----------------------------------------------------------------------
    task_records = scan_tasks_by_status(
        workspace_id, _ACTIVE_TASK_STATUSES, max_tasks=MAX_SCAN_TASKS
    )
    scanned_counts["task_dirs_scanned"] = len(task_records)

    for trec in task_records:
        meta = trec["meta"]
        task_id = trec["task_id"]
        task_kind = meta.get("task_kind", "")
        status = meta.get("status", "")
        execution = meta.get("execution", {})
        profile = execution.get("profile", meta.get("profile_hint", ""))
        created_at = meta.get("created_at", "")
        start_id = execution.get("start_id", "")
        needs_input_reason = execution.get("needs_input_reason")

        # C1. Draft tasks
        if status == "draft" and task_kind in ("implementation", "diagnosis", "review"):
            approval = get_approval(workspace_id, task_id)

            if task_kind == "implementation":
                if approval and is_approval_valid(approval, meta):
                    # approved_ready_to_start
                    item = {
                        "item_id": build_task_item_id(task_id, "approved_ready"),
                        "item_type": "approved_ready_to_start",
                        "priority": ITEM_TYPE_PRIORITY.get("approved_ready_to_start", 80),
                        "created_at": created_at,
                        "summary": f"Implementation draft approved, ready to start: {task_id}",
                        "task_id": task_id,
                        "task_kind": task_kind,
                        "profile": profile,
                        "consistency_status": "ok",
                        "recommended_action": {
                            "tool": "aota_profile_task_start",
                            "hint": f"Start implementation task {task_id}",
                        },
                    }
                    all_items.append(item)
                else:
                    # draft_requires_approval
                    item = {
                        "item_id": build_task_item_id(task_id, "needs_approval"),
                        "item_type": "draft_requires_approval",
                        "priority": ITEM_TYPE_PRIORITY.get("draft_requires_approval", 70),
                        "created_at": created_at,
                        "summary": f"Implementation draft needs approval: {task_id}",
                        "task_id": task_id,
                        "task_kind": task_kind,
                        "profile": profile,
                        "approval_present": approval is not None,
                        "consistency_status": "ok",
                        "recommended_action": {
                            "tool": "aota_profile_task_approve",
                            "hint": f"Review and approve implementation task {task_id}",
                        },
                    }
                    all_items.append(item)
            else:
                # diagnosis/review: no approval needed, can start directly
                item = {
                    "item_id": build_task_item_id(task_id, "ready_to_start"),
                    "item_type": "draft_ready_to_start",
                    "priority": ITEM_TYPE_PRIORITY.get("draft_ready_to_start", 90),
                    "created_at": created_at,
                    "summary": f"{task_kind.capitalize()} draft ready to start: {task_id}",
                    "task_id": task_id,
                    "task_kind": task_kind,
                    "profile": profile,
                    "consistency_status": "ok",
                    "recommended_action": {
                        "tool": "aota_profile_task_start",
                        "hint": f"Start {task_kind} task {task_id}",
                    },
                }
                all_items.append(item)

        # C2. needs_input tasks without decision
        if status == "needs_input":
            handoff = get_handoff_for_task(workspace_id, task_id, start_id) if start_id else None
            if handoff:
                decision = get_decision_for_handoff(workspace_id, handoff.get("handoff_id", ""))
                if decision is None:
                    item = {
                        "item_id": build_task_item_id(task_id, "needs_input_no_dec"),
                        "item_type": "needs_input_task_without_decision",
                        "priority": ITEM_TYPE_PRIORITY.get("needs_input_task_without_decision", 40),
                        "created_at": created_at,
                        "summary": f"Task needs input but no decision recorded: {task_id}",
                        "task_id": task_id,
                        "task_kind": task_kind,
                        "profile": profile,
                        "needs_input_reason": needs_input_reason,
                        "handoff_id": handoff.get("handoff_id"),
                        "consistency_status": "ok",
                        "recommended_action": {
                            "tool": "aota_handoff_open",
                            "hint": f"Open handoff for {task_id} and record decision",
                        },
                    }
                    all_items.append(item)

        # C3. Failed tasks unconsumed
        if status == "failed":
            handoff = get_handoff_for_task(workspace_id, task_id, start_id) if start_id else None
            if handoff:
                decision = get_decision_for_handoff(workspace_id, handoff.get("handoff_id", ""))
                if decision is None or handoff.get("state") == "pending":
                    item = {
                        "item_id": build_task_item_id(task_id, "failed_unconsumed"),
                        "item_type": "failed_task_unconsumed",
                        "priority": ITEM_TYPE_PRIORITY.get("failed_task_unconsumed", 40),
                        "created_at": created_at,
                        "summary": f"Failed task unconsumed: {task_id}",
                        "task_id": task_id,
                        "task_kind": task_kind,
                        "profile": profile,
                        "handoff_id": handoff.get("handoff_id"),
                        "handoff_state": handoff.get("state"),
                        "consistency_status": "ok",
                        "recommended_action": {
                            "tool": "aota_handoff_open",
                            "hint": f"Open handoff for failed task {task_id}",
                        },
                    }
                    all_items.append(item)

        # C4. Cancelled tasks unconsumed
        if status == "cancelled":
            handoff = get_handoff_for_task(workspace_id, task_id, start_id) if start_id else None
            if handoff:
                decision = get_decision_for_handoff(workspace_id, handoff.get("handoff_id", ""))
                if decision is None or handoff.get("state") == "pending":
                    item = {
                        "item_id": build_task_item_id(task_id, "cancelled_unconsumed"),
                        "item_type": "cancelled_task_unconsumed",
                        "priority": ITEM_TYPE_PRIORITY.get("cancelled_task_unconsumed", 45),
                        "created_at": created_at,
                        "summary": f"Cancelled task unconsumed: {task_id}",
                        "task_id": task_id,
                        "task_kind": task_kind,
                        "profile": profile,
                        "handoff_id": handoff.get("handoff_id"),
                        "handoff_state": handoff.get("state"),
                        "consistency_status": "ok",
                        "recommended_action": {
                            "tool": "aota_handoff_open",
                            "hint": f"Open handoff for cancelled task {task_id}",
                        },
                    }
                    all_items.append(item)

        # C5. Running task (informational)
        if status == "running" and include_informational:
            item = {
                "item_id": build_task_item_id(task_id, "running"),
                "item_type": "running_task",
                "priority": _INFORMATIONAL_PRIORITY,
                "created_at": created_at,
                "summary": f"Task is running: {task_id} ({profile}/{task_kind})",
                "task_id": task_id,
                "task_kind": task_kind,
                "profile": profile,
                "consistency_status": "warning",
                "recommended_action": {
                    "tool": "aota_profile_task_status",
                    "hint": f"Check status of running task {task_id}",
                },
            }
            all_items.append(item)

        # C6. Timeout tasks unconsumed (priority 35, between awaiting_user=30 and failed=40)
        if status == "timeout":
            handoff = get_handoff_for_task(workspace_id, task_id, start_id) if start_id else None
            if handoff:
                decision = get_decision_for_handoff(workspace_id, handoff.get("handoff_id", ""))
                if decision is None or handoff.get("state") == "pending":
                    timeout_meta = execution.get("timeout_triggered")
                    item = {
                        "item_id": build_task_item_id(task_id, "timeout_unconsumed"),
                        "item_type": "timeout_task_unconsumed",
                        "priority": ITEM_TYPE_PRIORITY.get("timeout_task_unconsumed", 35),
                        "created_at": created_at,
                        "summary": f"Timed out task unconsumed: {task_id}",
                        "task_id": task_id,
                        "task_kind": task_kind,
                        "profile": profile,
                        "handoff_id": handoff.get("handoff_id"),
                        "handoff_state": handoff.get("state"),
                        "timeout_triggered": timeout_meta,
                        "consistency_status": "ok",
                        "recommended_action": {
                            "tool": "aota_handoff_open",
                            "hint": f"Open handoff for timed out task {task_id}",
                        },
                    }
                    all_items.append(item)

    # -----------------------------------------------------------------------
    # D. Cross-reference for broken_lineage and artifact_gap
    #    (consistency checks that span multiple artifacts)
    # -----------------------------------------------------------------------
    # D1. broken_lineage: decisions referencing missing handoffs/tasks
    if dec_dir.is_dir():
        try:
            for entry in os.listdir(str(dec_dir)):
                if not entry.startswith("DECISION.") or not entry.endswith(".json"):
                    continue
                try:
                    ddata = read_decision(dec_dir / entry)
                except (OSError, json.JSONDecodeError):
                    continue

                decision_id = ddata.get("decision_id", "")
                handoff_id = ddata.get("handoff_id", "")
                source_task_id = ddata.get("source_task_id", "")
                created_at = ddata.get("created_at", "")

                issues: list[str] = []

                # Check source_handoff_id exists
                if handoff_id:
                    hpath = None
                    from ._handoff_common import find_handoff_path as _fhp
                    try:
                        hpath = _fhp(workspace_id, handoff_id)
                    except Exception:
                        pass
                    if hpath is None:
                        issues.append(f"handoff {handoff_id} not found")

                # Check source_task_id exists
                if source_task_id:
                    task_dir = get_task_dir(workspace_id, source_task_id)
                    if not task_dir.is_dir():
                        issues.append(f"source task {source_task_id} not found")
                    else:
                        try:
                            src_meta = load_meta(task_dir)
                            if src_meta.get("workspace_id") != workspace_id:
                                issues.append(f"source task {source_task_id} cross-workspace reference")
                        except Exception:
                            issues.append(f"source task {source_task_id} meta unreadable")

                if issues:
                    item = {
                        "item_id": build_decision_item_id(decision_id),
                        "item_type": "broken_lineage",
                        "priority": ITEM_TYPE_PRIORITY.get("broken_lineage", 10),
                        "created_at": created_at,
                        "summary": f"Broken lineage on decision {decision_id}: {'; '.join(issues)}",
                        "decision_id": decision_id,
                        "handoff_id": handoff_id,
                        "source_task_id": source_task_id,
                        "issues": issues,
                        "consistency_status": "broken",
                        "recommended_action": {
                            "tool": "aota_orchestration_lineage",
                            "hint": f"Inspect lineage for decision {decision_id}",
                        },
                    }
                    all_items.append(item)
        except OSError:
            pass

    # D2. artifact_gap: terminal tasks missing receipt, handoff card declared but missing
    for trec in task_records:
        meta = trec["meta"]
        task_id = trec["task_id"]
        status = meta.get("status", "")
        execution = meta.get("execution", {})
        start_id = execution.get("start_id", "")
        created_at = meta.get("created_at", "")

        if status in TERMINAL_STATUSES:
            # Check if completion receipt exists
            receipt_path_str = execution.get("completion_receipt_path", "")
            receipt_exists = False
            if receipt_path_str:
                task_dir = get_task_dir(workspace_id, task_id)
                receipt_file = task_dir / receipt_path_str
                receipt_exists = receipt_file.exists()

            handoff = get_handoff_for_task(workspace_id, task_id, start_id) if start_id else None

            # Terminal task should have receipt
            if not receipt_exists:
                item = {
                    "item_id": build_task_item_id(task_id, "receipt_missing"),
                    "item_type": "artifact_gap",
                    "priority": ITEM_TYPE_PRIORITY.get("artifact_gap", 20),
                    "created_at": created_at,
                    "summary": f"Terminal task {task_id} missing completion receipt",
                    "task_id": task_id,
                    "task_kind": meta.get("task_kind", ""),
                    "status": status,
                    "gap_detail": "completion_receipt_missing",
                    "consistency_status": "broken",
                    "recommended_action": {
                        "tool": "aota_profile_task_status",
                        "hint": f"Check status of task {task_id}",
                    },
                }
                all_items.append(item)

            # Check role card declaration (if handoff exists and declares card_exists=true but file missing)
            if handoff:
                role_art = handoff.get("role_artifact", {})
                if role_art.get("card_exists") is True:
                    task_dir = get_task_dir(workspace_id, task_id)
                    card_name = role_art.get("card_name", "")
                    if card_name:
                        card_path = task_dir / card_name
                        if not card_path.exists() or card_path.is_symlink():
                            item = {
                                "item_id": build_task_item_id(task_id, "card_gap"),
                                "item_type": "artifact_gap",
                                "priority": ITEM_TYPE_PRIORITY.get("artifact_gap", 20),
                                "created_at": created_at,
                                "summary": f"Role card declared but missing for task {task_id}: {card_name}",
                                "task_id": task_id,
                                "card_name": card_name,
                                "gap_detail": "role_card_declared_but_missing",
                                "consistency_status": "broken",
                                "recommended_action": {
                                    "tool": "aota_handoff_open",
                                    "hint": f"Open handoff for task {task_id} to inspect card declaration",
                                },
                            }
                            all_items.append(item)

    # -----------------------------------------------------------------------
    # Deduplicate by item_id — keep lowest priority (most urgent)
    # -----------------------------------------------------------------------
    seen: dict[str, dict[str, Any]] = {}
    for item in all_items:
        iid = item.get("item_id", "")
        if not iid:
            continue
        if iid in seen:
            if item.get("priority", 999) < seen[iid].get("priority", 999):
                seen[iid] = item
        else:
            seen[iid] = item
    all_items = list(seen.values())

    # -----------------------------------------------------------------------
    # Apply filters
    # -----------------------------------------------------------------------
    filtered: list[dict[str, Any]] = []
    for item in all_items:
        # item_type filter
        if item_type_filter and item.get("item_type") != item_type_filter:
            continue

        # priority_max filter
        if priority_max is not None and item.get("priority", 999) > priority_max:
            continue

        # consistency_status filter
        if consistency_filter and item.get("consistency_status") != consistency_filter:
            continue

        # include_informational
        if not include_informational and item.get("priority", 0) == _INFORMATIONAL_PRIORITY:
            continue

        filtered.append(item)

    # -----------------------------------------------------------------------
    # Sort: priority asc, created_at asc, item_id asc
    # -----------------------------------------------------------------------
    filtered.sort(key=lambda x: (x.get("priority", 999), x.get("created_at", ""), x.get("item_id", "")))

    # -----------------------------------------------------------------------
    # Truncation check
    # -----------------------------------------------------------------------
    truncated = len(filtered) > effective_limit
    items_out = filtered[:effective_limit]

    # -----------------------------------------------------------------------
    # Counts
    # -----------------------------------------------------------------------
    total = len(filtered)
    counts: dict[str, int] = {
        "total": total,
        "critical": sum(1 for it in items_out if it.get("priority", 999) <= 30),
        "awaiting_user": sum(1 for it in items_out if it.get("item_type") == "awaiting_user"),
        "pending_handoff": sum(1 for it in items_out if it.get("item_type") == "pending_handoff"),
        "approval_required": sum(1 for it in items_out if it.get("item_type") == "draft_requires_approval"),
        "ready_to_start": sum(1 for it in items_out if it.get("item_type") in ("approved_ready_to_start", "draft_ready_to_start")),
        "broken": sum(1 for it in items_out if it.get("consistency_status") in ("broken",)),
    }

    result: dict[str, Any] = {
        "status": "ok",
        "workspace_id": workspace_id,
        "count": len(items_out),
        "total": total,
        "truncated": truncated,
        "counts": counts,
        "scanned_counts": scanned_counts,
        "items": items_out,
    }

    return result
