# Plan Security and Audit Foundation

## Scope and activation

PF-WI-03A provides private, source-level identity and audit helpers. PF-WI-03 uses them for source-registered `aota_plan_create` and
`aota_plan_update` tools. Canonical task-main config preparation does not make
Plan mutation usable at runtime because identity injection remains absent.

The preferred identity source is Hermes-native per-invocation metadata. Source
inspection found that `PluginContext` exposes the active profile during plugin
registration, but registered handlers receive no trusted invocation principal,
session, toolset, or workspace binding. Therefore this foundation currently
uses an **unactivated deployment-injected fallback contract**:

- `AOTA_TRUSTED_PRINCIPAL=<Human-supplied principal at activation>`
- `AOTA_TRUSTED_AUTHORITIES=<Human-supplied authority list at activation>`
- optional `AOTA_TRUSTED_WORKSPACE_ID=<registered workspace id>`
- optional bounded `AOTA_ORIGIN_SESSION_ID=<id>`

No production default exists. Missing, malformed, duplicate, or unknown
authority values make the context unavailable. The injection is trusted only
when a controlled task-main deployment owns those environment values; profile
or runtime injection is deliberately outside this work item and requires its
own review and Human Checkpoint before activation.

## Identity boundary

`OrchestratorSecurityContext` is distinct from worker `SecurityContext`.
Plan write requires the fixed `orchestrator_profile` principal `task-main` and
explicit `plan_write` authority membership. Tool/model input cannot supply an
actor, principal, authority, audit event ID, audit filename, or audit path.

Any `AOTA_PROFILE_TASK_ID`, `AOTA_PROFILE_TASK_START_ID`, or
`AOTA_PROFILE_TASK_PROFILE` worker signal denies Plan write, including an
architect worker, with `PLAN_WRITE_FORBIDDEN_FOR_WORKER`. Other failures are
fail-closed with `ORCHESTRATOR_CONTEXT_UNAVAILABLE`,
`ORCHESTRATOR_PRINCIPAL_INVALID`, `ORCHESTRATOR_AUTHORITY_DENIED`, or
`ORCHESTRATOR_WORKSPACE_MISMATCH`.

## Project-owned audit ledger

Audit files are rooted only through the trusted workspace registry:

```text
<registered workspace root>/.aota/forge/audit/plan-mutations/pa_<generated-id>.json
```

Artifacts contain only the bounded principal projection, canonical operation
name, bounded target descriptor, revisions, timestamps, and status. They never
contain a full Plan, mutation payload, rationale/evidence text, raw command or
output, environment, credential, token, absolute path, traceback, worker task
directory, or global Hermes runtime path.

The root chain and artifact are containment-checked; symlinks, non-regular
files, path escapes, collisions, oversized files, invalid JSON, and bounded
scan overflow fail closed. The unresolved scan is read-only, exact-workspace,
and bounded to 200 audit files.

## State and future integration

States are `prepared`, `committed`, `denied`, `aborted`, and
`reconciliation_required`. This foundation implements only:

```text
prepared -> committed
prepared -> aborted
prepared -> reconciliation_required
```

A future Plan mutation must authorize, block on unresolved audit for the same
Plan, write `prepared`, mutate canonical `plan.json`, then commit the audit.
No prepared audit means no canonical mutation. A successful canonical mutation
whose audit commit cannot complete must preserve the prepared evidence, mark
`reconciliation_required` when possible, raise
`PLAN_AUDIT_RECONCILIATION_REQUIRED`, and block the next mutation for that
Plan. Reconciliation is intentionally not implemented here.

## Source configuration and deferred activation

Canonical task-main source configuration may list the Plan read/write and work
intake toolsets because every Plan mutation still requires the trusted principal
and explicit `plan_write` authority above. With neither deployment-owned value
injected, mutation fails closed; config visibility is not authorization.

Live activation, profile injection, deployment, reload/restart, profile
activation, live worker, and live E2E remain outside PF-WI-03A and require the
applicable Human Checkpoint.

## Profile Task worker boundary

Task-main deployment-owned `AOTA_TRUSTED_PRINCIPAL`,
`AOTA_TRUSTED_AUTHORITIES`, and `AOTA_TRUSTED_WORKSPACE_ID` are removed in the
Profile Task child shell before any worker-owned `AOTA_PROFILE_TASK_*` markers
are exported. The parent process environment is never modified. Consequently a
worker terminal process and every naturally inherited nested child process lack
that trusted Plan authority.

This boundary has three independent layers: exact child-environment
sanitization; explicit `aota_work_intake`, `aota_plan_read`, and
`aota_plan_write` disables in architect, reviewer, coder, and debugger
profiles; and the existing worker-marker authorization rejection. Disabled
toolsets and worker markers are not standalone security boundaries. Runtime
activation and live-worker verification remain subject to Human Checkpoint.
