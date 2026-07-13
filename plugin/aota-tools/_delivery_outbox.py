"""AOTA Completion-to-Origin Delivery Outbox (P11-L.1B).

Produces per-event durable outbox entries after a Profile Task reaches
terminal state (receipt + handoff durable).  The outbox lives on the AOTA
filesystem so it can be consumed by any process that has filesystem access
to the AOTA runtime root – currently the WebUI server process, which can
drain pending events and deliver them over SessionChannel/SSE.

Design principles:
  - Per-event files (pending/ → delivered/ atomic rename), NOT a single
    append-only JSONL that cannot be per-event acknowledged.
  - Stable event_id scheme (aota-task-terminal:<task_id>:<start_id>) that
    is idempotent under terminal-state reconciliation.
  - Payload is sanitised: NO raw command, worker log, env, token/secret,
    or full report.  Only structured identifiers + bounded summary.
  - Missing origin_session_id → event tagged undeliverable but task
    terminal/handoff is unaffected.
  - Outbox write failure does NOT block task finalization (best-effort).
  - stdlib only, no Hermes/WebUI imports (safe for standalone finalizer).

Outbox directory layout:
  <AOTA_RUNTIME_ROOT>/outbox/<workspace_id>/pending/<event_id>.json
  <AOTA_RUNTIME_ROOT>/outbox/<workspace_id>/delivered/<event_id>.json
"""

from __future__ import annotations

import datetime
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AOTA_RUNTIME_ROOT = os.environ.get("AOTA_RUNTIME_ROOT", "/aota-runtime")
OUTBOX_ROOT = Path(AOTA_RUNTIME_ROOT) / "outbox"

EVENT_KIND = "aota_task_terminal"
SCHEMA_VERSION = 1

# Terminal statuses that produce an outbox event
_PRODUCIBLE_STATUSES = frozenset(
    {"done", "failed", "needs_input", "cancelled", "timeout", "scope_violation"}
)

# Maximum length for a sanitised summary (characters)
_MAX_SUMMARY_LENGTH = 400

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def get_outbox_pending_dir(workspace_id: str) -> Path:
    """Return the pending (undelivered) outbox directory for *workspace_id*."""
    return OUTBOX_ROOT / workspace_id / "pending"


def get_outbox_delivered_dir(workspace_id: str) -> Path:
    """Return the delivered outbox directory for *workspace_id*."""
    return OUTBOX_ROOT / workspace_id / "delivered"


def get_event_path(pending_dir: Path, event_id: str) -> Path:
    """Full filesystem path for an outbox event file in *pending_dir*."""
    return pending_dir / f"{event_id}.json"


def get_delivered_path(delivered_dir: Path, event_id: str) -> Path:
    """Full filesystem path for a delivered outbox event file."""
    return delivered_dir / f"{event_id}.json"


# ---------------------------------------------------------------------------
# Event ID
# ---------------------------------------------------------------------------


def build_event_id(task_id: str, start_id: str) -> str:
    """Stable, deterministic event ID for (task_id, start_id).

    This ID is NOT random – it is derived from the task and start
    identifiers so that reconciliation of an already-terminal task never
    produces a duplicate event.
    """
    return f"aota-task-terminal:{task_id}:{start_id}"


# ---------------------------------------------------------------------------
# Sanitised summary builder
# ---------------------------------------------------------------------------


def _build_sanitized_summary(
    profile: str,
    terminal_status: str,
    needs_input_reason: Optional[str] = None,
    handoff_id: Optional[str] = None,
) -> str:
    """Build a short, structured, sanitised summary string.

    The summary is safe for UI rendering (no raw command, no worker log,
    no secrets).  It describes *what* happened at a high level.
    """
    parts: list[str] = []

    status_label = terminal_status.replace("_", " ")
    parts.append(f"task {status_label}")

    if terminal_status == "needs_input" and needs_input_reason:
        # needs_input_reason is already bounded (set by worker_outcome_submit
        # tool, not free-form user input dump).  Truncate if excessive.
        truncated = needs_input_reason.strip()
        if len(truncated) > _MAX_SUMMARY_LENGTH:
            truncated = truncated[:_MAX_SUMMARY_LENGTH] + "…"
        parts.append(f"reason: {truncated}")
    elif terminal_status == "done":
        parts.append("completed successfully")
    elif terminal_status == "failed":
        parts.append("exited with errors")
    elif terminal_status == "cancelled":
        parts.append("was cancelled by operator")
    elif terminal_status == "timeout":
        parts.append("exceeded time limit")
    elif terminal_status == "scope_violation":
        parts.append("violated allowed scope")

    if handoff_id:
        parts.append(f"handoff={handoff_id}")

    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Event payload builder
# ---------------------------------------------------------------------------


def build_terminal_event(
    task_id: str,
    start_id: str,
    profile: str,
    terminal_status: str,
    handoff_id: Optional[str] = None,
    origin_session_id: Optional[str] = None,
    needs_input_reason: Optional[str] = None,
    completed_at: Optional[str] = None,
) -> dict[str, Any]:
    """Build a sanitised outbox event payload for a terminal Profile Task.

    Args:
        task_id: AOTA Profile Task ID (pt_<timestamp>_<random>).
        start_id: Execution start ID (same as task_id for P5).
        profile: Named profile (coder, debugger, reviewer, architect).
        terminal_status: One of done|failed|needs_input|cancelled|timeout|
                         scope_violation.
        handoff_id: Durable handoff ID (ho_<timestamp>_<random>), if
                    handoff was created.
        origin_session_id: Optional Hermes WebUI session that started the
                           task.  When None/empty the event is marked
                           undeliverable.
        needs_input_reason: Bounded reason string for needs_input status.
        completed_at: ISO 8601 completion timestamp (UTC, Z suffix).  If
                      None, current time is used.

    Returns:
        Dict conforming to outbox event schema V1.

    The payload explicitly EXCLUDES:
      - raw command / shell command
      - worker log content
      - environment variables
      - tokens, secrets, API keys
      - full role report content (card/full)
      - SPEC.md or meta.json content
    """
    now = (
        completed_at
        or datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    )
    event_id = build_event_id(task_id, start_id)
    summary = _build_sanitized_summary(
        profile=profile,
        terminal_status=terminal_status,
        needs_input_reason=needs_input_reason,
        handoff_id=handoff_id,
    )

    event: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id,
        "kind": EVENT_KIND,
        "task_id": task_id,
        "start_id": start_id,
        "profile": profile,
        "terminal_status": terminal_status,
        "summary": summary,
        "completed_at": now,
        "created_at": now,
    }

    # handoff_id – optional, present when a durable handoff was created
    if handoff_id:
        event["handoff_id"] = handoff_id

    # origin_session_id – required for delivery targeting
    deliverable = bool(origin_session_id)
    if origin_session_id:
        event["origin_session_id"] = origin_session_id
    event["deliverable"] = deliverable

    # needs_input_reason – only present for needs_input status
    if terminal_status == "needs_input" and needs_input_reason:
        # Bounded in length (already truncated in summary)
        event["needs_input_reason"] = needs_input_reason[:2000]

    return event


# ---------------------------------------------------------------------------
# Atomic file I/O for outbox events
# ---------------------------------------------------------------------------


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Atomically write a JSON dict to *path* via temp sibling + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    fd, tmp_path_str = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.tmp_",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path_str, str(path))
    except Exception:
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> dict[str, Any]:
    """Read and parse a JSON file from *path*."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Public API — write / list / mark-delivered
# ---------------------------------------------------------------------------


def write_outbox_event(
    workspace_id: str,
    task_id: str,
    start_id: str,
    profile: str,
    terminal_status: str,
    handoff_id: Optional[str] = None,
    origin_session_id: Optional[str] = None,
    needs_input_reason: Optional[str] = None,
    completed_at: Optional[str] = None,
) -> Optional[str]:
    """Build and atomically write a pending outbox event for a terminal task.

    This is called by the finalizer AFTER the completion receipt, meta.json
    status update, and durable handoff have all been written successfully.

    The outbox write is intentionally best-effort: failure to write does
    NOT revert the terminal state or prevent the handoff from being
    consumable.  Task finalization is the source of truth; the outbox is a
    secondary delivery hint.

    Args:
        Same as :func:`build_terminal_event`.

    Returns:
        The event_id string on success, or None on any I/O/validation
        failure (logged to stderr).  Never raises.
    """
    # Only produce events for recognised terminal statuses
    if terminal_status not in _PRODUCIBLE_STATUSES:
        return None

    event = build_terminal_event(
        task_id=task_id,
        start_id=start_id,
        profile=profile,
        terminal_status=terminal_status,
        handoff_id=handoff_id,
        origin_session_id=origin_session_id,
        needs_input_reason=needs_input_reason,
        completed_at=completed_at,
    )
    event_id: str = event["event_id"]

    pending_dir = get_outbox_pending_dir(workspace_id)
    path = get_event_path(pending_dir, event_id)

    # Idempotent: if the event file already exists (from a previous
    # finalizer run for the same terminal state), skip.
    if path.exists():
        try:
            existing = _read_json(path)
        except (OSError, json.JSONDecodeError):
            existing = None
        if existing is not None and existing.get("event_id") == event_id:
            return event_id  # already written — idempotent

    try:
        _atomic_write_json(path, event)
    except OSError as e:
        print(
            f"OUTBOX_WRITE_FAILED: event_id={event_id}, error={e}",
            file=__import__("sys").stderr,
        )
        return None

    return event_id


def mark_event_delivered(
    workspace_id: str,
    event_id: str,
) -> bool:
    """Atomically move a pending outbox event to the delivered/ directory.

    Uses os.replace (atomic rename on the same filesystem) so no partial
    write is visible.

    Args:
        workspace_id: Workspace identifier.
        event_id: Event ID (aota-task-terminal:<task_id>:<start_id>).

    Returns:
        True if the event was moved; False if already delivered or missing.
    """
    pending_dir = get_outbox_pending_dir(workspace_id)
    delivered_dir = get_outbox_delivered_dir(workspace_id)

    src = get_event_path(pending_dir, event_id)
    dst = get_delivered_path(delivered_dir, event_id)

    if not src.exists():
        # Already delivered or never written — idempotent
        return dst.exists()

    if src.is_symlink() or dst.is_symlink():
        print(
            f"OUTBOX_MARK_FAILED: symlink detected for event_id={event_id}",
            file=__import__("sys").stderr,
        )
        return False

    delivered_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(str(src), str(dst))
        return True
    except OSError as e:
        print(
            f"OUTBOX_MARK_FAILED: event_id={event_id}, error={e}",
            file=__import__("sys").stderr,
        )
        return False


def list_pending_events(
    workspace_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List pending (undelivered) outbox events for *workspace_id*.

    Returns a list of event payloads sorted oldest-first (by created_at).
    The caller can filter by origin_session_id or other fields.

    This is the primary API used by the delivery adapter (separate process)
    to discover events that need to be fanned out over SessionChannel/SSE.
    """
    pending_dir = get_outbox_pending_dir(workspace_id)
    if not pending_dir.is_dir():
        return []

    events: list[dict[str, Any]] = []
    try:
        for entry in os.listdir(str(pending_dir)):
            if not entry.endswith(".json"):
                continue
            # Internal temp files from atomic_write should not be visible
            # (os.replace is atomic), but skip dotfiles anyway.
            if entry.startswith("."):
                continue

            event_path = pending_dir / entry
            if event_path.is_symlink():
                continue

            try:
                data = _read_json(event_path)
            except (OSError, json.JSONDecodeError):
                continue

            if not isinstance(data, dict) or data.get("kind") != EVENT_KIND:
                continue

            events.append(data)
    except OSError:
        return []

    events.sort(key=lambda e: e.get("created_at", ""))
    effective_limit = max(1, min(limit, 200))
    return events[:effective_limit]


def count_pending_events(workspace_id: str) -> int:
    """Return the number of pending (undelivered) outbox events."""
    return len(list_pending_events(workspace_id, limit=0))


# ---------------------------------------------------------------------------
# Session-targeted query  (for delivery adapter)
# ---------------------------------------------------------------------------


def list_pending_events_for_session(
    workspace_id: str,
    origin_session_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return pending outbox events whose ``origin_session_id`` matches.

    Args:
        workspace_id: Workspace identifier.
        origin_session_id: Target Hermes WebUI session ID.
        limit: Maximum results (default 50, max 200).

    Returns:
        List of event payloads sorted oldest-first.
    """
    all_pending = list_pending_events(workspace_id, limit=limit)
    return [
        e
        for e in all_pending
        if e.get("origin_session_id") == origin_session_id
    ]
