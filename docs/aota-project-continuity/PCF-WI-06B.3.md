# PCF-WI-06B.3 — CodeGraph Mutation Result and Lock Diagnostics

## Scope

本 Work Item 只改善 CodeGraph maintenance wrapper 的結果捕捉、判讀、lock 診斷、receipt 與 retry eligibility。不得修改 lifecycle state model，不新增 unlock/force/retry/raw-argv input，也不執行真實 CodeGraph mutation。

## Source closure

施工涵蓋：

- `_codegraph_result.py`：bounded CLI parser、mutation outcome taxonomy、post-status reconciliation、純 retry gate。
- `_codegraph_lock.py`：project-relative lock diagnosis；拒絕 symlink/path escape；不 unlink、不 kill。
- `_codegraph_maintenance.py`：bounded process capture、continued drain、timeout/process-group cleanup、receipt v2。
- `verify-project-continuity.py`：既有隔離 smoke 會執行 parser/outcome/lock/retry fixtures 與 fake launcher。
- `CODEGRAPH-MUTATION-RESULTS.md`：CodeGraph 1.1.1 confirmed contract 與限制。

## Receipt v2

Receipt 新增 `process`、`cli_result`、`post_status`、`lock_diagnostics`、`outcome`、`error_code`、`retryable`、`reconciled` 與 `retry_eligibility`。Receipt 不包含 approval token、private environment、executable path、raw stdout/stderr 或完整 source path。舊 receipt 不 retroactively 修改。

## Required invariants

- `exit_code=0` 不單獨判定 mutation 成功。
- post-status 是 index 最終狀態 authority。
- `stale + zero change + pending unchanged` 是 `zero_change_unexpected`。
- explicit CLI lock failure 才能判 `lock_not_acquired`；filesystem lock 只能是 inferred diagnosis。
- parser output 超過 cap 時繼續 drain 並標記 truncation，不因合法 progress 直接終止 mutation。
- retry 只產生判斷，不自動執行，且要求新 approval、新 token、新 Human Checkpoint。

## Execution boundary

```text
REAL_INDEX_MUTATION=NOT_EXECUTED
DEPLOYMENT=NOT_EXECUTED
RELOAD=NOT_EXECUTED
RESTART=NOT_EXECUTED
```
