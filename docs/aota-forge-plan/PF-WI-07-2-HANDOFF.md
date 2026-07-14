# PF-WI-07-2 Handoff to PF-WI-07-3

This package is ready for Hermes independent read-only review. It does not
contain a Hermes verdict and does not authorize Human activation.

## Review inputs

- `deploy/aota-forge-plan-files.yaml`
- `scripts/check-aota-forge-plan-readiness.sh`
- `scripts/aota_forge_plan_package.py`
- `scripts/deploy.sh`
- `scripts/backup-aota-forge-plan-activation.sh`
- `scripts/rollback-aota-forge-plan-activation.sh`
- `scripts/verify-deploy.sh`
- `/home/latios/hermes-stack/docker-compose.yml`
- `/home/latios/hermes-stack/.env.example`
- `docs/aota-forge-plan/ACTIVATION-MANIFEST.md`
- `docs/aota-forge-plan/ACTIVATION-RUNBOOK.md`
- `docs/aota-forge-plan/LEVEL-1-SMOKE-PLAN.md`

## Review questions

- Does the manifest cover all plugin Python files, `plugin.yaml`, five
  profiles, and every `skills/*/SKILL.md` without managing Plan/SPEC/audit or
  Profile Task artifacts?
- Are backup and rollback operations limited to manifest paths, with exact
  parity, checksum evidence, traversal rejection, and symlink escape rejection?
- Does Compose inject trusted variables only into `hermes-agent` and keep all
  values private?
- Does the deploy package avoid restart/recreate and defer Level 1/Level 2
  live activity to the Human Checkpoint?
- Are the readiness checks read-only and are the failure scenarios covered by
  the fixture validation?

## Scope facts

- domain/security source: unchanged
- profiles and skills: unchanged
- VERSION and plugin manifest: unchanged
- runtime files/containers: unchanged
- deploy/restart/recreate/live worker/live Plan mutation: not executed
- Git add/commit/push: not executed

Expected Hermes response: `approve_for_human_checkpoint`,
`approve_with_changes`, `block`, or `inconclusive`.
