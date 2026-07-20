# PCF-WI-09A — Artifact Contracts

## Common rules

All new or migrated workflow artifacts use this closed, bounded envelope.
Unknown fields are rejected except for the explicitly closed role payload.
IDs are stable, timestamps are UTC RFC 3339, text fields are bounded, secrets
are prohibited, and host paths are represented only by approved logical refs.

```yaml
schema_version: 1
artifact_type: plan | spec | card | result | review | diagnosis | architect_review | steward_result
artifact_id: <stable id>
project_id: <registered project id>
work_item_id: <work item id>
task_id: <optional task id>
revision: <integer>
status: draft | frozen | completed | partial | needs_input | needs_fix | blocked | failed
created_at: <UTC RFC3339>
created_by: task-main | project-steward | architect | coder | reviewer | debugger
source_refs: []
related_artifacts: []
summary: <bounded summary>
```

`artifact_id`, `project_id`, `work_item_id`, and references use logical IDs,
not unrestricted filesystem locations.  The writer supplies no hidden
authority: write authority remains a tool/profile concern.

## Plan contract

Plan owner and durable writer: `task-main`.

```yaml
plan_type: standalone | project_linked
objective: <bounded text>
user_outcome: <bounded text>
current_context: <bounded text>
assumptions: []
non_goals: []
scope: {included: [], excluded: []}
work_class: {planning_depth: P0 | P1 | P2, architecture_gate: A0 | A1 | A2}
approach: {steps: []}
risks: []
open_questions: []
validation_strategy: <bounded text>
rollback_strategy: <bounded text>
profile_strategy: <bounded text>
decision_log: []
```

A Plan states outcome, scope, sequencing, risk, and decision context.  It
does not contain per-file construction instructions; those belong in a SPEC.

## SPEC contract and routing

`task-main` owns the Plan-to-SPEC decision and freeze.  `spec_kind` is the
only task discriminator and maps immutably to the executing Profile:

| `spec_kind` | Target Profile |
| --- | --- |
| `implementation` | `coder` |
| `diagnosis` | `debugger` |
| `review` | `reviewer` |
| `architecture` | `architect` |
| `stewardship` | `project-steward` |

The caller does not choose an inconsistent target Profile.  Every SPEC has:

```yaml
spec_kind: implementation | diagnosis | review | architecture | stewardship
objective: <bounded text>
context_refs: []
acceptance_criteria: []
constraints: []
forbidden_actions: []
expected_artifacts: []
handoff_to: task-main
capability_contract:
  source_read: false
  source_write: false
  terminal: false
  bounded_project_command: []
  web: false
  control_plane_write: false
  project_metadata_write: false
  codegraph_read: false
  codegraph_rebuild: false
  deploy: false
  restart: false
  recreate: false
role_payload: {}
```

The runtime internally intersects this requested contract with Profile
maximums; it never trusts a caller to escalate a capability.

### Closed role payloads

| Kind | Required closed payload |
| --- | --- |
| `implementation` | `required_changes`, `behavioral_invariants`, `change_budget` (`max_changed_files`, `allow_create`, `allow_delete`, `allow_move`, `allow_dependency_change`), `allowed_validation_targets`, `forbidden_operations`, `checkpoint_conditions`, `compatibility_requirements` |
| `diagnosis` | `observed_symptoms`, `diagnostic_questions`, `reproduction_context`, `suspected_components`, `initial_hypotheses`, `evidence_plan`, `mutation_policy` (`readonly` or `isolated_reproduction_only`), `confidence_expectation` |
| `review` | `artifacts_under_review`, `review_dimensions`, `acceptance_mapping_required`, `verdict_rules`, `inconclusive_conditions`, `independence_requirements` |
| `architecture` | `review_questions`, `gate_criteria`, `constraints`, `risk_focus`, plus either `design_review` (`problem_statement`, `proposed_design`, alternatives, blast radius, rollback, compatibility, unresolved decisions, validation) or `spec_preflight` (`preflight_dimensions`) |
| `stewardship` | `project_match_questions`, `continuity_inputs`, `artifact_inventory_scope`, `document_update_scope`, `relationship_questions`, `close_completeness_criteria`, `codegraph_status_questions` |

Role payload schemas reject fields belonging to another kind.  The
stewardship payload is target-only in WI-09A and must be added in WI-09C.

### Revision and freeze contract

`create` creates `draft` revision 1.  `update` may change draft fields and
creates a new monotonically increasing revision with a content hash.  `freeze`
validates shared fields plus the selected role payload, writes immutable frozen
content, and binds every dispatched task to `spec_id`, frozen `revision`, and
`spec_hash`.  A frozen revision is never edited; changed intent requires a new
draft revision and a new freeze.  `update` rejects frozen content; `freeze`
rejects invalid/incomplete payloads; `start` rejects a stale or unbound task.

## Card, full result, review, and handoff

Workers deliver **small card + full report**.  The card is a complete bounded
decision summary, not a lossy lifecycle marker.

```yaml
# Card envelope
schema_version: 1
card_type: worker_result
role: coder | debugger | reviewer | architect | project-steward
task_id: <id>
spec_id: <id>
spec_revision: <integer>
spec_hash: <sha256>
project_id: <id>
work_item_id: <id>
outcome: completed | partial | needs_input | failed
verdict: pass | needs_fix | blocked | not_applicable
summary: <bounded text>
key_findings: []
validation_summary: []
scope_status: in_scope | partial | out_of_scope | not_applicable
limitations: []
risks: []
full_report_ref: <logical artifact ref>
evidence_refs: []
needs_full_report_review: false
needs_user_input: []
recommended_next_action: <bounded text>
role_summary: {}
```

`role_summary` is closed: coder (`changed_paths`, `acceptance_status`),
debugger (`root_cause`, `confidence`, `repair_recommendation`), reviewer
(`scope_compliance`, `correctness`, `required_fixes`), architect
(`trade_offs`, `architecture_risks`, `preconstruction_verdict`), or steward
(`project_match`, `continuity_delta`, `artifact_inventory`,
`close_completeness`).

Full reports have the following required sections: Status; Objective; Scope;
Work Performed / Analysis; Evidence; Acceptance Criteria; Deviations;
Limitations; Risks; Recommended Next Action; and Machine-readable Final Block.
The role filenames are `RESULT.md`, `DIAGNOSIS.md`, `REVIEW.md`,
`ARCHITECT_REVIEW.md`, and `STEWARD_RESULT.md` respectively.

```yaml
# Review fields in REVIEW.md / card role_summary
subject_spec_id: <id>
subject_result_id: <id>
verdict: pass | needs_fix | blocked
scope_compliance: <bounded result>
correctness: <bounded result>
validation_evidence: []
regression_risk: <bounded result>
documentation_consistency: <bounded result>
required_fixes: []
optional_improvements: []
residual_risks: []
recommended_decision: <recommendation only>
```

`recommended_decision` is never a durable decision.

```yaml
# All Profile -> task-main handoffs
schema_version: 1
role: coder | debugger | reviewer | architect | project-steward
task_id: <id>
spec_id: <id>
artifact_card_ref: <logical ref>
full_report_ref: <logical ref>
outcome: completed | partial | needs_input | failed
summary: <bounded text>
evidence_refs: []
needs_input: []
needs_full_report_review: false
recommended_next_action: <bounded text>
```

## Card-first consumption policy

`task-main` normally reads `handoff -> card -> durable decision -> ack`.
It must open the full report when any of the following holds:

- `outcome != completed`, or `verdict` is `needs_fix`/`blocked`;
- `needs_full_report_review=true`;
- the card reports a material risk or limitation;
- the card conflicts with established facts;
- task-main needs a subsequent SPEC, detailed user evidence, or close audit.

A low-risk completed `pass` need not cause a full report read.  Steward close
uses the same rule and must not paste a whole long report into task-main.

## Current implementation delta

The current engine names the discriminator `task_kind`, has only four values,
and routes them with a `PROFILE_HINT` map
(`plugin/aota-tools/_task_spec_common.py:43-62,91-93`).  It creates a draft but
does not expose the target `freeze` contract.  Existing coder reports write a
trusted `CARD.json` and `RESULT.md`, but the current card has a smaller,
role-specific shape (`plugin/aota-tools/_coder_report_submit.py:21-67`).
WI-09C owns this migration; no writer or schema was changed in WI-09A.
