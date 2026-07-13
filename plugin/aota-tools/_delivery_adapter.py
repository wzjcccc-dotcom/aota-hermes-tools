"""Delivery adapter: drain outbox → SessionChannel/SSE (P11-L.1B).

This module is designed to be imported by the **WebUI server process** to
consume outbox events produced by the standalone AOTA finalizer and deliver
them over Hermes SessionChannel (SSE) to the originating WebUI session.

Design:
  - At-least-once delivery: events are marked delivered ONLY after a
    successful SessionChannel.emit().  Failure keeps the event pending so
    the next drain cycle will retry.
  - Consumer-side idempotency: the WebUI public boundary owns structured
    emit/wakeup deduplication and carries event_id in the payload.
  - Missing origin_session_id → event is NOT delivered (it was tagged
    undeliverable at creation time) but is NOT an error.
  - WebUI is accessed through one bounded public helper
    (api.background_process.deliver_aota_terminal_event).
  - Safe to call from any WebUI lifecycle point: drainer, reaper, poll loop.

Usage (in WebUI process):
    from aota_tools._delivery_adapter import drain_pending_for_session

    # On session start / reconnect / poll cycle:
    delivered = drain_pending_for_session(
        workspace_id="aota-runtime",
        session_id="<active-session-id>",
    )

The adapter never raises on delivery failure; errors are best-effort logged.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from ._delivery_outbox import (
    EVENT_KIND,
    SCHEMA_VERSION,
    get_event_path,
    get_outbox_delivered_dir,
    get_outbox_pending_dir,
    list_pending_events_for_session,
    mark_event_delivered,
    write_outbox_event,
)

# Re-export core functions so consumers can import from this module
__all__ = [
    "drain_pending_for_session",
    "deliver_one_event",
    "list_undeliverable_events",
]

logger = logging.getLogger(__name__)

def _deliver_to_webui(event: dict[str, Any], session_id: str) -> bool:
    """Call the single WebUI boundary; import is lazy and retryable."""
    try:
        from api.background_process import deliver_aota_terminal_event
    except ImportError:
        logger.debug("AOTA WebUI delivery boundary unavailable", exc_info=True)
        return False
    try:
        result = deliver_aota_terminal_event(
            origin_session_id=session_id,
            event_id=str(event.get("event_id") or ""),
            payload=event,
        )
        return bool(
            getattr(result, "emitted", False)
            and getattr(result, "wakeup_started", False)
        )
    except Exception:
        logger.debug("AOTA WebUI delivery boundary failed", exc_info=True)
        return False


def _make_sse_payload(event: dict[str, Any]) -> dict[str, Any]:
    """Build the SSE event payload from an outbox event.

    The payload is a trimmed view safe for UI rendering — no raw logs,
    no full reports, no secrets.
    """
    # This is intentionally a RESTRICTED subset of the outbox event.
    # Additional fields from the outbox event are NOT forwarded to SSE.
    return {
        "event_id": event.get("event_id"),
        "kind": event.get("kind"),
        "task_id": event.get("task_id"),
        "profile": event.get("profile"),
        "terminal_status": event.get("terminal_status"),
        "summary": event.get("summary"),
        "handoff_id": event.get("handoff_id"),
        "completed_at": event.get("completed_at"),
    }


def deliver_one_event(
    workspace_id: str,
    event: dict[str, Any],
    session_id: str,
) -> bool:
    """Deliver a single outbox event to *session_id* via SessionChannel.

    Returns True on successful delivery and durable mark; False if delivery
    failed (event stays pending / retryable).

    Does NOT raise on delivery failure — the caller (batch drain) should
    continue with remaining events.
    """
    event_id: str | None = event.get("event_id")
    if not event_id:
        return False

    # Undeliverable events (no origin_session_id) are silently skipped at
    # the batch level; this is a defensive check.
    origin_sid: str | None = event.get("origin_session_id")
    if origin_sid and origin_sid != session_id:
        logger.warning(
            "deliver_one_event: event %s origin_session_id %s "
            "does not match target session %s — skipping",
            event_id,
            origin_sid,
            session_id,
        )
        return False

    # The WebUI boundary performs SessionChannel emit and card-first wakeup.
    # Keep the outbox pending until both operations report success.
    if _deliver_to_webui(event, session_id):
        return mark_event_delivered(workspace_id, event_id)
    return False


# ---------------------------------------------------------------------------
# Batch drain
# ---------------------------------------------------------------------------


def drain_pending_for_session(
    workspace_id: str,
    session_id: str,
    limit: int = 50,
) -> int:
    """Drain all pending outbox events for *session_id* and deliver via SSE.

    This is the primary integration point for the WebUI.  Call it:
      - When a session channel is subscribed (on SSE connect)
      - Periodically on a background poll cycle
      - After a task completion notification is received

    Each event is delivered at-most-once-per-call; failures are isolated
    (one failed event does not block others).  Delivered events are
    atomically renamed to delivered/ on the filesystem so the next drain
    cycle sees only still-pending events.

    Args:
        workspace_id: AOTA workspace identifier.
        session_id: Hermes WebUI session ID.
        limit: Max events to process per call (default 50).

    Returns:
        Number of events successfully delivered.
    """
    pending = list_pending_events_for_session(
        workspace_id=workspace_id,
        origin_session_id=session_id,
        limit=limit,
    )

    delivered_count = 0
    for event in pending:
        if deliver_one_event(workspace_id, event, session_id):
            delivered_count += 1

    return delivered_count


# ---------------------------------------------------------------------------
# Undeliverable event management
# ---------------------------------------------------------------------------


def list_undeliverable_events(
    workspace_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List pending events with no origin_session_id (undeliverable).

    These events can be safely archived or left in place — they are
    informational records that will never be fanned out to any session.
    Returns events sorted oldest-first.
    """
    from ._delivery_outbox import list_pending_events

    all_pending = list_pending_events(workspace_id=workspace_id, limit=limit)
    return [e for e in all_pending if not e.get("origin_session_id")]
