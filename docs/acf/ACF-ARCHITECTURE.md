# ACF Architecture — M0 Frozen Baseline

```text
DOCUMENT_ROLE=architecture_evidence
DOCUMENT_KIND=decision_package
MILESTONE=M0
ISSUE=11
PROJECT_ID=aota-hermes-tools
STATUS=FROZEN
STRUCTURED_APPENDIX_IS_SECOND_PLAN_AUTHORITY=no
```

本文件是 ACF（AOTA Context Fabric）M0 的 architecture evidence / decision
package。GitHub Issue #11 body 是唯一 Portable Plan semantic authority；本文件
及其餘 `docs/acf/*` 與 `deploy/evidence/issues/11/*` 是 Issue body 明確實作的
structured appendices（subordinate evidence），不是第二 Plan authority。

不得把本文件當作獨立 Plan state，也不得讓本文件覆蓋 Issue body。

## 1. Purpose

ACF 取代 AMF 1.x 以 Proxy per-turn interception 為核心的 context assembly
模型，改為 executor-neutral 的 Capture / Retrieval / Lifecycle fabric：

```text
Memory     → 讓 LLM 持續理解使用者（來源：人）
Knowledge  → 讓 LLM 取得結構化、可追溯、目前有效的知識（來源：文件/來源）
Experience → 讓 Agent 避免重複踩已驗證過的坑（來源：驗證過的行動）
```

```text
ACF_NAME=AOTA Context Fabric
LEGACY_AMF1_NAME=AOTA Memory Fabric
AMF2=historical_transitional_name_only
```

## 2. Authority planes

| Plane | Authority |
| --- | --- |
| Portable Plan semantics | GitHub Issue #11 body + structured appendices |
| Control comments (milestone_progress_index / development_notes / defect_register) | mutable operational indexes; never Plan authority |
| Event Log comments | append-only historical evidence; never Plan authority |
| Project truth / lifecycle reconciliation | Project Steward / deterministic project authority |
| Source history / checkpoints | Git |
| Executor runtime | selected executor adapter private artifacts |

```text
ISSUE_BODY_REMAINS_PLAN_AUTHORITY=yes
CONTROL_COMMENTS_PLAN_AUTHORITY=no
EVENT_LOG_PLAN_AUTHORITY=no
CHANGE_EXECUTOR != CHANGE_PLAN
```

## 3. Executor-neutral Core / adapters

```text
ACF_CORE_EXECUTOR_NEUTRAL=yes
CLI_FIRST_PUBLIC_ADAPTER=yes
MCP_IS_ADAPTER=yes
UNIFIED_CONTEXT_ROUTER_MVP_REQUIRED=no
```

- ACF Core 持有 retrieval semantics、ranking 契約、provenance envelope、
  degradation metadata、always-loaded context read；不得依賴任何
  executor profile/session/task/worktree identifier。
- 共用 policy 持有 portable trigger rules（mandatory retrieval triggers、
  typed retrieval-reason enum、Hot Memory budget、risky-task classification、
  no-silent-fabrication）。
- Adapters（CLI → MCP → HTTP → native tool）只做 executor-specific
  invocation 與 context 對映，不得重implement Core semantics。
- CLI 是第一級穩定 portable public adapter；MCP 永遠是 adapter，不是 Core。

## 4. AMF1 isolation

```text
NO_AMF1_MIGRATION=yes
NO_AMF1_DATA_WRITE=yes
NO_PROXY_DECOMMISSION_IN_M0=yes
AMF1_DATABASE_REUSE=no
AMF1_SCHEMA_REUSE=no
PROJECT_STATE_SCHEMA_IN_ACF=no
```

ACF 獨立建置。AMF1 舊資料第一階段只允許後續 read-only migration assessment。
AMF 1.x / AMF1 名稱不得追溯改為 ACF。

## 5. Lifecycle overview

```text
RAW / EVIDENCE
  → CANDIDATE
  → VALIDATED
  → ACTIVE / CANONICAL
  → SUPERSEDED / ARCHIVED
```

Memory / Knowledge / Experience 共用生命週期概念，但 promotion criteria
各自獨立（Memory 看 user evidence / repetition / stability / contradiction /
relevance；Knowledge 看 provenance / authority / version / conflict / scope；
Experience 看 runtime evidence / confirmed cause / verified resolution /
repeatability / generalizability / recurrence cost）。

LLM synthesis alone 不構成 Knowledge authority（LLM_SYNTHESIS_ALONE_NOT_
KNOWLEDGE_AUTHORITY=yes）。

## 6. M0-F closure status

```text
M0=completed
CURRENT_MILESTONE=M1
ACCEPTED_WRITABLE_BASE=<M0 known-good checkpoint SHA, see Issue #11 body>
HANDOFF_STATE=m1_w1_materialization_ready
```

詳細 decisions 見 Issue #11 body 與 `docs/acf/ACF-STORAGE-AND-SEARCH.md`、
`docs/acf/ACF-CAPTURE-AND-RETRIEVAL.md`。
