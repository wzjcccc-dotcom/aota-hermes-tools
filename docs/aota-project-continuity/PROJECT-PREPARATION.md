# Project Preparation

PCF-WI-04 將一個已選定的 `project_id` 投影成唯讀、bounded 的 Unified Project Brief，供 task-main、architect、coder、reviewer 與 debugger 共用。

## Boundary

`aota_project_prepare` 只接受 registered `workspace_id` 與 exact kebab-case `project_id`。它不接受 path、glob、shell、executable 或 output path，也不寫入 manifest、observed、Registry、Plan 或 Task SPEC。

## Resolution

工具先讀取固定 Registry 作 candidate lookup，再以其 manifest reference（或 workspace-root canonical fallback）重新讀取並驗證 `.aota/project.yaml`。Registry record SHA 與 live manifest SHA 不同時標為 `stale` 並回報 `registry_manifest_mismatch`；不 refresh Registry。

## Projection

relevant paths、commands 與 constraints 只取自 validated manifest。路徑保留 declared category，並拒絕 escape／symlink boundary；缺失路徑產生 warning。commands 一律為 `source=declared`、`execution_status=not_executed`。

readiness 是 source-level placeholder：Git 不主動觀測，CodeGraph 永遠 `runtime_not_verified`，deployment 依 manifest 明示 human checkpoint，runtime 不會被宣告 ready。

## Exclusions

本階段不執行 Git、CodeGraph、command、Registry refresh、observed refresh、Plan mutation、deploy、reload、restart 或 runtime/live smoke。