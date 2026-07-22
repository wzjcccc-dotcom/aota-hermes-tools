# AOTA Project Lifecycle Contract

## Canonical source

The minimal project lifecycle contract has exactly one canonical source:

```
plugin/aota-tools/_project_lifecycle_contract.py
```

It is also declared in `deploy/aota-lifecycle-inventory.yaml` under the
`project_lifecycle_contract` key.  Skills, SOUL files, and verifiers reference
or verify the canonical source; no second authority or duplicated list is
created.

## Contract elements

### Dispatch invariant

`PROJECT_AND_PROFILE_MUTATION_REQUIRES_TASK_MAIN_DISPATCH`

Protected project/profile mutation requires a task-main dispatched Profile
Task with a frozen SPEC.  This covers ONLY protected mutation classes, NOT
all filesystem writes.  Worker runtime artifact writes are exempt.

### Protected mutation classes

- `project_initialization` — scaffold/metadata/registry creation
- `project_git_lifecycle` — Git init/stage/commit/push
- `project_codegraph_lifecycle` — CodeGraph init/index/reindex
- `project_registry_mutation` — registry add/remove
- `project_closure` — authoritative project closure
- `project_metadata_mutation` — bounded project metadata mutation

### Runtime artifact write exemptions

These are NOT protected mutations and do NOT require task-main dispatch:

- `CARD` — worker role card (observation)
- `RESULT` — worker full report (observation)
- `worker_outcome` — terminal outcome submission
- `completion_receipt` — trusted finalizer receipt
- `handoff` — durable handoff artifact
- `review_decision` — reviewer decision artifact
- `task_runtime_meta` — task meta.json execution block
- `bounded_logs` — worker/finalizer logs
- `bounded_temporary_state` — scope events, baseline, temporary state

### task-main dispatch authority vs Project Steward execution ownership

**task-main authority** (originates dispatch):
- dispatch, spec_creation, spec_freeze, task_approval, task_start,
  durable_decision, work_item_closure, workspace_selection

**Project Steward authority** (executes dispatched operations):
- docs_update, artifact_link, registry_refresh, close_checks,
  context_prepare, relationship_resolve

Project Steward can execute dispatched lifecycle operations but cannot
originate un-dispatched protected mutation, create a SPEC, or self-declare
authoritative success.

### Initialization states

- `initialized_core` — scaffold/metadata/registry complete, Git/CodeGraph
  not yet guaranteed
- `initialized` — initialized_core + Git repository + CodeGraph index +
  aggregate verification + authoritative receipt
- `failed` — initialization failed at some stage

### Initialization receipt minimum fields

- `schema_version`
- `project_id`
- `workspace_id`
- `state` — one of `initialized_core`, `initialized`, `failed`
- `git_summary` — present/absent + commit head if initialized
- `codegraph_summary` — present/absent + index status if initialized
- `authority` — `trusted_finalizer` or `authoritative_receipt`
- `completed_at`

CARD/RESULT are worker observations; the trusted finalizer / authoritative
receipt is the final lifecycle result.  Project Steward cannot self-declare
authoritative success.

### Codex escalation boundary

- Current: `CURRENT_CODEX_FALLBACK_DEPENDENCY=REQUIRED` — Git/init tools not
  yet complete, so Codex fallback is required for host/infrastructure-level
  Git initialization.  This is a temporary gap.
- Target: `TARGET_CODEX_DEFAULT_PROJECT_OPERATOR=NOT_REQUIRED` — once
  bounded Git/init tools are implemented as Profile Task tools, Codex
  fallback for project Git operations is not required.
- Boundary: `CODEX_BOUNDARY=host_infra_escalation` — Codex is a Host/infra
  escalation path, not a general project operator or a replacement for
  task-main dispatch.

The temporary gap is NOT written as long-term architecture policy.

## Fail-closed reject rules

The contract rejects:

1. `non_task_main_protected_mutation_spec` — non-task-main profile attempting
   protected mutation
2. `unfrozen_spec` — SPEC not frozen
3. `binding_mismatch` — task/spec/binding identity mismatch
4. `profile_mismatch` — profile does not match resolved routing
5. `artifact_exemption_used_for_project_source_write` — runtime artifact
   exemption used to write project source
6. `steward_mutation_missing_binding` — steward mutation without frozen SPEC
   binding
7. `initialized_receipt_missing_git_codegraph_summary` — initialized receipt
   missing Git/CodeGraph summary
8. `receipt_authoritative_wrong_reconciliation_source` — receipt authority
   is not trusted_finalizer or authoritative_receipt
9. `unknown_initialization_state` — state not in INITIALIZATION_STATES
10. `unknown_protected_mutation_class` — mutation class not in
    PROTECTED_MUTATION_CLASSES

## Verification

`scripts/verify-project-lifecycle-contract-minimum.py` covers Cases A-H:

- **Case A**: Dispatch invariant has exactly one canonical source
- **Case B**: Protected mutation classes and runtime artifact exemptions are separated
- **Case C**: Steward execution ownership and task-main dispatch authority are separated
- **Case D**: initialized_core and initialized are distinguished
- **Case E**: Initialization receipt minimum fields and authority maintained
- **Case F**: Codex escalation boundary distinguishes current from target
- **Case G**: Single authority — no duplicated version lists across Python files
- **Case H**: Fail-closed reject rules present and match section 10.2

## Follow-up work items (not implemented in this task)

- PCF-WI-NEW-PROJECT-INITIALIZATION-CORE: scaffold/metadata/registry tools
- PCF-WI-CONTROLLED-GIT-LIFECYCLE-TOOLS: Git init/stage/commit/push tools
- PCF-WI-CONTROLLED-CODEGRAPH-LIFECYCLE-TOOLS: CodeGraph init/index/reindex tools
- PCF-WI-PROJECT-INITIALIZATION-CLOSURE: aggregate verification + authoritative receipt

These work items will implement the tools that enforce this contract; the
contract itself is the common foundation.