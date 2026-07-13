"""Audit ledger for AOTA Tool Layer enforcement.

Provides AuditEntry dataclass and write_audit_entry() for appending
JSONL entries to task_dir/audit-ledger.jsonl. 'worker' field is
sourced from SecurityContext.profile.

Audit write failures are logged but non-blocking (AUDIT_GAP).

P11-N.1: Supports both success and denied results. Denied entries for
STALE_SPEC and WORKSPACE_ESCAPE include bound/current revision/hash fields.
"""

from __future__ import annotations

import datetime
import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._security_context import SecurityContext


@dataclass(slots=True)
class AuditEntry:
    """A single audit entry for a tool invocation."""

    timestamp: str
    task_id: str
    worker: str  # sourced from SecurityContext.profile
    tool: str
    operation: str
    target: str
    approved: bool
    revision: int
    result: str
    error_code: str
    audit_event_id: str = ""
    # P11-N.1: Bound/current identity fields for denied entries
    bound_revision: str = ""
    bound_sha256: str = ""
    current_revision: str = ""
    current_sha256: str = ""


def write_audit_entry(
    ctx: "SecurityContext",
    tool: str,
    operation: str,
    target: str,
    approved: bool,
    result: str,
    error_code: str = "",
    # P11-N.1: Denied result fields
    is_denied: bool = False,
    audit_event_id: str = "",
    bound_revision: str = "",
    bound_sha256: str = "",
    current_revision: str = "",
    current_sha256: str = "",
) -> bool:
    """Append an AuditEntry as a JSONL line to task_dir/audit-ledger.jsonl.

    Returns True on success, False on failure (AUDIT_GAP — non-blocking).
    The 'worker' field is always ctx.profile.

    If SecurityContext is not available (ctx.available=False), this is
    a no-op and returns True (no audit for non-worker contexts).

    P11-N.1: When is_denied=True, the entry includes bound/current
    revision/hash fields for STALE_SPEC and WORKSPACE_ESCAPE tracking.
    """
    if not ctx.available:
        return True

    task_dir = ctx.task_dir
    if not task_dir:
        return False

    now = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    entry_data: dict = {
        "timestamp": now,
        "task_id": ctx.task_id,
        "worker": ctx.profile,
        "tool": tool,
        "operation": operation,
        "target": target,
        "approved": approved,
        "revision": ctx.current_spec_revision,
        "result": result,
        "error_code": error_code,
    }

    # P11-N.2: Include audit_event_id when provided
    if audit_event_id:
        entry_data["audit_event_id"] = audit_event_id

    # P11-N.1: Include bound/current identity fields for denied entries
    if is_denied:
        entry_data["bound_revision"] = bound_revision or str(ctx.bound_spec_revision)
        entry_data["bound_sha256"] = bound_sha256 or ctx.bound_spec_sha256
        entry_data["current_revision"] = current_revision or str(ctx.current_spec_revision)
        entry_data["current_sha256"] = current_sha256 or ctx.current_meta_spec_sha256
    else:
        entry_data["bound_revision"] = ""
        entry_data["bound_sha256"] = ""
        entry_data["current_revision"] = ""
        entry_data["current_sha256"] = ""

    ledger_path = Path(task_dir) / "audit-ledger.jsonl"

    try:
        # Ensure directory exists
        ledger_path.parent.mkdir(parents=True, exist_ok=True)

        line = json.dumps(entry_data, sort_keys=True) + "\n"

        with open(ledger_path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

        return True
    except OSError:
        # AUDIT_GAP — non-blocking
        return False
