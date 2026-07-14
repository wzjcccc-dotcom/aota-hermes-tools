# PF-WI-07-4B Human Activation Checkpoint

This checkpoint is Human-only. Codex has not backed up live runtime, changed
private environment values, deployed, reloaded, restarted, recreated, run a
live worker, mutated a live Plan, or run Level 1/Level 2 smoke.

Target commit subject:

`feat(aota-forge): complete plan foundation and activation package`

Stop immediately on any failed precondition or stop condition. Never print
secret values; report only `present`/`absent`, masked key names, hashes, and
the required verdict markers.

## 1. Confirm commit

~~~bash
cd /home/latios/workspace/aota-hermes-tools
git status --short
git log -1 --oneline
~~~

Confirm the target commit subject is present and there are no unexpected AOTA
changes. Do not push, amend, reset, clean, restore, checkout, or rebase.

## 2. Run readiness

~~~bash
cd /home/latios/workspace/aota-hermes-tools
./scripts/check-aota-forge-plan-readiness.sh
~~~

Expected: `READINESS_PASS`. If `READINESS_BLOCKED`, stop.

## 3. Run live backup

~~~bash
cd /home/latios/workspace/aota-hermes-tools
./scripts/backup-aota-forge-plan-activation.sh
~~~

Run this before deploy. Record the timestamped backup path from
`BACKUP_CREATED <path>`, then confirm that `backup-manifest.json` and
`checksums.sha256` exist in that path. The package excludes the private
`.env`; do not copy or display it. On failure or missing manifest/checksum,
stop and do not deploy.

## 4. Configure private trusted env

Human sets the values only in:

`/home/latios/hermes-stack/.env`

Set these names without recording their values:

~~~text
AOTA_TRUSTED_PRINCIPAL
AOTA_TRUSTED_AUTHORITIES
AOTA_TRUSTED_WORKSPACE_ID
~~~

The principal must identify `task-main`; authorities must include the required
Plan authorities; workspace binding follows the activation policy. Verify only
presence/absence:

~~~bash
for key in AOTA_TRUSTED_PRINCIPAL AOTA_TRUSTED_AUTHORITIES AOTA_TRUSTED_WORKSPACE_ID; do
  if grep -Eq "^${key}=.+$" /home/latios/hermes-stack/.env; then
    echo "${key}=present"
  else
    echo "${key}=absent"
  fi
done
~~~

Inject these values into `hermes-agent` only. Do not inject WebUI, write them
to SOUL, Skill, Plan, SPEC, or task metadata. If any required value is absent,
stop before deploy.

## 5. Deploy source/config

~~~bash
cd /home/latios/workspace/aota-hermes-tools
./scripts/deploy.sh
~~~

Expected: deployment receipt, source/runtime hash preparation, and
`ACTION_REQUIRED`; restart/recreate must not be executed by this step. On a
partial failure, stop and rollback.

## 6. Verify deployed parity

~~~bash
cd /home/latios/workspace/aota-hermes-tools
./scripts/verify-deploy.sh
~~~

Expected: runtime/source parity, target version `0.17.6`, worker sanitizer
present, worker Plan toolsets denied, task-main Plan toolsets retained, and
skill parity PASS. This proves file parity only; it does not prove live reload.
If verification fails, do not recreate Agent; investigate or rollback.

## 7. Recreate hermes-agent only

Only after Steps 1–6 pass:

~~~bash
cd /home/latios/hermes-stack
docker compose up -d --no-deps --force-recreate hermes-agent
docker compose ps hermes-agent
docker inspect hermes-agent --format '{{json .State}}'
~~~

Do not operate `hermes-webui`. For environment inspection, output key names
only and mask values:

~~~bash
docker inspect hermes-agent --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | sed -E 's/=.*$//' \
  | grep -E '^AOTA_TRUSTED_(PRINCIPAL|AUTHORITIES|WORKSPACE_ID)$' \
  | sort
~~~

## 8. Verify registration

Use the actual Hermes runtime registration/API inspection, not the source
manifest alone. Confirm and record only:

~~~text
version = 0.17.6
tools = 35
toolsets = 18
~~~

If registration remains `0.17.0`, report `LIVE_RELOAD_FAILED`, stop, and do
not run smoke.

## 9. Verify role isolation

Use live resolved tools/registration, not only source configuration. Confirm:

~~~text
task-main: aota_work_intake, aota_plan_read, aota_plan_write visible
architect: aota_work_intake, aota_plan_read, aota_plan_write absent/disabled
reviewer: aota_work_intake, aota_plan_read, aota_plan_write absent/disabled
coder: aota_work_intake, aota_plan_read, aota_plan_write absent/disabled
debugger: aota_work_intake, aota_plan_read, aota_plan_write absent/disabled
~~~

If any worker can see Plan write, report `SECURITY_BLOCK`, stop, and rollback.

## 10. Verify worker trusted-env absence

Start one minimal live Profile Task worker and have it report only:

~~~text
AOTA_TRUSTED_PRINCIPAL=absent
AOTA_TRUSTED_AUTHORITIES=absent
AOTA_TRUSTED_WORKSPACE_ID=absent
~~~

Do not output any other environment value and do not execute Plan mutation.
Expected: `WORKER_TRUSTED_ENV_ABSENT`. If any key is present, report
`SECURITY_BLOCK`, stop, and rollback.

## 11. Run temporary Level 1 smoke

Use a temporary workspace, never the formal workspace, and execute only the
Level 1 sequence in `docs/aota-forge-plan/LEVEL-1-SMOKE-PLAN.md`:

- registration
- classifier read-only smoke
- missing-context rejection
- wrong-principal rejection
- missing-authority rejection
- worker tool isolation
- worker trusted-env absence
- worker marker rejection
- temporary P1 Plan plus linked SPEC freeze

Do not run an implementation worker, `link_task` result lifecycle, Reviewer,
Work Item closure, or any Level 2 E2E. Keep the temporary Plan/SPEC/audit/
Profile Task artifacts as one unit or remove them as one unit according to the
Human cleanup decision.

## 12. Stop or rollback

Rollback on any of the following: runtime registration mismatch,
tools/toolsets count mismatch, worker Plan write visibility, worker trusted-env
inheritance, abnormal Plan write security rejection, deploy parity failure,
Agent unhealthy, temporary P1 smoke failure involving runtime/package,
secret exposure, or any other `SECURITY_BLOCK`.

~~~bash
cd /home/latios/workspace/aota-hermes-tools
./scripts/rollback-aota-forge-plan-activation.sh <backup-dir>
./scripts/verify-deploy.sh
cd /home/latios/hermes-stack
docker compose up -d --no-deps --force-recreate hermes-agent
docker compose ps hermes-agent
~~~

After rollback, verify previous runtime parity and registration. Recreate
`hermes-agent` only; do not touch `hermes-webui`. Do not alter Plan, SPEC,
audit ledger, or Profile Task artifacts during rollback.
