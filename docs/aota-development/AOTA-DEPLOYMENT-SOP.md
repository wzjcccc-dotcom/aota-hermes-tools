# AOTA Unified Profile / Plugin / Skill Deployment SOP

This document is the unified Standard Operating Procedure for deploying AOTA
Forge plugin tools, Profile configurations and SOULs, and AOTA Skills from the
canonical source repository to the Hermes runtime environment.

---

## 1. Source Validation

Before any deployment action:

1. Confirm the canonical source root (`/home/latios/workspace/aota-hermes-tools`)
   matches the project manifest digest.
2. Run `python_compileall` on all Python files in `plugin/aota-tools/` and
   `scripts/`. Syntax errors block deployment.
3. Run lifecycle source verifiers:
   - `scripts/verify-aota-tool-lifecycle.py` — validates tool inventory,
     toolset registration, Profile exposure, transport, path declarations,
     and managed manifest coverage.
   - `scripts/verify-aota-skill-lifecycle.py` — validates Skill inventory,
     source presence, owning Profile bindings, visibility policy, activation
     targets, and managed manifest pattern.
4. Run `python scripts/profile_runtime_assembly.py source` — validates the
   canonical Named Profile assembly manifest, SOUL active-skill parity, config
   parse, and Skill source existence.
5. Parse `deploy/*.yaml` to confirm valid YAML structure. Cross-reference
   referenced script and file paths against disk existence.

**Source PASS** does not imply runtime PASS.

---

## 2. Readiness

Run the prepared readiness check:

```bash
python scripts/aota_forge_plan_package.py readiness
```

Expected: `READINESS_PASS` or `READINESS_PASS_WITH_UNCOMMITTED_CHANGES`.

- Uncommitted changes are a warning, not a block, unless the Human Checkpoint
  requires a clean Git state before deployment.
- A lifecycle verifier failure blocks readiness.
- A `VERSION` / `plugin.yaml` version mismatch blocks readiness.

---

## 3. Backup

Managed deployment creates an automatic timestamped backup in
`.deploy-backups/`:

```bash
python scripts/aota_forge_plan_package.py backup
```

Or through the deploy script:

```bash
./scripts/deploy.sh
```

The backup contains every managed file's pre-deployment state, a backup
manifest, and SHA-256 checksums. The backup directory is printed on creation.

**Human/Host Checkpoint:** Record the backup timestamp and verify the backup
directory exists before proceeding.

---

## 4. Managed Deploy

Only managed manifest entries (`deploy/aota-forge-plan-files.yaml` with
`managed: true`) are deployed. The deploy operation:

1. Runs readiness (fail-closed if readiness blocks).
2. Creates a backup.
3. Copies each managed source file to its runtime destination atomically
   (tempfile + `os.replace`).
4. Removes managed files declared in `removed_files` from the runtime.
5. Preserves unmanaged paths (`scripts/`, `__pycache__/` — never touched).
6. Writes a deployment receipt to `.deploy-receipts/aota-forge-plan/<timestamp>/`.

The deploy command:

```bash
python scripts/aota_forge_plan_package.py deploy
```

Or through `deploy.sh`:

```bash
./scripts/deploy.sh
```

Both emit `ACTION_REQUIRED`; neither restarts or recreates a service.

**Do not** use manual `cp`/`rsync` as a substitute for managed deploy.
Manual copies bypass the backup, receipt, and parity verification chain.

---

## 5. Receipt

Every managed deploy creates a `deployment.json` receipt containing:

- `schema_version`, `source_version`, `tools`, `toolsets`
- `source_root`, `runtime_root`
- List of copied file IDs with source and runtime SHA-256 hashes
- `backup_path` reference
- Boolean flags: `compose_changed`, `restart_executed`, `live_smoke_executed`

The receipt is the **authoritative record** of what was deployed, from which
source revision, and to which runtime paths. CARD/RESULT artifacts from worker
tasks are not authoritative scope or deployment evidence — consult the scope
receipt or deployment receipt for authoritative scope/diff/delta evidence.

---

## 6. Source / Host Parity

After deploy, verify source-to-runtime hash parity:

```bash
python scripts/aota_forge_plan_package.py verify
```

Expected: `DEPLOY_VERIFY_PASS`.

Every managed source file must have the same SHA-256 as its runtime
destination. Missing or mismatched files produce `DEPLOY_VERIFY_BLOCKED` with
the mismatched IDs listed.

**Source PASS ≠ runtime PASS.** Hash parity confirms the files are identical,
but does not confirm the Hermes runtime has loaded, imported, or activated them.

Before process activation, run the static assembly gate:

```bash
python scripts/profile_runtime_assembly.py pre-activation
```

This gate permits `BOOTSTRAP_SNAPSHOT=pending_activation` when all source and
projection checks pass. After Agent/WebUI recreate, run
`profile_runtime_assembly.py post-activation`; a missing, stale, or invalid
bootstrap snapshot is then a runtime failure requiring rollback.

---

## 7. Host / Container Parity

Verify that the Agent container sees the same files as the Host:

```bash
docker compose exec hermes-agent sha256sum \
  /home/hermes/.hermes/plugins/aota-tools/*.py \
  /home/hermes/.hermes/profiles/*/config.yaml \
  /home/hermes/.hermes/skills/*/SKILL.md
```

Compare against the host-side SHA-256 values from the deployment receipt or
source-side `sha256sum` output.

Container paths differ from host paths:
- Host:  `/home/latios/.hermes/...`
- Agent: `/home/hermes/.hermes/...`
- WebUI: `/home/hermeswebui/.hermes/...`

Both containers must bind the same host device. Agent uses a direct bind mount;
WebUI uses a named volume backed by the same host device.

---

## 8. Agent / WebUI Force-Recreate

When the deployed change touches plugin modules, handlers, schemas,
`__init__.py`, `plugin.yaml`, toolsets, global Skills, Skill-loader, or shared
Profile/Skill runtime surfaces, both importing processes must be recreated:

```bash
cd /home/latios/hermes-stack
docker compose up -d --no-deps --force-recreate hermes-agent
docker compose up -d --no-deps --force-recreate hermes-webui
```

**Do not use `docker compose down`** — it stops both containers, which may
cause a service interruption. Use `--force-recreate` for the affected process
only.

Agent-only activation is allowed only when evidence proves the legacy WebUI
neither imports nor consumes the change. WebUI-only activation likewise
requires evidence the Agent is unaffected.

This step is a **Human/Host** operation; it is never run automatically by a
coder, debugger, or reviewer task.

---

## 9. New-Session / Runtime Smoke

After `force-recreate`, open a **new Hermes session** (not just `/reload`) to
pick up the re-registered tools, toolsets, Profiles, and Skills.

Run bounded runtime smoke:

1. Confirm `version=0.17.6`, `tools=59`, `toolsets=28` via Hermes registration
   inspection.
2. Verify the tool list contains the expected tools for each Profile.
3. Verify Profile-level toolset allow/deny matches the canonical manifest
   (e.g. task-main retains `aota_work_intake`/`aota_plan_read`/`aota_plan_write`;
   worker profiles deny them).
4. Verify the active Profile can dispatch the expected bounded tools.
5. Verify Skill-loaded context includes the expected AOTA Skills for the
   selected Profile.

**New session ≠ process reload.** A process reload may re-register tools but
does not clear the prompt Skill cache. Only a new session guarantees fresh
Skill resolution.

---

## 10. Rollback

To roll back to the previous deployed state:

```bash
python scripts/aota_forge_plan_package.py rollback .deploy-backups/<backup-dir>
```

Or:

```bash
./scripts/rollback.sh
```

The rollback:

1. Reads the backup manifest from the chosen backup directory.
2. Restores every managed file to its pre-deploy SHA-256.
3. Removes files that did not exist before deploy.
4. Verifies post-rollback parity against the backup manifest.
5. Prints `ACTION_REQUIRED` — recreate affected processes.

**Human/Host Checkpoint:** After rollback parity verification and before
recreate, confirm the restored paths match expected content (e.g. re-run
`scripts/verify-deploy.sh` if applicable).

---

## 11. Git Backup Checkpoint (Human/Host)

Before any deployment that changes managed source files, create a Git backup
checkpoint:

```bash
cd /home/latios/workspace/aota-hermes-tools
git status --short
git add -A
git commit -m "deploy checkpoint: <description>"
git tag deploy-checkpoint-<date>-<description>
```

This is a **Human/Host Checkpoint** — it is never executed by an automated
worker. The checkpoint provides a durable Git baseline for diff comparison,
rollback reference, and change attribution.

If the workspace has uncommitted changes that cannot be committed (e.g.
unrelated dirty paths), skip the commit and note the limitation in the
deployment receipt or handoff.

---

## 12. CodeGraph Refresh Checkpoint (Human/Host)

After a deployment that changes tool, Profile, or Skill definitions, refresh
the CodeGraph index:

```bash
aota_codegraph_rebuild(workspace_id="aota-hermes-tools", project_id="aota-hermes-tools")
```

This is a **Human/Host Checkpoint** — it is never executed by an automated
worker. CodeGraph refreshes require the rebuild toolset which is restricted to
task-main only.

Verify the rebuild by querying a known symbol:

```bash
aota_codegraph_query(workspace_id="aota-hermes-tools", project_id="aota-hermes-tools", search="<known_tool_or_class>")
```

---

## 13. Detect Missing / Broken / Unmanaged Projection

During post-deploy verification, check for these conditions:

| Condition | Detection | Resolution |
|-----------|-----------|------------|
| Missing Profile-local plugin projection | `scripts/profile_runtime_assembly.py runtime` reports `runtime:<profile>:plugin-projection` | Run `deploy.sh` or `profile_runtime_assembly.py runtime` |
| Broken Skill snapshot (bootstrap not updated) | `scripts/profile_runtime_assembly.py runtime` reports `runtime:<profile>:bootstrap-snapshot` | Recreate the profile's Hermes session |
| Unmanaged file in runtime plugin dir | Compare `ls` output against managed manifest | Remove or add to managed manifest |
| Stale file unexpectedly present | Mismatch between backup manifest and current runtime | Roll back or redeploy |

---

## 14. Source PASS vs Runtime PASS

It is critical to distinguish source verification from runtime verification:

| Stage | Evidence | Means |
|-------|----------|-------|
| **Source PASS** | `python_compileall` succeeds, verifier scripts report `*_PASS`, YAML parses | Source files are syntactically valid and structurally correct |
| **Deploy PASS** | `DEPLOY_VERIFY_PASS`, hash parity confirmed | Source files were copied to runtime with identical content |
| **Runtime PASS** | Hermes registration shows the tools/toolsets, new session confirms Skill loading, exact dispatch works | The runtime has loaded and activated the changes |

**A source PASS does not guarantee deploy PASS. A deploy PASS does not
guarantee runtime PASS.** Each stage requires its own evidence.

---

## 15. Limitations

- `VERIFIER_RUNTIME_EXECUTION` is never executed by a coder task — verifier
  scripts are not in the project manifest `commands.validate` and cannot be run
  through `aota_project_command_run`. Runtime execution is a Human/Host
  operation.
- `git diff --check` is deferred to the reviewer and the Human/Host Git
  checkpoint. Coder tasks do not run `git diff --check`.
- The deployment scripts do not read, copy, display, or store private `.env`
  values, API keys, tokens, or credentials.
- Container path differences are documented but the host path
  (`/home/latios/.hermes`) is the sole deployment, live-agent, and rollback
  authority.

---

## Reference

- `deploy/aota-forge-plan-files.yaml` — managed file inventory and patterns
- `deploy/aota-lifecycle-inventory.yaml` — tool, toolset, Skill, and Profile
  lifecycle governance
- `deploy/profile-runtime-assembly.yaml` — canonical Named Profile assembly
- `docs/aota-development/AOTA-RUNTIME-PATHS-AND-RELOADS.md` — runtime path
  mapping and reload requirements
- `docs/aota-development/AOTA-PLUGIN-TOOL-LIFECYCLE.md` — plugin tool lifecycle
- `docs/aota-development/AOTA-SKILL-LIFECYCLE.md` — Skill lifecycle
