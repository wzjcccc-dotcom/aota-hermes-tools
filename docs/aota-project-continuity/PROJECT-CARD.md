# Project Card

Project Card 是給 task-main/Profile 使用的 compact projection，不是完整 source truth，也不回傳原始 YAML。

## Input

Card 由已驗證的 `.aota/project.yaml` 產生，可選擇加入已驗證的 `.aota/observed.json`。declared 欄位保持 canonical；observed 僅提供 git、CodeGraph 與 Plan snapshot，缺失或衝突進入 `warnings`。

## Deterministic fields

輸出固定包含：`project_id`、`name`、`kind`、`status`、`summary`、`capabilities`、`root`、`relevant_paths`、`commands`、`git`、`codegraph`、`active_plan`、`constraints`、`warnings`。JSON response 使用排序 key、bounded 內容與 64 KiB cap。

範例：

```json
{"project_id":"aota-hermes-tools","name":"AOTA Hermes Tools","kind":"hermes-tooling","status":"active","root":".","git":null,"warnings":[]}
```

`active_plan.declared` 是 project manifest 的 reference；`active_plan.observed` 是 snapshot，不能當 Plan canonical truth。缺少 optional path 或找不到 referenced Plan 時只增加 warning，不改檔。

## 使用方式

`aota_project_scan` 回傳合法 manifest 的 bounded candidate records；`aota_project_search` 只在 scan candidates 的 id/name/summary/capabilities/kind 上做 deterministic keyword matching；`aota_project_open` 只依 `project_id` 開啟 compact Card。任何 operation 都是唯讀。
