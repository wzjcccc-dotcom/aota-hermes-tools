# Unified Project Brief v1

Unified Project Brief 是由已驗證資料生成的 deterministic projection，不是 canonical truth；canonical truth 仍是 `project.yaml`，observed 只是不保證新鮮的可重建 snapshot。

## Top-level schema

固定欄位：`status`、`schema_version`、`workspace_id`、`project_id`、`project`、`identity`、`registry`、`capabilities`、`relevant_paths`、`readiness`、`warnings`、`evidence`、`truncated`，以及選用的 `commands`、`constraints`、`plan`。

`identity` 指向 manifest path 與 SHA-256；`registry` 只保留 status/revision/fingerprint/source；`evidence` 只保留可追溯摘要。`readiness.codegraph` 只會投影 `source_wrapper_available_runtime_not_verified` 或 `runtime_unavailable`，不執行 CLI，也不宣稱 runtime ready。Brief 不嵌入 manifest、Registry、observed 或 Project Card 全文。

## Priority and readiness

Declared manifest 永遠優先於 observed。observed plan 與 declared plan 衝突時回報 warning；不自動修正。readiness 個別表示 manifest、registry、project_card、observed、git、codegraph、plan、deployment、runtime，沒有總分與假性 runtime ready。

## Bounds

warnings 預設至多 20；輸出目標為 32 KiB、硬上限 64 KiB。超出目標時依序縮減 warnings、commands 與 relevant path 尾端，仍維持 valid schema 並標記 `truncated=true`。