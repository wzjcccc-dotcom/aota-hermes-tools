"""Shared completion-observation and bounded-recovery contract helpers.

This module is deliberately stateless.  The running task's existing
``meta.execution`` object remains the durable authority for delivery state and
whether the one recovery has been consumed.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

COMPLETION_TRANSPORT_TERMINAL_BACKGROUND = "terminal_background"
# Historical receipts and handoffs can still carry the retired durable
# outbox/parent-wake value.  Keep the constant for read compatibility only;
# new Profile Task starts must never select it.
COMPLETION_TRANSPORT_LEGACY_DURABLE = "legacy_durable_delivery"
NEXT_ACTION_WAIT_FOR_COMPLETION_DELIVERY = "wait_for_completion_delivery"
NEXT_ACTION_RETRIEVE_AFTER_REENTRY = "retrieve_after_reentry"
NEXT_ACTION_OPEN_COMPLETION_HANDOFF = "open_completion_handoff"

# This is the one notification-loss window.  It is not a polling interval and
# is never reused to authorize a second recovery.
COMPLETION_NOTIFICATION_TIMEOUT_SECONDS = 30

BOUNDED_RECOVERY_REASONS = frozenset(
    {"completion_notification_timeout", "lost_completion_delivery"}
)
ORIGIN_ORCHESTRATOR_PRINCIPALS = frozenset({"task-main", "coordinator"})


class CompletionTransportContextError(ValueError):
    """The trusted host capability was absent or contradictory."""


def resolve_completion_transport() -> dict[str, Any]:
    """Resolve the start transport from the host's trusted capability.

    ``gateway.session_context.async_delivery_supported`` is the existing
    host authority used by ``terminal_tool`` and the Gateway.  Keeping this
    lookup lazy preserves standalone plugin imports while making an absent or
    malformed authority fail closed instead of accepting a model-provided
    transport choice.
    """
    try:
        from gateway.session_context import async_delivery_supported
    except (ImportError, AttributeError) as exc:
        raise CompletionTransportContextError(
            "completion_transport_context_missing"
        ) from exc
    try:
        supported = async_delivery_supported()
    except Exception as exc:  # pragma: no cover - host authority boundary
        raise CompletionTransportContextError(
            "completion_transport_context_missing"
        ) from exc
    if not isinstance(supported, bool):
        raise CompletionTransportContextError(
            "completion_transport_context_mismatch"
        )
    if not supported:
        # A finite/stateless caller cannot drain Hermes ProcessRegistry's
        # completion queue.  Do not silently switch a Profile Task to the
        # retired WebUI/outbox rail; reject before a Worker is launched.
        raise CompletionTransportContextError("terminal_background_required")
    return {
        "completion_transport": COMPLETION_TRANSPORT_TERMINAL_BACKGROUND,
        "completion_delivery_expected": False,
        "notify_on_complete": True,
    }


def _parse_utc(value: object) -> _dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.astimezone(_dt.timezone.utc)


def _format_utc(value: _dt.datetime) -> str:
    return value.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_completion_wait_contract(
    *,
    started_at: str,
    completion_transport: str,
    completion_delivery_expected: bool,
    timeout_deadline_at: str = "",
) -> dict[str, Any]:
    """Build the bounded start-response contract from control-plane facts."""
    started = _parse_utc(started_at) or _dt.datetime.now(_dt.timezone.utc)
    timeout_deadline = _parse_utc(timeout_deadline_at)
    if completion_delivery_expected:
        recovery_at = started + _dt.timedelta(
            seconds=COMPLETION_NOTIFICATION_TIMEOUT_SECONDS
        )
        if timeout_deadline is not None and timeout_deadline < recovery_at:
            recovery_at = timeout_deadline
        next_action = NEXT_ACTION_WAIT_FOR_COMPLETION_DELIVERY
    else:
        # Hermes native completion resumes the originating persistent session
        # only after the start turn yields.  A configured process deadline is
        # the only existing timing fact to reuse; the model never computes a
        # delay or polls inside the start turn.
        recovery_at = timeout_deadline or started
        next_action = NEXT_ACTION_RETRIEVE_AFTER_REENTRY
    return {
        "completion_transport": completion_transport,
        "completion_delivery_expected": bool(completion_delivery_expected),
        "next_action": next_action,
        "recovery_allowed_after": _format_utc(recovery_at),
    }


def completion_observation_context(
    meta: dict[str, Any], task_id: str, *, now: _dt.datetime | None = None
) -> dict[str, Any]:
    """Return the authoritative observation state for one active execution."""
    execution = meta.get("execution") or {}
    expected = bool(execution.get("completion_delivery_expected", False))
    start_id = execution.get("start_id", "")
    delivery_state = execution.get(
        "delivery_state", "pending" if expected else "not_expected"
    )
    active = bool(
        meta.get("status") == "running"
        and start_id == task_id
        and expected
        and delivery_state != "received"
    )
    allowed_at = _parse_utc(execution.get("recovery_allowed_after"))
    current = now or _dt.datetime.now(_dt.timezone.utc)
    return {
        "active": active,
        "delivery_state": delivery_state,
        "completion_transport": execution.get(
            "completion_transport", execution.get("transport", "")
        ),
        "completion_delivery_expected": expected,
        "recovery_allowed_after": execution.get("recovery_allowed_after", ""),
        "recovery_due": bool(active and allowed_at is not None and current >= allowed_at),
        "recovery_consumed": bool(execution.get("recovery_consumed", False)),
        "origin_session_id": meta.get("origin_session_id")
        or execution.get("parent_session_ref", ""),
    }


def is_origin_orchestrator(
    *, origin_session_id: object, observer_session_id: object, observer_principal: object
) -> bool:
    """Authorize recovery only to the trusted origin orchestrator session."""
    return bool(
        isinstance(origin_session_id, str)
        and origin_session_id
        and observer_session_id == origin_session_id
        and observer_principal in ORIGIN_ORCHESTRATOR_PRINCIPALS
    )
