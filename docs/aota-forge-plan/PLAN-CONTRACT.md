# AOTA Forge Plan Contract v1

## Canonical artifacts

A Plan is project-owned canonical JSON at:

```text
<registered workspace>/.aota/forge/plans/<plan_id>/plan.json
```

`PLAN.md` is only a deterministic projection. It is never trusted as input and
may be stale after a reported projection-write failure; `aota_plan_open` always
renders from validated canonical JSON.

## Create and update tools

`aota_plan_create` and `aota_plan_update` are registered in the
`aota_plan_write` toolset, which is intentionally **not enabled by any profile**.
Runtime trusted-principal injection is not configured.

Create accepts only bounded business fields and internally generates the Plan
ID. It creates a minimal `draft` Plan at revision 1. P0 is rejected with
`PLAN_NOT_APPLICABLE_FOR_P0` and produces no artifact.

Update requires `workspace_id`, an exact `plan_id`, `expected_revision`, one
fixed operation enum, and a strict operation-specific payload. It never accepts
paths, raw JSON, Markdown, generic patches, actor, principal, or authority.
Unknown operations and payload fields are rejected.

The fixed operations are:

- Plan: `set_plan_status`, `update_current_state`, `set_plan_next_action`
- Milestone: `add_milestone`, `set_milestone_status`,
  `set_active_milestone`, `record_milestone_evidence`
- Work item: `add_work_item`, `set_work_item_status`,
  `set_active_work_item`, `set_work_item_next_action`,
  `record_work_item_evidence`, `link_task`
- Decision: `record_decision`, `set_decision_status`

No list, delete, archive, migration, generic replace, or automatic Plan-to-SPEC binding
exists in this version. A task may explicitly carry a source reference only through
the strict `aota_task_spec` traceability contract; it never mutates the Plan.
`aota_work_classify` is a separate, source-registered intake classifier. Its
canonical task-main config listing is source preparation only; it may return P0
before any Plan exists, but it never creates, reads, or updates a Plan.
## Integrity and transaction order

Updates validate the current canonical Plan and SHA, enforce `expected_revision`,
block unresolved same-Plan audit state, apply one bounded operation in memory,
validate the complete resulting Plan, increment revision exactly once, and
render the projection in memory before a write.

A prepared audit is durable before canonical mutation. The canonical `plan.json`
write occurs before audit commit; projection write occurs only after a successful
commit. Canonical write failure aborts the prepared audit. Canonical success with
commit failure preserves the Plan and reports reconciliation required. Projection
failure preserves canonical JSON and committed audit and reports
`updated_projection_stale`.

`execution_completed` is not auto-closed. Task links do not change status.
Immutable Plan fields have no operation.

## Plan-to-SPEC traceability

Task SPECs may be `standalone` (the P0-compatible default) or explicitly
`plan_linked`. Plan-linked creation/refresh accepts only a Plan, milestone, and
work-item ID; the task-spec tool safely reads the exact canonical Plan, validates
its schema and SHA, and records the verified revision/SHA snapshot. A caller
cannot supply verification state, revision, or SHA.

Freezing revalidates the exact source and rejects drift with
`SPEC_SOURCE_PLAN_ADVANCED`; it never refreshes implicitly. The frozen SPEC keeps
its historical snapshot when the Plan later advances. Starting a task copies only
bounded verified source lineage from that frozen SPEC and never updates, links, or
closes a Plan work item. Architect-review ID existence resolution is deferred;
only the bounded ID format and required-presence rule are enforced.

## Orchestration lifecycle

Task-main first classifies bounded Intake Lite facts. P0 creates no formal Plan
and uses a standalone SPEC; P1 creates a lightweight Plan; P2 creates a full
Plan. Architect gate is independent: P0+A2 and P2+A0 are valid. The explicit
order is Plan create → bounded structure → active IDs → final classification
decision → required review/adoption → approval → plan-linked SPEC → required
preflight → freeze → successful task start → `link_task` → evidence →
task-main closure decision. A task completion or Reviewer pass does not close a
Work Item automatically.

## Deferred runtime work

Canonical task-main source configuration now lists `aota_work_intake`,
`aota_plan_read`, and `aota_plan_write`. This is source preparation under the
existing fail-closed authority check, not runtime enablement. Trusted-principal
injection, deployment/reload, live mutations, reconciliation repair, multi-Plan
discovery, Plan migration, and live Plan-to-SPEC operation remain deferred and
require the applicable Human Checkpoint.
