# AOTA Forge Plan Activation Manifest

Status: PF-WI-07-2 package construction only. No deployment, restart, reload,
recreate, trusted-env activation, live worker, live Plan/SPEC mutation, or
Level 1/Level 2 smoke was executed.

## Target

- activation target version: `0.17.6`
- expected current runtime version before activation: `0.17.0`
- tools: `35`
- toolsets: `18`
- Agent service: `hermes-agent`
- WebUI service: `hermes-webui`
- tool execution service: `hermes-agent`
- trusted env injection target: `hermes-agent` only
- private env source: `/home/latios/hermes-stack/.env`
- plugin runtime root: `/home/latios/.hermes/plugins/aota-tools`
- profiles runtime root: `/home/latios/.hermes/profiles`
- skills runtime root: `/home/latios/.hermes/skills`
- container plugin path: `/home/hermes/.hermes/plugins/aota-tools`
- container profiles path: `/home/hermes/.hermes/profiles`
- container skills path: `/home/hermes/.hermes/skills`

## PF-WI-07A security requirements

- `_profile_task_start.py` removes exactly `AOTA_TRUSTED_PRINCIPAL`,
  `AOTA_TRUSTED_AUTHORITIES`, and `AOTA_TRUSTED_WORKSPACE_ID` from the worker
  environment copy and shell child.
- `architect`, `reviewer`, `coder`, and `debugger` deny
  `aota_work_intake`, `aota_plan_read`, and `aota_plan_write`.
- `task-main` retains those three Plan/Foundation toolsets.
- worker marker authorization remains fail-closed with
  `PLAN_WRITE_FORBIDDEN_FOR_WORKER`.

## Preconditions

1. PF-WI-07-3 Hermes read-only review approves this package.
2. Human confirms the intended Git commit boundary.
3. `./scripts/check-aota-forge-plan-readiness.sh` returns
   `READINESS_PASS` or `READINESS_PASS_WITH_UNCOMMITTED_CHANGES`.
4. The private `.env` contains the activation values, but those values are
   never copied into tracked files, receipts, logs, prompts, Plan, SPEC, or
   task metadata.

## Package commands

- backup: `./scripts/backup-aota-forge-plan-activation.sh`
- deploy: `./scripts/deploy.sh`
- verify: `./scripts/verify-deploy.sh`
- rollback: `./scripts/rollback-aota-forge-plan-activation.sh <backup-dir>`

The deploy and rollback scripts emit `ACTION_REQUIRED`; neither performs a
restart or container operation.

## Stop conditions

Stop and do not activate if readiness blocks, the source/runtime hash receipt
does not match, WebUI receives any `AOTA_TRUSTED_*` declaration, or any worker
smoke shows a trusted variable. Roll back only through the manifest-driven
package and wait for the Human Checkpoint before recreating `hermes-agent`.

## External stack evidence

`/home/latios/hermes-stack` is not an independent Git repository. Its current
activation inputs therefore remain outside the canonical AOTA commit and must
be retained by Human as external stack state:

- `docker-compose.yml`
  - SHA-256: `ea1a657ea67998a032d0e8bffc94b3ac3beecae406af66b45f553766d20e8a80`
  - diff summary: no Git baseline is available in this directory; current
    file is recorded by exact hash only.
- `.env.example`
  - SHA-256: `96f3658ca0c64e9455cee98d75e1986e441ee4496c288111d0f34db10882d236`
  - diff summary: no Git baseline is available in this directory; current
    file is recorded by exact hash only.

The private `/home/latios/hermes-stack/.env` is intentionally not read,
hashed, copied, displayed, or staged. The existing network binding/documentation
drift is non-blocking and is not changed by this package.
