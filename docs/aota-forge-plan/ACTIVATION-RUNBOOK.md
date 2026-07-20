# AOTA Forge Plan Activation Runbook

This is a command package for PF-WI-07-4. It is not an activation command
sequence for PF-WI-07-2. Do not run it until PF-WI-07-3 review and the Human
Checkpoint are complete.

## Step 0 — Git review

```bash
cd /home/latios/workspace/aota-hermes-tools
git status --short
git diff --check
git diff --stat
```

## Step 1 — Readiness

```bash
./scripts/check-aota-forge-plan-readiness.sh
```

Expected: `READINESS_PASS` or `READINESS_PASS_WITH_UNCOMMITTED_CHANGES`.
Uncommitted changes are a warning unless the Human Checkpoint requires a
commit before deployment.

Read the current [runtime paths and reload matrix](../aota-development/AOTA-RUNTIME-PATHS-AND-RELOADS.md)
and `deploy/aota-lifecycle-inventory.yaml`. Readiness invokes both lifecycle
source verifiers; a lifecycle failure blocks managed deployment.

## Step 2 — Backup

```bash
./scripts/backup-aota-forge-plan-activation.sh
```

Record the printed timestamped backup directory. Do not copy or display the
private `/home/latios/hermes-stack/.env`.

## Step 3 — Configure private env

Set these names only in `/home/latios/hermes-stack/.env`, with values supplied
by the Human Checkpoint:

```text
AOTA_TRUSTED_PRINCIPAL
AOTA_TRUSTED_AUTHORITIES
AOTA_TRUSTED_WORKSPACE_ID
```

Do not put values in Git, `.env.example`, prompts, Plan, SPEC, receipts, or
task metadata. The Compose declaration is Agent-only.

## Step 4 — Deploy source/config

```bash
./scripts/deploy.sh
```

The script copies only the managed plugin, six profiles, and skills. It does
not restart or recreate a service.

## Step 5 — Verify deployed hashes and Profile assembly

```bash
./scripts/verify-deploy.sh
```

Expected before activation: `DEPLOY_VERIFY_PASS` followed by
`PRE_ACTIVATION_VERIFY=pass` and `ACTIVATION_ALLOWED=yes`.
A missing bootstrap snapshot is reported as `pending_activation` at this
stage; projection and static parity failures remain blocking.

After verification, perform a post-write scope propagation check: confirm that
scope.json for the active task projects the correct write_scope/read_scope and
that current-task scope events are isolated from foreign task identity and
process session. This step ensures scope boundaries are not cross-contaminated
by pre-existing dirty paths or unattributed workspace deltas.

## Step 6 — Recreate importing processes

Only after the Human Checkpoint and successful verification:

```bash
cd /home/latios/hermes-stack
docker compose up -d --no-deps --force-recreate hermes-agent
docker compose up -d --no-deps --force-recreate hermes-webui
```

For plugin module, handler, schema, `__init__.py`, `plugin.yaml`, toolset,
global Skill, Skill-loader, or shared Profile/Skill runtime changes, both are
required in the current legacy topology. Agent-only activation is allowed only
when evidence proves legacy WebUI neither imports nor consumes the change.
WebUI-only activation likewise requires evidence Agent is unaffected. Use the
package's inventory-derived activation targets; do not use `docker compose down`.
These commands are intentionally not run by this source-level work item.

After both processes are recreated, run the strict post-activation check:

```bash
python3 /home/latios/workspace/aota-hermes-tools/scripts/profile_runtime_assembly.py post-activation
```

Expected: `POST_ACTIVATION_VERIFY=pass` and
`PROFILE_ASSEMBLY_POST_ACTIVATION_PASS`. A failure requires rollback and a
second recreate to reactivate the previous managed version.

## Step 7 — Registration verify

Use the approved read-only Hermes registration inspection and confirm:

```text
version=0.17.6
tools=59
toolsets=28
```

## Step 8 — Role isolation verify

Confirm `task-main` has the three Plan/Foundation toolsets and each of
`architect`, `reviewer`, `coder`, and `debugger` has all three disabled.

## Step 9 — Worker trusted-env absence

Run only the minimal worker smoke from
`docs/aota-forge-plan/LEVEL-1-SMOKE-PLAN.md`. It may report only the three
absence markers defined there; do not print any other environment values.

## Step 10 — Temporary P1 smoke

Run only the temporary, non-production P1 sequence in the Level 1 plan. Do
not start an implementation worker.

## Step 10a — New session visibility

After any Profile, SOUL, Skill, or tool-definition activation, create a new
session. Verify registry/toolset/Profile resolution, final model tool list, and
exact dispatch separately; deployed hashes are not runtime proof.

## Step 11 — Stop / rollback

```bash
./scripts/rollback-aota-forge-plan-activation.sh <backup-dir>
cd /home/latios/hermes-stack
docker compose up -d --no-deps --force-recreate hermes-agent
docker compose up -d --no-deps --force-recreate hermes-webui
```

The recreate commands are Human-only and required only after rollback parity
verification when the rollback changed an importing runtime surface.

`AOTA_ACTIVATION_RUNBOOK_PASS`
