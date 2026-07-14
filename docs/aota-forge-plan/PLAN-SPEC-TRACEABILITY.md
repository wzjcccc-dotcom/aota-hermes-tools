# Plan → SPEC Traceability

## Contract

A SPEC is either `standalone` or `plan_linked`. P0 normally uses standalone;
P1/P2 use an explicit Plan, milestone, and Work Item reference. A Plan is never
itself an execution contract.

For `plan_linked`, `aota_task_spec_create` or `aota_task_spec_update` accepts
only the IDs. The tool reads validated canonical Plan JSON and writes the
trusted source snapshot (Plan revision/SHA, milestone, Work Item, optional
Architect review ID). Callers cannot provide trusted revision/SHA values.

## Freeze and start

Freeze revalidates the same source reference. The source Plan must be in an
allowed state and a Work Item requiring preflight must carry an Architect review
ID. If Plan revision/SHA advanced, freeze rejects with
`SPEC_SOURCE_PLAN_ADVANCED`; task-main refreshes the draft reference, reviews
the change, then freezes again. It never refreshes or freezes automatically.

Frozen SPECs retain their historical snapshot. They are immutable. Corrections
require a new revision or a new SPEC. Start requires the frozen current revision
and exact SPEC SHA; a Profile Task receives bounded verified lineage only and
never links, updates, or closes a Plan.

## Explicit post-start Plan mutation

Only after task start succeeds may task-main call
`aota_plan_update(operation=link_task)` using the real task ID, linked Work Item,
and current expected Plan revision. The operation is idempotent and does not
change Work Item state. A link failure is reconciliation work: do not restart the
task.

Task result and Reviewer artifacts are recorded separately as bounded Plan
evidence. `execution_completed` is not `closed`; only task-main may close after
acceptance, validation, scope, review, risk, evidence, and checkpoint checks.

## Scope

This is source-level contract documentation. Runtime trusted principal injection,
deployment/reload, live Plan mutation, live traceability, and live E2E are not
performed or claimed here.
