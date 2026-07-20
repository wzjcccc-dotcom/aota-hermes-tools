# Project Continuity Foundation — Project Contract

PCF-WI-01 定義專案的低頻 declared metadata 與可重建 observed snapshot。它不是 Registry、bootstrap 或 reconcile。

## Declared contract

`<project-root>/.aota/project.yaml` 使用 YAML safe loader，schema version 固定為 `1`。top-level 欄位為：`schema_version`、`project`、`summary`、`capabilities`、`paths`、`commands`、`runtime`、`codegraph`、`plan`、`constraints`；unknown fields、duplicate keys、錯誤型別都拒絕。

`project.id` 必須是 bounded lowercase kebab-case，`project.name`、`kind`、`status` 受長度與 enum 限制。manifest 內路徑一律相對 project root，拒絕 absolute、`..`、反斜線、NUL 與 symlink escape。不存在的 optional path 可接受，但會在 Card 產生 warning。

`plan.active_plan_id` 只能是 `null` 或 `plan_<kebab-case>`；它只是 optional reference，不修改 Plan v1。

## Observed contract

`<project-root>/.aota/observed.json` 是工具重建資料，包含 filesystem、git、codegraph、plan snapshot。它缺失不使 declared manifest 無效；損壞或 schema 不符則標為 `observed_invalid`。observed 永遠不能覆寫 declared，衝突只產生 warning。

## Security boundary

PCF tools 只接受 registered `workspace_id` 與安全 `project_id`，不接受任意 path、shell、executable 或寫入操作；掃描排除 `.git`、`.venv`、`node_modules`、`.codegraph`、deployment/runtime roots，且不讀秘密檔。

## 本階段不包含

Persisted Registry、CodeGraph CLI/init/sync/index、bootstrap/reconcile、Project Steward、Plan schema 修改、deploy/reload/restart、runtime/live smoke 與 Profile routing。
