# AOTA Profile Task Control Plane — Canonical Source Repository

This repository is the **canonical source of truth** for the AOTA (Architect-Overseer Task Automation) Hermes Profile Task Control Plane plugin, profiles, orchestration skill, and deploy toolchain.

---

## What This Repo Contains

| Path | Content |
|------|---------|
| `plugin/aota-tools/` | 40 Python files + `plugin.yaml` — the AOTA narrow-tools plugin |
| `profiles/task-main/` | Task-Main (architect/orchestrator) profile config + SOUL.md |
| `profiles/coder/` | Coder worker profile config + SOUL.md |
| `profiles/debugger/` | Debugger worker profile config + SOUL.md |
| `profiles/reviewer/` | Reviewer worker profile config + SOUL.md |
| `skills/aota-profile-task-orchestration/` | Orchestration skill (SKILL.md) |
| `scripts/deploy.sh` | Canonical → runtime deployment script |
| `scripts/rollback.sh` | Rollback to previous deployment |
| `scripts/verify-deploy.sh` | Read-only deploy verification (no modifications) |
| `scripts/cleanup-controlled-run.py` | Controlled run cleanup utility |
| `VERSION` | Canonical version (`0.13.0`) |

---

## Runtime Destination Paths

When deployed, sources are copied to these Hermes runtime locations:

| Source | Runtime Destination |
|--------|-------------------|
| `plugin/aota-tools/*.py + plugin.yaml` | `/home/hermeswebui/.hermes/plugins/aota-tools/` |
| `profiles/*/config.yaml + SOUL.md` | `/home/hermeswebui/.hermes/profiles/*/` |
| `skills/aota-profile-task-orchestration/SKILL.md` | `/home/hermeswebui/.hermes/skills/aota-profile-task-orchestration/` |

Profile-local `plugins/aota-tools` symlinks point to the global runtime plugin directory (not to this canonical repo).

---

## Deploy Flow

```
Canonical Source  ──[deploy.sh]──►  Deploy Copy  ──[copy]──►  Hermes Runtime
  (this repo)                        (.deploy-backups/)        (plugin dir)
```

1. **Preflight** — validates VERSION, canonical source exists
2. **Source validation** — `py_compile` all canonical `.py` files
3. **Destination validation** — confirms runtime plugin dir exists
4. **Backup** — `cp -a` current runtime to `.deploy-backups/<timestamp>/`
5. **Deploy** — copies `.py` + `plugin.yaml` to runtime
6. **Stale removal** — deletes `.py` in runtime not present in canonical
7. **Unmanaged preservation** — `scripts/`, `__pycache__/` in runtime are never touched
8. **Runtime compilation check** — `py_compile` deployed files
9. **Plugin import test** — attempted; skipped gracefully if import fails outside Hermes
10. **Version verify** — compares `VERSION` with `plugin.yaml` version
11. **Profile sync** — copies canonical `config.yaml` + `SOUL.md` to runtime profiles
12. **Skill sync** — copies canonical skill to runtime skill dir
13. **ACTION_REQUIRED** — always printed (plugin code changed)

### Usage

```bash
cd /workspace/aota-hermes-tools
./scripts/deploy.sh
```

---

## Restart Gate (No Auto-Restart)

Deploying or rolling back plugin code **does not automatically restart** any Hermes service. The scripts output:

```
ACTION_REQUIRED: Hermes profile restart or plugin reload is required
```

This is a deliberate safety gate — the operator must manually restart or signal reload when ready.

---

## Rollback Procedure

```bash
cd /workspace/aota-hermes-tools
./scripts/rollback.sh
```

The rollback script:
1. Finds the latest backup in `.deploy-backups/` (sorted by timestamp)
2. Restores plugin `.py` + `plugin.yaml` from backup to runtime
3. Restores profile `config.yaml` + `SOUL.md`
4. Restores skill files
5. Runs `py_compile` verification on restored files
6. Prints `ACTION_REQUIRED` (restart needed)

---

## Tool / Profile Matrix

| Profile | Enabled Toolsets | Role |
|---------|-----------------|------|
| **task-main** | aota_core, aota_fs_readonly, aota_repo_readonly, aota_web_readonly, aota_task_spec, aota_profile_task, aota_handoff, aota_orchestration, aota_operator | Architect, orchestrator — approves, dispatches, reviews lineage |
| **coder** | aota_core, aota_fs_readonly, aota_worker_outcome, aota_coder_artifact | Implementation worker — bounded writes only |
| **debugger** | aota_core, aota_fs_readonly, aota_repo_readonly, aota_web_readonly, aota_worker_outcome, aota_debugger_artifact | Read-only diagnosis, no mutation |
| **reviewer** | aota_core, aota_fs_readonly, aota_repo_readonly, aota_worker_outcome, aota_reviewer_artifact | Read-only review, no mutation |

- **30 tools** provided by the plugin (listed in `plugin.yaml` `provides_tools`)
- **14 toolsets** defined in `__init__.py`: `aota_core`, `aota_fs_readonly`, `aota_repo_readonly`, `aota_web_readonly`, `aota_fs_copy`, `aota_task_spec`, `aota_profile_task`, `aota_worker_outcome`, `aota_debugger_artifact`, `aota_reviewer_artifact`, `aota_coder_artifact`, `aota_handoff`, `aota_orchestration`, `aota_operator`
- **4 profiles** (task-main, coder, debugger, reviewer)

Worker profiles (coder, debugger, reviewer) are isolated from `aota_profile_task` and `aota_task_spec` toolsets — they cannot create, approve, start, or cancel profile tasks.

---

## Versioning

- **`VERSION`** file at repository root holds the canonical version (`0.13.0`)
- **`plugin.yaml`** in `plugin/aota-tools/` has a `version:` field that must match `VERSION`
- Both files are compared during deploy and verify

---

## Known Deferred Smoke Items

- Full Hermes-runtime plugin import test may fail outside the Hermes environment (expected — import requires Hermes SDK, which is only available at runtime). The deploy script skips this gracefully.
- `verify-deploy.sh` references runtime paths `/home/hermeswebui/.hermes/plugins/aota-tools/` and `/home/hermeswebui/.hermes/profiles/` — verification must be run on the Hermes host.
- `.deploy-backups/` directory is gitignored but must exist at deploy time (created automatically by `deploy.sh`).
- Profile `plugins/aota-tools` symlinks are managed by Hermes, not by this repo. Verify step checks they point to the correct runtime path.

---

## No Secrets

This repository contains **no secrets, API keys, or runtime credentials**. Provider keys in `config.yaml` reference environment variables (`AMF_PROXY_AGNES_KEY`) rather than embedding values. The reviewer profile has a `.env` file in its runtime directory that is **not** part of the canonical source — it must be maintained separately.
