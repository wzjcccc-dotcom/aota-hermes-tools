---
name: aota-runtime-smoke-verification
description: AOTA runtime smoke verification — bounded process-reload, new-session, and registration checks.
category: forge
tags: [aota, runtime, smoke, verification, deployment]
---

# AOTA Runtime Smoke Verification

Trigger for verifying that a deployed Plugin, Profile, or Skill change is
actually loaded and active in the Hermes runtime, as distinct from source or
deploy verification.

## Identity and boundary

Provide bounded runtime smoke verification after a managed deploy and process
recreate. Never perform source mutation, deploy, restart, recreate, CodeGraph
rebuild, Git write, or Profile Task dispatch.

Use only bounded read tools: `aota_runtime_info`, `aota_active_task_artifact_open`,
and project read tools. Runtime verification requires loaded tool/Skill evidence
or an actual Profile Task execution — not config declaration, SOUL declaration,
or hash parity.

## Stages

### Stage 1 — Registration smoke

After `force-recreate` of importing processes and a new session:

1. Confirm Hermes registration reports the expected version (`0.17.6`),
   tools count (`59`), and toolsets count (`28`).
2. Verify profile-level tool allow/deny matches the canonical lifecycle
   inventory (e.g. worker profiles deny `aota_work_intake`/`aota_plan_read`/
   `aota_plan_write`).
3. Verify that `aota_coder_file_mutation` and `aota_coder_command` are present
   for coder and absent for non-coder worker profiles.

### Stage 2 — Skill visibility smoke

Open a new Hermes session with the target Profile. Verify:

1. The expected AOTA Skills are loaded in prompt context.
2. The Skill-loaded guidance matches the canonical SKILL.md content.
3. No stale Skill from a previous deployment remains visible.

**New session ≠ process reload.** A process reload re-registers tools but does
not clear the prompt Skill cache.

### Stage 3 — Exact dispatch smoke

For each tool registered in the lifecycle inventory:

1. Confirm the tool appears in the Profile's tool list.
2. Dispatch the tool with minimally valid arguments.
3. Verify the expected behavior (readonly returns data, mutation returns
   written/patched, etc.).

### Stage 4 — Profile Task smoke

For profiles that execute Profile Tasks (coder, debugger, reviewer):

1. Create a minimal frozen SPEC that exercises the profile's core tools.
2. Start the Profile Task and wait for terminal receipt.
3. Verify the worker outcome (`completed`/`failed`/`needs_input`) matches
   expectations.
4. Open the handoff and confirm the CARD binding matches the frozen SPEC.

**Profile Task smoke ≠ source or deploy verification.** A completed source
verification does not guarantee the runtime will execute the worker correctly.

## Deployment and Runtime Guidance

- Profile runtime assembly is manifest-driven. The canonical assembly manifest
  (`deploy/profile-runtime-assembly.yaml`) declares active Skills per Profile;
  runtime projections are built from this manifest, not from file-system
  enumeration.
- Profile-local plugin and Skill projections are required when the Hermes
  loader uses the profile home (`~/.hermes/profiles/<name>/plugins/`,
  `~/.hermes/profiles/<name>/skills/`) rather than the global root. Verify
  the projection exists with hash parity against the canonical source.
- Config declaration (`profiles/<name>/config.yaml`) is not runtime availability
  evidence. A tool may be declared in config but fail to load due to missing
  module, import error, or registration failure.
- SOUL declaration is not Skill-load evidence. A Skill may be referenced in
  SOUL.md but not loaded if the Skill file is missing, the cache is stale, or
  the loader rejects it.
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
- Host/Codex construction verifies source validity. Hermes runtime verification
  confirms active loading. These are separate boundaries; a PASS in one does not
  imply PASS in the other.
- Project Steward owns documentation continuity, not Skill source implementation.
  Skill content and lifecycle belong to the owning Profile (task-main, coder).
- task-main must not manually fabricate deployment success. If a deployment
  receipt is missing or the runtime evidence contradicts the declared state,
  report the discrepancy; do not create a synthetic pass.
- Runtime-generated files (logs, snapshots, receipts, backups) are not managed
  source. They must not be added to the managed manifest or committed to Git.
- No worker has generic `/aota-runtime` file access. All runtime reads use
  bounded tools (`aota_runtime_info`, `aota_active_task_artifact_open`).

## Stop rules

Stop with `needs_input` for:
- Missing new session (cannot verify post-deploy visibility without one).
- Unrecreated importing processes (Agent/WebUI).
- Evidence of stale Skills or conflicting tool registrations.
- Any request to perform deployment, restart, recreate, CodeGraph rebuild,
  Git write, or Profile Task dispatch (this skill only verifies).

`AOTA_RUNTIME_SMOKE_VERIFICATION_SKILL_PASS`
