# HOST-WI-03A0 — Hermes Desktop Background Completion Delivery and Parent Session Wakeup Reconnaissance

Status: `PASS_RECONNAISSANCE_WITH_DECISION`

Decision: `AUTOMATIC_PARENT_WAKEUP_NOT_SUPPORTED`

Current AOTA Profile Task behavior is option **D**: durable completion artifacts are available for `task-main` to read; automatic wakeup of an official Hermes Desktop parent session is not established. If automatic parent wakeup becomes mandatory, option **C** is the minimum future boundary: a small trusted Hermes/backend delivery hook consuming the existing sanitized AOTA outbox. Direct option **A** is not proven compatible because Profile Task runs as a separate one-shot `hermes -z` process and does not enter Hermes native `async_delegation`/`gateway.wake` ownership.

## Evidence

Latest evidence pointer:

`deploy/evidence/host-migration/HOST-WI-03A0/latest.json`

Evidence directory:

`deploy/evidence/host-migration/HOST-WI-03A0/20260726T120515Z/`

The evidence set contains the source baseline, native completion call chain, Desktop/SSH capability, transport matrix, AOTA completion chain, identity mapping, cross-profile assessment, wakeup semantics, integration options, security assessment, non-executed smoke plan, independent review, verification and final result.

## Boundary summary

- Hermes native `delegate_task`/subagent completion is durable and at-least-once for native persistent channels.
- Gateway/CLI delivery creates a synthetic new turn; it does not splice a result into historical context.
- Desktop SSH token/owner nonce preserve backend ownership and reconnect transport; they do not provide a client event-cursor replay protocol.
- AOTA finalization writes receipt/meta/handoff and a sanitized filesystem outbox. Its adapter targets a WebUI `SessionChannel/SSE` boundary, and its own bridge document defers actual WebUI integration and reconnect validation.
- Named profiles have separate `state.db` files. No native cross-profile parent-session delivery route was found.

## Scope guard

This work item performed read-only reconnaissance. No Hermes, Desktop, AOTA runtime/source, profile, launcher, overlay or database was modified; Docker, Profile Task, `delegate_task`, real background work, CodeGraph/AMF mutation, commit and push were not performed.
