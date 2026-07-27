# HOST-WI-02B3 — Global State Database FTS Compatibility and Recovery Assessment

## Result

`FINAL=NEEDS_CONTROLLED_GLOBAL_DATABASE_RECOVERY`

The diagnosis is not a SQLite runtime capability false positive. System
sqlite3, system Python, and the Hermes venv Python all use SQLite 3.45.1 and
all pass isolated FTS5 and trigram fixtures. The global database opens in
read-only mode and its schema objects are enumerable, but `sessions` and
`messages` reads fail with `database disk image is malformed`; table-specific
integrity checks report `btreeInitPage()` errors. Both `messages_fts` and
`messages_fts_trigram` also fail constructor/count/MATCH access, and their
shadow-table reads fail. This confirms core database corruption with an FTS
corruption component.

## Evidence

Evidence root:

`deploy/evidence/host-migration/HOST-WI-02B3/20260726T051208Z/`

The six profile databases remain readable and pass quick/integrity checks.
The global schema-version table is 23, matching `hermes_state.py`; profile
versions are the observed 19/23 split. No session or message content was
exported. The original DB, WAL, and SHM were not copied, changed, or repaired.

## Source assessment

The source of truth is `hermes_state.py` (`SCHEMA_VERSION=23`). FTS recovery
logic is in `repair_state_db_schema()` and `_rebuild_fts_indexes()`; the
legacy inline FTS layout remains supported while v23 external-content storage
optimization is explicit/opt-in. The website storage document is stale at
schema version 21 and was not used as the current schema authority.

## Recovery decision

Recommended next work item: `HOST-WI-02B3R Controlled Global Database
Recovery`.

Its first step must be a consistent SQLite backup-API snapshot preserving the
original DB/WAL/SHM and verified before any mutation. Repair must be rehearsed
on a copy with rollback available. No verifier change is authorized because
CASE_A is disproven.

No repair, migration, rebuild, REINDEX, VACUUM, backup copy, Desktop start,
serve process, one-shot, commit, or push was performed by this work package.
