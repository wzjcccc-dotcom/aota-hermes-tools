---
name: aota-task-lifecycle
description: AOTA Profile Task binding contract from frozen SPEC through result, handoff, decision, and acknowledgement.
category: orchestration
---

# AOTA Task Lifecycle

Profile Task start derives `AOTA_WORKSPACE_ID`, `AOTA_PROJECT_ID`, roots,
workspace decision ID, and frozen SPEC identity from the frozen SPEC; callers
cannot override them.  `meta.json`, Card, Result, and Handoff must preserve
the same binding.  On any mismatch workers stop with `needs_input` and use
`binding_invalid`; they never guess another workspace.  task-main validates
the handoff binding and records the durable decision before acknowledgement.

The launcher also preserves a separate Hermes path binding:
`global_hermes_home`, `parent_profile_home`, and `target_profile_home`.
`HERMES_HOME` may identify the current parent Profile, but it is never passed
to credential bootstrap as the global authority. Early bootstrap/finalizer
failure must leave the bounded redacted worker log, canonical failure receipt,
and failure handoff; the fallback handoff does not require worker CARD/RESULT.
Canonical `spec_hash` and legacy `spec_sha256` are separate frozen bindings and
must both remain intact in receipts and handoffs.  `approval_status` is
`not_required` for diagnosis, review, architecture, and stewardship; only
implementation uses the approval API after freeze.

Credential bootstrap is global-authority-only: API keys come from the global
Hermes `.env`, OAuth is discovered from global `auth.json`, and no target,
default, parent-local, or arbitrary caller `.env` is read. The worker child
environment is bounded and explicitly sets `HERMES_HOME` to the normalized
global root; `-p <resolved_profile>` is the sole Profile selector.

The launcher manifest also freezes `resolved_profile`, provider, model,
Profile-config digest, and credential source type. A digest/provider/model
mismatch is `profile_launch_binding_mismatch`; parent model/provider values are
not a fallback. Early launcher failures always leave a bounded worker log,
failure receipt, and failure handoff, even when no worker artifact exists.

Project/artifact Phase 3 operations follow the same binding rule: current
workspace/project/lifecycle/artifact subjects come from trusted context and
canonical registry/declaration artifacts. Model calls use semantic refs only;
paths, IDs, digests, and revisions are resolved internally. Ambiguity,
registration conflicts, traversal, and symlink escape fail closed, while
lifecycle mutation stops at the required operator checkpoint.

All model-facing lifecycle/control calls follow the same rule: never invent,
copy, or retry control-plane identifiers, paths, revisions, hashes, sessions,
profiles, or lifecycle bindings. Use semantic current subjects and the
deterministic `next_action` returned by the tool.

## Active worker context preflight

Every worker reads its own frozen `SPEC`, `SCOPE`, and bounded `BINDING` through
`aota_active_task_artifact_open` before project-tier work. The three results
must agree on workspace/task/start/profile/spec identity. Missing, mismatched,
or denied reads fail closed with `needs_input`/`blocked`; workers never guess,
switch Profile, or use terminal/file fallback. Subject reads are available only
when the frozen reviewer/debugger/architect binding explicitly permits them.

## Canonical scope and task isolation

The frozen project scope is read from `spec.payload` by the shared
`_task_spec_scope.py` extractor. Payload keys take precedence even when their
value is `[]`; the bounded legacy top-level fallback is observable through
`scope_source=legacy_top_level`. `scope.json` is an atomic, immutable projection
bound to the SPEC id, revision, hashes, task/start identity, and `scope_digest`.

Task start captures `workspace-baseline.json`, including pre-existing tracked
dirty and untracked paths plus content fingerprints. Finalization treats
trusted, identity-bound `scope-events.jsonl` as the primary worker evidence and
reports only baseline deltas as postflight observations. A workspace-wide Git
diff is never a current-task worker action by itself. Missing or foreign event
identity is counted and unattributed project deltas fail closed.

## Project Lifecycle Contract

The minimal project lifecycle contract has one canonical source:
`plugin/aota-tools/_project_lifecycle_contract.py`, also declared in
`deploy/aota-lifecycle-inventory.yaml` under `project_lifecycle_contract`.

Key definitions (authoritative in the canonical source):

- **Dispatch invariant** `PROJECT_AND_PROFILE_MUTATION_REQUIRES_TASK_MAIN_DISPATCH`:
  protected project/profile mutation requires a task-main dispatched Profile
  Task with a frozen SPEC.  This covers ONLY protected mutation classes, NOT
  all filesystem writes.
- **Protected mutation classes**: project_initialization, project_git_lifecycle,
  project_codegraph_lifecycle, project_registry_mutation, project_closure,
  project_metadata_mutation.
- **Runtime artifact write exemptions**: CARD, RESULT, worker_outcome,
  completion_receipt, handoff, review_decision, task_runtime_meta,
  bounded_logs, bounded_temporary_state -- NOT protected mutations.
- **task-main dispatch authority** vs **Project Steward execution ownership**:
  task-main originates dispatch; Project Steward executes dispatched
  operations but cannot originate un-dispatched protected mutation.
- **Initialization states**: `initialized_core` (scaffold/metadata/registry
  complete, Git/CodeGraph not yet guaranteed) vs `initialized`
  (initialized_core + Git + CodeGraph + aggregate verification +
  authoritative receipt).
- **Initialization receipt authority**: CARD/RESULT are worker observations;
  the trusted finalizer / authoritative receipt is the final lifecycle
  result.  Project Steward cannot self-declare authoritative success.
- **Codex escalation boundary**: Codex is a Host/infra escalation path, not a
  general project operator.  Current Git/init gap requires Codex fallback
  (REQUIRED); target state is NOT_REQUIRED once bounded tools are implemented.
- **Fail-closed**: reject non-task-main protected mutation SPEC, unfrozen SPEC,
  binding mismatch, profile mismatch, artifact exemption used for project
  source write, steward mutation missing binding, initialized receipt missing
  Git/CodeGraph summary, receipt authoritative but wrong reconciliation source,
  unknown initialization state, unknown protected mutation class.

## Wait mode semantics

The task lifecycle distinguishes four wait modes for task completion
retrieval, classified by transport capability:

- **wakeup_capable_normal**: The transport supports wakeup notifications.
  Polling is PROHIBITED. The system must wait for a wakeup signal, not poll
  for completion.
- **non_wakeup_transport**: The transport does not support wakeup
  notifications. Explicit retrieval (checking status on demand via
  `aota_profile_task_status`) is allowed. The API Server transport has
  `supports_async_delivery=False`, which classifies it as
  `non_wakeup_transport` — it is NOT a handoff wakeup channel.
- **isolated_probe**: A bounded, one-time status check. Polling is allowed
  ONLY when the `POLLING_ALLOWED_FOR_ISOLATED_PROBE_ONLY` marker is present.
  Without the marker, isolated probe polling is rejected.
- **recovery**: Retry after a failure. Requires an explicit reason string.
  Recovery without a reason is rejected.

The canonical wait mode rules are defined in
`plugin/aota-tools/_skill_authority_contract.py` under `WAIT_MODE_RULES`.
The lifecycle inventory mirrors these as governance metadata. No second
authority is created.

New Profile Task starts have one completion gate: Hermes-native
`terminal_background`, using result readiness plus trusted session binding.
`completion_delivery_expected=false` means a missing or pending historical
outbox does not block `handoff_open`. A finite/stateless caller with
`async_delivery_supported() == false` is rejected before Worker launch with
`terminal_background_required`; it is never downgraded to the retired
`legacy_durable_delivery` rail. Missing or contradictory transport authority
also fails closed.

Historical legacy receipts remain readable so already-created tasks can be
closed without rewriting durable history. This read compatibility is not a
start mode and does not authorize producing a new legacy outbox event.

## Profile Task start transport admission

The start tool resolves transport support from trusted runtime evidence. The
model does not choose, classify, or override the completion transport.

### Classification rules

| Session context | Transport evidence | Wait mode |
|---|---|---|
| Persistent task-main session | `async_delivery_supported() == true` | Start with `terminal_background`; `wakeup_capable_normal` |
| `hermes -z`, API, or other finite/stateless caller | `async_delivery_supported() == false` | Reject with `terminal_background_required`; do not launch Worker |
| Any session | Capability missing or contradictory | Fail closed; do not launch Worker |
| One-time check | Explicit isolated probe with marker | `isolated_probe` |
| Post-failure retry | Explicit reason provided | `recovery` |

The generic `non_wakeup_transport` wait mode remains available for bounded
retrieval of historical tasks and non-Profile-Task workflows. It is not an
admitted transport for a new Profile Task start.

### START_RESULT=running / WAIT_MODE=wakeup_capable_normal

When `aota_profile_task_start` returns `running` and the wait mode is
`wakeup_capable_normal`:

- **NEXT_ALLOWED_ACTION**: Wait for the completion delivery signal. Do not call
  status, handoff list, process status, receipt, artifact, or operator-inbox
  completion observations while `next_action=wait_for_completion_delivery`.
  Those surfaces return `completion_delivery_pending` and the model must not
  switch tools.
- **FORBIDDEN_PROGRESS_ACTIONS** (all prohibited without `retrieval_reason`):
  1. Calling `aota_profile_task_status` without a `retrieval_reason`.
  2. Reading worker logs (`worker.<task_id>.log`) to check progress.
  3. Reading process output or inspecting process registry for progress.
  4. Inspecting repo changes (git diff/status) to infer worker activity.
  5. Checking artifact existence (CARD.json, RESULT.md, etc.) to infer
     completion.
  6. Reading output files or checking file sizes to gauge progress.
  7. Using `aota_path_info` or `aota_read_file` on task directories to poll
     for new artifacts.

### Bounded completion recovery

Only after the start response's `recovery_allowed_after` has passed, with no
delivery, may the origin orchestrator perform one recovery. Its only reasons
are `completion_notification_timeout` and `lost_completion_delivery`. The
first recovery aggregates terminal/running state, receipt, outcome, process
reconciliation, and handoff evidence. A terminal result points to
`open_completion_handoff`; a running result returns to waiting. Any later
recovery is rejected as `recovery_already_consumed`.

### Non-recovery conditions

The following observations are NOT recovery conditions and do NOT authorize
polling:

- Running status returned by a prior status query.
- Unchanged worker log size (the worker may simply not have written yet).
- Absence of new files or artifacts (the worker may still be working).
- Curiosity about worker health or progress.

### One authorized check does not authorize repeated polling

A single bounded recovery is an authorized completion check. It does NOT
authorize a second query or a fixed-interval retry. Historical status queries
on non-wakeup transports retain their typed retrieval-reason compatibility and
the existing repeated-query rejection.

### non_wakeup_transport explicit retrieval conditions

When observing a historical task already bound to `non_wakeup_transport`,
explicit retrieval via
`aota_profile_task_status` is allowed, but ONLY under these conditions:

1. **External re-entry**: A new session or turn that is not a busy-wait
   continuation of the same turn that started the task.
2. **Declared timeout**: The completion notification timeout has elapsed
   (`retrieval_reason=completion_notification_timeout`).
3. **Scheduled retrieval**: A scheduled or cron-triggered check
   (`retrieval_reason=non_wakeup_reentry`).
4. **User-triggered check**: The user explicitly requests a status check
   (`retrieval_reason=user_requested`).

Explicit retrieval must NOT happen within the same turn as task start. Do
not busy-wait in the same turn — start the task, then exit and wait for
re-entry.

## Binding, receipt, and handoff authority

After completion delivery, the canonical task-main closure invocation is:

```json
{}
```

for `aota_handoff_open`, followed by:

```json
{"decision":"accepted","rationale":"..."}
```

for `aota_orchestration_decision_record`, `{}` for `aota_handoff_ack`, and
`{"view":"closure"}` for `aota_profile_task_status`. The control plane
resolves current handoff/task/receipt/decision identity from trusted delivery,
origin session and durable artifacts. Missing subjects are deterministic;
multiple subjects return bounded semantic choices. Internal IDs, paths,
revisions and hashes are handler-only compatibility fields.

`aota_handoff_open({})` is the completion facade on this path. Its canonical
response includes a compact `completion` projection containing semantic
`current_role_card` and `current_role_result` references, worker outcome, and
authoritative completion-receipt facts. Task-main must consume that projection
instead of listing/searching for handoffs or separately probing CARD/RESULT or
receipt files.

The completion receipt is the trusted finalizer / authoritative result.
`done` requires exit code zero, a valid terminal worker outcome, and a
canonical receipt with `status=done`. Wakeups must never invent `done` when
a receipt is absent. CARD/RESULT are worker observations; they are not
authoritative lifecycle results.

task-main validates the handoff binding and records the durable decision
before acknowledgement. Handoff acknowledgment only means the orchestration
layer consumed the completion. It does NOT mean: task approved, source
correct, review passed, user accepted, or next task started. Auto-dispatch
is never implied by ack.

## Deployment and Runtime Guidance

- Profile runtime assembly is manifest-driven. The canonical assembly manifest
  (`deploy/profile-runtime-assembly.yaml`) declares active Skills per Profile.
- Profile-local plugin and Skill projections are required when the Hermes loader
  uses profile home (`~/.hermes/profiles/<name>/plugins/`,
  `~/.hermes/profiles/<name>/skills/`).
- Config declaration is not runtime availability evidence. A toolset may be
  declared in config but fail to register at runtime.
- SOUL declaration is not Skill-load evidence. A Skill referenced in SOUL.md may
  not be loaded if the file is missing or the cache is stale.
- Runtime verification requires loaded tool/Skill evidence or actual Profile
  Task execution. Hash parity between source and runtime is deploy evidence,
  not runtime evidence.
- Managed deploy and recreate requirements apply per the lifecycle inventory.
  All importing processes must be recreated after changes.
- Host/Codex construction vs Hermes runtime verification boundary: source PASS
  does not imply runtime PASS.
- Project Steward owns documentation continuity, not Skill source implementation.
- task-main must not manually fabricate deployment success. If a deployment
  receipt is missing or runtime evidence contradicts declared state, report
  the discrepancy.
- Runtime-generated files (logs, snapshots, receipts, backups) are not managed
  source.
- No generic `/aota-runtime` access; use bounded runtime tools
  (`aota_runtime_info`, `aota_active_task_artifact_open`).
## Trusted runtime minimal-invocation boundary

The model supplies semantic intent and explicit human decisions only. Exact
task/spec/start/revision/hash/session values are injected or resolved by the
control plane. A missing trusted session during freeze is reported as
`trusted_session_context_missing` with `retryable=false` and a bounded stop
action; it is never a reason to retry with guessed identifiers.

The lifecycle projection uses `session-state/` pointers for the current draft,
active frozen SPEC, active task, completion, handoff, decision, and completed
task. `profile-tasks/`, `handoffs/`, and `decisions/` remain durable history;
their quantity cannot create current-reference ambiguity. Freeze consumes the
exact draft pointer, finalization publishes completion/handoff pointers, and
acknowledgement closes the current pointers while preserving every historical
artifact.
