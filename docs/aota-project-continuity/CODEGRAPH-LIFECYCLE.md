# CodeGraph Lifecycle Contract

PCF-WI-06A/06B.5 defines a project-local, deterministic lifecycle projection.
The canonical project is resolved by registered `workspace_id` plus exact
`project_id`; arbitrary paths and legacy runtime projections are not accepted.

## Raw versus effective pending

The CodeGraph 1.1.1 scanner counts supported source files as pending while its
indexer refuses files larger than `MAX_FILE_SIZE=1048576`. The lifecycle keeps
that upstream observation unchanged:

- `raw_pending` / legacy `pending_changes`: exact CodeGraph counts.
- `effective_pending`: counts still requiring refresh/reindex.
- `known_exclusions.oversized_source`: high-confidence exclusions only.
- `oversized_sources`: bounded project-relative evidence; no source content.

An oversized exclusion is accepted only when the registered-root file is a
regular non-symlink supported source, is not ignored, is readable, exceeds the
authoritative limit, is explicitly absent from the index, and has an `added`
(or equivalent missing-membership) pending reason without parse/path/permission
errors. Missing evidence remains effective pending.

## Size-limit authority

The projection first accepts a configured limit carried by the trusted status
payload. If absent, CodeGraph version `1.1.1` uses the explicit compatibility
profile `1048576` and emits `codegraph_size_limit_from_version_profile`. Any
other version without a supplied authority fails closed with
`oversized_classification_unavailable`; it never silently assumes 1 MiB.

## States and coverage

- `not_configured`: manifest disables CodeGraph.
- `not_initialized`: index is absent or status says initialized=false.
- `initialized_empty`: initialized index has zero files/nodes/edges with source.
- `ready`: initialized, non-empty, no effective pending, and no reindex/refresh
  requirement. When exclusions exist, `semantic_ready=true` means the existing
  index is usable, not that source coverage is complete.
- `stale`: effective pending remains, or pending classification is incomplete.
- `refresh_recommended`, `reindex_recommended`, `invalid`, and
  `runtime_unavailable`: existing gate semantics remain unchanged.

All-oversized pending therefore projects to `ready`,
`semantic_ready=true`, `semantic_coverage=partial`,
`coverage_complete=false`, and warning
`codegraph_oversized_source_excluded`. Mixed pending remains `stale`.
`reindex_recommended=true` always wins and remains non-ready.

No transition performs mutation implicitly. Maintenance is explicit and its
receipt preserves raw/effective pending, known exclusions, and coverage fields.
The implementation does not patch CodeGraph, modify WebUI source, or mutate a
real index.
