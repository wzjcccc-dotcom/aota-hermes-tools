# HOST-WI-03B — AOTA Completion to Hermes Parent Turn Backend Adapter

Status: `HISTORICAL_SOURCE_IMPLEMENTATION_RETIRED_FOR_NEW_STARTS`

## 2026-08-01 terminal-background-only decision

This document preserves the delivery-adapter implementation history below. It
is no longer the target completion architecture for a new Profile Task.

New starts require Hermes-native `terminal_background`. When trusted
`async_delivery_supported()` is false, start returns
`terminal_background_required` before Worker launch instead of selecting this
outbox/parent-wake rail. Native finalization no longer writes a new outbox
event. Existing legacy receipts, handoffs, and already-started executions keep
read/finalization compatibility; historical pending events are left untouched.

The canonical wake trigger is task-main's start call to
`terminal_tool(background=True, notify_on_complete=True)`. The Worker writes
its outcome and finalizer artifacts, then exits. Hermes `ProcessRegistry`, not
the Worker, emits the completion event that resumes the originating task-main
session.

This work item implements the minimal source boundary selected by HOST-WI-03A1:

`AOTA sanitized outbox → Hermes backend watcher → parent ownership validation → gateway.wake.deliver_wake → synthetic user turn`

The AOTA finalizer now emits schema-v2 `aota_profile_task_terminal` events while retaining the existing `kind=aota_task_terminal` WebUI discriminator. Parent profile and session identity are frozen in the task execution contract. Result and handoff references are task-root-relative, bounded pointers; the adapter never reads full result content.

Hermes adds `gateway/aota_parent_wake.py` and a small supervised registration in `GatewayRunner.start()`/`stop()`. The feature flag `AOTA_PARENT_WAKE_ENABLED` defaults to disabled. The watcher uses pending/processing/delivered/failed directories, atomic claims, stale-claim recovery, bounded retries, hashed delivery receipts, duplicate suppression, profile/session ownership checks, and the existing `gateway.wake.deliver_wake` wrapper.

Cross-profile delivery is fail-closed unless the target profile is served by the current multiplexed backend and its profile-local session database resolves the frozen session. No direct parent `state.db` append is used; no `async_delegations` row is created.

## Source-only boundary

No formal runtime deploy, feature-flag enablement, backend restart, Desktop restart, live Profile Task, live wake, Docker start, or database mutation belongs to this work item. Those actions are reserved for `HOST-WI-03B-DEPLOYMENT-AND-LIVE-SMOKE`.

The isolated verifier is `scripts/verify-host-wi-03b-parent-turn-adapter.py`. It uses only temporary directories and fake gateway/wake objects.

## Completion gate boundary

Native CLI/Desktop tasks bind `completion_transport=terminal_background` with
`completion_delivery_expected=false`. Their completion gate is terminal
result readiness plus trusted session/task/SPEC/profile/project binding; a
pending or missing legacy outbox event is not a native handoff blocker.

Historical API/non-push tasks may already carry
`completion_transport=legacy_durable_delivery` and
`completion_delivery_expected=true`. Their durable records retain closure
compatibility, but this is no longer an admitted transport for a new start.
Missing, contradictory, or false async transport authority fails closed. This
source history does not authorize adapter deployment or event resubmission.
