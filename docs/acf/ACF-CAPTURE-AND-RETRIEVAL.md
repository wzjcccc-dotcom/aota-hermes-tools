# ACF Capture and Retrieval — M0 Frozen Baseline

```text
DOCUMENT_ROLE=architecture_evidence
DOCUMENT_KIND=decision_package
MILESTONE=M0
ISSUE=11
PROJECT_ID=aota-hermes-tools
STATUS=FROZEN
STRUCTURED_APPENDIX_IS_SECOND_PLAN_AUTHORITY=no
```

本文件收斂 capture / distillation boundary（M0-D）與 retrieval / agent
portability（M0-E）。Issue #11 body 仍為 Portable Plan semantic authority。

## 1. Capture / distillation invariants

```text
ONE_RAW_CAPTURE_PLANE=yes
RAW_CAPTURE_BEFORE_DISTILLATION=yes
PROJECT_STATE_IS_NOT_MEMORY=yes
WORKER_NOT_EXPERIENCE_AUTHORITY=yes
TASK_MAIN_NO_CHILD_REDISTILLATION=yes
LLM_SYNTHESIS_ALONE_NOT_KNOWLEDGE_AUTHORITY=yes
USER_MD_AUTO_MUTATION_FIRST_STAGE=no
AGENTS_MD_AUTO_MUTATION_FIRST_STAGE=no
```

## 2. Source classes

```text
SOURCE_CLASSES=
S1 daily conversation tower
S2 task-main orchestration
S3 workers (coder/debugger/reviewer/architect/project-steward)
S4 documents
S5 runtime evidence
S6 manual records
S7 project state
S8 conversation archive
```

- S3 worker raw evidence = task-scoped artifact rail（RAW_SOURCE_EVIDENCE）；
  task-main 不得 re-distill child execution evidence。
- S2 只產 orchestration-lesson candidates，綁定 decisions/handoffs/Plan
  evidence。
- 瑣碎 structured record 可直接 bypass semantic distillation。
- conversation distillation 採 incremental cursor（`distilled_until_turn`）。

## 3. Candidate / distillation authority

```text
Worker finalization → run_result + candidate hints
Runtime → commands/failures/diff/tests/receipts/final outcome
Shared Distiller → Experience Candidate
```

- Worker 不直接擔任最終 Experience authority。
- Candidate 永不直接寫 canonical Memory/Knowledge/Experience。
- USER.md / AGENTS.md promotion 第一階段只產 candidate，禁止自動 mutation
  （D15；Nightly Brain 可自動治理 Hot/Warm Memory，不可直接改 USER.md）。

## 4. Always-loaded vs on-demand

```text
ALWAYS_LOADED=
SOUL/USER/Hot Memory (<=1500 tokens)/minimal project bootstrap

ON_DEMAND=
Warm Memory/Knowledge/Experience/Records/Conversation Archive/
Historical Project State/Source Evidence
```

- Hot Memory 全量 preload，<=1500 tokens；不得用 retrieval hit count 作
  Hot 使用率代理。
- 禁止 per-turn global retrieval。

## 5. Mandatory triggers

```text
WARM_MEMORY_TRIGGER=new conversation / topic shift (NOT every turn)
EXPERIENCE_TRIGGER=risky task preflight / on-error
KNOWLEDGE_TRIGGER=project/private/ingested-domain questions,
                  provenance-asserting questions
HISTORY_TRIGGER=explicit historical intent only
PROJECT_STATE_TRIGGER=project execution/continuation (read from system state)
```

- 共用 policy 擁有 trigger rules；Core 擁有 retrieval semantics；adapters
  擁有 executor-specific invocation。
- first MVP 保持 memory_search / knowledge_search / experience_search 獨立
  可觀測；unified `context_search` router 非 MVP。

## 6. Result / degradation contract

```text
PORTABLE_RESULT_CONTRACT=
object_id, object_type, title, summary, scope, status,
confidence, score_components, provenance refs, validity/version,
retrieval_reason, retrieval mode metadata

FAILURE_DEGRADATION_CONTRACT=
embedding down → keyword_fallback
reranker down → pre-rerank order + degraded label
vector stale → index_lag surfaced
S3 down → structured unaffected, source_status=unavailable
DB down → typed acf_retrieval_failed
NO_SILENT_FABRICATION=yes
```

- 每個 degraded path 可觀察；failed mandatory trigger 記錄為 typed failed
  retrieval，不得靜默跳過。

## 7. M1 handoff

```text
REQUIRED_M1_CONTRACTS=
1 portable search result envelope + typed retrieval-reason enum
2 Core/library application-service contract (executor-neutral)
3 always-loaded assembly contract (Hot <=1500, turn-summary exclusion)
4 degradation metadata contract (no-silent-fabrication)
5 CLI first stable portable public adapter
6 retrieval audit (typed reason per retrieval)
7 adapter boundary test (executor context → portable query context)
```

M1 不得實作 full Memory intelligence / full Experience distiller / full
Knowledge compiler / production Nightly Brain / advanced OCR-VLM / AMF1
migration / Proxy removal / universal context router / automatic USER.md 或
AGENTS.md writes。
