# M0-F Closure Evidence — Issue #11 ACF

```text
DOCUMENT_ROLE=architecture_evidence
DOCUMENT_KIND=closure_evidence
MILESTONE=M0
ISSUE=11
PROJECT_ID=aota-hermes-tools
STRUCTURED_APPENDIX_IS_SECOND_PLAN_AUTHORITY=no
M0_F_STATUS=PASS_WITH_NON_BLOCKING_FINDINGS
```

本文件為 M0-F（Architecture Synthesis / Review / Steward Closure）的 closure
evidence。GitHub Issue #11 body 是唯一 Portable Plan semantic authority；本
文件與 `docs/acf/*` 為其 structured appendices。

## 1. Pre-state

```text
PRE_M0F_HEAD=c51926a3189e323d0893829111048de89a51e848
PRE_M0F_BRANCH=agent/aota-profile-efficiency-backup-20260802
PRE_M0F_WORKTREE_STATUS=dirty (3 modified, 8 untracked)
UNRELATED_DIRTY_PATHS_PRESERVED=yes
```

Unrelated dirty paths preserved untouched:

```text
M .aota/forge/plans/plan_20260729T075202_ff38a6a0/PLAN.md
M .aota/forge/plans/plan_20260729T075202_ff38a6a0/plan.json
M .gitignore
?? deploy/evidence/m3-h/
?? deploy/evidence/m8-b/
?? docs/aota-development/PORTABLE-EXECUTOR-PROJECTION-CONTRACT.md
?? docs/aota-development/PROJECT-STEWARD-INTEGRATION-CONTRACT.md
?? docs/aota-development/RECONCILIATION-RECEIPT-CONTRACT.md
?? docs/aota-forge-plan/M8-B-DEPLOYMENT-ROLLBACK-EVIDENCE-CONTRACT.md
```

Accepted writable base: Issue #11 body（Portable Plan authority）M0-F scope
explicitly authorizes materializing architecture docs in this repository
(`docs/acf/`), evidence under `deploy/evidence/issues/11/`, and M0 closure
materialization on the Issue. Canonical project identity is deterministic
(`.aota/project.yaml`: id=aota-hermes-tools, root=/home/latios/workspace/
aota-hermes-tools, status=active; registry matches).

## 2. M0-A..M0-E reconciliation

```text
M0_A_RECONCILED=yes  (audit findings confirmed; no standalone artifact)
M0_B_RECONCILED=yes  (facts corroborated by read-only evidence 2026-08-15)
M0_C_RECONCILED=yes  (architecture conclusions accepted, frozen in docs/acf)
M0_D_RECONCILED=yes  (docs/M0-D-...md; source-mutation semantics corrected)
M0_E_RECONCILED=yes  (docs/M0-E-...md; source-mutation semantics corrected)
```

### M0-B read-only corroboration (2026-08-15)

| Claim | Evidence |
| --- | --- |
| PostgreSQL 16.13 | `SELECT version()` → PostgreSQL 16.13 (Debian), container aota_postgres (pgvector/pgvector:pg16, healthy) |
| pgvector 0.8.2 | `pg_extension` → vector 0.8.2 |
| AMF1 live DB | `pg_database` → aota_meta (plus postgres) |
| AMF1 schema | `information_schema.schemata` in aota_meta → memory_fabric |
| vectors halfvec(1024)+HNSW | pg_type halfvec; pg_indexes → idx_aota_sessions_vector / idx_aota_memory_vector / idx_aota_session_turns_vector (halfvec_l2_ops, m=16, ef_construction=64) |
| MinIO service | container aota_minio (minio/minio, up), ports 9000/9001 |
| only bucket obsidian-vault, no ACF bucket | `mc ls` → obsidian-vault only |
| embedding model/dim | AOTA_Memory_Fabric `core/config.py`: EMBED_MODEL_NAME=Qwen/Qwen3-Embedding-0.6B, EMBED_DIM=1024 |
| embedding endpoint unavailable | GET http://192.168.25.11:8081/ and /health → connect failed |
| reranker candidate | AOTA_Engine `core/rerank/reranker_client.py`: RERANKER_BASE_URL=http://192.168.25.11:8082, model bge-reranker-v2-m3-Q8_0.gguf (BAAI/bge-reranker-v2-m3) |
| reranker endpoint unavailable | GET http://192.168.25.11:8082/v1/rerank → connect failed |
| no PostgreSQL FTS/BM25 today | pg_indexes in aota_meta: 0 GIN/to_tsvector indexes |
| no ACF project | workspace has no aota-context-fabric project/registration |

## 3. Reconciliation matrix (item → disposition)

```text
A. ACF naming                                          ACCEPT
B. storage authority model                             ACCEPT (DB canonical, S3 evidence, Wiki/vector derived, reranker ephemeral)
C. PostgreSQL topology                                 ACCEPT (reuse service, new isolated `acf` DB, internal schemas)
D. MinIO/S3 topology                                   ACCEPT (reuse service, new isolated `acf` bucket)
E. vector/embedding architecture                       ACCEPT (pgvector derived; metadata-driven; degrade_or_defer_not_autoswitch)
F. reranker architecture                               ACCEPT (non-authoritative ephemeral; activation DEFER while down)
G. lexical/FTS architecture                            ACCEPT (PostgreSQL native FTS MVP; CJK DEFER to M1 validation)
H. Wiki/materialized-view boundary                     ACCEPT (derived view, no canonical authority, no nightly git commits)
I. capture/distillation boundary                       ACCEPT (M0-D invariants frozen)
J. retrieval/agent-portability boundary                ACCEPT (M0-E contract frozen)
K. AMF1 isolation                                      ACCEPT (no DB/schema/data reuse, no migration, no writes)
L. Proxy boundary                                      ACCEPT (NO_PROXY_DECOMMISSION_IN_M0=yes)
M. governance skill drift                              FOLLOW_UP (source/deployed sync, owner=governance-sync workstream; non-blocking)
N. structured-appendix definition gap                  ACCEPT (resolved: repo-relative stable paths under deploy/evidence/issues/11/ + docs/acf/)
O. M0-B artifact absence                               FOLLOW_UP (evidence sufficient via returned report + corroboration; artifact absence non-blocking)
P. M0-D/M0-E source-mutation mismatch                  ACCEPT (corrected to SOURCE_MUTATION=yes, IMPLEMENTATION_MUTATION=no)
Q. backup automation gap                               FOLLOW_UP (M1 backup/restore automation + restore drill)
R. CJK search strategy                                 DEFER (M1 validation)
S. embedding/reranker endpoint outage                  FOLLOW_UP (availability restoration outside M0; non-blocking runtime condition)

REJECT:
- reuse AMF1 DB/schema
- Markdown as canonical truth
- vector index as canonical truth
- generic one-score promotion for M/K/E
- per-turn global retrieval
- Hermes-specific semantics inside ACF Core
- automatic model switching during outage
- generated Wiki nightly Git commits
```

## 4. Governance skill drift disposition

```text
GOVERNANCE_SOURCE_DRIFT=yes
WORKSPACE_REVISION=aota-portable-plan-governance SKILL.md 310 lines (repo)
DEPLOYED_REVISION=aota-portable-plan-governance SKILL.md 608 lines (runtime)
NEWER_REVISION=deployed/runtime copy
SEMANTIC_DIFF=control-comment two-class model; executor worktree boundary;
              PLAN_INIT Steward relationship_resolve/context_prepare stages;
              bounded Plan retirement; COMMENT_EVENT_LOG_ONLY=no →
              CONTROL_COMMENTS_MUTABLE_INDEXES=yes
DISPOSITION=FOLLOW_UP (option B)
DISPOSITION_OWNER=governance-skill-sync workstream (separate Issue)
DISPOSITION_TARGET=repo skills/ ← deployed runtime copy, incl. missing
              aota-worktree-governance workspace copy + parallel-development
              reference updates
GOVERNANCE_DRIFT_NON_BLOCKING=yes
GOVERNANCE_DRIFT_DISPOSITION_RECORDED=yes
```

Why non-blocking for M0: the drift adds operational machinery
(control-comment two-class model, worktree boundary, Steward PLAN_INIT stages)
on top of an unchanged semantic core. The semantic authority model used by
Issue #11 — body + structured appendices = Plan authority, comments never Plan
authority, Steward reconciliation, Git checkpoint — is identical in both
copies. This M0-F follows the deployed (newer) revision's control-comment
semantics, which matches the task's required control roles. Source/deployed
parity is NOT claimed. Repair belongs to a separate governance-sync workstream,
not to ACF M0 closure.

## 5. Independent review

```text
M0_F_INDEPENDENT_REVIEW
VERDICT=pass_with_findings
BLOCKING_FINDINGS=none
NON_BLOCKING_FINDINGS=
  R1 reviewer == synthesizer in this session (user-directed single-agent
     execution; no independent reviewer agent was dispatched)
  R2 governance skill source/deployed drift remains open (FOLLOW_UP)
  R3 M0-B/M0-A standalone artifacts absent (evidence sufficient)
PLAN_ALIGNMENT=pass
AUTHORITY_MODEL=pass
STORAGE_MODEL=pass
CAPTURE_RETRIEVAL_MODEL=pass
PORTABILITY_MODEL=pass
M1_HANDOFF=pass
UNRELATED_DIRTY_PRESERVED=yes
```

## 6. Project Steward reconciliation

```text
PROJECT_STEWARD_M0_RECONCILIATION=PASS
CANONICAL_PROJECT_IDENTITY=aota-hermes-tools
M0_REQUIRED_WORK_COMPLETE=yes
M0_ACCEPTANCE_SATISFIED=yes
REVIEW_SATISFIED=yes
ARCHITECTURE_DOCS_LINKED=yes (docs/acf/* + deploy/evidence/issues/11/)
GOVERNANCE_DRIFT_DISPOSITION_RECORDED=yes
SOURCE_CHANGES_COHERENT=yes (docs only, M0-scoped)
UNRELATED_DIRTY_PATHS_PRESERVED=yes
CHECKPOINT_CANDIDATE_KNOWN_GOOD=yes
M1_NEXT_ACTION_DETERMINISTIC=yes (Materialize W1 — ACF Core Contract + Portable CLI)
NO_RUNTIME_CLAIM=yes (no ACF runtime/DB/bucket created, no deploy/reload/live smoke)
```

## 7. Acceptance

```text
ACF_CONTEXT_FABRIC_CONTRACT=PASS
PORTABLE_PLAN_GOVERNANCE_ALIGNED=yes
SOURCE_OF_TRUTH_BOUNDARIES=PASS
MEMORY_KNOWLEDGE_EXPERIENCE_BOUNDARIES=PASS
STORAGE_TOPOLOGY_DECIDED=PASS
POSTGRES_ISOLATION_MODEL_DECIDED=PASS
VECTOR_INDEX_BOUNDARY_DECIDED=PASS
S3_SOURCE_EVIDENCE_BOUNDARY_DECIDED=PASS
EMBEDDING_PROVIDER_BOUNDARY_DECIDED=PASS
RERANKER_PROVIDER_BOUNDARY_DECIDED=PASS
WIKI_MATERIALIZATION_BOUNDARY_DECIDED=PASS
CANONICAL_DERIVED_EPHEMERAL_BOUNDARIES=PASS
AMF1_ISOLATION=PASS
M0_ACCEPTANCE=PASS
```
