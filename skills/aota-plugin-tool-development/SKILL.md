---
name: aota-plugin-tool-development
description: Canonical AOTA Forge workflow for plugin Tool, toolset, Profile exposure, deployment, and lifecycle verification.
category: forge
tags: [aota, forge, tool, toolset, lifecycle]
---

# AOTA Plugin Tool Development

Trigger for adding, changing, repairing, deploying, exposing, or validating an
AOTA Hermes plugin tool, toolset, Profile tool permission, or runtime handler.

1. Read `docs/aota-development/README.md`, the Tool Lifecycle SOP, and Runtime
   Paths/Reloads.
2. Discover actual importing process, transport, path/mount/owner, and env.
3. Define the tool contract; implement bounded code; register it; assign its
   toolset; explicitly configure Profile allow/deny; add fixture; update
   inventory and managed manifest; run source validation.
4. Stop before deployment/activation unless the SPEC authorizes it. If
   authorized: managed deploy, recreate all importing processes, open a new
   session, check final model visibility, exact dispatch, and rollback proof.

Hard stops: never assume a container path, confuse tool with toolset, modify a
Profile without checking transport, call hash parity runtime PASS, recreate
only Agent while legacy WebUI imports the plugin, or substitute a new session
for process reload.

Profile Task credential handlers are part of the plugin lifecycle contract:
there is one global Hermes authority, API keys are read only from global
`.env`, OAuth is discovered by Hermes from global `auth.json`, Profile-local
and default `.env` fallbacks are disabled, and both Agent and legacy WebUI
must be recreated after handler changes. Container paths may differ, but both
must bind the same host authority without copied secrets.

Completion states are separate: `PASS_SOURCE_TOOL_LIFECYCLE`,
`PASS_DEPLOYED_TOOL_LIFECYCLE`, `PASS_RUNTIME_TOOL_LIFECYCLE`, and
`PASS_EXACT_DISPATCH_TOOL_LIFECYCLE`.

`AOTA_PLUGIN_TOOL_DEVELOPMENT_SKILL_PASS`

## Deployment and Runtime Guidance

- Profile runtime assembly is manifest-driven. The canonical assembly manifest
  (`deploy/profile-runtime-assembly.yaml`) declares active Skills per Profile;
  runtime projections are built from this manifest, not from file-system
  enumeration.
- Profile-local plugin and Skill projections are required when the Hermes
  loader uses profile home (`~/.hermes/profiles/<name>/plugins/`,
  `~/.hermes/profiles/<name>/skills/`). Verify the projection exists with hash
  parity against the canonical source.
- Config declaration (`profiles/<name>/config.yaml`) is not runtime availability
  evidence. A tool may be declared in config but fail to load due to missing
  module, import error, or registration failure.
- SOUL declaration is not Skill-load evidence. A Skill may be referenced in
  SOUL.md but not loaded if the Skill file is missing or the cache is stale.
- Runtime verification requires loaded tool/Skill evidence (tool list, dispatch
  result, Skill-loaded prompt inspection) or actual Profile Task execution.
  Hash parity between source and runtime files is deploy evidence, not runtime
  evidence.
- Managed deploy (`aota_forge_plan_package.py deploy` or `deploy.sh`) is the
  only authorized deployment path. Manual `cp`/`rsync` bypasses backup, receipt,
  and parity verification and is forbidden.
- After a managed deploy, all importing processes (hermes-agent, hermes-webui)
  must be recreated (`docker compose up -d --no-deps --force-recreate`).
  Agent-only recreate is allowed only when evidence proves WebUI is unaffected.
- Host/Codex construction vs Hermes runtime verification boundary: source PASS
  does not imply deploy PASS, and deploy PASS does not imply runtime PASS.
- Project Steward owns documentation continuity, not Skill source
  implementation. Skill content and lifecycle belong to the owning Profile
  (task-main, coder).
- task-main must not manually fabricate deployment success. If a deployment
  receipt is missing or the runtime evidence contradicts the declared state,
  report the discrepancy; do not create a synthetic pass.
- Runtime-generated files (logs, snapshots, receipts, backups) are not managed
  source. They must not be added to the managed manifest or committed to Git.
- No generic `/aota-runtime` file access. All runtime reads use bounded tools
  (`aota_runtime_info`, `aota_active_task_artifact_open`).
