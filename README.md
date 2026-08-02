# AOTA Forge — Canonical Source Repository

This repository is the **canonical source of truth** for AOTA Forge — a controlled agentic software delivery control plane driven by specifications, role separation, bounded tools, durable handoffs, and human checkpoints. It contains the AOTA Profile Task plugin, profiles, AOTA Forge skills, and deploy toolchain.

---

## What This Repo Contains

| Path | Content |
|------|---------|
| `plugin/aota-tools/` | AOTA narrow-tools plugin (`plugin.yaml` is the canonical tool list) |
| `profiles/task-main/` | Task-Main (architect/orchestrator) profile config + SOUL.md |
| `profiles/coder/` | Coder worker profile config + SOUL.md |
| `profiles/debugger/` | Debugger worker profile config + SOUL.md |
| `profiles/reviewer/` | Reviewer worker profile config + SOUL.md |
| `skills/aota-profile-task-orchestration/` | AOTA Forge delivery orchestration skill (SKILL.md) |
| `skills/aota-spec-driven-implementation/` | AOTA Forge coder skill (SKILL.md) |
| `skills/aota-evidence-first-debugging/` | AOTA Forge debugger skill (SKILL.md) |
| `skills/aota-implementation-review/` | AOTA Forge reviewer skill (SKILL.md) |
| `scripts/deploy.sh` | Canonical → runtime deployment script |
| `scripts/rollback.sh` | Rollback to previous deployment |
| `scripts/verify-deploy.sh` | Read-only deploy verification (no modifications) |
| `scripts/cleanup-controlled-run.py` | Controlled run cleanup utility |
| `VERSION` | Canonical version (`0.17.6`) |

Workspace identity is bounded by the control plane: `workspace_id` is the
registered filesystem-authority key, while `project_id` is the project identity
validated by `.aota/project.yaml` inside that workspace. A workspace may hold
multiple projects; neither value is a filesystem path. For example,
`workspace_id=main-workspace` and `project_id=aota-hermes-tools` are intentionally
different values. `spec_hash` is the canonical frozen SPEC hash and
`spec_sha256` is the raw `SPEC.md` content SHA-256; they are separate bindings.
`active_frozen_spec` means the active frozen SPEC for the current trusted
task-main/coordinator session. A freeze records that session-local binding in
the runtime control-plane context when trusted session metadata is available;
the workspace-wide unique frozen SPEC is only the bounded fallback. A stale
session binding fails closed and never switches to another session's SPEC.

### Canonical model-facing invocation rule

Never invent, copy, or retry control-plane identifiers, paths, revisions,
hashes, sessions, profiles, or lifecycle bindings. Use semantic references and
the deterministic `next_action` returned by a tool. Explicit legacy fields may
remain accepted by handlers for compatibility, but they are not canonical
model-facing inputs.

### Latest Profile-efficiency validation

The current repeatable validation path is the
[`aota-profile-efficiency-rerun` Skill](skills/aota-profile-efficiency-rerun/SKILL.md).
The authorized 30-sample run on 2026-08-02 reached **92.8% composite
efficiency** (R7 baseline: 80.7%), with 100% completion, 93.3% correct
tool/parameter routing, 100% retry-stop safety, and zero samples over the
100-call cap. Per-Profile scores were: project-steward 90%, task-main 91%,
architect 90%, reviewer 98%, coder 95%, debugger 93%.

The complete modification history, deployment receipt, limitations, and
evidence path are recorded in
[`AOTA-PROFILE-EFFICIENCY-MODIFICATION-HISTORY-20260802.md`](docs/aota-development/AOTA-PROFILE-EFFICIENCY-MODIFICATION-HISTORY-20260802.md).
The JSON evidence is kept under `deploy/evidence/`; source/runtime activation
and live validation remain separate states.

---

## Host Deploy and Container Runtime Paths

The deploy, verify, and rollback scripts operate only on the host deploy destination:

```text
/home/latios/.hermes
```

Use `AOTA_HERMES_HOME_HOST=/custom/path` to select another existing absolute host root. The value must not be `/`, relative, empty, or contain control characters.

Docker Compose exposes the same host root in both runtime views (Agent direct
bind; WebUI named volume backed by the same host device):

```text
host:      /home/latios/.hermes
agent:     /home/hermes/.hermes
container: /home/hermeswebui/.hermes
```

The host path is the only deployment, live-agent, and rollback authority. The
container paths are explanatory only; they are never deployment targets.

| Source | Host deploy destination |
|--------|-------------------------|
| `plugin/aota-tools/*.py + plugin.yaml` | `${AOTA_HERMES_HOME_HOST-/home/latios/.hermes}/plugins/aota-tools/` |
| `profiles/*/config.yaml + SOUL.md` | `${AOTA_HERMES_HOME_HOST-/home/latios/.hermes}/profiles/*/` |
| `skills/*/SKILL.md` | `${AOTA_HERMES_HOME_HOST-/home/latios/.hermes}/skills/*/` |

`deploy/profile-runtime-assembly.yaml` is the canonical Named Profile assembly
manifest. Managed deployment projects the plugin files and each Profile's
declared AOTA Skills into every Profile-local runtime directory, while the
global plugin and Skill roots remain the shared source projections.

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
13. **Profile runtime assembly** — projects the plugin and Active AOTA Skills
    declared by the canonical assembly manifest into every Named Profile
14. **ACTION_REQUIRED** — always printed (a new session/process reload is required)

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
| **task-main** | orchestration/control-plane reads plus CodeGraph read/rebuild | Orchestrator — bounded routing, Plan/SPEC/task lifecycle, decisions; no source write |
| **project-steward** | project read/steward mutation, CodeGraph read, steward artifact | Project facts, bounded docs/artifact continuity; no source/terminal/rebuild |
| **coder** | readonly/project/CodeGraph read, Coder file mutation, fixed command runner, artifacts | Frozen-SPEC implementation; no unrestricted file or terminal |
| **debugger** | aota_core, aota_fs_readonly, aota_repo_readonly, aota_web_readonly, aota_worker_outcome, aota_debugger_artifact | Read-only diagnosis, no mutation |
| **reviewer** | aota_core, aota_fs_readonly, aota_repo_readonly, aota_worker_outcome, aota_reviewer_artifact | Read-only review, no mutation |
| **architect** | aota_core, aota_fs_readonly, aota_repo_readonly, aota_web_readonly, aota_worker_outcome, aota_architect_artifact | Read-only design review & spec preflight |

- **Tools** provided by the plugin are derived from `plugin/aota-tools/plugin.yaml`
  `provides_tools`; toolset membership is derived from the lifecycle inventory.
- task-main source config includes `aota_work_intake`, `aota_plan_read`, and `aota_plan_write`; Plan mutation remains fail-closed until deployment-owned trusted principal/authority injection is explicitly approved and performed.
- `aota_work_classify` deterministically classifies bounded facts only and never creates a Plan, SPEC, task, or artifact. See `docs/aota-forge-plan/WORK-CLASSIFICATION.md`.
- **6 profiles** (task-main, project-steward, architect, coder, reviewer, debugger)

Worker profiles (coder, debugger, reviewer, architect) are isolated from `aota_profile_task` and `aota_task_spec` toolsets — they cannot create, approve, start, or cancel profile tasks. They also explicitly disable `aota_work_intake`, `aota_plan_read`, and `aota_plan_write`; Profile Task launch removes deployment-owned `AOTA_TRUSTED_*` Plan authority from the worker child shell before worker markers are exported.

---

## Versioning

- **`VERSION`** file at repository root holds the canonical version (`0.17.6`)
- **`plugin.yaml`** in `plugin/aota-tools/` has a `version:` field that must match `VERSION`
- Both files are compared during deploy and verify

---

## Known Deferred Smoke Items

- Full Hermes-runtime plugin import test may fail outside the Hermes environment (expected — import requires Hermes SDK, which is only available at runtime). The deploy script skips this gracefully.
- `verify-deploy.sh` references runtime paths `/home/hermeswebui/.hermes/plugins/aota-tools/` and `/home/hermeswebui/.hermes/profiles/` — verification must be run on the Hermes host.
- `.deploy-backups/` directory is gitignored but must exist at deploy time (created automatically by `deploy.sh`).
- Profile-local plugin and Skill projections are managed by this repo's
  assembly manifest. `verify-deploy.sh` checks source/runtime hashes and the
  post-bootstrap Skill snapshot for every Named Profile.

---

## No Secrets

This repository contains **no secrets, API keys, or runtime credentials**. Provider keys in `config.yaml` reference environment variables rather than embedding values. Profile Task workers use only the global Hermes `.env` and `auth.json`; any Profile-local `.env` is outside the credential authority and is never a fallback.

---

## AOTA Forge Delivery Lifecycle

AOTA Forge defines three flow paths:

| Path | Applies to | Flow |
|------|-----------|------|
| **Fast** | Clear requirements, low risk, local changes | SPEC → approval → coder → minimal validation → decision |
| **Standard** | Multi-file, new features, medium risk | Convergence → SPEC → architect preflight (if risk warrants) → coder → reviewer (if needed) → validation (if needed) → decision |
| **Deep** | Control plane, auth, migrations, high blast radius | Convergence → DESIGN → architect design_review → SPEC → architect spec_preflight → Human approval → coder → reviewer → E2E → close |

**Architect gate**: Available (P11-J). Architect profile performs pre-construction design review and spec preflight. Verdict: approve / approve_with_changes / block / inconclusive. block/inconclusive prevents implementation. approve_with_changes requires correction before proceeding.

**Plan lifecycle**: Intake Lite → preliminary classification → proportional convergence → final classification → P0 standalone SPEC or P1/P2 Plan → required Architect gate → freeze/approval → task → explicit `link_task` → evidence/review → task-main closure → durable handoff. `execution_completed` and Reviewer pass are not automatic closure. See `docs/aota-forge-plan/ORCHESTRATION-LIFECYCLE.md`.

**Debug flow**: Diagnosis task → debugger (read-only) → diagnosis report → task-main decision → optional implementation follow-up.

## Role Matrix

| Profile | Responsibility | Write access | Main Skill |
|---------|---------------|-------------|------------|
| task-main | Convergence, classification, SPEC, orchestration | no | aota-profile-task-orchestration |
| coder | Implementation | current native write, future bounded | aota-spec-driven-implementation |
| debugger | Diagnosis | no | aota-evidence-first-debugging |
| reviewer | Post-implementation review | no | aota-implementation-review |
| architect | Pre-construction design review & spec preflight | no | aota-architecture-review |

**Note on coder write access**: Coder currently has native terminal/file access (unrestricted capability). Skill-level terminal constraints are behavioral only, not tool-layer enforcement. Tool-layer security boundaries will be implemented in P11-N/P11-L.

## Validation Model

| Tier | Name | Examples |
|------|------|---------|
| 0 | Static | syntax, importability, config parse, schema parse |
| 1 | Local Smoke | single function, single CLI, single endpoint, small fixture |
| 2 | Integration | multi-module interaction, service API, task state transition |
| 3 | Runtime | reload/restart, running process, container, profile loading |
| 4 | Live E2E | real session, profile task startup, background worker, handoff, decision |

Rules: task-main determines required tier. No default full pytest. Tier 0 ≠ Tier 3. Source correctness ≠ runtime-loaded. Historical E2E ≠ current live PASS.

## Security Model

### Mutation Audit Boundary (P11-N.2)

All mutation tool security rejections are automatically audited by the Tool Layer's `mutation_audit_boundary`. This trusted boundary:

- Catches `SecurityError` subclasses → writes denied audit → re-raises original error
- Generates `audit_event_id` per tool call for exactly-once tracking
- Fail-closed: if audit writer fails, raises `AUDIT_GAP` and blocks the mutation
- Sanitizes target paths (redacts absolute paths, removes control characters)
- Identity from trusted env vars only — never from model input
- Does NOT wrap report/outcome submit tools (they must remain submittable after security failure)

## Known Gaps (updated)

- ~~WORKSPACE_ESCAPE manual audit gap~~ — **Closed in P11-N.2**
- Worker log tee (still open)
- Git task baseline (P11-L)
- Task-owned change attribution (P11-L)
- Unrestricted coder terminal (P11-L)
- P11-L bounded construction tools
- P11-M bounded diagnostics

---

## Post-Write Verification Read

After a coder task writes files, a post-write verification read must be performed
using `aota_project_file_read` (or equivalent bounded read tools) to confirm:

1. The written content is correct and matches the intended changes.
2. No file outside the SPEC `write_scope` was modified.
3. No file in the `forbidden_scope` was accidentally touched.
4. The SHA-256 of the written files matches expectations.

This verification is distinct from validation commands (syntax checks, script
execution) — it is a content-level fidelity check.

---

## CARD Non-Authoritative vs Receipt Authoritative Scope Result

Worker task artifacts (CARD.json / RESULT.md) are **non-authoritative** for
scope compliance, deployment verification, or runtime evidence. They represent
the worker's self-reported summary.

The **authoritative** source of scope truth is:

| Purpose | Authoritative Source |
|---------|---------------------|
| Scope projection | `scope.json` (bound to SPEC id, revision, task/start identity, scope_digest) |
| Worker action events | `scope-events.jsonl` (identity-bound with task_id, start_id, process_session_id) |
| Workspace baseline | `workspace-baseline.json` (pre-existing dirty/untracked paths) |
| Deployment state | `.deploy-receipts/<package>/<timestamp>/deployment.json` |
| Task closure | Completion receipt (canonical `status=done`, `exit_code=0`, `outcome=completed`) |

CARD/RESULT should be used for human-readable summaries and task-main review,
but scope compliance, violation counts, and deployment parity must be verified
from the authoritative sources above.

---

## Active-Task SPEC / SCOPE / BINDING Access

Every worker task reads its own frozen `SPEC`, `SCOPE`, and bounded `BINDING`
through `aota_active_task_artifact_open` before performing any project-tier
work. The three results must agree on:

- `workspace_id`
- `task_id` / `start_id`
- `project_id`
- `resolved_profile`
- `spec_id` / `spec_revision`

Missing, mismatched, or denied reads fail closed with `needs_input` or
`blocked`. Workers never guess project IDs, switch Profiles, or fall back to
terminal/file reads. Subject reads (reviewer/debugger/architect reading the
reviewed task SPEC) are available only when the frozen binding explicitly
permits them.

---

## Scope Propagation and Current-Task Event Isolation

Scope propagation follows these rules:

| Source | Priority | Scope Origin |
|--------|----------|-------------|
| `spec.payload.{read,write,forbidden}_scope` | Highest | Canonical frozen SPEC |
| Legacy top-level `{read,write,forbidden}_scope` | Fallback | Pre-schema-v1 SPECs |

`scope.json` is an atomic, immutable projection bound to the SPEC identity.
Payload keys take precedence even when their value is `[]`.

**Current-task event isolation** means:

1. Task start captures a workspace baseline (`workspace-baseline.json`) of
   pre-existing tracked-dirty and untracked paths with content fingerprints.
2. Worker actions produce `scope-events.jsonl` entries bound to the current
   task identity (`task_id`, `start_id`, `process_session_id`).
3. Events with a foreign `task_id`, `start_id`, or `process_session_id` are
   counted as invalid/foreign and excluded from current-task attribution.
4. Postflight comparison reports only deltas from the baseline as new worker
   observations.
5. Workspace-wide Git diff is never a current-task worker action by itself.
6. Unattributed project deltas (no matching scope event) fail closed.

---

## Git Backup Procedure (Human / Host Checkpoint)

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
worker (coder, debugger, reviewer). It provides a durable Git baseline for:

- Diff comparison before/after deployment
- Rollback reference (revert to checkpoint commit)
- Change attribution

If the workspace has uncommitted changes that cannot be committed (e.g.
unrelated dirty paths in an already-dirty workspace), skip the commit and
note the limitation in the deployment receipt or task handoff. Do not use
`git diff --check` as coder validation — that is deferred to reviewer and
the Human/Host Git checkpoint.

---

## CodeGraph Update Procedure (Human / Host Checkpoint)

After a deployment that changes tool, Profile, or Skill definitions, refresh
the CodeGraph index:

```bash
aota_codegraph_rebuild(workspace_id="main-workspace", project_id="aota-hermes-tools")
```

This is a **Human/Host Checkpoint** — it is never executed by an automated
worker. CodeGraph rebuild requires the `aota_codegraph_rebuild` toolset which
is restricted to task-main only.

After rebuild, verify by querying a known symbol:

```bash
aota_codegraph_query(workspace_id="main-workspace", project_id="aota-hermes-tools", search="<known_tool_or_class>")
```

CodeGraph reads (`aota_codegraph_status`, `aota_codegraph_query`,
`aota_codegraph_explore`) are available to all profiles. Only task-main
may trigger a rebuild.

---

## Limitations from Uncommitted Dirty Workspace

Working in a Git workspace with uncommitted changes introduces several
limitations:

| Limitation | Impact |
|-----------|--------|
| **Baseline ambiguity** | `workspace-baseline.json` captures current dirty state, but the Git diff cannot distinguish pre-existing from task-introduced changes without scope-event attribution. |
| **Rollback complexity** | A Git checkout or reset would lose both pre-existing and task changes. Rollback via `.deploy-backups/` is preferred. |
| **Verification noise** | `git diff --stat` from a dirty workspace includes pre-existing changes, making it harder to verify only the task's intended changes. |
| **Change attribution** | Uncommitted changes from prior work may be incorrectly attributed to the current task. Scope-event identity binding mitigates this. |
| **Deploy confidence** | Deploying from a dirty workspace may include unintended changes. The `READINESS_PASS_WITH_UNCOMMITTED_CHANGES` status is a warning. |

Best practice: commit or stash unrelated changes before starting a task that
modifies managed files. If the dirty state is unavoidable, scope-event
isolation and baseline comparison ensure only the current task's actions are
attributed to it.

---

## 工具功能中文使用說明（以 `plugin.yaml` 為準）

AOTA（Architect-Overseer Task Automation）外掛程式提供的狹義工具（narrow tools）與工具集（toolsets）分別以 `plugin/aota-tools/plugin.yaml` 的 `provides_tools` 和 lifecycle inventory 為準，支援 6 個 Hermes 設定檔（task-main、project-steward、architect、coder、reviewer、debugger）。以下依工具集分組說明每個工具的功能、參數與使用時機。

---

### aota_core（核心工具集）

#### 1. aota_runtime_info
- **功能**：回傳 AOTA 執行時期基礎元資料（唯讀）。不接受任何路徑或指令輸入，不執行 shell，不修改檔案系統。
- **參數**：無。
- **回傳**：JSON，包含 plugin 名稱、runtime_root、profile_task_root、路徑是否存在等資訊。

---

### aota_fs_readonly（唯讀檔案系統工具集）

#### 2. aota_path_info
- **功能**：檢查註冊工作區內本機路徑的存在性、類型與大小。取代 `stat`、`test -e/-d`、`file`、`readlink` 等指令。不回傳目錄內容、不讀取檔案內容、不進行雜湊運算。
- **參數**：
  - `workspace_id`（必填）：註冊的工作區識別碼（如 `aota-runtime`）。
  - `path`（必填）：相對於工作區的路徑。
- **回傳**：JSON，包含 `exists`、`type`（file/directory/symlink/other/missing）、`size_bytes`、`mtime`、`mode`、`is_symlink`、`symlink_target`。

#### 3. aota_read_file
- **功能**：在註冊工作區內進行有限制的文字檔案讀取。取代 `cat`、`sed -n`、`head`、`tail`。支援行號範圍與位元組限制截斷。拒絕二進位檔案。
- **參數**：
  - `workspace_id`（必填）：工作區識別碼。
  - `path`（必填）：相對於工作區的檔案路徑。
  - `start_line`（選填）：起始行號（從 1 開始，預設 1）。
  - `end_line`（選填）：結束行號（包含）。
  - `max_bytes`（選填）：回傳最大位元組數（預設 65536，硬上限 262144）。
- **回傳**：JSON，包含 `content`、`size_bytes`、`start_line`、`end_line_returned`、`total_lines`、`truncated`。

#### 4. aota_search_files
- **功能**：在註冊工作區內進行有限制的檔名/內容搜尋。取代 `grep`、`rg`、`find`、`wc`。V1 使用文字子字串搜尋（無正規表示式）。支援三種模式：content（含預覽的比對結果）、files_only（唯一檔案路徑）、count（計數）。
- **參數**：
  - `workspace_id`（必填）：工作區識別碼。
  - `query`（必填）：搜尋查詢（文字子字串，非正規表示式）。
  - `path`（選填）：搜尋目錄（相對於工作區，預設為工作區根目錄）。
  - `mode`（選填）：content / files_only / count（預設 content）。
  - `case_sensitive`（選填）：是否區分大小寫（預設 false）。
  - `file_glob`（選填）：檔案過濾 glob 模式（如 `*.py`）。
  - `max_results`（選填）：最大結果數（預設 50，硬上限 200）。
  - `max_file_bytes`（選填）：跳過大於此值的檔案（預設 2097152）。
- **回傳**：JSON，包含 `results`（依模式不同而異）、`result_count`、`matched_file_count`、`truncated`。

---

### aota_repo_readonly（唯讀 Git 仓库工具集）

#### 5. aota_repo_status_readonly
- **功能**：在註冊工作區內讀取 Git 工作目錄狀態證據。回傳分支名稱、HEAD commit、是否髒污（dirty），以及有限數量的 staged/unstaged/untracked 項目。不接受模型提供的 Git 參數，僅使用固定的唯讀 Git 指令。
- **參數**：
  - `workspace_id`（必填）：工作區識別碼（須為 Git 仓库根目錄或其子目錄）。
  - `max_entries`（選填）：每類別最大項目數（預設 100，硬上限 500）。
- **回傳**：JSON，包含 `branch`、`head_commit`、`is_dirty`、`staged`、`unstaged`、`untracked`。

#### 6. aota_repo_diff_readonly
- **功能**：在註冊工作區內讀取 Git diff 證據。取代 `git diff`、`git diff --stat`、`git diff --name-status`。支援三種模式：summary（簡潔統計）、files（名稱-狀態）、patch（統一 diff 格式）。範圍可選 unstaged 或 staged。不接受任意修訂版本或 pathspec。
- **參數**：
  - `workspace_id`（必填）：工作區識別碼。
  - `mode`（選填）：summary / files / patch（預設 summary）。
  - `scope`（選填）：unstaged / staged（預設 unstaged）。
  - `max_bytes`（選填）：最大輸出位元組數（預設 65536，硬上限 262144）。
- **回傳**：JSON，依模式不同包含 `raw_stat` / `files` / `patch` 以及 `changed_file_count`、`insertions`、`deletions` 等統計。

---

### aota_web_readonly（唯讀網路工具集）

#### 7. aota_web_fetch
- **功能**：取得一個已知的公開 HTTP/HTTPS URL。包含 SSRF/DNS-rebinding 防護（驗證對端 IP 為全球公開位址）。僅使用標準程式庫，無第三方依賴。支援三種輸出模式：readable_text（去除 HTML 標籤）、raw_text（原始內文）、json（JSON 解析）。
- **參數**：
  - `url`（必填）：要取得的公開 HTTP 或 HTTPS URL。
  - `mode`（選填）：readable_text / raw_text / json（預設 readable_text）。
  - `max_bytes`（選填）：回應內文最大讀取位元組數（預設 262144，上限 1048576）。
  - `timeout_seconds`（選填）：連線與讀取超時秒數（預設 15，上限 30）。
- **回傳**：JSON，包含 `final_url`、`status`、`content_type`、`title`、`content`、`bytes_read`、`truncated`、`redirect_count`。

---

### aota_fs_copy（檔案複製工具集）

#### 8. aota_file_copy
- **功能**：在允許清單內的工作區中進行位元組精確的檔案或目錄複製。使用 `shutil` 進行實際複製操作，不使用 shell cp/rsync。支援檔案覆寫（需明確設定），目錄複製不允許覆寫現有目錄。含資源預檢（檔案數量上限 10000，總大小上限 10 GiB）及磁碟空間檢查。
- **參數**：
  - `workspace_id`（必填）：工作區識別碼。
  - `source`（必填）：相對於工作區的來源路徑（檔案或目錄）。
  - `destination`（必填）：相對於工作區的目的地路徑。
  - `overwrite`（選填）：是否允許覆寫現有目的地（預設 false）。
- **回傳**：JSON，包含 `source_type`、`bytes_copied`、`file_count`、`overwritten`、`status`。

---

### aota_task_spec（任務規格工具集）

### Phase 1 minimal governance invocations

Plan、Work Item、SPEC 與 approval 的 canonical model surface 只提交語意；workspace、project、Plan/Work Item/SPEC ID、revision、hash、Profile 與 approval binding 由 trusted current context 和 canonical artifact 解析。多候選一律 bounded fail-closed，不選最新或第一筆。

### Phase 3 artifact/project minimal invocation

Artifact、workspace/project governance、registration/initialization、observed
state、lifecycle checkpoint、stewardship 與 project evidence 工具共用同一
control-plane resolver。模型只提交 semantic reference、查詢、operation、
rationale 或必要的人類選擇；canonical schema 不要求 workspace/project ID、
root、path、registry/manifest location、hash 或 revision。常用 reference
包括 `current_project_declaration`、`current_project_observed_state`、
`current_completion_report` 與 `current_stewardship_subject`。

```json
{"artifact_ref":"current_project_declaration"}
{"operation":"refresh_current_project_observed_state"}
{"operation":"reconcile_current_project","rationale":"確認 declaration、registry 與 observed state 一致"}
{"action":"archive","rationale":"專案已完成，請先由 operator 確認"}
```

Resolver 不使用 cwd、basename、mtime、第一筆或最新 fallback；零匹配、多
匹配、stale binding、path traversal 與 symlink escape 均 fail-closed。project
initialization 在本 source phase 僅產生 bounded plan/fixture evidence，不建立
真實專案。

### Phase 4 remaining-tools minimal invocation

The final eleven tools use the same trusted projection. The model provides
semantic paths/queries, bounded view preferences, copy intent, task intent,
human decisions, or intake facts:

```json
{"path":"README.md"}
{"query":"control-plane","file_glob":"*.py"}
{}
{"source":"src/a.txt","destination":"docs/a.txt"}
{"title":"Bounded intake","summary":"Read-only fixture classification","facts":{}}
```

The control plane resolves current workspace/repository/task/decision/handoff
and injects the legacy handler envelope. Missing or ambiguous current subjects
return deterministic `current_subject_missing` or
`current_subject_ambiguous`; read/query results are bounded and every result
provides a `next_action`. Legacy explicit fields remain handler-only
compatibility inputs.

- 建立 current Plan：`{title: "README 診斷", objective: "確認 README 可正確讀取"}`
- 建立 current Work Item：`{operation: "add_work_item", payload: {title: "唯讀診斷", objective: "完成 README 檢查", acceptance_criteria: ["產出診斷證據"]}}`
- 建立／凍結 current SPEC：`{spec_kind: "diagnosis", objective: "唯讀確認 README.md"}` → `{spec_ref: "current_draft_spec"}`
- 提交 approval decision：`{decision: "approve", rationale: "範圍與驗收條件已確認"}`

更新使用 `plan_ref=current_plan`、`spec_ref=current_draft_spec` 或 trusted current context；不得要求模型搬運長 ID、revision 或 digest。P0 diagnosis 在沒有 Plan authority 時維持 standalone，不被強迫建立行政 Plan。

#### 9. aota_task_spec_create
- **功能**：建立 draft SPEC。模型只提交語意與必要 scope；workspace/project/Work Item、Profile、revision、hash、approval 與 defaults 由 trusted context、resolver 和 canonical artifacts 產生。P0 standalone 不建立行政 Plan，也不要求模型發明 Work Item ID；P1/P2 或 subject-bound work 若無唯一 authority 會 bounded fail-closed。
- **最小 P0 diagnosis invocation**：`{spec_kind: "diagnosis", objective: "唯讀確認 README.md", read_scope: ["README.md"], write_scope: []}`。`workspace_id` 不在 canonical model schema；由 trusted runtime context 注入。
- **P0 architecture subject**：模型可提交 `subject_ref: "current_work_classification"`，或讓 P0 architecture 使用此預設；控制面把 trusted classification digest 綁成內部 `work_classification` reference，不要求模型提供 artifact ID。
- **回傳**：JSON，包含 `task_id`、`status`、`next_action=freeze_current_spec`、`allowed_next_tool_schema`、`next_options`、`draft_spec_sha256`；draft SHA 與 frozen `spec_hash`/`spec_sha256` 永遠分離。

#### 10. aota_task_spec_update
- **功能**：更新 current draft 的語意欄位。canonical model invocation 不提交 task ID 或 expected revision；控制面從唯一 current draft 取得 optimistic revision。舊 exact fields 僅保留 handler compatibility。
- **最小 invocation**：`{spec_ref: "current_draft_spec", patch: {objective: "更新後的診斷目標"}}`。
- **回傳**：JSON，包含 `status`、`revision`、`next_action`、`draft_spec_sha256`。

---

### aota_profile_task（設定檔任務工具集）

#### 11. aota_profile_task_start
- **功能**：以 bounded `task_ref` 啟動目前 trusted task-main/coordinator session 綁定且已驗證的 frozen AOTA Profile Task；沒有 session binding 時才使用 workspace-wide unique fallback。控制面自動取得 workspace/task/revision、canonical `spec_hash`、raw `spec_sha256`、approval 與固定 Profile binding；stale binding 會 fail-closed，不會切換到其他 SPEC，也不會更新 Plan。回傳 `completion_transport`、`completion_delivery_expected`、固定 `next_action` 與控制面計算的 `recovery_allowed_after`；wakeup-capable task 必須等待 completion delivery，不可改查 status/handoff list。舊的明確 binding 欄位仍只在 handler 相容層接受，不再暴露給模型 schema。
- **參數**：
  - `task_ref`（必填）：固定 enum，目前為 `active_frozen_spec`。
  - `timeout_seconds`（選填）：工作者程序的超時秒數（30–86400）。
- **雜湊定義**：`expected_spec_hash` 是 `canonical_hash()` 的 frozen `spec_hash`；`expected_spec_sha256` 是 raw `SPEC.md` 內容 SHA-256，兩者不可互換。
- create-time `draft_spec_sha256`（以及相容欄位 `spec_sha256`）只代表 draft 內容；freeze response/meta 的 `spec_sha256` 才是 frozen raw SHA，semantic start 使用 freeze 後兩個 exact bindings。
- **回傳**：JSON，包含 `status`、`start_id`、`profile`、`process_session_id`、`completion_transport`、`completion_delivery_expected`、`next_action`、`recovery_allowed_after` 等。

#### Canonical freeze / approval examples

- Freeze current draft: `{spec_ref: "current_draft_spec"}`
- Approve implementation checkpoint: `{task_ref: "active_frozen_spec"}`
- Start semantic task: `{task_ref: "active_frozen_spec"}`

Model-facing AOTA tools follow one rule: the model supplies intent, required
content, scope, acceptance criteria, and explicit human decisions; the control
plane supplies and validates identity, binding, lifecycle state, profile,
approval, revision, hashes, digests, and derived defaults. Missing trusted
session context is a deterministic stop, not an invitation to retry with a
guessed ID.

#### 12. aota_profile_task_status
- **功能**：查詢 AOTA 設定檔任務的狀態，含生命週期調解（reconciliation）。對 wakeup-capable active task，`completion_delivery_pending` 會阻止 status/handoff/process/receipt 等替代 polling；`recovery_allowed_after` 後只允許 origin orchestrator 使用一次 bounded recovery，並直接聚合 terminal/running、receipt、outcome、process reconciliation 與 handoff evidence。冪等（idempotent）：不修改終端任務。
- **參數**：
  - `workspace_id`（必填）：工作區識別碼。
  - `task_id`（必填）：要查詢的現有 AOTA 任務 ID。
- **回傳**：JSON，包含 `status`、`profile`、`revision`、`spec_sha256`、`start_id`、`started_at`、`completed_at`、`exit_code`、`registry_state`、`receipt_present`、`reconciliation_state`、`handoff`、`orchestration`、`artifacts` 等豐富資訊。

#### 13. aota_profile_task_cancel
- **功能**：請求取消一個正在執行的 AOTA 設定檔任務。記錄持久的取消意圖（durable cancellation intent），使用 Hermes ProcessRegistry 終止原始向工作者程序發送訊號。三階段交易：鎖定-驗證-意圖、注册表終止、鎖定-持久化結果。不接受任意的程序 ID 或訊號。
- **參數**：
  - `workspace_id`（必填）：工作區識別碼。
  - `task_id`（必填）：要取消的現有 AOTA 任務 ID。
- **回傳**：JSON，包含 `status`、`termination_request_id`、`termination_state`、`registry_state` 等。

#### 14. aota_profile_task_approve
- **功能**：對 current frozen implementation SPEC 記錄明確的人員檢查點決策（approve / reject / request_changes）。控制面解析 exact subject、revision、canonical hash 與 raw SHA；僅 approve 建立 APPROVAL.json。診斷/審查不需要 approval。
- **參數**：
  - `decision`（必填）：`approve` / `reject` / `request_changes`。
  - `rationale`（必填）：bounded 人類決策理由或修改範圍。
- **回傳**：JSON，包含 `status`、`approval_id`、`approved_at`、`approval_source`。

---

### aota_worker_outcome（工作者結果提交工具集）

#### 15. aota_worker_outcome_submit
- **功能**：為目前正在執行的設定檔任務提交終端結果。任務身分從信任的執行環境變數（`AOTA_PROFILE_TASK_*`）取得，而非從模型輸入。僅允許執行中的任務使用。相同結果冪等；衝突的第二個結果會被拒絕。
- **參數**：
  - `outcome`（必填）：completed / failed / needs_input。
  - `reason`（選填）：原因說明（outcome=needs_input 時必填，上限 200 字元）。
- **回傳**：JSON，包含 `status`（submitted/idempotent/rejected）。

---

### aota_debugger_artifact（除錯器報告工具集）

#### 16. aota_debugger_report_submit
- **功能**：為目前正在執行的除錯器任務提交診斷報告。寫入 DIAGNOSIS_CARD.json（精簡）與 DIAGNOSIS.md（完整報告）。僅限 debugger 設定檔工作者使用。任務身分從信任的執行環境取得。
- **參數**：
  - `summary`（必填）：簡短診斷摘要（上限 2000 字元）。
  - `root_cause`（選填）：識別的根因（上限 2000 字元）。
  - `confidence`（必填）：診斷信心度（0.0–1.0）。
  - `key_evidence`（選填）：支援診斷的關鍵證據陣列（每項上限 500 字元，最多 20 項）。
  - `open_questions`（選填）：未解決的問題陣列（每項上限 500 字元，最多 10 項）。
  - `missing_inputs`（選填）：診斷所需的遺失輸入陣列（每項上限 200 字元，最多 10 項）。
  - `recommended_next_action`（選填）：建議的下一步動作（上限 500 字元）。
  - `full_report`（選填）：完整診斷報告內文（上限 10000 字元）。
- **回傳**：JSON，包含 `status`、`card`、`artifact` 檔名。

---

### aota_reviewer_artifact（審查者報告工具集）

#### 17. aota_reviewer_report_submit
- **功能**：為目前正在執行的審查者任務提交審查報告。寫入 REVIEW_CARD.json（精簡）與 REVIEW.md（完整報告）。受檢任務 ID 從任務 meta.json 讀取（非來自模型輸入）。僅限 reviewer 設定檔工作者使用。
- **參數**：
  - `verdict`（必填）：pass / fail / inconclusive。
  - `summary`（選填）：簡短審查摘要（上限 2000 字元）。
  - `key_evidence`（選填）：關鍵證據陣列（每項上限 500 字元，最多 20 項）。
  - `blocking_findings`（選填）：阻擋性發現（verdict 為 fail/inconclusive 時使用，每項上限 500 字元，最多 20 項）。
  - `missing_evidence`（選填）：遺失或無法取得的證據陣列（每項上限 500 字元，最多 20 項）。
  - `scope_findings`（選填）：範圍遵循發現（每項上限 500 字元，最多 10 項）。
  - `recommendation`（選填）：對 task-main 的建議（上限 500 字元）。
  - `full_report`（選填）：完整審查內文（上限 10000 字元）。
- **回傳**：JSON，包含 `status`、`card`、`artifact` 檔名。

---

### aota_coder_artifact（實作者報告工具集）

#### 18. aota_coder_report_submit
- **功能**：為目前正在執行的實作者任務提交報告。寫入 CARD.json（精簡）與 RESULT.md（完整報告）。僅限 coder 設定檔工作者使用。任務身分從信任的執行環境取得。
- **參數**：
  - `summary`（必填）：實作任務結果的簡短摘要（上限 2000 字元）。
  - `changed_paths`（選填）：實作任務變更的檔案列表（每項上限 500 字元，最多 50 項）。
  - `validation`（選填）：執行的驗證步驟與結果（每項上限 500 字元，最多 20 項）。
  - `risks`（選填）：已知風險或疑慮（每項上限 500 字元，最多 10 項）。
  - `follow_up`（選填）：建議的下一步或後續動作（上限 500 字元）。
  - `full_report`（選填）：完整報告內文（上限 10000 字元）。
- **回傳**：JSON，包含 `status`、`card`、`artifact` 檔名。

---

### aota_handoff（交接工具集，P8-D）

Phase 2 canonical completion path uses trusted completion delivery and the
current origin session to resolve the subject. The model supplies no internal
workspace, task, handoff, decision, receipt, revision, hash, or path fields;
the old explicit handler arguments remain compatibility-only. The normal chain
is `open current handoff → record decision → ack current handoff → read
terminal closure`.

#### 19. aota_handoff_list
- **功能**：一般 operator 查詢仍可用 bounded semantic filters；若 current completion subject 已存在，控制面直接回傳 `next_action=open_current_handoff`，不使用 list 尋找完成結果。
- **參數**：
  - `limit`（選填）：最大回傳數（預設 10，上限 50）。
  - `terminal_status`（選填）：依終端狀態過濾（done / failed / needs_input / cancelled）。
  - `profile`（選填）：依設定檔過濾（coder / debugger / reviewer）。
- **回傳**：JSON，包含 `count` 與 `handoffs` 陣列。

#### 20. aota_handoff_open
- **功能**：不帶參數即開啟 current completion handoff；控制面驗證 origin session、task/start/SPEC/project/profile、receipt/outcome、binding hashes 與 Card。若 Card 足夠，下一步為 `review_card_and_record_decision`。
- **參數**：可選 bounded semantic `handoff_ref`，不接受模型提供內部 ID。
- **回傳**：JSON，包含交接元資料、`role_artifact`、`card_content`、`card_missing`、`subject_task_id`、`needs_input_reason` 以及相關決策資訊。

#### 21. aota_handoff_ack
- **功能**：不帶參數即從 current decided handoff 解析 decision，執行冪等 ack；成功回傳 `next_action=read_terminal_closure`。模型不提供 handoff 或 decision ID。
- **參數**：`ack_ref`（選填 semantic selector）、`note`（選填，上限 4000 字元）。
- **回傳**：JSON，包含 `acknowledged`、`decision`、`idempotent` 等。

---

### aota_orchestration（編排工具集，P8-E / P9）

#### 22. aota_orchestration_decision_record
- **功能**：canonical path 只接收模型判斷與理由，控制面從 current completion handoff 建立 decision binding。
- **參數**：
  - `decision`（必填）：編排決策值（accepted / needs_followup / needs_user_input / review_required / reopen_required / no_action）。
  - `rationale`（必填）：決策理由（上限 4000 字元）。
  - `followup_task_kind`（選填）：後續任務類型（review 等，依決策值有必填或禁止的約束）。
- **回傳**：JSON，包含 `decision_id`、`decision`、`state`、`followup`、`human_checkpoint`。

#### 23. aota_followup_task_create
- **功能**：基於先前記錄的編排決策建立一個有限制的後續任務草稿。從決策成品衍生來源交接、前驅任務 ID 與受檢任務 ID。不會自動啟動任務。
- **參數**：
  - `workspace_id`（必填）：工作區識別碼。
  - `decision_id`（必填）：作為基礎的編排決策 ID。
  - `task_kind`（選填）：後續任務類型（依決策值有約束）。
  - `title`（必填）：標題（上限 200 字元）。
  - `goal`（必填）：目標描述（上限 8000 字元）。
  - `risk_level`（必填）：low / medium / high。
  - `known_inputs`（選填）：已知輸入陣列。
  - `read_scope`（必填）：可讀路徑 glob 模式陣列。
  - `write_scope`（選填）：可寫路徑 glob 模式陣列。
  - `forbidden_scope`（選填）：禁止路徑 glob 模式陣列。
  - `acceptance_criteria`（必填）：驗收標準陣列。
  - `validation_policy`（選填）：驗證政策陣列。
  - `stop_conditions`（必填）：停止條件陣列。
  - `evidence_required`（必填）：所需證據陣列。
- **回傳**：JSON，包含 `task_id`、`decision_id`、`task_kind` 等。

#### 24. aota_orchestration_decision_resume
- **功能**：透過記錄使用者輸入摘要與預期的後續任務類型，將 `needs_user_input` 決策從 `awaiting_user` 狀態轉換為 `ready_for_followup` 狀態。不會建立任何任務、更新 SPEC、核准、啟動工作者、修改來源任務、修改交接或產生新決策。
- **參數**：
  - `user_input_summary`（必填）：使用者輸入摘要（上限 4000 字元，請勿包含機密或令牌）。
  - `followup_task_kind`（必填）：恢復後預期的後續任務類型（implementation / diagnosis / review）。
- **回傳**：JSON，包含 `decision_id`、`state`、`idempotent`。

#### 25. aota_orchestration_decision_list
- **功能**：列出工作區的編排決策，按建立時間由舊到新排序。僅回傳精簡元資料（不含完整成品、原因或任務 SPEC）。預設僅列出活躍決策（awaiting_user、ready_for_followup、recorded），不列出已關閉的決策。
- **參數**：
  - `state`（選填）：依狀態過濾（awaiting_user / ready_for_followup / followup_created / closed / recorded）。
  - `limit`（選填）：最大回傳數（預設 10，上限 50）。
- **回傳**：JSON，包含 `count` 與 `decisions` 陣列（每項含 decision_id、handoff_id、source_task_id、decision、state 等）。

#### 26. aota_orchestration_decision_open
- **功能**：開啟一個精確的編排決策，回傳完整的元資料，包含來源綁定、決策值、原因、狀態、恢復元資料、後續元資料、人員檢查點、來源交接狀態與來源任務精簡狀態。不會自動開啟角色完整成品、後續 SPEC 或仓库 diff。
- **參數**：
- **參數**：可選 bounded semantic `decision_ref`；current completion decision 由控制面解析。
- **回傳**：JSON，包含完整的決策資料、`source_binding`、`source_handoff`、`source_task`、`followup`、`resume`、`human_checkpoint`。

#### 27. aota_orchestration_lineage
- **功能**：從 task_id 或 decision_id 開始的有界限世系時間線遍歷（lineage timeline traversal）。遵循權威關係：task → produced_handoff → produced_decision → created_followup → predecessor_task。最多 20 個節點，含循環偵測。在前驅不明確時，應在建立後續任務前使用此工具。
- **參數**：
  - `workspace_id`（必填）：工作區識別碼。
  - `task_id`（選填）：開始遍歷的任務 ID（與 decision_id 互斥）。
  - `decision_id`（選填）：開始遍歷的決策 ID（與 task_id 互斥）。
- **備註**：必須提供 task_id 或 decision_id 其中之一，不可同時提供。
- **回傳**：JSON，包含 `timeline`（節點陣列）、`cycle_detected`、`truncated`、`missing_nodes`。

---

### aota_operator（運營者工具集，P10）

#### 28. aota_operator_inbox_list
- **功能**：列出工作區的運營者收件匣項目。掃描交接、決策、任務以建立統一的收件匣。項目按優先級（數字越低越緊急）與建立時間排序。支援依 item_type、priority_max、consistency_status 過濾。唯讀：不修改任何檔案。
- **參數**：
  - `limit`（選填）：最大回傳項目數（預設 20，上限 100）。
  - `item_type`（選填）：依項目類型過濾（pending_handoff / awaiting_user / ready_for_followup / draft_requires_approval / approved_ready_to_start / draft_ready_to_start / needs_input_task_without_decision / failed_task_unconsumed / cancelled_task_unconsumed / timeout_task_unconsumed / broken_lineage / artifact_gap / running_task）。
  - `priority_max`（選填）：僅顯示優先級≤此值的項目。
  - `consistency_status`（選填）：依一致性狀態過濾（ok / warning / broken）。
  - `include_informational`（選填）：是否包含資訊性項目（running_task，預設 false）。
- **回傳**：JSON，包含 `count` 與 `items` 陣列（每項含 item_id、item_type、priority、summary、recommended_action 等）。

#### 29. aota_operator_inbox_open
- **功能**：不帶參數即開啟 current relevant completion item，附帶 Card/evidence projection；一般 operator semantic search 仍可保留 bounded filter。唯讀，不修改檔案。
- **參數**：
  - `inbox_ref`（選填）：bounded semantic selector，預設 current relevant item。
- **回傳**：JSON，包含 `item_id`、`item_type` 以及依類型不同的證據（handoff、decision、task、approval 資料及相關 artifacts）。

#### 30. aota_operator_consistency_check
- **功能**：對工作區執行一致性稽核。執行 23+ 條一致性規則，針對交接、決策與任務成品。接受 workspace_id 與恰好一個（task_id / handoff_id / decision_id）。回傳整體狀態（ok / warning / broken）與詳細的檢查陣列（含證據）。唯讀，不修改檔案。
- **參數**：
  - `workspace_id`（必填）：工作區識別碼。
  - `task_id`（選填）：檢查特定任務的一致性（與 handoff_id、decision_id 互斥）。
  - `handoff_id`（選填）：檢查特定交接的一致性（與 task_id、decision_id 互斥）。
  - `decision_id`（選填）：檢查特定決策的一致性（與 task_id、handoff_id 互斥）。
- **備註**：必須提供 task_id、handoff_id 或 decision_id 其中之一，不可同時提供多個。
- **回傳**：JSON，包含 `status`（ok/warning/broken）、`checks` 陣列（每項含 `code`、`status`、`evidence`）。

#### 31. aota_project_registry_refresh
- **功能**：在 registered workspace 內掃描並重建 derived Registry。使用 bounded lock、同目錄 tempfile、fsync 與 atomic replace；不修改 project.yaml/observed.json。
- **參數**：`workspace_id`（必填）、`dry_run`、`include_invalid`。

#### 32. aota_project_registry_open
- **功能**：唯讀開啟 Registry metadata，回傳 schema/revision/fingerprint、fresh/stale/missing/invalid 狀態與 bounded records；不自動 refresh。
- **參數**：`workspace_id`（必填）。

#### 33. aota_project_relationship_brief
- **功能**：將 Registry search candidates 投影成 task-main decision support，提供 declared capabilities、relevant paths、relationship options 與 uncertainty；不替 task-main 作最終決策。
- **參數**：`workspace_id`、`request_summary`（必填）、`limit`、`candidate_project_ids`。

#### 34. aota_project_prepare
- **功能**：從 selected project 的 Registry candidate 與重新驗證的 canonical manifest 產生 bounded Unified Project Brief；不執行 command、不 refresh Registry、不寫入任何檔案。
- **參數**：`workspace_id`、`project_id`（必填），以及 `include_commands`、`include_constraints`、`include_plan_reference`、`max_warnings`。

### Runtime current-state authority

`profile-tasks/` 保存 durable historical truth：SPEC、task、receipt、outcome、Card/report、handoff 與 decision。`session-state/<workspace>/<project>/<session_digest>/` 保存目前 session 的 bounded pointers；`indexes/` 僅預留給可重建 derived data，不能成為 current authority。

所有 current semantic references（`current_draft_spec`、`active_frozen_spec`、`active_task`、`current_completion`、`current_handoff`、`current_decision`、`current_completed_task`）都必須先由 trusted runtime context 讀取 exact session-state pointer，再驗證 durable artifact。正常路徑不得掃描歷史、依 mtime/latest/first 或 workspace-wide uniqueness 猜測 subject。pointer writer 使用 per-pointer lock、同目錄 temporary file、flush/fsync、atomic replace 與 exact binding validation；pointer stale/mismatch 或 trusted context 缺失時 fail-closed。

既有 `session-active-spec/` 與舊 runtime 只作 bounded compatibility fallback；canonical 新 session 不會因 pointer 缺失而靜默改用其他 session 或 workspace history。

---

### 工具集與設定檔對應矩陣

| 設定檔 | 啟用的工具集 | 角色 |
|---------|-------------|------|
| **task-main** | 編排/control-plane 唯讀工具，加上 CodeGraph read/rebuild | 編排者—分類、Plan/SPEC lifecycle、核准、分派、決策；無 source write |
| **project-steward** | project read/steward mutation、CodeGraph read、steward artifact | 專案事實與有界 docs/artifact continuity；無 source/terminal/rebuild |
| **coder** | readonly/project/CodeGraph read、Coder file mutation、fixed command runner、artifact | 實作工作者—frozen SPEC 有界寫入；無 unrestricted file/terminal |
| **debugger** | aota_core, aota_fs_readonly, aota_repo_readonly, aota_web_readonly, aota_worker_outcome, aota_debugger_artifact | 唯讀診斷，不進行修改 |
| **reviewer** | aota_core, aota_fs_readonly, aota_repo_readonly, aota_worker_outcome, aota_reviewer_artifact | 唯讀審查，不進行修改 |
| **architect** | aota_core, aota_fs_readonly, aota_repo_readonly, aota_web_readonly, aota_worker_outcome, aota_architect_artifact | 唯讀設計審查與規格預檢 |

工作者設定檔（coder、debugger、reviewer、architect）與 `aota_profile_task` 及 `aota_task_spec` 工具集隔離——它們無法建立、核准、啟動或取消設定檔任務。
