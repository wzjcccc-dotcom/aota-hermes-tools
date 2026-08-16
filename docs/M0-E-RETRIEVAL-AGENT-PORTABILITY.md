# M0_E_RETRIEVAL_AGENT_PORTABILITY

```text
PROJECT_ID=aota-hermes-tools
ISSUE_NUMBER=11
MILESTONE=M0
LANE_ID=M0-E-RETRIEVAL-AGENT-PORTABILITY
```

Architecture/design lane for the ACF retrieval policy and agent-portability
contract: which context classes are always loaded vs on demand, which
retrieval triggers are mandatory vs autonomous, where the executor-neutral
ACF Core boundary sits, and what the portable search/result/failure contracts
are. No CLI / MCP / Tool implementation, no Skill modification, no Issue /
database / S3 / runtime mutation was performed. The lane materialized this
document in the repository worktree (SOURCE_MUTATION=yes, see closure block);
it performed no implementation mutation.

```text
STATUS=PASS_WITH_FINDINGS

ALWAYS_LOADED_CONTRACT=
SOUL/agent contract + USER contract + Hot Memory (<=1500 tokens, full
preload) + minimal current project bootstrap (portable identity: project_id,
plan revision, current milestone/work-item pointer). No conversation turn
summaries, no warm/archived content, no executor-private identifiers
(session/profile/task ids) in the always-loaded set.

ON_DEMAND_CONTRACT=
Warm Memory, Knowledge, Experience, Records, Conversation Archive,
Historical Project State, Source Evidence. Retrieved through the per-class
search/open APIs only, with a typed retrieval reason. Full object bodies are
fetched on demand (card/summary first); never injected preemptively.

WARM_MEMORY_TRIGGER_MODEL=
mandatory: new conversation start; meaningful topic shift (detected, then
typed reason=topic_shift). NOT every turn. Hot Memory budget (<=1500) and Hot
selection are context-assembly policy, never retrieval ranking. Repeated warm
retrieval within one conversation without an intervening topic shift is a
policy violation (auditable via retrieval reason).

EXPERIENCE_TRIGGER_MODEL=
mandatory: risky-task preflight (risk classes: coding / debug / deploy /
migration / CLI mutation / Git-GitHub mutation / infra-runtime operation) and
on-error. NOT mandatory for pure read / summary / translation / general
analysis. Retrieval reasons: preflight / on_error.

KNOWLEDGE_TRIGGER_MODEL=
mandatory: project/private/ingested-domain questions, and any question whose
answer will assert facts that require source/provenance. Hybrid search
(metadata filter + FTS + vector + rerank); retrieval priority canonical Wiki
-> structured claims -> source evidence. Not mandatory for general/external
questions (outside ACF responsibility).

HISTORY_RECORD_PROJECT_TRIGGER_MODEL=
Conversation: only on explicit historical/previous-discussion intent
(reason=history_intent); never automatic archive retrieval. Records: on
personal-record query (reason=record_query). Project State: on project
execution/continuation (reason=project_continuation); read from system state,
never from conversation or distilled memory.

ACF_CORE_AGENT_NEUTRAL=PASS

CORE_RESPONSIBILITIES=
retrieval semantics per class (query -> filter/embedding/keyword composition),
filters (scope / lifecycle status / validity-version), ranking contract
(score components, reranker stage, deterministic tie-break), provenance-bearing
result envelope, degradation metadata, and the always-loaded context read.
Zero knowledge of any executor profile/session/task/worktree identifiers.

SHARED_AGENT_POLICY_RESPONSIBILITIES=
portable, executor-neutral policy: mandatory retrieval triggers + typed
retrieval-reason enum; risky-task classification for Experience preflight;
Hot Memory token-budget and always-loaded assembly rules; no-silent-
fabrication behavior on degraded retrieval. Not Hermes-specific, not ACF Core
storage/ranking logic.

ADAPTER_RESPONSIBILITIES=
executor-specific mechanics only: how the current agent invokes CLI/MCP/HTTP/
native tool; mapping executor context (profile id, session id, task id,
worktree path) to portable query context (project_id, scope, risk class);
turn-loop placement of mandatory retrievals; reporting typed failures.
Adapters must not re-implement Core semantics.

INTERFACE_PRIORITY=
1 Core/library contract, 2 CLI, 3 MCP adapter, 4 HTTP adapter, 5 agent-native
tool adapter (confirmed per Issue #11 2.3 / D05).

CLI_ROLE=
CLI is the first stable portable PUBLIC adapter: the canonical portable
invocation surface (aota memory/knowledge/experience ...). Business semantics
live in the Core/library contract; CLI is one adapter over that deeper
application-service contract and must not fork semantics. MCP is never ACF
Core itself.

MVP_SEARCH_SURFACE=
independently observable: memory_search (Warm), knowledge_search,
experience_search, plus always-loaded context read (SOUL/USER/Hot/bootstrap)
and object open/detail. No universal router.
UNIFIED_CONTEXT_ROUTER_MVP_REQUIRED=no

PORTABLE_RESULT_CONTRACT=
object_id, object_type, title, summary, scope, status (lifecycle),
confidence, score_components (semantic/keyword/metadata/rerank), provenance
refs (source identity -> producer -> distiller), validity/version (version,
authority, conflict, valid period), retrieval_reason, retrieval mode
metadata (full|degraded). Experience additionally: task/run context,
tool/environment, symptom, verified cause, verified resolution, prevention,
verification, recurrence cost, repeatability/generalizability, outcome.
Semantic envelope only; no transport-specific JSON-RPC/MCP schema frozen.

FAILURE_DEGRADATION_CONTRACT=
embedding unavailable -> keyword/FTS+metadata mode, search_mode=
keyword_fallback; reranker unavailable -> pre-rerank order + components,
label_reranker=degraded, order_not_final=true; vector index stale ->
index_lag surfaced, stale vectors never served as current without the flag;
S3 source unavailable -> canonical structured results unaffected, source
links carry source_status=unavailable, retrieval priority falls back upward
(source -> claims -> wiki); DB unavailable -> typed acf_retrieval_failed,
no cache-as-truth fabrication. NO_SILENT_FABRICATION=yes: every degraded
path is observable; a failed mandatory trigger is recorded as a typed failed
retrieval, never silently skipped.

M5_PORTABILITY_TEST_RECOMMENDATION=
two distinct Agent/runtime clients (at least one non-Hermes; a shell/CLI
client qualifies) against the same ACF Core data, same seeded fixture, same
scenario matrix (context assembly with Hot <=1500 accounting; memory/
knowledge/experience queries with identical object_id sets and score
components; risk-class preflight; injected degradation shapes). Assertions:
no executor identifiers in Core inputs/outputs; identical portable results;
ACF Core git diff empty. Evidence under deploy/evidence/m5-portability/.

REQUIRED_M1_CONTRACTS=
1) portable search result envelope + typed retrieval-reason enum (shared
policy); 2) Core/library application-service contract, executor-neutral
inputs/outputs; 3) always-loaded assembly contract incl. Hot <=1500 token
accounting and turn-summary exclusion invariant (M0-D NB-7); 4) degradation
metadata contract with no-silent-fabrication invariant; 5) CLI as first
stable portable public adapter (hot read + per-class search + open detail);
6) retrieval audit: typed reason recorded per retrieval; 7) adapter boundary
test: executor context -> portable query context translation only.

REQUIRED_M5_WORK=
M5 portability test harness per recommendation; MCP adapter candidate +
second runtime adapter (Hermes native tool candidate or shell/CLI client);
CLI contract stabilization; Warm Memory retrieval policy, Experience
preflight/on-error, and Knowledge mandatory-search policy exercised in real
clients; evidence-gated decision on unified context_search router (needs
M2/M3/M4 acceptance metrics + a calibrated routing policy).

BLOCKING_FINDINGS=
none (retrieval/portability contract fully definable; no implementation
blocked)

NON_BLOCKING_FINDINGS=
NF-1 M0-A Governance audit artifact is not materialized on disk, in git
history/branches, or in Issue #11 comments at M0-E execution time; M0-E
grounds governance in Issue #11 sections 3/15/16/17 + runtime
aota-portable-plan-governance (GOVERNANCE_REVISION=portable-planning-
governance, 608-line runtime revision) instead. M0-F should confirm M0-A
coverage and record its artifact path.
NF-2 aota-portable-plan-governance skill drift: workspace SKILL.md (310
lines) lags the runtime copy (608 lines, includes worktree boundary /
retirement / control-comment sections). This lane cites the runtime
revision; a sync decision is outstanding (no Skill modification performed,
per out-of-scope).
NF-3 M0-D NB-7: AMF1 context_pack injects legacy_turn_summary into agent
context, violating the always-loaded contract; M1 must encode the exclusion
invariant in the assembly contract.
NF-4 Hermes runtime has a typed-retrieval precedent (aota-task-lifecycle
retrieval_reason) that is executor-specific; ACF standardizes the portable
retrieval-reason enum in shared policy so adapters converge on one taxonomy.
NF-5 task-main card-first completion contract is a Hermes-specific instance
of context economy; M0-E generalizes it into the portable result contract
(summary/card always present, full body on open) so every adapter inherits
the same economy.

SOURCE_MUTATION=yes
IMPLEMENTATION_MUTATION=no
MUTATION_SCOPE=docs/M0-E-RETRIEVAL-AGENT-PORTABILITY.md
COMMIT_PERFORMED=yes
COMMIT_REF=Issue #11 ACCEPTED_WRITABLE_BASE (M0 known-good checkpoint)
ISSUE_MUTATION=no
DATABASE_MUTATION=no
S3_MUTATION=no
RUNTIME_MUTATION=no
```

## Evidence base (read-only, 2026-08-15)

- Issue #11 body (gh read-only): context-loading principle §2.2, portable
  core + interface priority §2.3 / D04/D05, Memory model §7 / D10-D12,
  Knowledge model §8, Experience model §9 / D22, USER.md §10, Retrieval
  policy §18, CLI direction §19, M0 work items + M5 scope §21, decisions
  D01-D22, non-goals §24, risks R1/R4 §25.
- M0-D capture/distillation boundary (`docs/M0-D-CAPTURE-DISTILLATION-
  BOUNDARY.md`): S1-S8 source classes, shared envelope fields, NB-1..NB-8,
  required M1 contracts (retrieval side cross-referenced below).
- Portable planning governance: `~/.hermes/skills/aota-portable-plan-
  governance/SKILL.md` runtime revision (executor portability,
  CHANGE_EXECUTOR != CHANGE_PLAN, worktree mechanics excluded, cross-client
  handoff minimum, private identifiers never Plan authority). Workspace copy
  (`skills/aota-portable-plan-governance/SKILL.md`) is outdated (NF-2).
- Task-main / worker architecture (integration evidence only):
  `profiles/task-main/SOUL.md` (star topology, card-first completion
  contract, PROJECT_DECISION != PROJECT_BINDING), `profiles/coder/SOUL.md`
  (bounded tools, role artifacts), `skills/aota-task-lifecycle/SKILL.md`
  (typed retrieval_reason governance for task status).

## Decision A — Context classes

### A1 Always loaded

```text
SOUL / agent contract      = role identity + standing behavior contract
USER contract              = stable long-term user contract (USER.md class)
Hot Memory                 = full preload, HOT_MEMORY_TOKEN_BUDGET <= 1500
current project bootstrap  = minimal portable identity only where applicable:
                             project_id, plan revision, current milestone /
                             work-item pointer
```

Never in the always-loaded set:

```text
Warm Memory / Knowledge / Experience / Records / Conversation Archive /
Historical Project State / Source Evidence          (all on demand)
conversation turn summaries                          (M0-D NB-7 violation)
executor-private identifiers (session id, profile id, task id, SPEC hash,
artifact paths, worktree mechanics)                  (portable-plan-governance)
```

The Hot Memory <=1500 token policy is a context-assembly budget and remains
outside generic search ranking: Hot entries are never produced or ranked by
`memory_search`; they are read as one complete block. Hot selection/aging is
governed by user evidence + relevance exposure in Nightly Brain (Issue §7),
never by retrieval hit count.

### A2 On-demand classes and their read path

| Class | Read path | Detail fetch |
|---|---|---|
| Warm Memory | memory_search | memory open |
| Knowledge | knowledge_search (hybrid) | knowledge open (canonical -> claims -> source) |
| Experience | experience_search (preflight/on-error) | experience open (full verified record) |
| Records | record query (attached module) | record open |
| Conversation Archive | archive search (history_intent only) | transcript open |
| Historical Project State | project state read | state detail |
| Source Evidence | source link from any result | source fetch (S3) |

## Decision B — Retrieval triggers

Mandatory triggers are shared, portable, executor-neutral policy. Every
retrieval — mandatory or autonomous — carries a typed `retrieval_reason`
(enum below) so policy compliance is observable and auditable; this is the
portable generalization of the Hermes `retrieval_reason` pattern (NF-4).

```text
RETRIEVAL_REASON_ENUM=
conversation_start | topic_shift | preflight | on_error |
provenance_required | project_domain_question | history_intent |
record_query | project_continuation | autonomous
```

### B1 Warm Memory

```text
new conversation            -> mandatory (reason=conversation_start)
meaningful topic shift      -> mandatory (reason=topic_shift)
every user turn             -> forbidden
```

A warm retrieval without an intervening topic shift repeats
`reason=conversation_start|topic_shift` in the same conversation: policy
violation, surfaced by audit (M1 retrieval audit).

### B2 Experience

```text
risky task preflight        -> mandatory (reason=preflight)
on-error                    -> mandatory (reason=on_error)
pure read / summary /
translation / analysis      -> not mandatory
```

Risk classes (shared policy): coding / debug / deploy / migration / CLI
mutation / Git-GitHub mutation / infra-runtime operation. Classification of
a task into a risk class is the adapter's job (it knows the executor context
and the operation surface); the class list itself is shared policy so all
agents classify identically.

### B3 Knowledge

```text
project / private / ingested-domain question  -> mandatory
source / provenance-required question         -> mandatory
general / external question                   -> outside ACF responsibility
```

Knowledge search is hybrid (metadata filter + FTS + vector + rerank) with
retrieval priority canonical Wiki -> structured claims -> source evidence
(Issue §8). "Provenance-required" means the agent will assert a fact whose
validity depends on source: then retrieval is mandatory before assertion.

### B4 Conversation / Records / Project State

```text
Conversation  -> explicit historical/previous-discussion intent only
                (reason=history_intent); no automatic archive retrieval
Records       -> personal-record query (reason=record_query); attached
                module, never burdens ACF core (Issue §13)
Project State -> project execution / continuation (reason=
                project_continuation); read from system state, never from
                conversation or distilled memory (Issue §2.1 / M0-D S7)
Web / external -> outside ACF responsibility; adapters may route elsewhere
```

## Decision C — Agent portability (ACF Core boundary)

ACF Core MUST NOT know:

```text
Hermes profile IDs           task-main session IDs
Codex sessions               OpenCode sessions
specific MCP host            worktree mechanics / branch / lane rules
profile-tasks paths          SPEC hashes / start_ids / decision ids
control-plane paths          runtime receipt ids
```

Adapter layer MAY know these. Boundary test (one sentence contract):

```text
ACF Core must accept and produce only portable semantics; every executor
identifier enters and leaves through an adapter, never through the Core
service contract.
```

This mirrors portable-plan-governance: `CHANGE_EXECUTOR != CHANGE_PLAN`
and "the shared core must not require them to parse or continue a plan".
For ACF the same holds for parse/continue of a retrieval conversation:
switching Hermes -> Codex -> OpenCode must not change Core behavior or
require Core edits; only the adapter changes.

## Decision D — Interface model

```text
INTERFACE_PRIORITY=
1 Core/library contract     (business semantics owner)
2 CLI                       (first stable portable public adapter)
3 MCP adapter               (never ACF Core itself, D05)
4 HTTP adapter
5 agent-native tool adapter
```

CLI role: the CLI is the canonical portable public operation contract for
humans and agents (shell/script, and any runtime whose native tooling shells
out), but it is an adapter over a deeper application-service contract inside
Core. Semantic decisions (filters, ranking, provenance, degradation) live in
Core exactly once; CLI, MCP, HTTP, and native adapters are thin projections.
Any adapter that re-implements semantics is a defect.

## Decision E — Search API surface for MVP

```text
MVP_SEARCH_SURFACE (independently observable, per-class):
memory_search          Warm Memory (never Hot)
knowledge_search       hybrid search, canonical-first
experience_search      preflight / on-error
context read           always-loaded assembly (SOUL/USER/Hot/bootstrap)
object open            detail fetch per class
```

`UNIFIED_CONTEXT_ROUTER_MVP_REQUIRED=no` (Issue §18). Future
`context_search` may be added only as a coordinating facade over the
per-class APIs, never as a replacement authority, and only when:

```text
1. per-class APIs have independently validated baselines
   (M2 memory precision; M3 experience preflight/on-error hit rate; M4
   knowledge provenance success)
2. a routing policy (when a query goes to which class, or multi-class merge)
   has been calibrated in Stage A (D16) with evidence
3. cross-class result merging has a defined contract: dedupe across classes,
   scope resolution, score normalization, provenance preservation
4. mandatory triggers remain expressible without the router
   (router optional for autonomous search only)
```

## Decision F — Policy placement

```text
ACF Core            retrieval semantics; filters (scope/status/validity);
                    ranking contract (score components, reranker stage,
                    deterministic tie-break); provenance-bearing results;
                    degradation metadata; always-loaded context read

Shared Agent Policy portable mandatory triggers + retrieval_reason enum;
                    risk-class list for Experience preflight; Hot <=1500
                    budget and always-loaded assembly rules; no-silent-
                    fabrication behavior

Adapter/Skill       how the current agent invokes CLI/MCP/native tool;
                    executor-context -> portable-query-context mapping;
                    turn-loop placement; typed failure reporting

Never in Core       task-main / Hermes profile rules, session bindings,
                    profile routing, worktree mechanics
```

## Decision G — Result contract

Portable minimum (semantic envelope; transport schema not frozen):

```text
object_id            machine-managed, portable
object_type          memory|knowledge|experience|record|project_state|conversation
title / summary      card always present (card-first economy, NF-5)
scope                run-specific|project|stack-environment|cross-project|global
status               lifecycle state (VALIDATED/ACTIVE/CANONICAL/SUPERSEDED/ARCHIVED)
confidence           per promotion governance, not LLM self-rating alone
score_components     semantic / keyword / metadata / rerank (never one opaque score)
provenance_refs      source identity -> producer -> distiller chain; evidence refs
validity/version     knowledge: version, authority, conflict state, valid period
retrieval_reason     typed trigger that produced this retrieval
retrieval_mode       full | degraded (see Decision H)
```

Experience additionally (D14 / Issue §9):

```text
task/run context     tool / environment
symptom              cause (verified)      prevention
resolution (verified) verification         outcome
recurrence cost      repeatability / generalizability
```

## Decision H — Failure / degradation

```text
H1 embedding unavailable -> keyword/FTS + metadata filtering mode;
   search_mode=keyword_fallback; never presented as full hybrid result
H2 reranker unavailable  -> pre-rerank order returned with score components
   intact; label_reranker=degraded; order_not_final=true
H3 vector index stale    -> index_lag surfaced; stale vector hits never
   served as current without the flag; rebuild is background maintenance,
   outside the retrieval path
H4 S3 source unavailable -> canonical structured results unaffected;
   source links carry source_status=unavailable; retrieval priority falls
   back upward (source -> claims -> wiki) — the Issue §8 priority order is
   itself the degradation ladder
H5 DB unavailable        -> typed acf_retrieval_failed; read-through cache
   only with cache_epoch + validation, never cache-as-truth without it
```

Invariant:

```text
NO_SILENT_FABRICATION=yes
a failed mandatory trigger is recorded as a typed failed retrieval and
surfaced to the agent adapter (proceed-with-warning or block is adapter
policy); it is never silently skipped or replaced with fabricated context.
```

## Decision I — M5 portability acceptance design

```text
M5_TEST_STRUCTURE=
fixture:  seeded ACF data (memory/knowledge/experience with known
          provenance), shared read-only dataset
clients:  client A = CLI (control), client B = second distinct runtime
          adapter (MCP or Hermes native tool or another shell/Agent)
          — at least one client is non-Hermes
scenario matrix:
  S1 context assembly  both clients assemble identical always-loaded set
                       with identical Hot <=1500 token accounting
  S2 memory search     same query -> identical object_id set + identical
                       score components (Core ranking deterministic)
  S3 experience        same risk class -> identical preflight set; same
                       error signature -> identical on-error set
  S4 knowledge         same provenance-required query -> identical
                       canonical objects + identical provenance chains
  S5 degradation       same injected failure (H1/H2/H3/H4/H5) -> identical
                       degradation metadata shapes on both clients
assertions:
  no executor identifier in any Core input/output across both clients
  ACF Core git diff after both clients run = empty (no Core modification)
evidence:
  written test report + receipts under deploy/evidence/m5-portability/
  (stable repo-relative path + checkpoint, per local-evidence policy)
```

## Required M1 contracts (retrieval side)

```text
R1  portable search result envelope (Decision G) merged into the shared
    M0-D envelope; one canonical schema
R2  Core/library application-service contract: per-class query semantics +
    always-loaded context read; executor-neutral inputs/outputs
R3  always-loaded assembly contract: SOUL/USER/Hot/bootstrap read with
    Hot <=1500 token accounting and turn-summary exclusion invariant (NB-7)
R4  degradation metadata contract (search_mode / component flags /
    index_lag / source_status) with NO_SILENT_FABRICATION invariant
R5  CLI adapter as first stable portable public adapter: context read +
    memory/knowledge/experience search + object open
R6  retrieval audit: typed retrieval_reason recorded per retrieval (read
    receipt), consumed by M6 governance audit
R7  adapter boundary test: executor context -> portable query context
    translation only; no Core semantics in adapters
```

M0-D required M1 contracts 1-8 (envelope, source-class registry, provenance
chain, cursors, capture adapters, dedupe keys, candidate-store boundary,
invariant validation) stand unchanged; R1-R7 add the retrieval side.

## Required M5 work

```text
1. M5 portability test harness + fixture per Decision I
2. MCP adapter candidate + second runtime adapter (Hermes native tool
   candidate or shell/CLI client)
3. CLI contract stabilization (freeze portable surface exercised in M1-M4)
4. policy enforcement in real clients: Warm Memory retrieval policy,
   Experience preflight/on-error, Knowledge mandatory-search policy
   (Issue §21 M5 scope)
5. evidence-gated context_search router decision (needs M2/M3/M4 acceptance
   metrics + calibrated routing policy)
```

## Findings

```text
BLOCKING_FINDINGS=
none (M0-E retrieval/portability contract is fully definable; no
implementation blocked)

NON_BLOCKING_FINDINGS=
NF-1  M0-A Governance audit artifact is not materialized on disk, in git
      history/branches, or in Issue #11 comments at M0-E execution time.
      M0-E grounds governance in Issue #11 sections 3/15/16/17 and the
      runtime aota-portable-plan-governance revision instead. M0-F should
      confirm M0-A coverage (naming consistency, executor-neutral boundary,
      AMF1/ACF authority separation) and record the audit artifact path.
NF-2  aota-portable-plan-governance skill drift: workspace SKILL.md (310
      lines) lags the runtime copy (608 lines, includes worktree boundary /
      retirement / control-comment sections). This lane cites the runtime
      revision; sync decision is outstanding. No Skill modification was
      performed (out of scope).
NF-3  M0-D NB-7: AMF1 context_pack injects legacy_turn_summary into agent
      context, violating the always-loaded contract. M1 must encode the
      exclusion invariant in the assembly contract (R3).
NF-4  Hermes aota-task-lifecycle retrieval_reason is executor-specific;
      ACF standardizes the portable retrieval-reason enum in shared policy
      so all adapters converge on one taxonomy.
NF-5  task-main card-first completion contract is a Hermes-specific
      instance of context economy; generalized into the portable result
      contract (card always present, body on open) so every adapter inherits
      the same economy.

SOURCE_MUTATION=no
ISSUE_MUTATION=no
RUNTIME_MUTATION=no
DATABASE_MUTATION=no
S3_MUTATION=no
```

New lane artifact only (no commit/push): `docs/M0-E-RETRIEVAL-AGENT-PORTABILITY.md`.
