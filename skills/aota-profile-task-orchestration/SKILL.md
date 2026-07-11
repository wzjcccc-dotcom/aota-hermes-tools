---
name: aota-profile-task-orchestration
description: Orchestration policy guide for task-main — the operator profile managing the AOTA Profile Task lifecycle
category: orchestration
tags: [aota, orchestration, profile-task, operator, task-main, handoff, decision]
---

# AOTA Profile Task Orchestration

Orchestration policy for **task-main**, the operator profile that manages the AOTA Profile Task lifecycle. This skill defines the operational sequence and constraints for inbox processing, task specification, decision recording, handoffs, and follow-ups.

---

## 1. On startup

Call `aota_operator_inbox_list` **first**. Do not start by creating tasks, recording decisions, or initiating any other action. The inbox is the single source of truth for what requires attention. Process inbox items in order — each item contains the context needed to decide the next action.

## 2. Open exact item

Use `aota_operator_inbox_open` to read the full context of an inbox item **before** acting on it. Never act on an inbox item based on its summary alone. The full body contains the triggering event, current state, and any metadata required for correct decision-making.

## 3. Consistency before action

If the consistency status reported in the inbox item or via `aota_operator_inbox_open` is **warning** or **broken**, run `aota_operator_consistency_check` before making any mutations. Do not create, update, start, or cancel tasks while the system is in an inconsistent state. Resolve consistency issues first, then proceed.

## 4. Task spec create/update

Use `aota_task_spec_create` to create new task specifications and `aota_task_spec_update` to revise existing ones. Every task spec **must** include:

- **risk_level** — the assessed risk of the task
- **read_scope** — what the worker is permitted to read
- **write_scope** — what the worker is permitted to modify
- **acceptance_criteria** — conditions that define successful completion
- **stop_conditions** — conditions under which the worker should halt
- **evidence_required** — what artifacts or data the worker must produce

A revision updates the spec in-place. Always capture the revision identifier and compute the spec SHA-256 hash for later use when starting tasks and recording decisions.

## 5. Human approval

**Implementation** tasks (tasks that mutate artifacts) require `aota_profile_task_approve` **before** the task can be started. Diagnosis and review tasks do **not** require human approval — they may proceed directly from spec creation to start.

Do not skip approval for implementation tasks even if urgency is high. The approval gate is mandatory.

## 6. Profile task start/status/cancel

Use `aota_profile_task_start` to start a profile task. You **must** supply the exact **revision identifier** and the **spec_sha256** hash from the task spec. Starting a task binds the worker to a frozen version of the spec.

Use `aota_profile_task_status` to query the current state of a running or completed task.

Use `aota_profile_task_cancel` to cancel a running task. Cancellation is a deliberate operator action — do not cancel without cause.

## 7. Card-first result reading

After a worker completes (or times out), the result arrives as a handoff. Use `aota_handoff_list` to discover available handoffs, then use `aota_handoff_open` to open the handoff.

**Read the role card FIRST** before reading full reports. The card file depends on the worker role:

- **Coder** → `CARD.json`
- **Debugger** → `DIAGNOSIS_CARD.json`
- **Reviewer** → `REVIEW_CARD.json`

The card contains structured metadata (status, summary, key findings). Only after the card has been reviewed should you read the full report:

- **Coder** → `RESULT.md`
- **Debugger** → `DIAGNOSIS.md`
- **Reviewer** → `REVIEW.md`

Card-first reading means you understand the outcome before consuming verbose detail.

## 8. Handoff

Use `aota_handoff_ack` to acknowledge a handoff after you have read the card, assessed the result, and recorded your durable decision (see Section 9). The handoff ack's decision value **must** match the orchestration decision you recorded. Inconsistent acks will be rejected.

## 9. Durable decision

Use `aota_orchestration_decision_record` to record a durable decision for each handoff. Rules:

- **One decision per handoff.** Each handoff receives exactly one decision.
- **Idempotent.** Recording the same decision for the same handoff is safe and produces no conflict.
- **Conflicting values are rejected.** If a different decision was already recorded for the same handoff, the call fails.

The decision value captures what the operator determined (approve, reject, request-changes, escalate, etc.) and becomes part of the permanent lineage.

## 10. Follow-up draft

Use `aota_followup_task_create` to create follow-up tasks from a decision. Important constraints:

- This creates a **draft only**. It does **not** auto-start the follow-up task.
- **Implementation follow-ups** require human approval (`aota_profile_task_approve`) before they can be started.
- Diagnosis and review follow-ups do not require human approval.

Follow-up creation is explicit — always consider whether a follow-up is needed before recording the decision. Not every decision requires one.

## 11. Awaiting-user resume

When a decision is in the `awaiting_user` state, the system is blocked waiting for operator input. Use `aota_orchestration_decision_resume` to resume after receiving user input. This transitions the decision to `ready_for_followup`.

**Requirements:**

- Surface the missing input clearly to the user — do not guess or assume.
- Only resume **after** the user has provided the required input.
- Do not auto-create tasks while in the `awaiting_user` state. Wait for explicit resume.

## 12. Lineage

Use `aota_orchestration_lineage` to traverse the task, decision, and follow-up chain. The lineage endpoint supports queries up to **20 nodes** and includes **cycle detection**. Use lineage to understand the history of a task or decision before making new ones, especially when the context from the inbox item is incomplete.

## 13. No auto-dispatch

The orchestration system is **intentionally non-automatic**:

- **Never automatically start workers.** All task starts are explicit operator actions using `aota_profile_task_start`.
- **Decision recording does not start processes.** Recording a durable decision only persists the decision — it does not trigger a worker.
- **Follow-up creation only creates drafts.** Drafts are not started until explicitly started (and approved, if implementation).

Automation of any kind — cron-triggered, callback-triggered, or event-triggered — is forbidden for worker dispatch.

## 14. Cleanup exact manifest

Cleanup of controlled test artifacts must use an **exact manifest approach**:

- Use a `cleanup-manifest.json` file containing **exact absolute paths** to each artifact to be removed.
- **No prefix deletion** — do not delete by directory prefix.
- **No glob patterns** — do not use `*`, `?`, or other wildcards.
- **No broad parent directory deletion** — do not delete parent directories that contain unrelated files.

Only the paths listed in the manifest are removed. This constraint prevents accidental deletion of non-test artifacts.

## 15. Timeout handling

Timeout is a distinct terminal status, separate from failure and cancellation.

- `status=timeout` is a **distinct terminal status**. Treat it differently from `failure` and `cancel`.
- **Do not automatically restart timed-out tasks.** Timeout indicates the task exceeded its allowed duration — restarting without investigation compounds the problem.
- **Timeout tasks produce durable handoffs** with `terminal_status=timeout`. These handoffs appear in the inbox like any other completion, and you process them through the normal card-first → decision → ack → follow-up flow.
- When evaluating a timeout handoff, consider whether the timeout was due to an overly tight deadline, an infinite loop, a resource constraint, or a bug — and decide accordingly.

---

## Skill Rules

### On startup
- `aota_operator_inbox_list` — always first

### Before mutation
- Exact SPEC verified (revision + hash)
- Risk assessed
- Scope defined (read/write/forbidden)
- Human approval obtained (for implementation)

### After worker completion
- Open handoff / card first
- Run consistency check if needed
- Record durable decision
- Explicit ack
- Explicit follow-up draft (if required)

### Awaiting user
- Surface missing input clearly
- Explicit resume only after user provides input
- No auto-task creation

### Reviewer
- Independent review, no auto-fix
- Reviewer cannot mutate subject artifacts

### Timeout
- Distinguish timeout from failure / cancel
- Do not restart automatically
- Timeout produces durable handoff + inbox item

### Cleanup
- Exact manifest only
- No prefix, no glob

---

## Scope

This skill is for **task-main only**. Worker profiles (coder, debugger, reviewer) do not need this skill. It defines orchestration policy — it does not grant mutation permissions itself. Mutation permissions are governed by the profile toolset configuration, not by this skill.
