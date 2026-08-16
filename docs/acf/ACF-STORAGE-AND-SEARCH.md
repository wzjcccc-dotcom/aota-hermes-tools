# ACF Storage and Search — M0 Frozen Baseline

```text
DOCUMENT_ROLE=architecture_evidence
DOCUMENT_KIND=decision_package
MILESTONE=M0
ISSUE=11
PROJECT_ID=aota-hermes-tools
STATUS=FROZEN
STRUCTURED_APPENDIX_IS_SECOND_PLAN_AUTHORITY=no
```

本文件為 storage / search 的 M0 凍結 decisions，依據 M0-B reconnaissance
（read-only evidence，2026-08-15 佐證）與 M0-C 收斂。Issue #11 body 仍為
Portable Plan semantic authority。

## 1. Storage plane classification

```text
CANONICAL_STRUCTURED_TRUTH=PostgreSQL
RAW_SOURCE_EVIDENCE=S3/MinIO
MARKDOWN_WIKI=derived_materialized_view
VECTOR_INDEX=derived_rebuildable
RERANKER_RESULT=ephemeral_non_authoritative
GIT=source/history/checkpoint authority
```

禁止：

```text
Markdown 作為 canonical truth
vector index 作為 canonical truth
generic one-score promotion for M/K/E
```

## 2. PostgreSQL topology

```text
POSTGRES_DEPLOYMENT_MODEL=reuse_existing_service
ACF_DATABASE_MODEL=new_isolated_database
ACF_DATABASE_NAME=acf
AMF1_DATABASE_REUSE=no
AMF1_SCHEMA_REUSE=no
PROJECT_STATE_SCHEMA_IN_ACF=no
```

- 現有 PostgreSQL 16.13 + pgvector 0.8.2（container `aota_postgres`）可服務。
- ACF 建立**新的隔離 database `acf`**；不重用 AMF1 `aota_meta` database 或
  `memory_fabric` schema。
- ACF 內部 domain 以 PostgreSQL schema 分離（preferred）：

```text
ACF_INTERNAL_DOMAIN_MODEL=
core
capture
memory
knowledge
experience
records
index
```

- pgvector 是 derived index（`VECTOR_BACKEND=pgvector`），不是 canonical
  truth。目前 AMF1 已證實 halfvec(1024)+HNSW 可行
  （`idx_aota_sessions_vector` / `idx_aota_memory_vector` /
  `idx_aota_session_turns_vector`，halfvec_l2_ops, m=16, ef_construction=64）。
  ACF 沿用 halfvec 型別族，但維度由 metadata 決定。

## 3. Vector / embedding

```text
VECTOR_BACKEND=pgvector
CURRENT_VECTOR_MODEL=halfvec(1024)+HNSW
CANONICAL_OBJECT_FIXED_VECTOR_DIMENSION=no
EMBEDDING_METADATA_REQUIRED=yes

CURRENT_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
CURRENT_EMBEDDING_DIMENSION=1024
EMBEDDING_MODEL_CHANGE_MVP=no
EMBEDDING_OUTAGE_MODEL=degrade_or_defer_not_autoswitch
```

- 模型/維度 metadata-driven；MVP 不換 embedding model。
- outage 時 degrade（keyword/FTS+metadata fallback）或 defer，**不得自動
  切換模型**。

## 4. Reranker

```text
CURRENT_RERANKER_MODEL=BAAI/bge-reranker-v2-m3
RERANKER_REQUIRED_FOR_CORE=no
RERANKER_RESULT=ephemeral_non_authoritative
```

- reranker 對 Core 非必要；結果僅為 ephemeral 排序調整，不作 authority。
- 服務未恢復前 DEFER reranker activation。

## 5. Lexical search / FTS

```text
LEXICAL_SEARCH_MVP=PostgreSQL_native_FTS
CJK_SEARCH_STRATEGY=deferred_to_M1_validation
EXTERNAL_SEARCH_ENGINE_MVP=no
```

- MVP 用 PostgreSQL native FTS；不引入外部 search engine。
- CJK tokenizer/segmentation 選擇 DEFER 到 M1 validation
  （M1 必須先驗證 CJK search strategy，再凍結 language-specific 實作）。

## 6. MinIO / S3 source evidence plane

```text
MINIO_DEPLOYMENT_MODEL=reuse_existing_service
ACF_BUCKET_MODEL=new_isolated_bucket
ACF_BUCKET_NAME=acf
```

- 現有 MinIO service 可重用；唯一既有 bucket 為 `obsidian-vault`，
  無衝突。`acf` bucket 名稱與 ACF database 名稱一致、scope 明確。
- S3 object classes：

```text
S3_OBJECT_CLASSES=
sources/
parsed/
assets/
runtime-evidence/
conversation/
agent-runs/
snapshots/
```

- source immutability：

```text
SOURCE_IMMUTABILITY_MODEL=
content-hash/versioned objects; originals not overwritten
```

## 7. Wiki / materialized view boundary

```text
WIKI_CANONICAL_AUTHORITY=no
PREFERRED_LOCAL_RUNTIME_ROOT=/home/latios/.local/share/aota/acf/
WIKI_LIVE_VIEW=<root>/wiki/
HOT_MEMORY_EXPORT=<root>/context/
GENERATED_WIKI_GIT_COMMITS=no
HOT_MEMORY_TOKEN_BUDGET<=1500
```

- Wiki = local filesystem materialized view；Hot Memory exports = derived。
- 禁止 generated Wiki nightly Git commits。
- local runtime root 為 M0 direction，**M0 不建立**；M1 materialize。

## 8. Backup / rebuild responsibility

```text
CANONICAL_EVIDENCE_REQUIRE_BACKUP=PostgreSQL + S3
DERIVED_INDEXES_REQUIRE_REBUILD_CONTRACT=yes
BACKUP_AUTOMATION=deferred_to_M1
```

- canonical PostgreSQL + S3 evidence 需要 backup（M1 實作 backup/restore
  automation 與 restore drill）。
- derived indexes（vector / FTS / wiki）需要 rebuild contract；來源不可逆時
  一律可從 canonical 重建。

## 9. Graceful degradation

```text
EMBEDDING_OUTAGE=keyword/FTS+metadata fallback, search_mode=keyword_fallback
RERANKER_OUTAGE=pre-rerank order + components, label_reranker=degraded, order_not_final=true
VECTOR_INDEX_STALE=index_lag surfaced; stale vectors never served as current without flag
S3_SOURCE_UNAVAILABLE=canonical structured results unaffected; source links carry source_status=unavailable
DB_UNAVAILABLE=typed acf_retrieval_failed; no cache-as-truth fabrication
NO_SILENT_FABRICATION=yes
```

## 10. M0 acceptance evidence

```text
STORAGE_TOPOLOGY_DECIDED=PASS
POSTGRES_ISOLATION_MODEL_DECIDED=PASS
VECTOR_INDEX_BOUNDARY_DECIDED=PASS
S3_SOURCE_EVIDENCE_BOUNDARY_DECIDED=PASS
EMBEDDING_PROVIDER_BOUNDARY_DECIDED=PASS
RERANKER_PROVIDER_BOUNDARY_DECIDED=PASS
WIKI_MATERIALIZATION_BOUNDARY_DECIDED=PASS
CANONICAL_DERIVED_EPHEMERAL_BOUNDARIES=PASS
AMF1_ISOLATION=PASS
```
