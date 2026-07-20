---
name: aota-skill-development
description: Canonical AOTA Forge workflow for Skill contract, scope, deployment, visibility, cache refresh, and behavior proof.
category: forge
tags: [aota, forge, skill, lifecycle, profile]
---

# AOTA Skill Development

Trigger for adding, changing, deploying, binding, or validating an AOTA Skill,
Profile active Skill, SOUL reference, or Skill-loader configuration.

Read the development README, Skill Lifecycle SOP, and Runtime Paths/Reloads.
Define owner, trigger, scope, and non-goals; choose global/profile-local
strategy; create canonical source; configure Profile visibility/disabled policy;
update manifest and inventory; verify deployed paths and prompt/context
visibility; refresh importing processes as required; open a new session; then
perform the bounded behavior smoke.

Never treat a SOUL mention, global presence, `SKILL.md` existence, or hash
parity as activation. Do not assume named Profiles inherit global Skills or
that `/reload-skills` clears prompt cache.

`AOTA_SKILL_DEVELOPMENT_SKILL_PASS`

## Deployment and Runtime Guidance

- Profile runtime assembly is manifest-driven. The canonical assembly manifest
  (`deploy/profile-runtime-assembly.yaml`) declares active Skills per Profile.
- Profile-local plugin and Skill projections are required when the Hermes loader
  uses profile home (`~/.hermes/profiles/<name>/plugins/`,
  `~/.hermes/profiles/<name>/skills/`).
- Config declaration is not runtime availability evidence. A Skill declared in
  config may not be loaded if the file is missing or the cache is stale.
- SOUL declaration is not Skill-load evidence. A Skill referenced in SOUL.md may
  not be loaded if the file is missing, the cache is stale, or the loader
  rejects it.
- Runtime verification requires loaded tool/Skill evidence (tool list, dispatch
  result, Skill-loaded prompt inspection) or actual Profile Task execution.
  Hash parity between source and runtime files is deploy evidence, not runtime
  evidence.
- Managed deploy and recreate requirements: after a managed deploy, all
  importing processes must be recreated. Agent-only recreate is allowed only
  when evidence proves WebUI is unaffected.
- Host/Codex construction vs Hermes runtime verification boundary: source PASS
  does not imply deploy PASS, and deploy PASS does not imply runtime PASS.
- Project Steward owns documentation continuity, not Skill source
  implementation. Skill content and lifecycle belong to the owning Profile.
- task-main must not manually fabricate deployment success. If a deployment
  receipt is missing or runtime evidence contradicts declared state, report
  the discrepancy.
- Runtime-generated files (logs, snapshots, receipts, backups) are not managed
  source and must not be added to the managed manifest.
- No generic `/aota-runtime` access; use bounded tools
  (`aota_runtime_info`, `aota_active_task_artifact_open`).
