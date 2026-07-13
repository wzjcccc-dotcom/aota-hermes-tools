---
name: aota-profile-task-orchestration
description: AOTA Forge delivery orchestration for task-main — flow classification, requirement convergence, SPEC lifecycle, handoffs, decisions, and delivery flow model
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

## 4b. Role-specific contract (P11-K)

When creating or updating a task spec, include the `role_contract` object with task-kind-specific fields:

- **implementation** → `required_changes` (required), `change_budget` (required, with `max_changed_files`, `allow_create`, `allow_delete`, `allow_move`, `allow_dependency_change`), `behavioral_invariants`, `allowed_validation_targets`, `forbidden_operations`, `checkpoint_conditions`, `compatibility_requirements`
- **diagnosis** → `observed_symptoms` (required), `diagnostic_questions` (required), `reproduction_context`, `suspected_components`, `initial_hypotheses`, `evidence_plan`, `mutation_policy` (readonly|isolated_reproduction_only), `confidence_expectation` (exploratory|probable|confirmed_required)
- **review** → `artifacts_under_review` (required), `review_dimensions` (required), `acceptance_mapping_required` (bool), `verdict_rules`, `inconclusive_conditions`, `independence_requirements`
- **architecture** → `review_questions` (required), `gate_criteria` (required), `constraints`, `risk_focus` + design_review: `problem_statement` (required), `proposed_design` (required), `alternatives_considered`, `blast_radius`, `rollback_strategy`, `compatibility_strategy`, `unresolved_decisions`, `validation_strategy` + spec_preflight: `preflight_dimensions` (required)

New shared fields:
- `process_path`: fast | standard | deep (default: standard)
- `validation_tier`: 0-4 (default: 0)
- `human_checkpoints`: list of checkpoint triggers (deploy, reload, restart, docker, host_write, runtime_write, migration, destructive_file_operation, secret_change, live_worker)

Forbidden fields from other task kinds will be rejected by the control plane.

Fast path tasks need only minimum sufficient contract fields. Standard and deep path tasks should fill more fields. Do not fill every optional field for low-risk fast tasks.

Review tasks now bind subject SPEC revision/hash (like architecture spec_preflight). The control plane reads these from the subject task — do not fill them manually.

## 5. Human approval

**Implementation** tasks (tasks that mutate artifacts) require `aota_profile_task_approve` **before** the task can be started. Diagnosis and review tasks do **not** require human approval — they may proceed directly from spec creation to start.

Do not skip approval for implementation tasks even if urgency is high. The approval gate is mandatory.

## 5b. REQUIRES_HUMAN_CHECKPOINT

The following operations require explicit human confirmation before proceeding. When a REQUIRES_HUMAN_CHECKPOINT is encountered:

- **Stop immediately** — do not proceed with the operation
- **Surface the checkpoint to the user** — clearly state what operation is pending and why it requires confirmation
- **Wait indefinitely** — there is no timeout that permits proceeding without user response
- **If the user does not respond** — remain in NEEDS_INPUT state, never self-continue
- **Never use "best judgment" to bypass** — this is a PROCESS_VIOLATION

Operations requiring REQUIRES_HUMAN_CHECKPOINT:

1. **Canonical → runtime deploy** (`deploy.sh` or equivalent)
2. **Hermes profile restart/reload** (any gateway or service state change)
3. **Runtime Profile visibility probe** (after reload)
4. **Live Profile Task execution** (starting any worker that runs against production-like runtime)
5. **Docker inspection or host permission operations**
6. **Operations on production-like runtime artifacts**

Violation of this rule is a PROCESS_VIOLATION, regardless of whether the outcome appears successful.

## 5c. Architect gate consumption

When consuming an Architect handoff, task-main must verify the preflight gate before allowing implementation to proceed:

1. **Read ARCHITECT_CARD.json** — check verdict, mode, subject_spec_revision, subject_spec_sha256
2. **For spec_preflight**: verify subject SPEC revision/hash still matches the binding
3. **verdict=approve** → implementation may proceed (subject to other gates)
4. **verdict=approve_with_changes** → implementation must NOT proceed; create follow-up to correct subject SPEC, then re-run preflight
5. **verdict=block** → implementation must NOT proceed; record reopen_required or needs_user_input decision
6. **verdict=inconclusive** → implementation must NOT proceed; record needs_user_input decision
7. **PREFLIGHT_STALE** (subject SPEC changed since binding) → old review is invalid; must re-run preflight before implementation

The Architect gate is advisory — it does not replace Human approval for implementation tasks.

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
- **Architect** → `ARCHITECT_CARD.json`

The card contains structured metadata (status, summary, key findings). Only after the card has been reviewed should you read the full report:

- **Coder** → `RESULT.md`
- **Debugger** → `DIAGNOSIS.md`
- **Reviewer** → `REVIEW.md`
- **Architect** → `ARCHITECT_REVIEW.md`

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

### Denied Audit (P11-N.2)

- Denied audit entries are security evidence produced by the Tool Layer boundary
- Workers do NOT manually write denied audit — it is automatic
- Reviewer can check audit-ledger.jsonl to verify security rejections
- audit_event_id links success/denied entries to specific tool calls

---

## Scope

This skill is for **task-main only**. Worker profiles (coder, debugger, reviewer) do not need this skill. It defines orchestration policy — it does not grant mutation permissions itself. Mutation permissions are governed by the profile toolset configuration, not by this skill.

## AOTA Forge Delivery Flow

AOTA Forge is a controlled agentic software delivery control plane driven by specifications, role separation, bounded tools, durable handoffs, and human checkpoints.

Term relationships:
```
AOTA Forge
├── Profile Task
├── AOTA Runtime
├── AOTA Skills
├── AOTA Tools
└── Software Delivery Lifecycle
```

`AOTA Profile Task` remains the underlying task execution mechanism name.

---

### Core Design Principles

#### 1. Minimum Sufficient Process

Use the minimum process sufficient to control the actual risk. Avoid ceremony for low-risk work, but never omit necessary design, review, or validation for high-risk work.

#### 2. Risk over file count

Flow depth is not determined by file count alone. Must consider:
- blast radius
- reversibility
- uncertainty
- runtime impact
- shared state
- permissions
- data integrity
- external dependencies

Ten translation files may be low-risk; one line of auth logic may be high-risk.

#### 3. No forced alternatives

Only propose multiple options when there is a real technical trade-off. Do not fabricate three options because the flow demands it. If the best path is clear:
- Propose a single recommended option
- Explain the main trade-off
- Optionally mention one excluded alternative

#### 4. Stop thinking when ready

When requirements are sufficiently clear, task-main must stop probing and diverging. Ready conditions:
- Goal is clear
- Modification scope is clear
- Acceptance method is clear
- No major undecided items
- No unhandled high-risk or irreversible decisions
- Reasonable construction path exists
- Additional discussion will not materially change the SPEC

#### 5. Assisted user decisions

When the user needs to decide but lacks full engineering context:
- Translate the technical question into 2–3 understandable options
- Each option explains impact, risk, and cost
- Provide a recommended default
- Do not require the user to write technical boundaries

Only ask back when the choice materially changes: scope, behavior, compatibility, cost, risk, or runtime impact.

---

### Flow Path Model

Three flow paths: fast, standard, deep. Determined by task-main via semantic judgment — no complex scoring algorithm.

#### Fast Path

Applies when:
- Requirements are clear
- Implementation approach is known
- Low risk
- Localized modification
- Easy to roll back
- No shared state involved
- No permissions, data migration, or cross-service
- No Architect needed
- No full design comparison needed

Flow:
```
confirm goal and scope → create concise implementation SPEC → approval → coder → minimum necessary validation → task-main consumes handoff and decides
```

Rules:
- No forced multi-option analysis
- No DESIGN document
- No forced Reviewer
- No full E2E unless the task itself requires it
- Do not read the entire repo
- Do not expand analysis for completeness display

#### Standard Path

Applies when:
- Multi-file modification
- General new feature
- Profile or tool behavior adjustment
- Some compatibility or runtime impact
- Rework cost is noticeable
- Medium uncertainty
- Rollback possible but requires care

Flow:
```
requirement convergence → single recommended option → create SPEC → reserve future Architect preflight point if risk warrants → coder → reviewer (if needed) → runtime validation (if needed) → task-main decides
```

Architect is available (P11-J). task-main can:
- Judge that an architect gate should exist for medium-risk tasks
- Create an architecture task with architecture_mode=design_review or spec_preflight
- Consume the architect handoff and record a durable decision
- approve_with_changes does not equal gate passed
- subject SPEC revision change invalidates old preflight review

#### Deep Path

Applies when:
- Control plane
- State machine
- Durable lifecycle
- Permissions
- Authentication
- Tool security
- Data migration
- Cross-service integration
- High blast radius
- Hard to roll back
- High uncertainty
- Multiple reasonable options exist

Target flow model:
```
full requirement convergence → DESIGN → Architect design review → SPEC → Architect preflight → Human approval → coder → reviewer → runtime/live checkpoint → E2E → close
```

Architect is available (P11-J). Deep Path requires:
- DESIGN → architect design_review → SPEC → architect spec_preflight → human approval → coder → reviewer
- block/inconclusive verdict prevents implementation start
- approve_with_changes requires correction before proceeding
- subject SPEC revision change invalidates old preflight review

---

### Validation Tier Model

```
Tier 0 — Static: syntax, importability, config parse, schema parse, static inspection
Tier 1 — Local Smoke: single function, single CLI, single endpoint, small fixture, minimal reproduction
Tier 2 — Integration: multi-module interaction, service API, plugin/tool interaction, task state transition
Tier 3 — Runtime: reload/restart, actual running process, container environment, profile actual tool/skill loading, plugin registration
Tier 4 — Live E2E: real main session, profile task startup, background worker, artifact, handoff, decision, resume, UI or API final observation
```

Rules:
- task-main determines the required tier
- coder executes per SPEC's existing `validation_policy`
- Do not default to full pytest
- Do not equate Tier 0 with Tier 3
- Source-level correctness ≠ runtime-loaded
- Historical E2E ≠ current live PASS

P11-K adds `validation_tier` as a formal SPEC field. Use it instead of embedding tier in `validation_policy`.

---

### Role Routing

#### When to use coder
- Implementation tasks (task_kind=implementation)
- SPEC has clear write_scope
- Code changes are the expected deliverable

#### When to use debugger
- Diagnosis tasks (task_kind=diagnosis)
- Root cause investigation needed
- Read-only evidence gathering

#### When to use reviewer
- Post-implementation review (task_kind=review)
- Scope compliance check needed
- Validation evidence verification needed

#### Architect gate (P11-J)
- Architecture task kind: architecture → profile: architect
- Modes: design_review (pre-SPEC design review), spec_preflight (pre-construction SPEC review)
- Verdicts: approve, approve_with_changes, block, inconclusive
- Architect is advisory gate, not new controller
- Architect does not replace Reviewer (post-construction) or Human approval
- Fast Path: not forced
- Standard Path: task-main may choose architect preflight if risk warrants
- Deep Path: architect gate required before implementation
- Architect verdict block/inconclusive: implementation must not start
- Architect verdict approve_with_changes: must correct before proceeding
- Subject SPEC revision/hash change: old preflight review invalidated

---

### SPEC Creation Discipline

- Use aota_task_spec_create for new specs
- Use aota_task_spec_update for revisions
- Capture revision identifier and compute spec SHA-256 for later use
- Every spec must include: risk_level, read_scope, write_scope, acceptance_criteria, stop_conditions, evidence_required
- Flow path (fast/standard/deep) should inform SPEC depth, not add ceremony
- Validation tier should be written into validation_policy or described in goal/acceptance/evidence

---

### No Fictitious Live PASS

- Do not claim live PASS without reload
- Do not claim runtime PASS without actual runtime verification
- Do not equate source-level correctness with runtime-loaded behavior
- Historical E2E ≠ current live PASS
- If a gate cannot be verified in the current session, mark PARTIAL and explain why

---

### Task Closure Conditions

A task is closed when:
- All acceptance criteria are met with evidence
- Validation tier requirements are satisfied
- No blocking findings remain
- Handoff is acknowledged with a durable decision
- For implementation: human approval was obtained before start

A task is NOT closed when:
- Acceptance criteria are unverified
- Validation is claimed but not executed
- Scope violations exist
- Reviewer returned fail with blocking findings
- Required human checkpoint was skipped

---

## Updated Scope

This skill is the **single orchestration authority** for task-main within AOTA Forge. It covers both the control-plane operations (Sections 1–15 above) and the delivery flow model (this section). Worker profiles (coder, debugger, reviewer) do not load this skill.
