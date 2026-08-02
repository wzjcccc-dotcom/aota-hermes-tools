# Native Terminal Completion Runbook

This runbook verifies the Hermes-native `terminal_background` completion chain
and the durable closure that follows handoff acknowledgement. It is source /
isolated by default; it does not authorize deploy, restart, live Worker,
outbox mutation, API self-post, pytest, or Git commit/push.

## 1. Canonical architecture

```text
task-main → classify → SPEC → freeze → start
→ terminal_tool(background=True, notify_on_complete=True)
→ launcher → Worker → finalizer → launcher exit
→ ProcessRegistry event → originating session resume
→ handoff → decision → ack / closure receipt
```

Native CLI/Desktop normal path uses `ProcessRegistry` with
`completion_transport=terminal_background` and
`completion_delivery_expected=false`. AOTA finalizer/session-state owns the
result and current binding. New Profile Tasks never select the WebUI/outbox
rail. A finite/stateless context is rejected with
`terminal_background_required` before Worker launch. Historical legacy
receipts remain readable, but no new native finalization writes an outbox
event.

## 2. Source authority map

| Area | Authority |
|---|---|
| Wake transport | Hermes `tools/process_registry.py` |
| Worker result | AOTA `_profile_task_finalize.py` and completion receipt |
| Current binding | `_session_state_authority.py` |
| Subject resolution | `_completion_subject_resolver.py` |
| Handoff | `_handoff_open.py`, `_handoff_ack.py`, durable handoff rail |
| Decision | `_orchestration_decision_record.py` |
| Closure | ack artifact / closure receipt plus consumed current pointers |
| Compact evidence | Handoff and CARD; CARD `finalizer_pending` is a generation snapshot |

## 3. Runtime projection map

Verify the loaded projection, not just declarations:

- tools: 61; toolsets: 28; profiles: 6;
- `notify_on_complete=true` and native transport binding;
- finalizer reconciliation is durable before handoff readiness;
- ProcessRegistry completion queue and originating-session resume path exist;
- task-main Skill projection contains this reference contract after managed deploy.

## 4. Desktop lifecycle

After a managed deploy, the user must close Windows Hermes Desktop, run
`/home/latios/.local/bin/hermes-host serve --stop`, reopen Desktop, and create a
new task-main session. Do not use plain `hermes gateway run` or `hermes-host
serve` as a Desktop-owned lifecycle replacement.

## 5. Source verification commands

```bash
python3 scripts/verify-aota-native-terminal-completion-chain.py
python3 -m compileall -q plugin/aota-tools scripts
python3 scripts/verify-aota-phase2-completion-migration.py
python3 scripts/verify-aota-native-terminal-handoff-gate.py
python3 scripts/verify-aota-parent-wake-retirement.py
python3 scripts/verify-aota-runtime-session-state-authority.py
python3 scripts/verify-aota-session-state-finalizer-projection.py
git diff --check
```

`verify-aota-parent-wake-retirement.py` proves the retired adapter stays absent
and is not an acceptance gate for live Profile Task completion.

The aggregated verifier is source / temporary-fixture only. It covers ack
ordering, closure write failure, post-ack lookup, stale-without-closure,
cross-session and decision mismatch, duplicate ack, completed/needs_input/
failed/timeout/cancelled outcomes, CARD snapshot semantics, and true pending
reconciliation.

## 6. Minimal live smoke

Only when explicitly authorized, use one bounded P0 diagnosis or known reliable
read-only Profile. Read only the first heading of `README.md`, set
`mutation_allowed=false`, use no network, and touch no other files. Never use
the live smoke to repair historical events.

## 7. Expected outputs

Source pass requires:

```text
NATIVE_CHAIN_SOURCE=PASS
ACK_CLOSURE=PASS
LEGACY_ISOLATION=PASS
RUNTIME_RESTART_REQUIRED=no
TOOLS_BEFORE=61
TOOLS_AFTER=61
TOOLSETS_BEFORE=28
TOOLSETS_AFTER=28
PROFILES_BEFORE=6
PROFILES_AFTER=6
NEW_MODEL_FIELDS=0
NEW_MODEL_TOOLS=0
```

If live smoke is run, additionally require worker completion, finalizer
reconciliation, native ProcessRegistry event, originating-session resume,
handoff, decision, ack/closure receipt, `AotaParentWake` not involved, and
polling/filesystem-bypass/manual-event counts all zero.

## 8. No-poll rules

After native `start` succeeds, wait for native re-entry. Do not call status,
process inspection, filesystem completion reads, handoff open, or manual event
actions. `aota_profile_task_status(view=closure)` is a bounded closure query
after re-entry, not a progress poll.

## 9. Failure signatures

| Signature | Next inspection |
|---|---|
| `terminal_background_required` at start | caller is finite/stateless; move the task to a persistent task-main session |
| Legacy transport at a new start | fail: deployed source/runtime parity is stale |
| Launcher exit without native event | `notify_on_complete`, ProcessRegistry |
| Native event without resume | session/event-loop adapter |
| Resume but handoff pending | finalizer readiness / resolver binding |
| Ack succeeds but closure is stale | ack record, consumed pointer resolver |
| `finalizer_pending=true` in CARD | treat as snapshot; inspect receipt/meta |
| Outbox pending | preserve; native path is not outbox-gated |
| Historical legacy receipt cannot close | verify read compatibility only; do not create/resubmit outbox events |

## 10. Rapid repair decision tree

```text
start != terminal_background
  → inspect runtime projection and async capability; do not redesign wake
launcher exit, no ProcessRegistry event
  → inspect notify_on_complete and completion queue
event, no session resume
  → inspect Desktop/Gateway adapter lifecycle
resume, no handoff
  → inspect finalizer receipt/reconciliation and exact session binding
handoff + decision, ack response stale
  → inspect ack write-before-pointer-close and consumed-pointer closure lookup
CARD finalizer_pending=true, durable receipt reconciled
  → PASS closure; label CARD as generation snapshot
reconciliation genuinely pending
  → block handoff/ack according to existing contract
```

## 11. Legacy/API boundary

`terminal_background` must not require outbox `delivered`. New Profile Task
starts never use the legacy durable delivery gate. Historical legacy receipts
remain readable and are verified independently, but must not be resubmitted or
used to justify a fallback. Do not remove Hermes Gateway, ProcessRegistry, or
the Desktop event loop as part of native verification.

## 12. API_SERVER_KEY and runtime environment

Do not read, print, hash, or log secret values. `API_SERVER_KEY` is retained:
Hermes API bearer authentication has an independent active reader.
Keep `/home/latios/.hermes/.env` mode 600. `gateway/run.py` imports only its
explicit allowlist from `AOTA_HOST_RUNTIME_ENV`; it must not load all `AOTA_*`.

The Host source no longer contains `AotaParentWakeAdapter` or its `gateway/run.py`
wiring. A currently running pre-reload backend may still have the old code in
memory until the explicitly authorized Desktop reload. Existing
`AOTA_PARENT_WAKE_*` environment assignments are then inert; no env key is
removed by this source-only work and no user env file is edited.

## 13. Historical parent-wake events

Never claim, move, delete, or resubmit historical pending outbox events. A
parent-wake activation boundary is a compatibility filter, not native success
evidence.

## 14. Upgrade checklist

1. Inspect the watchlist source files and current dirty state.
2. Run the aggregated source verifier and targeted compile checks.
3. Confirm 61 / 28 / 6 inventory parity and zero new model fields/tools.
4. Confirm ack evidence is durable before pointer consumption.
5. Confirm post-ack closure reports `EXPECTED_CONSUMED_AFTER_CLOSURE`.
6. Confirm CARD snapshot and durable finalizer semantics are separated.
7. Review environment names/readers without secret values.
8. If activation is authorized, deploy through the managed path, follow Desktop lifecycle, then create a fresh task-main session.

## 15. Rollback boundary

Rollback only the managed source/projection change that failed verification.
Do not reset unrelated dirty paths, edit historical outbox state, remove
API_SERVER_KEY, or alter the native transport. If runtime activation has not
been authorized, source PASS remains `PASS_WITH_LIMITATIONS` rather than a live
PASS.
