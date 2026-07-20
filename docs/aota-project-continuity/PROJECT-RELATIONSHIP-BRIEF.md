# Project Relationship Brief

## 目的

`aota_project_relationship_brief` 將 Registry search candidates 投影成 task-main 可直接消費的 bounded decision support。它回答「哪些專案可能相關、為什麼、哪些 declared capability 可沿用」，不替 task-main 做最終 reuse/extend/new 決策。

## 輸入

```json
{
  "workspace_id": "aota-hermes-tools",
  "request_summary": "restricted Hermes plugin",
  "limit": 3
}
```

也可提供最多 10 個 `candidate_project_ids` 作為 bounded filter。工具不接受 project path、README、source tree、CodeGraph query、shell 或 Plan/SPEC mutation。

## Evidence source

候選只來自 fresh Registry，或明確標示為 `source=live_scan` 的 fallback。capability 只能來自 manifest `capabilities`；relevant paths、commands、constraints、active plan reference 只能來自同一份 declared Project Contract。observed data 不提升為 declared truth，資料夾名稱不視為 capability。

每個 reusable capability 都帶：

```yaml
capability: <declared value>
source_field: capabilities
project_id: <candidate id>
```

## Relationship options

工具輸出的 bounded options：

- `reuse`：已有 declared capability 可能直接沿用。
- `extend`：候選在 request 上有較強 evidence，可能值得深入評估擴充。
- `experiment`：有部分關聯，但仍需隔離驗證。
- `new`：目前 Registry 找不到足夠相關候選；不是自動建立專案。
- `uncertain`：證據不足，不硬判方向。

分數、relevance level、match reasons、matched fields 都是 deterministic ranking evidence。`strongest_candidate` 只是排序後的最強候選，不是架構決策。

## 輸出與限制

輸出包含 Registry status/revision/stale/source、bounded candidates、relationship options、relevance、declared reusable capabilities、relevant paths、constraints、active plan reference、warnings，以及 `decision_support`。若沒有足夠 evidence，`strongest_candidate` 為 null，並提醒 task-main 進一步調查；不自動建立 project、experiment、Plan、SPEC 或 Profile。

Registry stale/missing/invalid 時 briefing 保留狀態與 limitation，不自動 refresh、不暗中寫入。inactive candidate 只在明確 candidate filter 時納入，並附 `inactive_project` warning。
