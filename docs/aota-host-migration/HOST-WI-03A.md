# HOST-WI-03A — Host-native Profile Task Runner Activation

Status: `PASS_WITH_NON_BLOCKING_FINDINGS`

This work item moves new AOTA Profile Task launches from the Docker-era
`/usr/local/bin/hermes` wrapper to the canonical Host launcher
`/home/latios/.local/bin/hermes-host`. The launcher remains Host-migration
owned and is referenced by absolute path; it is not copied into the AOTA
plugin.

## Contract

- Default runner: `/home/latios/.local/bin/hermes-host`.
- `AOTA_HERMES_RUNNER` is an explicit override only after absolute-path,
  regular-file, executable, owner/mode, parent-chain, realpath, and hash
  validation.
- PATH lookup, implicit Docker fallback, shell command strings, `sh -c`, and
  `/usr/local/bin/hermes` are rejected.
- New launch manifests freeze `runner_path`, `runner_realpath`,
  `runner_identity_hash`, `runner_contract_version`, and `host_mode` while
  retaining compatibility with old manifest parsing.
- Workers use argv arrays with `shell=False`, `stdin=DEVNULL`, the canonical
  workspace as `cwd`, and an allowlisted environment. The Host launcher loads
  the private runtime environment; its values are never serialized into task
  artifacts or evidence.

## Verification and deployment

The isolated verifier is
`scripts/verify-host-wi-03a-profile-task-runner.py`. It passed 15 runner,
manifest, environment, toolset, and schema-v2 compatibility checks. Existing
shell-closure and launcher-hardening regressions also pass. Verification used
temporary fixtures only; no formal Profile Task, worker, Docker, parent wake,
Desktop restart, database write, commit, or push was performed.

Canonical deployment used `scripts/aota_forge_plan_package.py` and produced a
backup and deployment receipt. Source/runtime plugin parity passed for 89
managed Python files. Runtime discovery was static/import/read-only and
confirmed `aota_profile_task_start` plus the Host runner contract without
creating a task.

Evidence:

- `deploy/evidence/host-migration/HOST-WI-03A/latest.json`
- `deploy/evidence/host-migration/HOST-WI-03A/20260727T005041Z/RESULT.md`

The only non-blocking finding is that the existing Profile runtime
post-activation bootstrap snapshot remains pending. This work item stopped at
the permitted pre-activation/deployment boundary; activation and live
Profile Task smoke belong to `HOST-WI-03B-DEPLOYMENT-AND-LIVE-SMOKE`.
