# HOST-WI-02B-DEPLOYMENT-RESUME

## Closure status

The canonical deployment to `/home/latios/.hermes` executed successfully and
static parity passed. The work item is not closed because the required live
one-shot smoke could not obtain a provider response.

`FINAL=FAIL_HOST_CLI_SMOKE_AND_RUNTIME_PRESERVATION`

The one-shot used the requested `opencode-zen` provider and
`deepseek-v4-flash-free` model. It returned a connection error after retries;
the required `AOTA_FORGE_HOST_PARITY_OK` response was not obtained. The failed
one-shot also changed the global `state.db` hash, so runtime preservation is
explicitly not claimed for the CLI-smoke interval.

## Deployment evidence

Evidence is under:

`deploy/evidence/host-migration/HOST-WI-02B-DEPLOYMENT-RESUME/20260726T034358Z/`

Deployment backup:

`/home/latios/workspace/aota-hermes-tools/.deploy-backups/20260726T033807Z`

Deployment receipt:

`/home/latios/workspace/aota-hermes-tools/.deploy-receipts/aota-forge-plan/20260726T033809Z/deployment.json`

The deployment produced 708 logical managed paths, 243 physical targets, five
valid projections, and 465 deduplicated writes. Official Hermes, launcher,
private env metadata, config, profile databases, Skills, tool source, and
symlink topology were not deployment targets. The exact orphan allowlist had
11 already-absent paths; no generic cleanup was used.

## Not executed

Desktop reconnect, gateway validation, `aota_runtime_info`, known-workspace
read smoke, and profile visibility live smoke remain pending. No worker,
delegate task, CodeGraph mutation, AMF mutation, commit, or push was executed.

The next action is an operator-approved live provider/network smoke, followed
by a fresh state-preservation check. HOST-WI-02C remains out of scope.
