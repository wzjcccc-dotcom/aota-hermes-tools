"""Shared utilities for AOTA Operator Inbox & Consistency Audit (P10).

Provides: workspace validation, task discovery, handoff/decision/task compact
readers, consistency check engine, item_id generation, priority constants.

stdlib only. No third-party dependencies.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from ._handoff_common import (
    _TERMINAL_STATUSES as _HC_TERMINAL_STATUSES,
    _ROLE_ARTIFACT_MAP,
    find_handoff_path,
    get_handoff_pending_dir,
    get_handoff_ack_dir,
    read_handoff,
    HANDOFF_ROOT,
    PROFILE_TASK_ROOT,
)
from ._orchestration_common import (
    get_decision_dir,
    find_decision_path,
    read_decision,
    _DECISION_DECISIONS,
    _FOLLOWUP_TASK_KINDS,
)
from ._task_spec_common import (
    get_task_dir,
    load_meta,
    TASK_KINDS,
    STATUS_DRAFT,
    read_json,
)

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_FORBIDDEN_CHARS = ("\x00", "/", "\\", " ")


def validate_workspace_id(workspace_id: str) -> Optional[str]:
    """Validate workspace_id (no path traversal, no NUL)."""
    if not workspace_id:
        return "workspace_id is empty"
    if len(workspace_id) > 128:
        return "workspace_id exceeds 128 characters"
    for ch in _FORBIDDEN_CHARS:
        if ch in workspace_id:
            return f"workspace_id contains forbidden character: {ch!r}"
    if ".." in workspace_id:
        return "workspace_id contains '..' segment"
    return None


def validate_item_id(item_id: str) -> Optional[str]:
    """Validate item_id format (no path traversal, no NUL)."""
    if not item_id:
        return "item_id is empty"
    if len(item_id) > 128:
        return "item_id exceeds 128 characters"
    for ch in _FORBIDDEN_CHARS:
        if ch in item_id:
            return f"item_id contains forbidden character: {ch!r}"
    if ".." in item_id:
        return "item_id contains '..' segment"
    if not item_id.startswith("oi_"):
        return "item_id must start with 'oi_'"
    return None


def parse_item_id(item_id: str) -> Optional[dict[str, str]]:
    """Parse a validated item_id into its components.

    Returns dict with keys: type, id, suffix (optional).
    type is one of: handoff, decision, task, approval
    """
    if not item_id.startswith("oi_"):
        return None
    rest = item_id[3:]  # strip "oi_"

    # Determine type prefix
    if rest.startswith("handoff_"):
        item_type = "handoff"
        inner = rest[len("handoff_"):]
        return {"type": item_type, "id": inner}
    elif rest.startswith("decision_"):
        item_type = "decision"
        inner = rest[len("decision_"):]
        return {"type": item_type, "id": inner}
    elif rest.startswith("task_"):
        item_type = "task"
        inner = rest[len("task_"):]
        # task_id format: pt_YYYYMMDDTHHMMSS_XXXXXXXX
        # Task ID length: pt_ = 3, YYYYMMDD = 8, T = 1, HHMMSS = 6, _ = 1, 8 hex = 8
        # Total prefix: 3 + 8 + 1 + 6 + 1 + 8 = 27
        if inner.startswith("pt_"):
            task_id_len = 27  # pt_YYYYMMDDTHHMMSS_XXXXXXXX
            if len(inner) >= task_id_len:
                raw_task_id = inner[:task_id_len]
                suffix = inner[task_id_len:]
                if suffix.startswith("_"):
                    suffix = suffix[1:]
                result: dict[str, str] = {"type": item_type, "id": raw_task_id}
                if suffix:
                    result["suffix"] = suffix
                return result
        return None
    elif rest.startswith("approval_"):
        item_type = "approval"
        inner = rest[len("approval_"):]
        return {"type": item_type, "id": inner}
    else:
        return None


# ---------------------------------------------------------------------------
# Priority constants
# ---------------------------------------------------------------------------

ITEM_TYPE_PRIORITY: dict[str, int] = {
    "pending_handoff": 50,
    "awaiting_user": 30,
    "ready_for_followup": 60,
    "draft_requires_approval": 70,
    "approved_ready_to_start": 80,
    "draft_ready_to_start": 90,
    "needs_input_task_without_decision": 40,
    "failed_task_unconsumed": 40,
    "cancelled_task_unconsumed": 45,
    "timeout_task_unconsumed": 35,
    "broken_lineage": 10,
    "artifact_gap": 20,
    "running_task": 999,
}

TERMINAL_STATUSES: frozenset = _HC_TERMINAL_STATUSES

# Max scan limits
MAX_SCAN_TASKS = 200
MAX_ITEMS = 100

# ---------------------------------------------------------------------------
# Bounded task scanning
# ---------------------------------------------------------------------------


def scan_tasks(
    workspace_id: str,
    max_tasks: int = MAX_SCAN_TASKS,
) -> list[dict[str, Any]]:
    """Scan task directories for a workspace, bounded.

    Returns list of dicts with keys: meta, task_dir, task_id.
    """
    profile_root = PROFILE_TASK_ROOT / workspace_id
    if not profile_root.is_dir():
        return []

    tasks: list[dict[str, Any]] = []
    scanned = 0

    try:
        for entry in sorted(os.listdir(str(profile_root))):
            task_dir = profile_root / entry
            if not task_dir.is_dir() or task_dir.is_symlink():
                continue
            if not entry.startswith("pt_"):
                continue
            if scanned >= max_tasks:
                break
            scanned += 1

            meta_path = task_dir / "meta.json"
            if not meta_path.exists() or meta_path.is_symlink():
                continue
            try:
                meta = read_json(meta_path)
            except (OSError, json.JSONDecodeError):
                continue

            if meta.get("workspace_id") != workspace_id:
                continue

            tasks.append({"meta": meta, "task_dir": task_dir, "task_id": entry})
    except OSError:
        pass

    return tasks


def scan_tasks_by_status(
    workspace_id: str,
    statuses: set[str],
    max_tasks: int = MAX_SCAN_TASKS,
) -> list[dict[str, Any]]:
    """Scan tasks filtered by status."""
    all_tasks = scan_tasks(workspace_id, max_tasks=max_tasks)
    return [t for t in all_tasks if t["meta"].get("status", "") in statuses]


# ---------------------------------------------------------------------------
# Handoff discovery helpers
# ---------------------------------------------------------------------------


def get_handoff_for_task(
    workspace_id: str, task_id: str, start_id: str
) -> Optional[dict[str, Any]]:
    """Find a handoff for a task+start combination."""
    from ._handoff_common import find_existing_handoff

    hid = find_existing_handoff(workspace_id, task_id, start_id)
    if not hid:
        return None

    hpath = find_handoff_path(workspace_id, hid)
    if not hpath:
        return None

    try:
        return read_handoff(hpath)
    except (OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Decision discovery helpers
# ---------------------------------------------------------------------------


def get_decision_for_handoff(
    workspace_id: str, handoff_id: str
) -> Optional[dict[str, Any]]:
    """Find a decision for a handoff by handoff_id match."""
    dec_dir = get_decision_dir(workspace_id)
    if not dec_dir.is_dir():
        return None

    try:
        for entry in os.listdir(str(dec_dir)):
            if not entry.startswith("DECISION.") or not entry.endswith(".json"):
                continue
            try:
                dec = read_decision(dec_dir / entry)
            except (OSError, json.JSONDecodeError):
                continue
            if dec.get("handoff_id") == handoff_id:
                return dec
    except OSError:
        pass
    return None


def get_decision_for_source_task(
    workspace_id: str, task_id: str
) -> Optional[dict[str, Any]]:
    """Find the first decision that references a task as source_task_id."""
    dec_dir = get_decision_dir(workspace_id)
    if not dec_dir.is_dir():
        return None

    try:
        for entry in os.listdir(str(dec_dir)):
            if not entry.startswith("DECISION.") or not entry.endswith(".json"):
                continue
            try:
                dec = read_decision(dec_dir / entry)
            except (OSError, json.JSONDecodeError):
                continue
            if dec.get("source_task_id") == task_id:
                return dec
    except OSError:
        pass
    return None


# ---------------------------------------------------------------------------
# Approval check
# ---------------------------------------------------------------------------


def get_approval(workspace_id: str, task_id: str) -> Optional[dict[str, Any]]:
    """Read APPROVAL.json for a task, if it exists."""
    task_dir = get_task_dir(workspace_id, task_id)
    approval_path = task_dir / "APPROVAL.json"
    if not approval_path.exists() or approval_path.is_symlink():
        return None
    try:
        return read_json(approval_path)
    except (OSError, json.JSONDecodeError):
        return None


def is_approval_valid(
    approval: dict[str, Any], meta: dict[str, Any]
) -> bool:
    """Check if APPROVAL matches current meta revision and spec hash."""
    return (
        approval.get("revision") == meta.get("revision")
        and approval.get("spec_sha256") == meta.get("spec_sha256")
    )


# ---------------------------------------------------------------------------
# Item ID builders
# ---------------------------------------------------------------------------


def build_handoff_item_id(handoff_id: str) -> str:
    """Build deterministic item_id for a handoff item."""
    return f"oi_handoff_{handoff_id}"


def build_decision_item_id(decision_id: str) -> str:
    """Build deterministic item_id for a decision item."""
    return f"oi_decision_{decision_id}"


def build_task_item_id(task_id: str, reason: str = "") -> str:
    """Build deterministic item_id for a task item."""
    if reason:
        return f"oi_task_{task_id}_{reason}"
    return f"oi_task_{task_id}"


def build_approval_item_id(task_id: str) -> str:
    """Build deterministic item_id for an approval item."""
    return f"oi_approval_{task_id}"


# ---------------------------------------------------------------------------
# Consistency check rule codes (shared string constants)
# ---------------------------------------------------------------------------

# Rule codes for consistency audit
CC_TERMINAL_RECEIPT_MISSING = "TASK_TERMINAL_RECEIPT_MISSING"
CC_NON_TERMINAL_RECEIPT_CLAIM = "NON_TERMINAL_RECEIPT_CLAIM"
CC_HANDOFF_MISSING_RECOVERABLE = "HANDOFF_MISSING_RECOVERABLE"
CC_HANDOFF_TASK_BINDING = "HANDOFF_TASK_BINDING"
CC_HANDOFF_TERMINAL_MISMATCH = "HANDOFF_TERMINAL_MISMATCH"
CC_ROLE_CARD_DECLARATION = "ROLE_CARD_DECLARATION_MISMATCH"
CC_CARD_FILENAME = "CARD_FILENAME_MISMATCH"
CC_ACK_DECISION_MISMATCH = "ACK_DECISION_MISMATCH"
CC_DECISION_ACK_PENDING = "DECISION_ACK_PENDING"
CC_SOURCE_HANDOFF_EXISTS = "SOURCE_HANDOFF_EXISTS"
CC_SOURCE_BINDING_CONSISTENCY = "SOURCE_BINDING_CONSISTENCY"
CC_SINGLE_ACTIVE_DECISION = "SINGLE_ACTIVE_DECISION"
CC_FOLLOWUP_TASK_CREATED = "FOLLOWUP_TASK_CREATED"
CC_FOLLOWUP_TASK_EXISTS = "FOLLOWUP_TASK_EXISTS"
CC_FOLLOWUP_SOURCE_DECISION = "FOLLOWUP_SOURCE_DECISION"
CC_FOLLOWUP_HANDOFF_CONSISTENCY = "FOLLOWUP_HANDOFF_CONSISTENCY"
CC_FOLLOWUP_PREDECESSOR = "FOLLOWUP_PREDECESSOR"
CC_TASK_KIND_CONSTRAINT = "TASK_KIND_CONSTRAINT"
CC_REVIEW_SUBJECT = "REVIEW_SUBJECT"
CC_REOPEN_PREDECESSOR = "REOPEN_PREDECESSOR"
CC_APPROVAL_REV_HASH = "APPROVAL_REVISION_HASH"
CC_APPROVAL_STALE = "APPROVAL_STALE"
CC_RESUME_KIND_MISMATCH = "RESUME_KIND_MISMATCH"
CC_TIMEOUT_METADATA = "TIMEOUT_METADATA_MISSING"
