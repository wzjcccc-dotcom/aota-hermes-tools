# AGENTS.md — AOTA Forge Repository Instructions

This file defines repository-wide construction rules for AI coding agents working in:

```text
/home/latios/workspace/aota-hermes-tools
```

Task-specific scope, acceptance criteria, write boundaries, and validation requirements belong in the active frozen SPEC or explicit work instruction. This file does not replace the AOTA Plan, SPEC, Skills, Profile contracts, or canonical plugin schemas.

## Repository role

This repository is the canonical source of truth for AOTA Forge:

* AOTA narrow-tools plugin
* Named Profiles
* Active and Reference Skills
* Plan, SPEC, Profile Task, handoff, evidence, and closure contracts
* Project stewardship and workspace binding
* Managed deployment, verification, and rollback tooling

The canonical source repository is not the live runtime.

## Authority order

When instructions conflict, use this order:

1. Explicit operator safety boundary
2. Frozen AOTA SPEC and trusted task binding
3. Canonical source contracts and schemas
4. Profile SOUL and Active Skill
5. This `AGENTS.md`
6. General repository documentation
7. Inference

Do not silently resolve a material conflict. Stop and report the exact conflict.

## Core engineering principles

* Use the smallest implementation that satisfies the frozen scope.
* Reuse existing canonical helpers and contracts.
* Do not create parallel registries, lifecycle systems, validation frameworks, or deployment systems.
* Each invariant must have one canonical authority.
* Tool schema, description, handler, docs, receipts, and verifier must agree.
* Preserve backward compatibility unless the SPEC explicitly authorizes a breaking change.
* Fail closed on ambiguity, stale revisions, identity mismatch, scope mismatch, or missing authority.
* Never use LLM inference as a substitute for ID, hash, revision, binding, approval, or scope verification.
* Keep control-plane identifiers machine-managed where possible.
* Do not broaden scope to clean up unrelated technical debt.

## Profile and authority boundaries

* `task-main` owns Plan/SPEC orchestration, dispatch, durable decisions, and closure.
* Workers never mutate Plans or make final orchestration decisions.
* Workers never start or delegate another Profile Task.
* `project-steward` may perform only frozen-SPEC-authorized project governance operations.
* `architect`, `reviewer`, and `debugger` are read-only.
* Coder mutation must stay within the frozen `write_scope`.
* Do not grant a Profile tools merely because another Profile has overlapping read needs.
* Tool availability and required authority determine execution ownership.

## Workspace and project identity

* `workspace_id` is a registered filesystem-authority registry key.
* `project_id` is a project identity validated by `.aota/project.yaml` inside that workspace.
* They may be equal but must never be assumed equal.
* A workspace may contain multiple projects.
* Neither ID is an arbitrary filesystem path.
* `/aota-runtime` is a control-plane artifact root, not a project-tier workspace.
* Workers do not generate, switch, or rediscover frozen workspace/project bindings.
* Existing active task or frozen SPEC bindings take precedence over discovery.

## Plan, SPEC, and task rules

* P0 uses a standalone SPEC and does not create an administrative Plan.
* P1/P2 use the canonical Plan lifecycle.
* A Profile Task executes one frozen SPEC.
* A completed Worker or Reviewer pass does not automatically close a Work Item.
* Frozen SPECs are immutable.
* Fixes require a new revision or a new SPEC.
* Implementation approval binds to the exact canonical revision and hashes.
* Do not auto-approve.
* Do not auto-refresh and freeze an advanced Plan/SPEC without review.
* Do not restart a task merely because Plan linking or evidence recording failed.

## Hash and revision rules

* Canonical `spec_hash` and raw file-content `spec_sha256` are distinct bindings.
* Do not merge, alias, overwrite, or substitute one for the other.
* Revision and hash validation must remain exact.
* Compatibility aliases may be accepted internally, but canonical tool schemas should expose one clear interface.
* LLM callers should not be required to manually copy hashes or long identifiers when the control plane can resolve them safely.

## Tool implementation rules

* Prefer bounded, narrow, deterministic tools.
* Do not add unrestricted terminal or arbitrary filesystem access.
* All path arguments must be validated and remain inside the authorized root and scope.
* Reject path traversal, symlink escape, control characters, invalid IDs, and unknown fields.
* Use `additionalProperties: false` for strict schemas unless an existing canonical contract requires otherwise.
* Error responses must use stable bounded error codes.
* Avoid long free-form error messages when structured next actions are available.
* Do not let individual tools define incompatible meanings for `current`, `active`, `subject`, or similar references.
* Shared semantic references must use one canonical resolver.

## Skill rules

* Active Skills contain operational summaries.
* Reference Skills contain detailed contracts loaded only when needed.
* Do not duplicate a full Reference Skill contract into an Active Skill.
* Do not introduce a second Skill registry or projection mechanism.
* Do not load or modify unrelated Skills.
* When changing a Skill, follow the canonical Skill-development contract.
* When changing plugin tools or schemas, follow the canonical plugin-tool-development contract.

## Validation rules

Default validation is limited to:

* syntax and parse checks
* `py_compile`
* schema validation
* existing contract verifiers
* small isolated fixtures or smoke checks

Do not add or run broad pytest suites unless the SPEC explicitly requires them.

Source validation does not imply runtime validation.

Do not claim runtime or live PASS without:

* managed deploy
* required reload/restart
* active runtime parity
* explicit live smoke evidence

## Deployment and runtime safety

Do not perform any of the following unless explicitly authorized by the operator and frozen SPEC:

* deploy
* rollback
* reload
* restart
* recreate
* Docker mutation
* Host service mutation
* credential or secret changes
* runtime writes
* live Profile Task execution
* Git commit or push

Deployment authority is the Host runtime root declared by the repository. Container paths are runtime views, not deployment targets.

Always preserve the manual restart/reload gate.

## Git and dirty-worktree rules

* Inspect existing status before modification.
* Preserve unrelated dirty paths.
* Do not reset, checkout, stash, clean, discard, or overwrite unrelated changes.
* Do not amend commits.
* Do not commit or push unless explicitly instructed.
* Report every changed path.
* Avoid repository-wide formatting.

## Documentation rules

* Update documentation only when directly affected by the implementation.
* Do not manually maintain counts that can be generated or verified.
* Distinguish current architecture, runtime state, compatibility behavior, and historical milestones.
* Do not present source preparation as live runtime activation.
* Keep README concise; detailed contracts belong in canonical docs or Skills.
* Historical work-item labels should not remain the primary description of current plugin capability.

## Secrets

* Never print, read, copy, modify, or commit secrets.
* Provider keys must remain environment references.
* Do not inspect unrelated `.env`, credential, token, or auth files.
* Redact accidental secret-like values from reports.

## Required final report

Every construction task must report:

```text
STATUS
SUMMARY
FILES_CHANGED
VALIDATION_PERFORMED
LIMITATIONS
NOT_PERFORMED
UNRELATED_DIRTY_PATHS_PRESERVED
```

Do not claim completion without evidence.

## Stop conditions

Stop rather than improvising when:

* The frozen scope conflicts with the workspace.
* A required authority or binding is unavailable.
* A change requires runtime mutation not authorized by the task.
* A compatibility-preserving implementation is not possible.
* The task would require modifying files outside `write_scope`.
* Canonical contracts disagree and no authority order resolves them.
* Required evidence cannot be produced with allowed tools.
