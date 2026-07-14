# AOTA Forge Work Classification v1

## Scope

`aota_work_classify` is a source-level, deterministic decision-support tool in
the `aota_work_intake` toolset. The toolset is registered but deliberately not
enabled by any profile.

It accepts a registered `workspace_id`, bounded title and summary, and a strict
intake-facts object. `workspace_id` is verified only; classifier code does not
read workspace content, accept paths, scan files, or write artifacts.

The classifier never creates or updates a Plan, Architect review, SPEC, task,
handoff, Todo item, or runtime action. It has no LLM, network, clock,
randomness, filesystem mutation, or profile-activation dependency.

## Independent axes

The result has three independent axes:

- `planning_depth`: `P0` (no formal Plan), `P1` (lightweight Plan), or `P2`
  (full Plan).
- `architect_gate`: `A0` (none), `A1` (optional), or `A2` (required).
- `delivery_path`: `fast`, `standard`, or `deep`.

P0 does not imply A0: a bounded security or irreversible change can classify
as P0/A2/deep. P2 does not imply A2: a large low-risk documentation migration
can classify as P2/A0/standard. A2 always derives `deep`; `fast` requires
P0/A0 plus local-or-none writes, non-runtime impact, no human checkpoint, and
none/syntax/isolated validation.

## Deterministic rules

P2 has hard triggers including migration, control-plane or topology change,
durable contract plus dependency, two repositories/services, qualifying
multi-profile work, milestones, five work items, four
sessions, two checkpoints, and a requested Plan with three work items.

Absent P2, P1 follows multi-item/session work, dependencies, cross-session
work (including the fixed three-work-item/three-session F3 case), multi-module
work, multiple profiles, checkpoints, or a requested Plan.
P0 requires the exact single bounded-task conditions.

A2 hard triggers include security, migration, irreversible change,
cross-service protocol, deployment topology, control-plane, high blast radius,
production impact with rollback, novel competing designs, high ambiguity,
qualifying high uncertainty, and requested Architect review. A1 advisory
triggers include durable contracts, schema or external dependency change,
novel/competing designs, medium ambiguity/uncertainty, cross-repository or
cross-service scope, elevated write scope, and integration/live/E2E validation.

Delivery derives only after the independent P/A decisions: A2 and explicit
high-risk signals are `deep`; eligible P0/A0 work is `fast`; all other results
are `standard`.

## Input completeness and reasons

Unknown fact keys, bad types, invalid bounds/enums, control characters, and
oversized title/summary are rejected with stable classifier error codes. A
missing fact, or an `unknown` ambiguity/uncertainty value, returns
`classification_status=needs_input`, bounded `missing_facts`, fixed question
codes, and `recommended_next_step=request_missing_facts`. No absent risk fact
is defaulted to false.

Successful outputs use fixed, ordered reason-code arrays by axis plus a first
(dominant) reason. They include `hard_triggers`, `advisory_triggers`, derived
Plan/Architect booleans, `architect_action`, and a fixed recommended next step.
Canonical output contains no free-form generated reason.

## Override

`override` is caller-supplied classification instruction, not trusted human
authorization or an approval record. It has a required bounded rationale and
can only escalate P0→P1/P2, A0→A1/A2, and fast→standard/deep. Downward changes,
including any hard-trigger downgrade, return
`CLASSIFICATION_OVERRIDE_DENIED`. Overrides do not mutate facts or create any
artifact.

## Orchestration use

Task-main uses the classifier twice: first after Intake Lite, then after only
proportional convergence. A `needs_input` result is converted into 1–3
high-impact native `clarify` questions from the returned fixed question codes;
the answers update facts and trigger reclassification. P0 routes to a
standalone SPEC, P1 to a lightweight Plan, and P2 to a full Plan. These are
workflow actions by task-main, not classifier side effects.

## Deferred work

Canonical task-main source configuration now lists the classifier and Plan
toolsets, but trusted-principal runtime integration, deployment,
reload/restart, runtime profile activation, live worker, live Plan mutation,
and live E2E verification remain deferred. Source registration/configuration is
not runtime activation. The classifier itself remains independent of the
optional task-spec traceability contract.
