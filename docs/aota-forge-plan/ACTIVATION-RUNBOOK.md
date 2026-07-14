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

The script copies only the managed plugin, five profiles, and skills. It does
not restart or recreate a service.

## Step 5 — Verify deployed hashes

```bash
./scripts/verify-deploy.sh
```

Expected: `DEPLOY_VERIFY_PASS`.

## Step 6 — Recreate Agent only

Only after the Human Checkpoint and successful verification:

```bash
cd /home/latios/hermes-stack
docker compose up -d --no-deps --force-recreate hermes-agent
```

Expected: `hermes-agent` recreated; `hermes-webui` unchanged. This command is
intentionally not run by PF-WI-07-2.

## Step 7 — Registration verify

Use the approved read-only Hermes registration inspection and confirm:

```text
version=0.17.6
tools=35
toolsets=18
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

## Step 11 — Stop / rollback

```bash
./scripts/rollback-aota-forge-plan-activation.sh <backup-dir>
cd /home/latios/hermes-stack
docker compose up -d --no-deps --force-recreate hermes-agent
```

The second command is Human-only and is required only after rollback parity
verification. Never restart `hermes-webui` as part of this package.
