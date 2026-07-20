# PCF-WI-08 — CodeGraph minimal simplification

## Before / after

```text
Before: status/lifecycle → checkpoint → approval → backup → claim → lock
        → mutation → receipt → reconciliation
After:  status → query/explore → optional rebuild → status
```

The source surface is exactly `aota_codegraph_status`,
`aota_codegraph_query`, `aota_codegraph_explore`, and
`aota_codegraph_rebuild`. Bootstrap, refresh, reindex, lifecycle status, and
lock diagnostics are not registered tools.

Approval/backup modules and host creator scripts are removed. The managed
plugin manifest declares their runtime-module counterparts as `removed_files`;
the deployment package backs an existing stale file up, deletes it only during
an explicit deploy, verifies absence, and can restore it during rollback.

This work item is source-only. `REAL_CODEGRAPH_REBUILD=NOT_EXECUTED`,
`DEPLOYMENT=NOT_EXECUTED`, `RUNTIME_RELOAD=NOT_EXECUTED`, and
`SERVICE_RECREATE=NO` for this implementation phase.
