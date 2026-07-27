# HOST-WI-00 — Docker Baseline Capture and Offline Rollback Preservation

## Scope

Capture the current Docker Hermes Agent/WebUI deployment, private rollback material, sanitized evidence, AOTA runtime baseline, override mappings, and a guarded Docker stop. This work item does not install or start Host Hermes and does not migrate or retire overrides.

## Preconditions

- Read the live Compose file, `.env` metadata only, AOTA Forge declarations, runtime assembly, lifecycle inventory, overrides, and `.hermes` structure.
- A fresh active-task gate must report zero active, pending, and running tasks before Docker stop. HOST-WI-00-REMEDIATION may use only the exact operator authorization ID recorded in sanitized evidence to exclude pre-existing legacy records; observed and effective counts remain separate.
- No source mutation, CodeGraph mutation, task metadata mutation, `sudo`, `down -v`, prune, or Docker object deletion is allowed.

## Tool and artifacts

Run from the repository:

```text
python3 scripts/capture-host-migration-docker-baseline.py inspect
python3 scripts/capture-host-migration-docker-baseline.py capture
python3 scripts/capture-host-migration-docker-baseline.py verify
python3 scripts/capture-host-migration-docker-baseline.py status
python3 scripts/capture-host-migration-docker-baseline.py stop
```

The private root is `/home/latios/.local/state/aota-host-migration/HOST-WI-00/<UTC timestamp>` with mode 700. It contains raw Docker metadata, resolved Compose, `.env`, the `.hermes` archive, overrides, volume material, and private manifests. The sanitized evidence root is `deploy/evidence/host-migration/HOST-WI-00/<timestamp>`; `latest.json` is a regular JSON pointer, never a symlink.

Evidence includes `baseline.json`, `verification.json`, `BASELINE.md`, `ROLLBACK.md`, `override-mapping.json`, `runtime-baseline.json`, and `docker-stop-receipt.json`. Secrets are represented only by environment key names and redaction booleans.

## Security model

The tool uses exact paths, atomic private directory publication, regular-file hashes, no symlink target traversal, mode 600 for private files, and a bounded secret-pattern scan. Raw `docker inspect`, resolved config, and `.env` never enter evidence or stdout. Named volumes are derived from the actual Compose config; bind-backed volumes are referenced to their host path and independent volumes are tarred through a read-only, network-none helper only when the existing image can do so.

## Stop gates and verification

`stop` requires capture PASS, verify PASS, a fresh zero-effective-task gate, a valid rollback runbook, sanitized evidence PASS, private backup PASS, volume preservation PASS, and zero new task records since capture start. The remediation override preserves observed legacy task counts, performs no metadata mutation, deletion, or process termination, and rejects any newly created task. It invokes only `docker compose stop`, then records the receipt. It never uses `kill`, `rm`, `down`, `down -v`, or prune.

## Rollback

Use the timestamped `ROLLBACK.md` in evidence. It restores only from the private backup after checking port collisions and Host Hermes ownership, preserves owners/modes, uses the recorded Compose project, and explicitly forbids destructive Docker cleanup.

## Limitations

This is a Docker baseline and preservation package, not proof that Host Hermes is installed or runnable. WebUI restarting is recorded as a degraded baseline and does not block remediation capture or stop. Live Profile Task smoke is skipped under remediation authorization; task-main wakeup remains not observable.

## HOST-WI-00-REMEDIATION invariants

- Runtime-generated cache paths (`__pycache__`, `.pyc`, and canonical cache exclusions) are classified lexically before readability, stat, hash, open, or copy checks.
- The historical unreadable `agent/prompt_builder/__pycache__/prompt_builder.cpython-312.pyc` is preserved unchanged, excluded from regular-file manifests, and listed only by relative path/class/reason in sanitized evidence.
- The one-shot operator authorization is exact: `HOST-WI-00-IGNORE-LEGACY-TASK-STATE-20260724`; it is never a default bypass.

## HOST-WI-01 handoff

HOST-WI-01 may begin only after this work item has a verified rollback-preserving baseline and an operator has reviewed any active-task or Docker stop blocker. It must install vanilla Host Hermes and validate desktop remote connection without reusing this work item to modify AOTA behavior.
