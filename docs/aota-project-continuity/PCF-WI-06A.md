# PCF-WI-06A — Source Closure

## Scope

This work item adds CodeGraph lifecycle semantics, a read-only lifecycle
projection, gate-aware bootstrap/refresh/reindex handlers, bounded fake
fixtures, Project Brief static readiness integration, and isolated source
validation.

## Explicit non-goals

The task does not run `codegraph init`, `sync`, `index`, or `reindex` against
canonical projects. It does not deploy, reload, restart, recreate containers,
assign maintenance tools to a Profile, or modify any real `.codegraph`
directory. The legacy `/home/latios/.hermes/hermes-agent-project` remains
legacy and non-authoritative.

## Evidence

`python3 -m compileall -q plugin/aota-tools scripts/verify-project-continuity.py`
passes. `python3 scripts/verify-project-continuity.py` emits:

```text
PCF_CODEGRAPH_LIFECYCLE_PASS
PCF_ISOLATED_SMOKE_PASS
```

The isolated fixture covers disabled/not-initialized/empty/ready/stale/reindex
projection, missing checkpoint, trusted fake bootstrap and refresh, fixed argv,
post-status readiness, receipt token redaction, process safety inherited from
PCF-WI-05B, and temporary-root cleanup.

The source regression also covers the actual CodeGraph 1.1.1 status field names
and rejects a CLI-reported project/index binding mismatch. Current canonical
read-only evidence shows `aota-hermes-tools` and `hermes-overrides` require
reindex with backup evidence; `hermes-agent` and `hermes-webui` report a shared
parent index and remain invalid until their project-local binding is repaired.

## Status boundary

Source status is `PASS_SOURCE_WITH_LIMITATIONS` until the existing trusted
approval context is activated for a real runtime. `REAL_INDEX_MUTATION` is
`NOT_EXECUTED`. WI-06B is responsible for controlled bootstrap/refresh of the
four canonical projects after an explicit Human Checkpoint.

## Next work item

`PCF-WI-06B — Controlled Initial CodeGraph Bootstrap and Refresh`

Expected actions:

- `aota-hermes-tools`: inspect, refresh if needed
- `hermes-overrides`: inspect, refresh if needed
- `hermes-agent`: bootstrap
- `hermes-webui`: bootstrap
