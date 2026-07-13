# AOTA Forge — Canonical Source Repository

This repository is the **canonical source of truth** for AOTA Forge — a controlled agentic software delivery control plane driven by specifications, role separation, bounded tools, durable handoffs, and human checkpoints. It contains the AOTA Profile Task plugin, profiles, AOTA Forge skills, and deploy toolchain.

---

## What This Repo Contains

| Path | Content |
|------|---------|
| `plugin/aota-tools/` | 52 Python files + `plugin.yaml` — the AOTA narrow-tools plugin |
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
| `VERSION` | Canonical version (`0.17.1`) |

---

## Host Deploy and Container Runtime Paths

The deploy, verify, and rollback scripts operate only on the host deploy destination:

```text
/home/latios/.hermes
```

Use `AOTA_HERMES_HOME_HOST=/custom/path` to select another existing absolute host root. The value must not be `/`, relative, empty, or contain control characters.

Docker Compose bind-mounts that host root into the WebUI container runtime view:

```text
host:      /home/latios/.hermes
container: /home/hermeswebui/.hermes
```

The container path is explanatory only; it is never a deployment target.

| Source | Host deploy destination |
|--------|-------------------------|
| `plugin/aota-tools/*.py + plugin.yaml` | `${AOTA_HERMES_HOME_HOST-/home/latios/.hermes}/plugins/aota-tools/` |
| `profiles/*/config.yaml + SOUL.md` | `${AOTA_HERMES_HOME_HOST-/home/latios/.hermes}/profiles/*/` |
| `skills/*/SKILL.md` | `${AOTA_HERMES_HOME_HOST-/home/latios/.hermes}/skills/*/` |

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
| **task-main** | aota_core, aota_fs_readonly, aota_repo_readonly, aota_web_readonly, aota_task_spec, aota_profile_task, aota_handoff, aota_orchestration, aota_operator | Orchestrator — flow classification, SPEC, dispatch, decisions |
| **coder** | aota_core, aota_fs_readonly, aota_worker_outcome, aota_coder_artifact | Implementation worker — spec-driven, bounded writes |
| **debugger** | aota_core, aota_fs_readonly, aota_repo_readonly, aota_web_readonly, aota_worker_outcome, aota_debugger_artifact | Read-only diagnosis, no mutation |
| **reviewer** | aota_core, aota_fs_readonly, aota_repo_readonly, aota_worker_outcome, aota_reviewer_artifact | Read-only review, no mutation |
| **architect** | aota_core, aota_fs_readonly, aota_repo_readonly, aota_web_readonly, aota_worker_outcome, aota_architect_artifact | Read-only design review & spec preflight |

- **32 tools** provided by the plugin (listed in `plugin.yaml` `provides_tools`)
- **16 toolsets** defined in `__init__.py`: `aota_core`, `aota_fs_readonly`, `aota_repo_readonly`, `aota_web_readonly`, `aota_fs_copy`, `aota_task_spec`, `aota_profile_task`, `aota_worker_outcome`, `aota_debugger_artifact`, `aota_reviewer_artifact`, `aota_coder_artifact`, `aota_architect_artifact`, `aota_handoff`, `aota_orchestration`, `aota_operator`, `aota_plan_read`
- **5 profiles** (task-main, coder, debugger, reviewer, architect)

Worker profiles (coder, debugger, reviewer, architect) are isolated from `aota_profile_task` and `aota_task_spec` toolsets — they cannot create, approve, start, or cancel profile tasks.

---

## Versioning

- **`VERSION`** file at repository root holds the canonical version (`0.17.1`)
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

---

## AOTA Forge Delivery Lifecycle

AOTA Forge defines three flow paths:

| Path | Applies to | Flow |
|------|-----------|------|
| **Fast** | Clear requirements, low risk, local changes | SPEC → approval → coder → minimal validation → decision |
| **Standard** | Multi-file, new features, medium risk | Convergence → SPEC → architect preflight (if risk warrants) → coder → reviewer (if needed) → validation (if needed) → decision |
| **Deep** | Control plane, auth, migrations, high blast radius | Convergence → DESIGN → architect design_review → SPEC → architect spec_preflight → Human approval → coder → reviewer → E2E → close |

**Architect gate**: Available (P11-J). Architect profile performs pre-construction design review and spec preflight. Verdict: approve / approve_with_changes / block / inconclusive. block/inconclusive prevents implementation. approve_with_changes requires correction before proceeding.

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

## 工具功能中文使用說明（31 個工具）

AOTA（Architect-Overseer Task Automation）外掛程式提供 31 個狹義工具（narrow tools），分屬於 15 個工具集（toolsets），支援 5 個 Hermes 設定檔（task-main、coder、debugger、reviewer、architect）。以下依工具集分組說明每個工具的功能、參數與使用時機。

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

#### 9. aota_task_spec_create
- **功能**：建立一個有限制的 AOTA 任務規格成品（draft SPEC）。僅建立草稿，不核准也不開始執行。應在任何設定檔執行任務之前使用。支援三種任務類型：implementation（實作）、diagnosis（診斷）、review（審查）。含完整的範圍表示式驗證、語意規則檢查、欄位長度限制等。
- **參數**：
  - `workspace_id`（必填）：工作區識別碼。
  - `task_kind`（必填）：implementation / diagnosis / review。
  - `title`（必填）：標題（上限 200 字元）。
  - `goal`（必填）：目標描述（上限 8000 字元）。
  - `risk_level`（必填）：low / medium / high。
  - `known_inputs`（選填）：已知輸入/上下文參考陣列。
  - `read_scope`（必填）：定義可讀路徑的 glob 模式陣列。
  - `write_scope`（選填）：定義可寫路徑的 glob 模式陣列。
  - `forbidden_scope`（選填）：明確禁止的 glob 模式陣列。
  - `acceptance_criteria`（必填）：可衡量的驗收標準陣列。
  - `validation_policy`（選填）：驗證/審查政策項目陣列。
  - `stop_conditions`（必填）：停止任務執行的條件陣列。
  - `evidence_required`（必填）：完成所需的證據項目陣列。
  - `subject_task_id`（選填）：受檢任務 ID（審查任務必填）。
  - `parent_task_id`（選填）：父任務 ID。
- **回傳**：JSON，包含 `task_id`、`status`、`profile_hint`、`spec_sha256` 等。

#### 10. aota_task_spec_update
- **功能**：修改現有的草稿 AOTA 任務規格。需要樂觀修訂版號比對（optimistic revision matching）。不核准也不開始執行。可更新所有可變規格欄位，並在需要時清除舊的 `needs_input` 狀態與核准記錄。
- **參數**：
  - `workspace_id`（必填）：工作區識別碼。
  - `task_id`（必填）：要更新的現有任務 ID。
  - `expected_revision`（必填）：預期的目前修訂版號（樂觀鎖定用）。
  - `title` / `goal` / `risk_level` / `known_inputs` / `read_scope` / `write_scope` / `forbidden_scope` / `acceptance_criteria` / `validation_policy` / `stop_conditions` / `evidence_required`（均為選填）：要更新的欄位。
- **回傳**：JSON，包含 `status`、`revision`、`spec_sha256` 等。

---

### aota_profile_task（設定檔任務工具集）

#### 11. aota_profile_task_start
- **功能**：使用衍生自任務類型的固定命名設定檔，啟動一個現有的已驗證 AOTA 草稿任務。驗證精確的修訂版號與 SPEC SHA-256，從 task_kind 衍生設定檔（implementation→coder、diagnosis→debugger、review→reviewer），並使用 Hermes 背景完成軌道。對於實作任務：僅在收到明確的人員核准（精確修訂版/雜湊）後才可呼叫。
- **參數**：
  - `workspace_id`（必填）：工作區識別碼。
  - `task_id`（必填）：要啟動的現有 AOTA 任務 ID。
  - `expected_revision`（必填）：預期的 SPEC 修訂版號。
  - `expected_spec_sha256`（必填）：預期的 SPEC.md 完整 SHA-256 雜湊。
  - `timeout_seconds`（選填）：工作者程序的超時秒數（30–86400）。
- **回傳**：JSON，包含 `status`、`start_id`、`profile`、`process_session_id` 等。

#### 12. aota_profile_task_status
- **功能**：查詢 AOTA 設定檔任務的狀態，含生命週期調解（reconciliation）。回傳目前的任務狀態、終端狀態、收據狀態、注册表狀態與調解元資料。對於執行中的任務，會嘗試透過完成收據或程序注册表進行調解。冪等（idempotent）：不修改終端任務。
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
- **功能**：為草稿 AOTA 任務記錄明確的人員檢查點核准（P8-C）。建立 APPROVAL.json。僅適用於實作任務（診斷/審查不需要核准）。不開始執行、不修改 SPEC、不選擇設定檔、不建立其他任務。核准綁定精確的修訂版號+雜湊；規格更新會使核准失效。
- **參數**：
  - `workspace_id`（必填）：工作區識別碼。
  - `task_id`（必填）：要核准的現有 AOTA 任務 ID。
  - `expected_revision`（必填）：預期的 SPEC 修訂版號。
  - `expected_spec_sha256`（必填）：預期的 SPEC.md SHA-256 雜湊。
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

#### 19. aota_handoff_list
- **功能**：列出工作區中待處理的持久交接（pending handoffs），按建立時間由舊到新排序。回傳精簡元資料，不含完整卡片內容。可選擇性地依 terminal_status 或 profile 過濾。每個交接項目會一併顯示相關的決策元資料（decision state、awaiting_user、followup_task_id）。
- **參數**：
  - `workspace_id`（必填）：工作區識別碼。
  - `limit`（選填）：最大回傳數（預設 10，上限 50）。
  - `terminal_status`（選填）：依終端狀態過濾（done / failed / needs_input / cancelled）。
  - `profile`（選填）：依設定檔過濾（coder / debugger / reviewer）。
- **回傳**：JSON，包含 `count` 與 `handoffs` 陣列。

#### 20. aota_handoff_open
- **功能**：開啟一個精確的持久交接，回傳其元資料與精簡角色卡片內容（CARD.json / DIAGNOSIS_CARD.json / REVIEW_CARD.json）。不會自動開啟完整報告成品（RESULT.md / DIAGNOSIS.md / REVIEW.md）。若角色卡片遺失，回傳 `card_missing=true` 且交接維持待處理狀態。
- **參數**：
  - `workspace_id`（必填）：工作區識別碼。
  - `handoff_id`（必填）：要開啟的交接 ID（如 `ho_20250101T120000_a1b2c3d4`）。
- **回傳**：JSON，包含交接元資料、`role_artifact`、`card_content`、`card_missing`、`subject_task_id`、`needs_input_reason` 以及相關決策資訊。

#### 21. aota_handoff_ack
- **功能**：確認（acknowledge）一個持久交接的消費。將交接從 pending/ 移動到 acknowledged/，並在旁建立 ack 成品。原子性操作，相同決策冪等，拒絕衝突的第二個確認。確認不會自動分派任何任務或核准任何結果。
- **參數**：
  - `workspace_id`（必填）：工作區識別碼。
  - `handoff_id`（必填）：要確認的交接 ID。
  - `decision`（必填）：編排決策（accepted / needs_followup / needs_user_input / review_required / reopen_required / no_action）。
  - `note`（選填）：選擇性的附註（上限 4000 字元）。
- **回傳**：JSON，包含 `acknowledged`、`decision`、`idempotent` 等。

---

### aota_orchestration（編排工具集，P8-E / P9）

#### 22. aota_orchestration_decision_record
- **功能**：為一個交接記錄編排決策（orchestration decision）。建立持久的決策成品，捕捉應基於交接完成訊號採取的行動。不會自動建立任何任務、啟動工作者、修改交接、修改確認，或接受模型指定的來源任務/交接/前驅任務 ID。
- **參數**：
  - `workspace_id`（必填）：工作區識別碼。
  - `handoff_id`（必填）：要記錄決策的交接 ID。
  - `decision`（必填）：編排決策值（accepted / needs_followup / needs_user_input / review_required / reopen_required / no_action）。
  - `reason`（選填）：決策原因/上下文（上限 4000 字元）。
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
  - `workspace_id`（必填）：工作區識別碼。
  - `decision_id`（必填）：要恢復的編排決策 ID。
  - `user_input_summary`（必填）：使用者輸入摘要（上限 4000 字元，請勿包含機密或令牌）。
  - `followup_task_kind`（必填）：恢復後預期的後續任務類型（implementation / diagnosis / review）。
- **回傳**：JSON，包含 `decision_id`、`state`、`idempotent`。

#### 25. aota_orchestration_decision_list
- **功能**：列出工作區的編排決策，按建立時間由舊到新排序。僅回傳精簡元資料（不含完整成品、原因或任務 SPEC）。預設僅列出活躍決策（awaiting_user、ready_for_followup、recorded），不列出已關閉的決策。
- **參數**：
  - `workspace_id`（必填）：工作區識別碼。
  - `state`（選填）：依狀態過濾（awaiting_user / ready_for_followup / followup_created / closed / recorded）。
  - `limit`（選填）：最大回傳數（預設 10，上限 50）。
- **回傳**：JSON，包含 `count` 與 `decisions` 陣列（每項含 decision_id、handoff_id、source_task_id、decision、state 等）。

#### 26. aota_orchestration_decision_open
- **功能**：開啟一個精確的編排決策，回傳完整的元資料，包含來源綁定、決策值、原因、狀態、恢復元資料、後續元資料、人員檢查點、來源交接狀態與來源任務精簡狀態。不會自動開啟角色完整成品、後續 SPEC 或仓库 diff。
- **參數**：
  - `workspace_id`（必填）：工作區識別碼。
  - `decision_id`（必填）：要開啟的編排決策 ID。
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
  - `workspace_id`（必填）：工作區識別碼。
  - `limit`（選填）：最大回傳項目數（預設 20，上限 100）。
  - `item_type`（選填）：依項目類型過濾（pending_handoff / awaiting_user / ready_for_followup / draft_requires_approval / approved_ready_to_start / draft_ready_to_start / needs_input_task_without_decision / failed_task_unconsumed / cancelled_task_unconsumed / timeout_task_unconsumed / broken_lineage / artifact_gap / running_task）。
  - `priority_max`（選填）：僅顯示優先級≤此值的項目。
  - `consistency_status`（選填）：依一致性狀態過濾（ok / warning / broken）。
  - `include_informational`（選填）：是否包含資訊性項目（running_task，預設 false）。
- **回傳**：JSON，包含 `count` 與 `items` 陣列（每項含 item_id、item_type、priority、summary、recommended_action 等）。

#### 29. aota_operator_inbox_open
- **功能**：依 item_id 開啟一個精確的運營者收件匣項目，附帶證據投影（evidence projection）。確定性地解析 item_id 以找到確切的交接、決策、任務或核准成品。回傳精簡元資料與基於 item_type 的相關證據投影。不接受路徑/task_id/handoff_id/decision_id 覆寫。唯讀，不修改檔案。
- **參數**：
  - `workspace_id`（必填）：工作區識別碼。
  - `item_id`（必填）：精確的運營者收件匣項目 ID（格式如 `oi_handoff_<hid>`、`oi_decision_<did>`、`oi_task_<tid>_<reason>`、`oi_approval_<tid>`）。
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

---

### 工具集與設定檔對應矩陣

| 設定檔 | 啟用的工具集 | 角色 |
|---------|-------------|------|
| **task-main** | aota_core, aota_fs_readonly, aota_repo_readonly, aota_web_readonly, aota_task_spec, aota_profile_task, aota_handoff, aota_orchestration, aota_operator | 架構師/編排者—核准、分派、審查世系 |
| **coder** | aota_core, aota_fs_readonly, aota_worker_outcome, aota_coder_artifact | 實作工作者—僅有界限寫入權限 |
| **debugger** | aota_core, aota_fs_readonly, aota_repo_readonly, aota_web_readonly, aota_worker_outcome, aota_debugger_artifact | 唯讀診斷，不進行修改 |
| **reviewer** | aota_core, aota_fs_readonly, aota_repo_readonly, aota_worker_outcome, aota_reviewer_artifact | 唯讀審查，不進行修改 |
| **architect** | aota_core, aota_fs_readonly, aota_repo_readonly, aota_web_readonly, aota_worker_outcome, aota_architect_artifact | 唯讀設計審查與規格預檢 |

工作者設定檔（coder、debugger、reviewer、architect）與 `aota_profile_task` 及 `aota_task_spec` 工具集隔離——它們無法建立、核准、啟動或取消設定檔任務。
