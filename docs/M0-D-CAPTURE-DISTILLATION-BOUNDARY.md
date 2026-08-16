# M0_D_CAPTURE_DISTILLATION_BOUNDARY

```text
PROJECT_ID=aota-hermes-tools
ISSUE_NUMBER=11
MILESTONE=M0
LANE_ID=M0-D-CAPTURE-DISTILLATION-BOUNDARY
```

Inventory of the capture / candidate / distillation / authority
boundary for every ACF data source, grounded in the existing runtime
(Hermes conversation stores, AMF1 memory fabric, AOTA Forge control plane).
No implementation, no schema, no runtime, database, S3 or GitHub mutation was
performed. The lane materialized this document in the repository worktree
(SOURCE_MUTATION=yes, see closure block); it performed no implementation
mutation.

```text
STATUS=PASS_WITH_FINDINGS

SOURCE_CLASSES_COVERED=
daily_conversation_tower,
task_main_orchestration,
workers,
documents,
runtime_evidence,
manual_records,
project_state,
conversation_archive

CAPTURE_MODEL_COMPLETE=yes
DISTILLATION_MODEL_COMPLETE=yes
AUTHORITY_BOUNDARIES_CLEAR=yes

DOUBLE_DISTILLATION_RISK=
task_main_re_distilling_child_execution_evidence,
AMF1_dual_capture_legacy_aota_sessions_and_memory_fabric,
proxy_webui_journal_double_capture_guarded_by_skip_reason

PROJECT_STATE_MEMORY_LEAK_RISK=
low_by_contract,
medium_in_AMF1_current_context_pack_injecting_legacy_turn_summary
```

## Evidence base (read-only, 2026-08-14)

- Hermes runtime: `~/.hermes/state.db` (sessions/messages, compression
  lineage `parent_session_id`, `archived` soft-hide, no transcript TTL);
  WebUI transcripts `~/.hermes/webui/sessions/<sid>.json` and event journals
  `_run_journal/<sid>/<rid>.jsonl` + `_turn_journal/<sid>~<pid>.jsonl`;
  deletion ledger `_deleted_webui_sessions.json`.
- AMF1 (`/home/latios/AOTA/AOTA_Memory_Fabric`): journal ingest
  (`hermes_webui_journal_ingest.py`, source agent `hermes_webui_journal`,
  capture source `hermes_webui_run_journal`), per-turn summarizer, Nightly
  Brain pipeline (candidate select → distillation → object cards → shards →
  embedding), PostgreSQL schema `memory_fabric`; legacy engine copies
  conversations into `aota_meta.aota_sessions/aota_session_turns` with
  `last_distilled_turn_index`.
- AOTA Forge control plane: `/aota-runtime/profile-tasks/<ws>/<task_id>/`
  (SPEC.md, meta.json, scope.json, CARD/RESULT/DIAGNOSIS/REVIEW,
  `worker-outcome.<start_id>.json`, `completion.<start_id>.json`,
  `worker.<start_id>.log` with `AOTA_CAPTURE_START/END` markers),
  `/aota-runtime/handoffs/<ws>/{pending,acknowledged}/`,
  `/aota-runtime/decisions/<ws>/DECISION.<id>.json`,
  `/aota-runtime/session-state/<ws>/<project>/<sd_*>/` pointers,
  project `.aota/project.yaml` + `.aota/registry/projects.json`,
  plans `.aota/forge/plans/<plan_id>/plan.json`,
  receipts `.deploy-receipts/<pkg>/<ts>/deployment.json`,
  backups `.deploy-backups/<ts>/`, evidence `deploy/evidence/<lane>/`.
- Governance: Issue #11 (ACF Portable Plan) sections 4–14, D13/D14/D15,
  AGENTS.md authority order, M8-B receipt invariants.

## Per-source boundary contract

### S1 Daily conversational tower

```text
raw evidence producer  = Hermes runtime sessions (desktop/CLI/subagent):
                          state.db sessions+messages; WebUI transcript JSON
                          + run journals + turn journals (SSE event stream)
capture trigger        = turn/run terminal event (event=="done",
                          terminal_state=="completed"); no user-declared
                          conversation end required; periodic ingest
                          (AMF1 currently polls journals every minute)
canonical raw/evidence destination class
                       = RAW_SOURCE_EVIDENCE (object store) + Conversation
                          Archive plane; AMF1 turns table is a derived copy,
                          not canonical truth
candidate producer     = per-turn preprocessor/summarizer producing
                          value_type / value_score / memory_candidate /
                          distill_priority (candidate hint stage only)
distiller              = Nightly Brain (incremental, cursor-based);
                          topic segmentation → event detection → candidate
                          extraction; most segments produce NONE
validation authority   = user evidence (explicit confirmation, repetition,
                          stability, contradiction, relevance) — never LLM
                          synthesis alone
promotion authority    = Nightly Brain 2.0 Memory/Experience/Knowledge
                          reconcilers under Calibration → Shadow →
                          Autonomous governance; human review for
                          USER.md/AGENTS.md promotions (D15)
provenance requirement = source_agent, capture_source, external_session_id,
                          turn_index, occurred_at, event signature
                          (dedupe_run_id, dedupe_seq)
duplicate/distillation protection
                       = UNIQUE(session_id, turn_index) with
                          skip_reason="existing"; event signature dedupe;
                          ack-only skip; distilled_until_turn cursor
incremental cursor     = (session_id, turn_index) today;
                          REQUIRED distilled_until_turn per session for ACF
may generate           = Memory Candidate, Knowledge Candidate,
                          Experience Candidate, Record (rare),
                          NONE (majority)
```

### S2 task-main orchestration

```text
raw evidence producer  = task-main profile session + control-plane writes:
                          orchestration decisions
                          (DECISION.<id>.json), handoffs
                          (pending/acknowledged + .ack.json closure),
                          session-state pointers (active-task.json,
                          current-decision.json, ...), Plan evidence ops
                          (record_*_evidence), workspace selection records
capture trigger        = durable decision recording
                          (aota_orchestration_decision_record), handoff
                          creation/acknowledgement, Plan evidence
                          recording, milestone/work-item evidence links
canonical raw/evidence destination class
                       = STRUCTURED_DATABASE (control-plane structured
                          truth: decisions rail, handoff rail, Plan store)
candidate producer     = orchestration-lesson candidate extractor
                          (planning/coordination lessons, scope-class
                          mistakes, routing lessons) — bound to
                          decisions/handoffs/Plan evidence only
distiller              = Shared Distiller / Nightly Brain 2.0
                          (orchestration-lesson lane)
validation authority   = task-main durable decision + operator checkpoint;
                          Project Steward for project-lifecycle
                          reconciliation (PLAN_INIT/MILESTONE_CLOSE/PLAN_CLOSE)
promotion authority    = same as S1; orchestration lessons promote into
                          Experience/Operational Rule candidates, never
                          auto-mutated (AGENTS.md candidate only, D15)
provenance requirement = workspace_id, project_id, plan revision,
                          decision_id, handoff_id, spec revision/hash,
                          session digest
duplicate/distillation protection
                       = revision-checked Plan ops; single durable decision
                          per subject; idempotent handoff ack
                          (closure_history); do_not_reopen guidance;
                          STRICT boundary: task-scoped artifacts
                          (profile-tasks/<ws>/<task_id>/) are worker
                          evidence — task-main must not re-distill them
incremental cursor     = decision/handoff sequence + plan revision;
                          no cross-task cursor needed
may generate           = Experience Candidate (orchestration lessons),
                          Record (durable decisions), Project State update
                          (Plan/handoff lifecycle is its own state class),
                          NONE (child execution evidence)
```

### S3 Workers (coder / debugger / reviewer / architect / project-steward)

```text
raw evidence producer  = worker run: CARD.json + RESULT.md (coder),
                          DIAGNOSIS_CARD.json + DIAGNOSIS.md (debugger),
                          REVIEW_CARD.json + REVIEW.md (reviewer),
                          ARCHITECT_CARD.json + ARCHITECT_REVIEW.md,
                          STEWARD_CARD.json + STEWARD_RESULT.md,
                          worker.<start_id>.log (bounded, redacted,
                          5 MB cap, AOTA_CAPTURE_START/END markers),
                          task_runtime_meta (meta.json execution block),
                          scope.json, workspace-baseline.json
capture trigger        = worker finalization (finalizer writes
                          completion.<start_id>.json + handoff marker;
                          worker outcome single submission)
canonical raw/evidence destination class
                       = RAW_SOURCE_EVIDENCE (task-scoped artifact rail:
                          profile-tasks/<ws>/<task_id>/ = durable
                          historical truth)
candidate producer     = worker finalization → run_result + candidate
                          hints (REQUIRED contract; hints do not exist yet
                          — finalizer today writes observation artifacts
                          only)
distiller              = Shared Distiller → Experience Candidate
                          (runtime → commands/failures/diff/tests/receipts/
                          final outcome evidence)
validation authority   = reviewer independent evidence pass + task-main
                          durable decision; Experience validation needs
                          verified cause / verified resolution /
                          repeatability / generalizability / recurrence cost
promotion authority    = Experience Curator (M3/M6); worker is NEVER final
                          Experience authority (D14)
provenance requirement = task_id, start_id, spec_id/revision/hash,
                          workspace/project binding, profile identity,
                          outcome (completed/failed/needs_input)
duplicate/distillation protection
                       = immutable task-scoped artifacts; single
                          worker_outcome submission (finalizer precedence:
                          cancel > timeout > outcome); canonical role
                          artifact map; no re-distillation by task-main
incremental cursor     = task_id-keyed; watermark = completion receipt
                          per task; no in-task cursor
may generate           = Experience Candidate (primary), Knowledge
                          Candidate (possible, from RESULT evidence with
                          provenance), NONE (pure lifecycle observations)
```

### S4 Documents

```text
raw evidence producer  = humans/agents authoring documents (repo docs,
                          manuals, engineering reports, meeting notes,
                          user papers, web-research exports); today no
                          document pipeline exists (no Document Router /
                          anydoc / OCR / VLM — greenfield for M7)
capture trigger        = explicit ingest (M4 initial scope: manual
                          Markdown/text ingest); later Document Router
                          with per-source scan
canonical raw/evidence destination class
                       = RAW_SOURCE_EVIDENCE (originals) → Canonical
                          Document → Claims/Relations/Metadata →
                          STRUCTURED_DATABASE (canonical Knowledge);
                          MARKDOWN_WIKI = materialized view only (never
                          silent canonical truth; MD edit → import
                          candidate → governance)
candidate producer     = Knowledge Compiler → Claim candidates
distiller              = Knowledge Curator (Nightly Brain 2.0)
validation authority   = source provenance / authority / version /
                          conflict resolution; LLM synthesis alone !=
                          Knowledge authority; human-confirmed notes and
                          explicit user decisions (per scope) are valid
promotion authority    = Knowledge Curator under governance stages;
                          promotion to ACTIVE/CANONICAL requires
                          provenance chain
provenance requirement = source identity, document hash/version, ingest
                          timestamp, authority class (normative / manual /
                          user-decision), claim→source chunk linkage
duplicate/distillation protection
                       = content hash/version dedupe; canonical claim ids;
                          MD direct edit never silently mutates canonical
incremental cursor     = per-source ingest watermark (file hash/mtime,
                          folder scan position)
may generate           = Knowledge Candidate (primary), Record (decision
                          records), NONE (speculative synthesis)
```

### S5 Runtime evidence

```text
raw evidence producer  = deterministic producers: managed deploy/verify/
                          rollback operations (deployment.json receipts,
                          backup-manifest.json + checksums.sha256),
                          verification evidence DB, deploy/evidence/<lane>/
                          artifacts (verification.json, RESULT.md,
                          latest.json pointers), worker bounded logs,
                          smoke runs
capture trigger        = receipt/backup/evidence write at operation
                          completion; worker log capture wrapper
canonical raw/evidence destination class
                       = RAW_SOURCE_EVIDENCE (object/source evidence plane)
candidate producer     = resolved-failure extractor: confirmed cause +
                          successful fix + final outcome → Experience
                          Candidate input
distiller              = Shared Distiller / Experience Curator
validation authority   = verified cause, verified resolution, repeatable
                          reproduction, generalizability, meaningful
                          recurrence cost
promotion authority    = Experience Curator (M3/M6)
provenance requirement = schema_version, source_version, source_hashes,
                          runtime_hashes, backup_path, UTC timestamp,
                          lane id; invariants GIT_CHECKPOINT !=
                          DEPLOYMENT_RECEIPT != BACKUP_RECEIPT !=
                          SOURCE_HISTORY (M8-B)
duplicate/distillation protection
                       = checksums; timestamp-keyed receipts; evidence
                          lanes append-only; receipts are not runtime proof
incremental cursor     = receipt timestamp/ID; latest.json pointers;
                          no semantic cursor needed
may generate           = Experience Candidate (primary, only when a
                          resolved failure is confirmed), Record
                          (operational), NONE (most receipts are
                          operational, not experiential)
```

### S6 Manual records

```text
raw evidence producer  = user/agent manually maintained records; today no
                          Personal Records system exists (M8 scope:
                          note / expense / todo / idea / event /
                          reference); de-facto sources are user-authored
                          files and AMF1 admin records
capture trigger        = explicit user submission / import (MD edit →
                          import candidate → governance → canonical
                          mutation)
canonical raw/evidence destination class
                       = STRUCTURED_DATABASE (Records as attached module,
                          must not burden ACF core)
candidate producer     = none required for trivial structured records
                          (they bypass semantic distillation)
distiller              = Housekeeping-Records Maintenance (Nightly Brain)
                          only; no semantic distillation for trivial
                          records
validation authority   = user is the authority for personal records
promotion authority    = user-elevated promotion only (Record → Memory/
                          Knowledge/Experience candidate is explicit and
                          rare)
provenance requirement = author, timestamp, import source, record id
duplicate/distillation protection
                       = record id + content hash; import dedupe
incremental cursor     = record sequence id; import cursor
may generate           = Record (primary), Memory/Knowledge/Experience
                          Candidate (only on explicit user elevation),
                          NONE
```

### S7 Project State

```text
raw evidence producer  = deterministic project authority: .aota/project.yaml
                          (canonical) + observed.json; derived
                          .aota/registry/projects.json; plans
                          .aota/forge/plans/<plan_id>/plan.json; decisions
                          rail (PROJECT_DECISION, workspace selection);
                          session-state bindings; trusted finalizer
                          receipts (completion.<start_id>.json,
                          initialization receipts)
capture trigger        = protected mutation classes executed via
                          authorized orchestrator dispatch
                          (project_initialization, git, codegraph,
                          registry, closure, metadata) with receipts
canonical raw/evidence destination class
                       = STRUCTURED_DATABASE (Project State = system
                          state plane; read from system, never distilled
                          from conversation)
candidate producer     = none (state machine transitions are
                          deterministic; no candidate stage)
distiller              = none (reconciliation, not distillation)
validation authority   = Project Steward / deterministic project
                          authority; task-main dispatch; trusted
                          finalizer receipt (authoritative_receipt /
                          trusted_finalizer); EXECUTION_STATUS !=
                          RECONCILIATION_RESULT
promotion authority    = n/a (state transitions, not promotions)
provenance requirement = project_id, workspace_id, plan revision,
                          registry_revision, receipt authority, git head
duplicate/distillation protection
                       = revision-checked transitions; registry derived
                          and rebuildable from project.yaml; worker
                          observations (CARD/RESULT) never equal receipt
incremental cursor     = plan revision, registry_revision, git commits
may generate           = Project State update (its own class) ONLY;
                          NONE for Memory/Knowledge/Experience
                          (PROJECT_STATE_MEMORY_LEAK=no)
```

### S8 Conversation Archive

```text
raw evidence producer  = Hermes conversation stores (state.db
                          sessions/messages with compression lineage and
                          FTS; WebUI transcripts + run/turn journals;
                          deletion ledger)
capture trigger        = session/turn terminal events; explicit archive
                          (soft-hide via set_session_archived; compression
                          continuation via parent_session_id); deletion is
                          explicit only, no TTL
canonical raw/evidence destination class
                       = Conversation Archive plane (RAW_SOURCE_EVIDENCE,
                          raw historical) — kept for history/provenance
                          only; never canonical Memory/Knowledge/Experience
candidate producer     = none (archive is the input to incremental
                          distillation, not a candidate producer itself)
distiller              = Nightly Brain incremental distillation consumes
                          archive with distilled_until_turn cursor
validation authority   = n/a (append-only history; provenance is the
                          contract)
promotion authority    = n/a
provenance requirement = session_id, message id, timestamps, compression
                          lineage, deletion ledger entries
duplicate/distillation protection
                       = unique message/session ids; append-only;
                          soft-hide never deletes; distillation idempotent
                          via cursor
incremental cursor     = per-session distilled_until_turn (ACF REQUIRED;
                          today turn_index serves as cursor)
may generate           = NONE directly (historical only; feeds S1
                          distillation)
```

## Boundary confirmations (Issue #11 sections 9–14)

```text
DAILY_CONVERSATION_CONTRACT=
raw_capture_first=yes (journal/transcript before any distillation)
nightly_incremental_distillation=yes (cursor-based, distilled_until_turn)
no_mandatory_conversation_end_signal=yes (terminal event driven)
majority_segments_may_produce_NONE=yes (value_type ephemeral gate,
  distill_priority gate, ack-only skip)

TASK_MAIN_CONTRACT=
orchestration_lessons_only=yes
no_re_distillation_of_child_execution=yes (task-scoped artifacts are
  worker evidence; boundary enforced by artifact rail ownership)
project_state_handoff=yes (durable handoff artifact, not chat summary)

WORKER_CONTRACT=
worker_finalization_run_result_candidate_hints=target (today: CARD/RESULT/
  outcome/receipt observations; hints contract required M3)
runtime_to_evidence=yes (bounded logs, receipts)
shared_distiller_to_experience_candidate=yes (D14)
worker_not_final_experience_authority=yes (reviewer evidence + task-main
  decision + Experience Curator)

DOCUMENT_CONTRACT=
llm_synthesis_alone_not_knowledge_authority=yes
source_provenance_required=yes
wiki_markdown_is_materialized_view=yes
md_edit_imports_candidate=yes

RUNTIME_EVIDENCE_CONTRACT=
deterministic_receipts=yes
experience_candidate_only_after_verified_failure=yes

MANUAL_RECORD_CONTRACT=
trivial_records_bypass_semantic_distillation=yes
user_is_record_authority=yes

PROJECT_STATE_CONTRACT=
read_from_system_state=yes
no_project_state_leak_into_memory=yes

MEMORY_CONTRACT=
user_evidence_based=required (AMF1 today is conversation-synthesis-based:
  finding NB-2)
hot_memory_token_budget<=1500=required (M2)
warm_retrieval_triggers=new_conversation/topic_shift=required (M2)

USER_MD_AGENTS_MD_CONTRACT=
promotion_candidate_only=yes
no_autonomous_mutation_first_stage=yes (D15, Stage A Calibration)
```

## Recommended shared envelope fields (M1)

```text
candidate_id            (machine-managed)
source_class            (S1..S8 enum)
source_ref              (exact producer artifact: session_id+turn_index,
                         task_id, decision_id, receipt path, doc hash)
captured_at / occurred_at
cursor_position         (per-source incremental cursor value)
candidate_type          (memory|knowledge|experience|record|lesson|none)
lifecycle_state         (RAW/EVIDENCE → CANDIDATE → VALIDATED →
                         ACTIVE/CANONICAL → SUPERSEDED/ARCHIVED, Issue §5)
confidence / evidence_refs
provenance_chain        (source identity → producer → distiller)
dedupe_key              (unique per source class)
promotion_hint          (target class; never a promotion itself)
```

## Recommended cursor state (M1)

```text
conversation:      (session_id, turn_index) + distilled_until_turn
workers:           task_id / start_id watermark (completion receipt)
task-main:         decision_id / handoff_id sequence + plan revision
runtime evidence:  receipt timestamp / id (+ latest.json pointers)
documents:         per-source ingest watermark (file hash / mtime)
manual records:    record sequence id + import cursor
project state:     plan revision / registry_revision / git head
archive:           per-session distilled_until_turn (same as S1)
```

## Required M1 contracts

```text
1. Shared evidence/candidate envelope (fields above) — one canonical schema
2. Source-class registry (S1..S8) with producer identity contract
3. Provenance chain contract (capture → candidate → validated)
4. Cursor state persistence (per-source incremental cursors)
5. Capture adapter contract: generalize beyond WebUI journal to state.db /
   native Hermes sessions / CLI (today only hermes_webui_journal is wired)
6. Dedupe-key contract per source class
7. Candidate store boundary: candidates never write canonical Memory/
   Knowledge/Experience directly
8. Explicit no-project-state-in-memory and no-llm-synthesis-authority
   invariants encoded in envelope validation
```

## Required later milestones

```text
M2 Memory MVP:      user-evidence-based promotion, Hot<=1500, warm retrieval
M3 Experience MVP:  worker candidate hints (finalization → run_result +
                    hints), Shared Distiller, pre-task/on-error retrieval
M4 Knowledge MVP:   manual doc ingest → claims → canonical knowledge →
                    wiki materialized view → provenance
M6 Nightly Brain 2.0: Memory Reconciler / Knowledge Curator / Experience
                    Curator + Calibration → Shadow → Autonomous governance;
                    incremental conversation + worker-run distillation;
                    audit receipts; retry idempotency
M7 Document pipeline: Document Router / anydoc / OCR / VLM
M8 Records / Archive / AMF1 read-only migration assessment
```

## Findings

```text
BLOCKING_FINDINGS=
none (M0-D boundary contract is fully definable; no implementation blocked)

NON_BLOCKING_FINDINGS=
NB-1  AMF1 dual capture: legacy aota_meta.aota_sessions/aota_session_turns
      (with last_distilled_turn_index) and memory_fabric both hold copies
      of the same conversations in one Postgres instance. ACF must define
      ONE raw plane + archive; do not inherit both.
NB-2  AMF1 memory candidates are LLM-conversation-synthesis-based
      (summarizer value_type/value_score/memory_candidate), not
      user-evidence-based. Conflicts with ACF "Memory 從人來"; ACF needs
      explicit user-evidence signals (confirmation/contradiction/repetition).
NB-3  No promotion/validation authority exists in AMF1 today
      (closest is memory_units.status='active'). ACF M2/M6 must materialize
      the VALIDATED → ACTIVE promotion gate.
NB-4  Worker candidate hints do not exist: finalizer writes observation
      artifacts only (CARD/RESULT/outcome/receipt). run_result + candidate
      hints contract required before M3.
NB-5  task-main orchestration-lesson candidates do not exist: decisions
      and handoff rails exist but no lesson extractor. Must be defined so
      task-main lessons are bound to decisions/handoffs, never to child
      task artifacts.
NB-6  WebUI journals stale since ~2026-07-23 while state.db is current:
      current AMF1 capture (journal-only) has a transport gap. ACF raw
      capture must cover native Hermes sessions (desktop/CLI/subagent),
      not only WebUI journals.
NB-7  AMF1 context_pack injects legacy_turn_summary (conversation
      summaries) into agent context — the exact leak pattern ACF forbids
      (always-loaded = SOUL/USER/Hot Memory/current bootstrap only).
      ACF context injection must exclude turn summaries.
NB-8  Session-state pointers live under /aota-runtime (container view on
      host: ~/.hermes/aota-runtime); canonical/repo copies are separated
      by deployment receipts. M1 must keep control-plane state on the
      structured plane and never treat runtime pointers as evidence.

SOURCE_MUTATION=yes
IMPLEMENTATION_MUTATION=no
MUTATION_SCOPE=docs/M0-D-CAPTURE-DISTILLATION-BOUNDARY.md
COMMIT_PERFORMED=yes
COMMIT_REF=Issue #11 ACCEPTED_WRITABLE_BASE (M0 known-good checkpoint)
ISSUE_MUTATION=no
RUNTIME_MUTATION=no
DATABASE_MUTATION=no
S3_MUTATION=no
```

New lane artifact only (no commit/push): `docs/M0-D-CAPTURE-DISTILLATION-BOUNDARY.md`.
