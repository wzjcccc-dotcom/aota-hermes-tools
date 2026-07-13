"""Shared mutation audit boundary for AOTA Tool Layer (P11-N.2).

Provides mutation_audit_boundary() context manager that:
1. Builds MinimalAuditIdentity from trusted env vars
2. Generates audit_event_id per tool call
3. Catches SecurityError subclasses → writes denied audit → re-raises
4. Catches audit writer failure → raises AuditGapError (fail-closed)
5. Prevents duplicate audit (one event = one audit)
6. Does NOT swallow non-security exceptions

Usage:
    with mutation_audit_boundary(
        tool_name="aota_file_copy",
        operation="file_copy",
        raw_target=destination,
    ) as audit:
        result = do_work()
        audit.mark_success()
        return result
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generator

from ._errors import SecurityError, AuditGapError
from ._audit import write_audit_entry

if TYPE_CHECKING:
    from ._security_context import SecurityContext


@dataclass(slots=True)
class MinimalAuditIdentity:
    """Minimal trusted identity for denied audit.

    Built from AOTA_PROFILE_TASK_* env vars — never from model input.
    Used when full SecurityContext may not yet be established (e.g. path
    resolution failure before SecurityContext build completes).
    """
    workspace_id: str = ""
    task_id: str = ""
    start_id: str = ""
    profile: str = ""
    task_kind: str = ""
    task_dir: str = ""


def _build_minimal_identity() -> MinimalAuditIdentity:
    """Build MinimalAuditIdentity from trusted env vars.

    Returns empty identity if env vars are absent (non-worker context).
    """
    workspace_id = os.environ.get("AOTA_PROFILE_TASK_WORKSPACE_ID", "")
    task_id = os.environ.get("AOTA_PROFILE_TASK_ID", "")
    start_id = os.environ.get("AOTA_PROFILE_TASK_START_ID", "")
    profile = os.environ.get("AOTA_PROFILE_TASK_PROFILE", "")
    profile_task_root = os.environ.get(
        "AOTA_PROFILE_TASK_ROOT", "/aota-runtime/profile-tasks"
    )
    task_kind = ""  # Will be filled from meta.json if available

    task_dir = ""
    if workspace_id and task_id:
        from pathlib import Path
        task_dir = str(Path(profile_task_root) / workspace_id / task_id)
        # Try to read task_kind from meta.json
        meta_path = Path(task_dir) / "meta.json"
        if meta_path.exists():
            try:
                import json
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                task_kind = meta.get("task_kind", "")
            except (OSError, json.JSONDecodeError):
                pass

    if not all([workspace_id, task_id, start_id, profile]):
        return MinimalAuditIdentity()

    return MinimalAuditIdentity(
        workspace_id=workspace_id,
        task_id=task_id,
        start_id=start_id,
        profile=profile,
        task_kind=task_kind,
        task_dir=task_dir,
    )


def sanitize_target(raw_target: str, max_len: int = 200) -> str:
    """Sanitize a raw target path for audit logging.

    - Truncates length
    - Removes control characters
    - Does not expand home paths
    - Absolute paths are redacted to <absolute-path-redacted>
    - Relative paths preserved as-is (workspace-relative)
    """
    if not raw_target:
        return ""

    # Remove control characters
    sanitized = "".join(
        c for c in raw_target if c.isprintable() or c in ("/", "-", "_", ".")
    )

    # Redact absolute paths
    if sanitized.startswith("/"):
        sanitized = "<absolute-path-redacted>"

    # Truncate
    if len(sanitized) > max_len:
        sanitized = sanitized[:max_len] + "...[truncated]"

    return sanitized


class _AuditState:
    """Mutable state for a single mutation_audit_boundary invocation."""
    __slots__ = ("_audited", "_event_id", "_success_marked")

    def __init__(self) -> None:
        self._audited: bool = False
        self._event_id: str = ""
        self._success_marked: bool = False

    @property
    def event_id(self) -> str:
        return self._event_id

    @property
    def success_marked(self) -> bool:
        return self._success_marked

    def mark_success(self) -> None:
        self._success_marked = True

    @property
    def audited(self) -> bool:
        return self._audited

    def set_audited(self) -> None:
        self._audited = True


@contextmanager
def mutation_audit_boundary(
    tool_name: str,
    operation: str,
    raw_target: str,
    ctx: "SecurityContext | None" = None,
) -> Generator[_AuditState, None, None]:
    """Context manager that automatically writes denied audit on security rejection.

    1. Generates audit_event_id
    2. On SecurityError: writes denied audit (fail-closed), re-raises original error
    3. On success (mark_success called): caller writes success audit via ctx
    4. On non-security exception: passes through without audit
    5. Prevents duplicate audit

    If ctx is None, builds from get_security_context().
    If SecurityContext.available=False (non-worker), boundary is a no-op pass-through.

    The success audit is written by the caller after mark_success() — this
    boundary handles denied audit only, ensuring fail-closed behavior.
    """
    # Generate event ID for this tool call
    event_id = str(uuid.uuid4())
    state = _AuditState()
    state._event_id = event_id

    # Build context if not provided
    if ctx is None:
        from ._security_context import get_security_context
        ctx = get_security_context()

    # Non-worker context: no audit, just pass through
    if not ctx.available:
        yield state
        return

    sanitized_target = sanitize_target(raw_target)

    try:
        yield state
    except SecurityError as e:
        # Security rejection — write denied audit
        if not state.audited:
            state.set_audited()
            success = write_audit_entry(
                ctx=ctx,
                tool=tool_name,
                operation=operation,
                target=sanitized_target,
                approved=ctx.is_approved,
                result="denied",
                error_code=e.error_code,
                is_denied=True,
                audit_event_id=state.event_id,
            )
            if not success:
                # Fail-closed: audit writer failed, mutation must not proceed
                raise AuditGapError(
                    f"denied audit write failed for {e.error_code}; "
                    f"original error: {e.detail}"
                ) from e
        # Re-raise original security error
        raise
    # Non-security exceptions pass through without audit


def build_minimal_identity() -> MinimalAuditIdentity:
    """Public alias for _build_minimal_identity()."""
    return _build_minimal_identity()
