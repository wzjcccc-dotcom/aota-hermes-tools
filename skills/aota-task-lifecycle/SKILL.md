---
name: aota-task-lifecycle
description: AOTA Profile Task binding contract from frozen SPEC through result, handoff, decision, and acknowledgement.
category: orchestration
---

# AOTA Task Lifecycle

Profile Task start derives `AOTA_WORKSPACE_ID`, `AOTA_PROJECT_ID`, roots,
workspace decision ID, and frozen SPEC identity from the frozen SPEC; callers
cannot override them.  `meta.json`, Card, Result, and Handoff must preserve
the same binding.  On any mismatch workers stop with `needs_input` and use
`binding_invalid`; they never guess another workspace.  task-main validates
the handoff binding and records the durable decision before acknowledgement.

The launcher also preserves a separate Hermes path binding:
`global_hermes_home`, `parent_profile_home`, and `target_profile_home`.
`HERMES_HOME` may identify the current parent Profile, but it is never passed
to credential bootstrap as the global authority. Early bootstrap/finalizer
failure must leave the bounded redacted worker log, canonical failure receipt,
and failure handoff; the fallback handoff does not require worker CARD/RESULT.
Canonical `spec_hash` and legacy `spec_sha256` are separate frozen bindings and
must both remain intact in receipts and handoffs.  `approval_status` is
`not_required` for diagnosis, review, architecture, and stewardship; only
implementation uses the approval API after freeze.

Credential bootstrap is global-authority-only: API keys come from the global
Hermes `.env`, OAuth is discovered from global `auth.json`, and no target,
default, parent-local, or arbitrary caller `.env` is read. The worker child
environment is bounded and explicitly sets `HERMES_HOME` to the normalized
global root; `-p <resolved_profile>` is the sole Profile selector.

The launcher manifest also freezes `resolved_profile`, provider, model,
Profile-config digest, and credential source type. A digest/provider/model
mismatch is `profile_launch_binding_mismatch`; parent model/provider values are
not a fallback. Early launcher failures always leave a bounded worker log,
failure receipt, and failure handoff, even when no worker artifact exists.

## Active worker context preflight

Every worker reads its own frozen `SPEC`, `SCOPE`, and bounded `BINDING` through
`aota_active_task_artifact_open` before project-tier work. The three results
must agree on workspace/task/start/profile/spec identity. Missing, mismatched,
or denied reads fail closed with `needs_input`/`blocked`; workers never guess,
switch Profile, or use terminal/file fallback. Subject reads are available only
when the frozen reviewer/debugger/architect binding explicitly permits them.

## Canonical scope and task isolation

The frozen project scope is read from `spec.payload` by the shared
`_task_spec_scope.py` extractor. Payload keys take precedence even when their
value is `[]`; the bounded legacy top-level fallback is observable through
`scope_source=legacy_top_level`. `scope.json` is an atomic, immutable projection
bound to the SPEC id, revision, hashes, task/start identity, and `scope_digest`.

Task start captures `workspace-baseline.json`, including pre-existing tracked
dirty and untracked paths plus content fingerprints. Finalization treats
trusted, identity-bound `scope-events.jsonl` as the primary worker evidence and
reports only baseline deltas as postflight observations. A workspace-wide Git
diff is never a current-task worker action by itself. Missing or foreign event
identity is counted and unattributed project deltas fail closed.

## Deployment and Runtime Guidance

- Profile runtime assembly is manifest-driven. The canonical assembly manifest
  (`deploy/profile-runtime-assembly.yaml`) declares active Skills per Profile.
- Profile-local plugin and Skill projections are required when the Hermes loader
  uses profile home (`~/.hermes/profiles/<name>/plugins/`,
  `~/.hermes/profiles/<name>/skills/`).
- Config declaration is not runtime availability evidence. A toolset may be
  declared in config but fail to register at runtime.
- SOUL declaration is not Skill-load evidence. A Skill referenced in SOUL.md may
  not be loaded if the file is missing or the cache is stale.
- Runtime verification requires loaded tool/Skill evidence or actual Profile
  Task execution. Hash parity between source and runtime is deploy evidence,
  not runtime evidence.
- Managed deploy and recreate requirements apply per the lifecycle inventory.
  All importing processes must be recreated after changes.
- Host/Codex construction vs Hermes runtime verification boundary: source PASS
  does not imply runtime PASS.
- Project Steward owns documentation continuity, not Skill source implementation.
- task-main must not manually fabricate deployment success. If a deployment
  receipt is missing or runtime evidence contradicts declared state, report
  the discrepancy.
- Runtime-generated files (logs, snapshots, receipts, backups) are not managed
  source.
- No generic `/aota-runtime` access; use bounded runtime tools
  (`aota_runtime_info`, `aota_active_task_artifact_open`).
