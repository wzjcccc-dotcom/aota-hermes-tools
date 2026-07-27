# HOST-WI-02B2 — Host Provider Connectivity and Runtime Preservation Closure

## Result

`FINAL=FAIL_STATE_DATABASE_INTEGRITY`

The Host launcher and official Hermes executable are aligned with the
canonical Hermes home. The provider resolution path is valid:

- `opencode-zen` is registered by the bundled provider plugin.
- `deepseek-v4-flash-free` is selected by the canonical `zen-deepseek-free`
  model alias.
- The provider credential and optional base-url key are present in the
  canonical `/home/latios/.hermes/.env`; values are not copied into evidence.
- The launcher loads only its bounded private runtime environment, then the
  official CLI loads the canonical Hermes `.env` through its normal resolver.
- No launcher, private runtime env, canonical config, or official executable
  mutation was required or applied.

The configured non-authenticated endpoint was reachable and returned HTTP
401. Public OpenCode DNS, TCP, TLS, and unauthenticated HTTP reachability also
passed. The previous Hermes failure is classified as `PROXY_FAILURE` at the
`APIConnectionError` layer; an authenticated provider test was not executed
because the safety gate requires explicit approval to transmit the credential.

## Hard blocker

Read-only SQLite checks show:

- `/home/latios/.hermes/state.db`: `quick_check` and `integrity_check` fail while
  loading `messages_fts` with `database disk image is malformed (11)`.
- All six profile databases pass both checks.
- No zero-byte database was found; WAL/SHM files were not removed or
  checkpointed.

No repair, checkpoint, one-shot retry, or database mutation was performed.
The global database failure prevents a valid runtime-preservation claim and
stops the work item before the Desktop checkpoint.

## Evidence

The immutable timestamped evidence is selected by:

`deploy/evidence/host-migration/HOST-WI-02B2/latest.json`

It includes provider resolution, bounded launcher environment comparison,
connectivity/error classification, database inventory/integrity,
session-preservation classification, runtime-mutation classification, CLI
smoke, Desktop smoke state, verification, and `RESULT.md`.

`deploy/evidence/host-migration/HOST-WI-02B/latest.json` now points to this
closure result without replacing the previous deployment evidence.

Desktop reconnect, gateway/live smoke, known-workspace smoke, authenticated
provider validation, and the requested parity one-shot remain pending. No
worker, delegate, CodeGraph/AMF mutation, commit, or push was performed.

Next work item remains `HOST-WI-02C0` after the database integrity issue is
resolved by an explicitly authorized operator action.
