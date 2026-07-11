"""aota_orchestration_lineage — bounded lineage timeline traversal (P9).

Traces the lineage graph starting from a task_id or decision_id, following
authoritative relationships: task -> produced_handoff -> produced_decision ->
created_followup -> predecessor_task. Maximum 20 nodes, cycle detection.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from ._orchestration_common import (
    find_decision_path,
    get_decision_dir,
    read_decision,
    validate_decision_id,
    validate_workspace_id,
)
from ._handoff_common import (
    find_handoff_path,
    read_handoff,
    HANDOFF_ROOT,
)
from ._task_spec_common import get_task_dir, load_meta

TOOL_NAME = "aota_orchestration_lineage"
TOOLSET_NAME = "aota_orchestration"

MAX_NODES = 20

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Bounded lineage timeline traversal starting from a task_id or "
        "decision_id. Follows authoritative relationships: "
        "task -> produced_handoff -> produced_decision -> created_followup -> "
        "predecessor_task. Maximum 20 nodes with cycle detection. "
        "Use this before creating a follow-up when the predecessor is ambiguous."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {
                "type": "string",
                "description": "Registered workspace identifier (e.g. 'aota-runtime')",
            },
            "task_id": {
                "type": "string",
                "description": "Task ID to start traversal from (mutually exclusive with decision_id)",
            },
            "decision_id": {
                "type": "string",
                "description": "Decision ID to start traversal from (mutually exclusive with task_id)",
            },
        },
        "required": ["workspace_id"],
        "oneOf": [
            {"required": ["task_id"]},
            {"required": ["decision_id"]},
        ],
        "additionalProperties": False,
    },
}


def handle(args: dict, **_kwargs) -> str:
    """Handle aota_orchestration_lineage tool invocation."""
    try:
        result = _do_lineage(args)
        return json.dumps(result, sort_keys=True)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, sort_keys=True)


# ------------------------------------------------------------------
# Read helpers
# ------------------------------------------------------------------


def _read_task_compact(workspace_id: str, task_id: str) -> Optional[dict[str, Any]]:
    """Read task meta.json and return compact node data, or None."""
    task_dir = get_task_dir(workspace_id, task_id)
    if not task_dir.is_dir():
        return None
    try:
        meta = load_meta(task_dir)
        return {
            "type": "task",
            "task_id": meta.get("task_id", task_id),
            "task_kind": meta.get("task_kind", ""),
            "status": meta.get("status", ""),
        }
    except (OSError, json.JSONDecodeError):
        return None


def _read_handoff_compact(workspace_id: str, handoff_id: str) -> Optional[dict[str, Any]]:
    """Read handoff and return compact node data, or None."""
    hpath = find_handoff_path(workspace_id, handoff_id)
    if hpath is None:
        return None
    try:
        data = read_handoff(hpath)
        return {
            "type": "handoff",
            "handoff_id": data.get("handoff_id", handoff_id),
            "state": data.get("state", ""),
        }
    except (OSError, json.JSONDecodeError):
        return None


def _read_decision_compact(workspace_id: str, decision_id: str) -> Optional[dict[str, Any]]:
    """Read decision and return compact node data, or None."""
    dpath = find_decision_path(workspace_id, decision_id)
    if dpath is None:
        return None
    try:
        dec = read_decision(dpath)
        return {
            "type": "decision",
            "decision_id": dec.get("decision_id", decision_id),
            "decision": dec.get("decision", ""),
            "state": dec.get("state", ""),
        }
    except (OSError, json.JSONDecodeError):
        return None


# ------------------------------------------------------------------
# Lookup helpers
# ------------------------------------------------------------------


def _find_handoff_by_task_id(workspace_id: str, task_id: str) -> Optional[str]:
    """Scan pending+acknowledged handoffs for one whose task_id matches."""
    for subdir in ("pending", "acknowledged"):
        scan_dir = HANDOFF_ROOT / workspace_id / subdir
        if not scan_dir.is_dir():
            continue
        try:
            for entry in os.listdir(str(scan_dir)):
                if not entry.startswith("handoff.") or not entry.endswith(".json"):
                    continue
                hid = entry[len("handoff.") : -len(".json")]
                try:
                    data = read_handoff(scan_dir / entry)
                except (OSError, json.JSONDecodeError):
                    continue
                if data.get("task_id") == task_id and data.get("workspace_id") == workspace_id:
                    return hid
        except OSError:
            continue
    return None


def _find_decision_by_handoff_id(workspace_id: str, handoff_id: str) -> Optional[str]:
    """Scan decisions dir for one whose handoff_id matches."""
    ddir = get_decision_dir(workspace_id)
    if not ddir.is_dir():
        return None
    try:
        for entry in os.listdir(str(ddir)):
            if not entry.startswith("DECISION.") or not entry.endswith(".json"):
                continue
            try:
                dec = read_decision(ddir / entry)
            except (OSError, json.JSONDecodeError):
                continue
            if dec.get("handoff_id") == handoff_id:
                return dec.get("decision_id", "")
    except OSError:
        pass
    return None


def _read_full_decision(workspace_id: str, decision_id: str) -> Optional[dict[str, Any]]:
    """Read full decision data."""
    dpath = find_decision_path(workspace_id, decision_id)
    if dpath is None:
        return None
    try:
        return read_decision(dpath)
    except (OSError, json.JSONDecodeError):
        return None


def _get_handoff_source_task_id(workspace_id: str, handoff_id: str) -> Optional[str]:
    """Get the source task_id from a handoff."""
    hpath = find_handoff_path(workspace_id, handoff_id)
    if hpath is None:
        return None
    try:
        data = read_handoff(hpath)
        return data.get("task_id")
    except (OSError, json.JSONDecodeError):
        return None


def _get_followup_task_id(workspace_id: str, decision_id: str) -> Optional[str]:
    """Get followup.task_id from a decision."""
    dec = _read_full_decision(workspace_id, decision_id)
    if dec is None:
        return None
    return dec.get("followup", {}).get("task_id")


def _get_predecessor_task_id(workspace_id: str, task_id: str) -> Optional[str]:
    """Get predecessor_task_id from task meta or orchestration_context."""
    task_dir = get_task_dir(workspace_id, task_id)
    if not task_dir.is_dir():
        return None
    try:
        meta = load_meta(task_dir)
        return meta.get("orchestration_context", {}).get("predecessor_task_id")
    except (OSError, json.JSONDecodeError):
        return None


# ------------------------------------------------------------------
# Traversal
# ------------------------------------------------------------------


def _do_lineage(args: dict) -> dict[str, Any]:
    workspace_id: str = args.get("workspace_id", "")
    task_id: Optional[str] = args.get("task_id")
    decision_id: Optional[str] = args.get("decision_id")

    # ------------------------------------------------------------------
    # 1. Validate inputs
    # ------------------------------------------------------------------
    err = validate_workspace_id(workspace_id)
    if err:
        return {"status": "error", "error": f"invalid_workspace_id: {err}"}

    if task_id and decision_id:
        return {
            "status": "error",
            "error": "only one of task_id or decision_id allowed, not both",
        }
    if not task_id and not decision_id:
        return {
            "status": "error",
            "error": "one of task_id or decision_id is required",
        }

    # ------------------------------------------------------------------
    # 2. Setup
    # ------------------------------------------------------------------
    timeline: list[dict[str, Any]] = []
    visited_ids: set[str] = set()
    missing_nodes: list[str] = []
    cycle_detected = False
    truncated = False

    def _add_node(node: dict[str, Any]) -> bool:
        """Add a node. Returns False if full or cycle."""
        nonlocal cycle_detected, truncated
        nid = (
            node.get("task_id")
            or node.get("handoff_id")
            or node.get("decision_id")
            or ""
        )
        if not nid:
            return True
        if nid in visited_ids:
            cycle_detected = True
            return False
        if len(timeline) >= MAX_NODES:
            truncated = True
            return False
        visited_ids.add(nid)
        timeline.append(node)
        return True

    # ------------------------------------------------------------------
    # 3. BFS queue of (id, type) pairs to explore
    # ------------------------------------------------------------------
    # type: "task", "handoff", "decision"
    queue: list[tuple[str, str]] = []

    if task_id:
        queue.append((task_id, "task"))
    if decision_id:
        queue.append((decision_id, "decision"))

    while queue and len(timeline) < MAX_NODES and not cycle_detected:
        cid, ctype = queue.pop(0)

        if cid in visited_ids:
            cycle_detected = True
            break

        # Resolve and add the node
        if ctype == "task":
            node = _read_task_compact(workspace_id, cid)
            if node is None:
                m = f"task:{cid}"
                if m not in missing_nodes:
                    missing_nodes.append(m)
                continue
            _add_node(node)

            # Forward: task -> produced_handoff
            hid = _find_handoff_by_task_id(workspace_id, cid)
            if hid and hid not in visited_ids:
                queue.append((hid, "handoff"))

            # Backward: task -> predecessor_task
            pid = _get_predecessor_task_id(workspace_id, cid)
            if pid and pid not in visited_ids:
                queue.append((pid, "task"))

        elif ctype == "handoff":
            node = _read_handoff_compact(workspace_id, cid)
            if node is None:
                m = f"handoff:{cid}"
                if m not in missing_nodes:
                    missing_nodes.append(m)
                continue
            _add_node(node)

            # Forward: handoff -> produced_decision
            did = _find_decision_by_handoff_id(workspace_id, cid)
            if did and did not in visited_ids:
                queue.append((did, "decision"))

            # Backward: handoff -> source_task
            sid = _get_handoff_source_task_id(workspace_id, cid)
            if sid and sid not in visited_ids:
                queue.append((sid, "task"))

        elif ctype == "decision":
            node = _read_decision_compact(workspace_id, cid)
            if node is None:
                m = f"decision:{cid}"
                if m not in missing_nodes:
                    missing_nodes.append(m)
                continue
            _add_node(node)

            # Forward: decision -> created_followup
            ftid = _get_followup_task_id(workspace_id, cid)
            if ftid and ftid not in visited_ids:
                queue.append((ftid, "task"))

            # Backward: decision -> source_handoff
            dec = _read_full_decision(workspace_id, cid)
            if dec:
                hid_from_dec = dec.get("handoff_id", "")
                if hid_from_dec and hid_from_dec not in visited_ids:
                    queue.append((hid_from_dec, "handoff"))

    return {
        "status": "ok",
        "workspace_id": workspace_id,
        "timeline": timeline,
        "cycle_detected": cycle_detected,
        "truncated": truncated,
        "missing_nodes": missing_nodes,
    }
