# AOTA Forge Orchestration Lifecycle

## Intake and routing

Task-main performs **Intake Lite** first: title, desired outcome, known scope,
deliverable, constraints/risk signals, explicit Plan/Architect requests, and
live/deploy expectation. It then calls `aota_work_classify` with bounded facts.
The classifier has no side effects and independently selects P0/P1/P2, A0/A1/A2,
and fast/standard/deep.

When classification reports missing facts, task-main asks only 1–3 high-impact
native `clarify` questions based on its fixed question codes, updates intake
facts, and classifies again. Clarify is not a ritual; it is only for information
that would materially alter routing, scope, acceptance, write boundary,
architecture, or Human Checkpoint.

`todo` is a current-session checklist. It may contain next actions or pending
artifacts, but never canonical milestones, closure, Plan revision, SPEC SHA,
decisions, evidence, or cross-session roadmap.

## P0 / P1 / P2

| Depth | Durable structure | Architect relationship |
|---|---|---|
| P0 | No formal Plan; standalone SPEC | A0/A1/A2 remain independent; P0+A2 is valid |
| P1 | Lightweight Plan, one bounded milestone and Work Items | A1 optional; A2 required |
| P2 | Full Plan, milestones, dependencies, queue | P2+A0 is valid; selective preflight is risk-driven |

P1/P2 lifecycle: create draft Plan; add bounded structure; select active IDs;
record final classification; obtain/adopt necessary design review; approve;
create plan-linked SPEC; preflight if required; freeze; start; explicitly link
the task; record evidence; then decide closure.

## Gates and role isolation

Architect provides design review or SPEC preflight only. Reviewer independently
provides review evidence only. Workers execute frozen SPECs only. None of these
roles receives Plan write authority. `approve_with_changes` requires correction;
`block`/`inconclusive` stops for a decision or checkpoint.

Plan write operations are bounded and revision-checked. Task-main does not
blind-retry a revision conflict, bypass an audit reconciliation gate, link inside
SPEC/task helpers, or restart a task after link/evidence mutation failure.

## Closure and handoff

Task completion records `execution_completed` plus bounded evidence. A Reviewer
pass is evidence, not a closure command. task-main closes only after checking
terminal state, acceptance, validation tier, scope, review requirement, residual
risk, Human Checkpoint, and Plan evidence.

Create a durable session handoff after decision closure plus a durable artifact,
not merely a chat summary. Include Plan/revision, active work, confirmed
decisions, open questions, artifact references, next action/executor, checkpoint
state, and `do_not_reopen` guidance.

## Activation status

Canonical task-main source config lists `aota_work_intake`, `aota_plan_read`,
and `aota_plan_write`. This is **source preparation only**: Plan write remains
fail-closed without deployment-owned trusted principal and authority injection.
No deployment, reload, runtime profile activation, live Plan mutation, or live
E2E is implied or performed.

## Worker authority boundary

Profile Task launch removes the three deployment-owned `AOTA_TRUSTED_*` Plan
authority keys in the child shell before it exports its exact
`AOTA_PROFILE_TASK_*` identity. That child environment is inherited by worker
terminal descendants, so task-main Plan authority does not pass to a worker.
Architect, reviewer, coder, and debugger explicitly disable Plan intake/read/
write toolsets; worker-marker authorization remains the final independent
rejection layer.
