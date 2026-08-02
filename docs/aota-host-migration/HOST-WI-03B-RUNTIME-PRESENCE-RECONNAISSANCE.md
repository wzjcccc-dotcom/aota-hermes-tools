# HOST-WI-03B Runtime Presence Reconnaissance

Date: 2026-07-29

> Historical reconnaissance. On 2026-08-01 the target architecture changed to
> terminal-background-only admission for new Profile Tasks. The findings below
> remain evidence for why the WebUI/outbox adapter was retired from new starts.

## Result

`FINAL=NEEDS_PARENT_WAKE_CONFIGURATION_OR_SCHEMA_RECONCILIATION`

The host source contains `gateway/aota_parent_wake.py`; the editable Hermes
runtime resolves that canonical host source, and `GatewayRunner.start`/`stop`
contain feature-gated supervised registration and graceful shutdown wiring.
The adapter calls the unchanged compatible native entry point
`gateway.wake.deliver_wake`. No upstream Hermes native equivalent for the
external AOTA Profile Task completion outbox was found.

The blocking finding is workspace selection: AOTA producer events are written
under workspace `aota-hermes-tools`, while the enabled adapter has no protected
environment override and therefore resolves its default workspace
`aota-runtime`. The schema and event fields are compatible, but the default
consumer path does not contain the producer's pending events.

## Verification

The 37-fixture static verifier passed. Targeted `py_compile` and `git diff
--check` passed. The last observed backend startup logged the adapter as armed;
no event claim, native wake, automatic parent resume, or live task was run or
claimed by this read-only assessment.

The AOTA Forge deployment inventory covers the AOTA producer/plugin surface but
does not manage Hermes core files. Existing dirty source changes were preserved.
An independent Orca review was attempted but could not execute because the
local `orca-ide` AppImage could not mount without FUSE; no alternate reviewer
route was substituted.

Evidence: [HOST-WI-03B Runtime Presence Reconnaissance](../../deploy/evidence/host-migration/HOST-WI-03B-RUNTIME-PRESENCE-RECONNAISSANCE/20260729T020919Z/RESULT.md)

## Updated source authority boundary

The current source binding is now explicit and capability-derived:

| Trusted host capability | AOTA completion transport | Delivery gate |
|---|---|---|
| `async_delivery_supported() == true` | `terminal_background` | result/receipt/handoff/session binding; outbox is non-blocking |
| `async_delivery_supported() == false` | no start | `terminal_background_required` before Worker launch |

Historical `legacy_durable_delivery` records remain readable but are not a new
start mode. Missing or contradictory transport authority fails closed. `gateway/run.py`
imports only the proven non-secret AOTA runtime allowlist, preserves explicit
process environment precedence, and does not load unknown `AOTA_*` keys.
This source reconciliation does not claim runtime activation or live delivery.
