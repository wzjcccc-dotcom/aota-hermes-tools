# Project Registry

## 定位

Registry 是 workspace-level 的 **scan-derived、rebuildable、machine-maintained、non-canonical** 索引。唯一 canonical source 仍是各專案的 `.aota/project.yaml`；Registry 不覆寫 manifest、不保存完整 YAML、README、source tree、secret 或 observed truth。

## 路徑與邊界

對註冊 workspace `workspace_id`，工具固定推導：

```text
<registered-workspace>/.aota/registry/projects.json
<registered-workspace>/.aota/registry/projects.lock
```

工具只接受 registered `workspace_id`，不接受 root、registry path、shell、executable 或 output path。掃描以 `resolve_workspace()` 得到的 root 為邊界，拒絕 symlink directory escape，排除 `.git`、virtualenv、`node_modules`、`.codegraph`、deployment/runtime 與 deploy receipt 目錄；並精確排除 `deploy/runtime-projects/**/.aota/project.yaml`，因其是 runtime projection 而非 canonical candidate。一般 `deploy/` 內容不因此排除。

## Schema v1

Registry 包含：`schema_version`、`workspace_id`、`registry_revision`、`generated_at`、`source_fingerprint`、`project_count`、`projects`、`invalid_projects`、`duplicate_project_ids`、`scan_warnings`。

每個 project record 只保存搜尋/briefing 必要的 declared projection：`project_id`、workspace-relative `root`、`manifest_path`、`manifest_sha256`、`name`、`kind`、`status`、bounded `summary`、`capabilities`、`keywords`、`relevant_paths`、`commands`、`active_plan_id`、`constraints`、`warnings`。不保存絕對路徑。

invalid project 不進 `projects`。duplicate ID 會把該 ID 的所有 manifest 記入 `invalid_projects` 與 `duplicate_project_ids`，不靜默挑選一份。

## Revision 與 fingerprint

首次成功 refresh 為 revision 1。fingerprint 是 deterministic SHA-256，涵蓋 workspace identity、manifest path/hash、valid/invalid/duplicate 集合；不涵蓋 timestamp 或 filesystem mtime。相同 source fingerprint 且 records 未變時 refresh 回傳 `changed=false` 並維持 revision；manifest 或集合改變才增加 revision。

## Refresh

`aota_project_registry_refresh` 流程：resolve workspace → bounded lock → scan/validate → duplicate detection → deterministic build → compare → tempfile 同目錄寫入、flush/fsync、atomic replace、directory fsync → receipt。lock、output、manifest size 都有界限。既有 Registry malformed 時可安全重建並回報 warning；失敗不應留下 partial Registry，也不修改 manifest 或 observed.json。

## Open 與 stale

`aota_project_registry_open` 是唯讀，不自動 refresh。狀態為：`fresh`、`stale`、`missing`、`invalid`。stale 證據包括 manifest set、manifest hash、project ID、invalid/duplicate 集合或 workspace identity 改變；timestamp 不是唯一證據。

## Search 與 fallback

`aota_project_search` 優先使用 fresh Registry，排序為 deterministic score、active 優先、project ID tie-break。exact project ID/name 高於 capability/kind，再高於 summary/keyword。query 與 limit bounded。

Registry missing/stale/invalid 時不暗中寫 Registry；此版本使用既有 bounded live scan fallback，回傳 `source=live_scan`、Registry 狀態與 warning。Registry fresh 時回傳 `source=registry`。

## Git policy

Registry 是 generated artifact；canonical source 可用精準 `.aota/registry/` ignore 規則排除，但不可忽略整個 `.aota/`，因 `.aota/project.yaml` 是 canonical source。本 Work Item 不建立 canonical repo 真實 Registry。

## 不包含

本契約不包含 observed refresh、Project Preparation、bootstrap、reconcile、CodeGraph integration、Plan schema、SQLite/daemon/API、deploy/reload/restart 或 live Profile smoke。
