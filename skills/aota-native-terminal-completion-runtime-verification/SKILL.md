---
name: aota-native-terminal-completion-runtime-verification
description: Verify Hermes-native terminal completion, wake, handoff, and durable closure after Hermes, AOTA launcher, finalizer, Desktop backend, ProcessRegistry, or session-state changes.
---

# AOTA Native Terminal Completion Runtime Verification

Use this reference Skill after a Hermes upgrade or any change to the AOTA
launcher/finalizer, Desktop backend lifecycle, ProcessRegistry, terminal tool,
handoff resolver, ack/closure resolver, or session-state authority. Keep the
verification source/isolated unless the user explicitly authorizes a bounded
live smoke.

## Canonical chain

```text
task-main
→ classify → SPEC create → freeze → start
→ terminal_tool(background=True, notify_on_complete=True)
→ launcher → Worker → finalizer → launcher exit
→ ProcessRegistry completion event
→ originating session resume
→ handoff → decision → ack/closure receipt
```

Use these authorities:

- ProcessRegistry: native completion/wake transport.
- AOTA finalizer and completion receipt: result and reconciliation authority.
- Session-state: current trusted binding authority.
- Handoff/CARD: compact evidence; CARD worker `finalizer_pending` is a generation snapshot.
- Decision plus ack/closure receipt: durable orchestration closure.
- Outbox and AotaParentWakeAdapter: historical compatibility only; never gate a new start or native handoff.

## No-poll contract

After a successful native start, do not query status, query process state, read
filesystem completion artifacts, open handoff, or use a manual event action.
Wait for native re-entry. Use `aota_profile_task_status(view=closure)` only as
bounded closure evidence after re-entry or as an explicitly typed recovery
probe.

## Upgrade watchlist

Check source parity for:

- AOTA: `_profile_task_start.py`, `_profile_task_launcher.py`, `_profile_task_finalize.py`, `_completion_observation.py`, `_completion_subject_resolver.py`, `_handoff_open.py`, `_handoff_ack.py`, `_profile_task_status.py`, `_session_state_authority.py`.
- Hermes Host: `tools/terminal_tool.py`, `tools/process_registry.py`, `gateway/run.py`, `gateway/session_context.py`, and the Desktop/Gateway event-loop adapter.

## Source and isolated verification

Run the aggregated verifier first; it must not launch a Worker:

```bash
python3 scripts/verify-aota-native-terminal-completion-chain.py
```

Then run bounded checks appropriate to changed files:

```bash
python3 -m compileall -q plugin/aota-tools scripts
python3 scripts/verify-aota-phase2-completion-migration.py
python3 scripts/verify-aota-native-terminal-handoff-gate.py
python3 scripts/verify-aota-parent-wake-retirement.py
python3 scripts/verify-aota-runtime-session-state-authority.py
python3 scripts/verify-aota-session-state-finalizer-projection.py
git diff --check
```

Run `verify-aota-parent-wake-retirement.py` to prove the retired adapter stays
absent; it is not a live native completion acceptance gate.

Expect `TOOLS_BEFORE=61`, `TOOLS_AFTER=61`, `TOOLSETS_BEFORE=28`,
`TOOLSETS_AFTER=28`, `PROFILES_BEFORE=6`, `PROFILES_AFTER=6`, and zero new
model-facing fields/tools. Do not use pytest for this chain verifier.

## Ack and closure checks

Confirm `aota_handoff_ack` validates the matching durable decision, writes the
bound ack record before moving the handoff or consuming pointers, and returns
`acknowledged`, `ack_id`, task/start/handoff/decision bindings, terminal
outcome, `closure_state=closed`, closure receipt reference, closed pointers,
closed time, and `next_action=closure_complete`.

After ack, `aota_profile_task_status(view=closure)` must resolve the exact
trusted session's consumed completion pointer plus the ack artifact and report
`EXPECTED_CONSUMED_AFTER_CLOSURE`. A missing pointer with no ack must report
`UNEXPECTED_STALE_BEFORE_CLOSURE`; it must not scan latest/mtime, cross
sessions, guess IDs, or ask the model for internal IDs.

Treat reconciliation metadata and the completion receipt as terminal authority.
Treat CARD `scope_observation.finalizer_pending=true` as
`card_generation_snapshot`, `not_terminal_reconciliation_authority`. Block
handoff/ack when reconciliation is genuinely pending.

## Desktop lifecycle and bounded live smoke

Require a managed deploy before runtime reload. The Desktop-owned SOP is:

1. Complete the managed deploy.
2. Have the user close Windows Hermes Desktop.
3. Run `/home/latios/.local/bin/hermes-host serve --stop`.
4. Have the user reopen Desktop.
5. Create a fresh task-main session.

Do not substitute plain `hermes gateway run` or `hermes-host serve` for the
Desktop-owned lifecycle. If live smoke is explicitly authorized, run one P0
diagnosis or known reliable read-only Profile, read only `README.md`'s first
heading, set `mutation_allowed=false`, use no network, and touch no other
files. Pass only when the worker completes, finalizer reconciles, native
ProcessRegistry event resumes the originating session, handoff/decision/ack
closure completes, `AotaParentWake` is uninvolved, and polling/filesystem
bypass/manual event counts are zero.

## Rapid failure localization

| Signature | Inspect |
|---|---|
| Start returns `terminal_background_required` | caller is finite/stateless; use a persistent task-main session |
| New start uses legacy transport | fail source/runtime parity; fallback is retired |
| Launcher exits without native event | `notify_on_complete` and ProcessRegistry queue |
| Native event without session resume | session/event-loop adapter |
| Session resumes but handoff is pending | finalizer readiness and resolver binding |
| Handoff succeeds but post-ack status is stale | ack closure evidence and consumed-pointer resolver |
| CARD says `finalizer_pending=true` | treat as snapshot; inspect durable receipt/meta |
| Outbox remains pending | preserve it; native path is not outbox-gated |
| Historical legacy receipt fails closure | verify read compatibility only; do not resubmit an event |

## Environment boundary

Review names and readers without printing secret values. Keep `API_SERVER_KEY`
because Hermes API bearer authentication has an independent reader; preserve
`.hermes/.env` mode 600. The Host adapter and `gateway/run.py` wiring are absent.
Existing `AOTA_PARENT_WAKE_*` assignments are inert after backend reload and may
be removed only in a separately authorized, backed-up env edit. Do not claim,
move, delete, or clean historical outbox events.

See [NATIVE-TERMINAL-COMPLETION-RUNBOOK.md](../../docs/aota-development/NATIVE-TERMINAL-COMPLETION-RUNBOOK.md)
for the operator checklist and rollback boundary.
