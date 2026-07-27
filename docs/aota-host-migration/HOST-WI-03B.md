# HOST-WI-03B — AOTA Completion to Hermes Parent Turn Backend Adapter

Status: `SOURCE_IMPLEMENTATION`

This work item implements the minimal source boundary selected by HOST-WI-03A1:

`AOTA sanitized outbox → Hermes backend watcher → parent ownership validation → gateway.wake.deliver_wake → synthetic user turn`

The AOTA finalizer now emits schema-v2 `aota_profile_task_terminal` events while retaining the existing `kind=aota_task_terminal` WebUI discriminator. Parent profile and session identity are frozen in the task execution contract. Result and handoff references are task-root-relative, bounded pointers; the adapter never reads full result content.

Hermes adds `gateway/aota_parent_wake.py` and a small supervised registration in `GatewayRunner.start()`/`stop()`. The feature flag `AOTA_PARENT_WAKE_ENABLED` defaults to disabled. The watcher uses pending/processing/delivered/failed directories, atomic claims, stale-claim recovery, bounded retries, hashed delivery receipts, duplicate suppression, profile/session ownership checks, and the existing `gateway.wake.deliver_wake` wrapper.

Cross-profile delivery is fail-closed unless the target profile is served by the current multiplexed backend and its profile-local session database resolves the frozen session. No direct parent `state.db` append is used; no `async_delegations` row is created.

## Source-only boundary

No formal runtime deploy, feature-flag enablement, backend restart, Desktop restart, live Profile Task, live wake, Docker start, or database mutation belongs to this work item. Those actions are reserved for `HOST-WI-03B-DEPLOYMENT-AND-LIVE-SMOKE`.

The isolated verifier is `scripts/verify-host-wi-03b-parent-turn-adapter.py`. It uses only temporary directories and fake gateway/wake objects.
