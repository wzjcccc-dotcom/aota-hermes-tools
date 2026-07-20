# PCF-WI-02/03 — Project Registry and Relationship Briefing

## Scope completed

- Derived Registry schema v1 under registered workspace `.aota/registry/projects.json`。
- Deterministic source fingerprint、revision、不必要 bump 抑制。
- Lock + same-directory tempfile/fsync/atomic replace refresh。
- Read-only Registry open 與 manifest/hash/identity based stale detection。
- Registry-backed deterministic search，missing/stale/invalid 明確 live-scan fallback。
- Compact relationship briefing，保留 declared evidence 與 uncertainty，不作最終決策。
- Plugin registration、source smoke、契約文件與下一 Work Item handoff。

## Acceptance mapping

| Requirement | Source |
|---|---|
| Registry scan/contract/revision/stale/atomic write | `plugin/aota-tools/_project_registry.py` |
| Search and source attribution | `plugin/aota-tools/_project_open.py`, `_project_registry.py` |
| Relationship evidence | `plugin/aota-tools/_project_relationship.py` |
| Registration | `plugin/aota-tools/__init__.py`, `plugin/aota-tools/plugin.yaml` |
| Isolated regression/smoke | `scripts/verify-project-continuity.py` |
| Contract docs | `PROJECT-REGISTRY.md`, `PROJECT-RELATIONSHIP-BRIEF.md` |

## Validation tier

本次只做 source-level validation：compile、isolated temporary workspace smoke、plugin registration/schema 檢查、`git diff --check`。沒有 deploy、reload、restart、container recreate、runtime tool load、Profile task 或 live smoke。

## Known limitations

- 沒有在 canonical workspace 建立真實 Registry artifact。
- 沒有更新或生成 observed.json。
- live fallback 沿用 WI-01 bounded scan；duplicate 在 Registry refresh 會阻擋該 ID，但 fallback 保留 WI-01 的既有第一筆候選行為並標示 live source。
- 沒有 CodeGraph integration、semantic search、embedding、daemon、database、API 或 Dashboard。
- 完整 runtime package readiness 仍依外部 compose/runtime 環境；本 Work Item 不偽造外部檔案或消除該 limitation。

## Deferred items

- PCF-WI-04：Project Preparation Contract；需定義從 Registry candidate 到 task-main 深入檢查的 bounded preparation evidence。
- 後續 deployment Work Item：在明確 human checkpoint 後部署 plugin，更新 profile toolset policy，執行真實 model-driven smoke。

## Next Work Item handoff: PCF-WI-04

Prerequisites：WI-01 Project Contract、WI-02/03 Registry APIs、fresh/stale semantics、relationship schema。可沿用 symbols：`resolve_workspace`、`load_project`、`project_card`、`registry_status`、`load_search_records`、`search_records`。需保留 declared-vs-derived 邊界，不可把 Registry 當 authority。

Human checkpoints：任何 deploy/reload/restart、runtime registry 建立、Profile integration 或 live worker 都必須另行明確核准。Deferred risks：registry refresh lock contention、外部 package readiness、live runtime source identity、後續 preparation 的 output cap 與 path containment。
