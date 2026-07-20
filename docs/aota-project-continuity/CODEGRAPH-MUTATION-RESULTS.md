# CodeGraph Mutation Results

## Confirmed CodeGraph 1.1.1 contract

- `sync` accepts an optional project path and `--quiet`; it has no JSON output mode.
- Non-quiet `sync` emits `Already up to date` for zero changes, or `Synced N changed files` with Added/Modified/Removed details.
- `init` and `index` emit human summaries such as `Indexed N files` and node/edge counts.
- `status --json` is the structured status contract: `fileCount`, `nodeCount`, `edgeCount`, `pendingChanges`, `projectPath`, `indexPath`, and `index.reindexRecommended`.
- The library returns a zero-shaped sync result when it cannot acquire its file lock; the CLI can therefore exit 0 and print `Already up to date`.
- The lock is `.codegraph/codegraph.lock`, containing the owner PID. CodeGraph treats locks older than two minutes as stale and may unlink them. The wrapper never unlinks or kills anything.

## Mutation result model

Before this model can run, PCF-WI-07-H requires a host-created file-backed
single-use approval bound to the exact manifest SHA. The consumer atomically
claims it before the maintenance lock or CLI and writes safe approval metadata
into the mutation receipt. Environment approval variables cannot authorize a
fallback mutation; a failed or timed-out claimed attempt remains consumed.

The wrapper captures bounded stdout/stderr, drains beyond the cap, records truncation, and parses in this order:

1. structured JSON payload;
2. documented CodeGraph summary;
3. bounded text fallback;
4. unparsed.

`exit_code=0` is never sufficient for success. Post-status remains the final index-state authority.

Supported outcomes:

- `mutation_applied`
- `mutation_applied_post_status_not_ready`
- `zero_change_expected`
- `zero_change_unexpected`
- `lock_not_acquired`
- `mutation_not_started`
- `mutation_partial`
- `mutation_failed`
- `mutation_timeout`
- `mutation_output_invalid`
- `post_status_invalid`
- `post_status_not_ready`

The normalized CLI projection is bounded and does not preserve raw output. Explicit lock text is confirmed CLI evidence; a filesystem lock alone is only inferred evidence.

## WebUI failure reclassification

For the supplied normalized case (`stale`, pending added=2, exit 0, counts and pending unchanged, post state `stale`), the deterministic result is:

```text
outcome=zero_change_unexpected
error_code=codegraph_mutation_zero_change_unexpected
retryable=false
reconciled=true
root_cause_confidence=diagnosis_incomplete
```

No lock failure is asserted without explicit CLI evidence. The current CodeGraph contract makes a lock-related zero-shape possible, but the observed output does not prove that branch.

## Lock safety

Diagnostics expose only the project-relative lock path, bounded PID metadata, bounded age, and owner liveness. Symlinks, path escapes, malformed content, and oversized content are rejected or marked malformed. There is no unlock tool, automatic unlink, stale deletion, or owner kill.

## Retry gate

Retry is a pure projection and never executes a retry. It requires a receipt, reconciled prior mutation, unchanged manifest SHA, readable index, suitable current lifecycle, no active maintenance process, released/absent lock, cleaned trusted environment, invalidated previous approval, and a new Human Checkpoint. A zero-change result with an unconfirmed lock is `diagnosis_incomplete`, not retryable.

## Limitations

CodeGraph 1.1.1 does not expose a structured sync result through the CLI. Mutation counts therefore come from the documented summary when available; an unparsed result plus ready post-status is accepted only as `pass_with_limitation` and never invents counts.

## PCF-WI-07-H source registration

The source-only registration probe reports `readonly=5`, `maintenance=3`, and `total CodeGraph tools=8` (before this CodeGraph surface: `0`). The readonly surface includes lifecycle and lock diagnostics; the maintenance surface remains deliberately unassigned to general profiles.

This phase performed no managed deployment, runtime reload/recreate, host approval creation, or real CodeGraph mutation. Runtime parity and a fixture-only no-mutation gate smoke remain the managed-deployment follow-up.
