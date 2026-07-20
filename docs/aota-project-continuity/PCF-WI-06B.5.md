# PCF-WI-06B.5 — CodeGraph Oversized Source Scope Fix

## Result

This source-only work item fixes lifecycle projection rather than patching
CodeGraph 1.1.1. The confirmed root cause remains:

```text
PRIMARY_ROOT_CAUSE=codegraph_1_1_1_scanner_indexer_size_policy_mismatch
CONFIDENCE=high
```

The implementation retains CodeGraph raw pending counts, computes effective
pending independently, and records bounded oversized-source exclusions with
partial semantic coverage. Mixed pending and reindex-required cases remain
non-ready. Unknown size-limit authority fails closed.

## Scope and safety

Changed scope is limited to the AOTA plugin lifecycle/maintenance receipt,
the continuity smoke entry point, and project-continuity documentation. The
implementation does not patch the CodeGraph package, modify WebUI source,
modify `.gitignore`, modify `project.yaml`, or read/write a real `.codegraph`
database. No refresh, sync, reindex, bootstrap, deploy, reload, restart, or
Git write operation is part of this work item.

## Validation

The isolated classifier covers supported oversized, exact-limit, ignored,
unsupported, symlink/escape, already-indexed, legitimate, mixed, all-over-
size, reindex-priority, unknown-version, and unavailable-authority boundaries.
The required source checks are:

```text
python3 -m compileall -q plugin/aota-tools scripts             PASS
python3 scripts/verify-project-continuity.py                  PASS
python3 scripts/aota_forge_plan_package.py fixture             PASS
git diff --check                                               PASS
```

Markers include `PCF_CODEGRAPH_OVERSIZED_CLASSIFICATION_PASS`,
`PCF_ISOLATED_SMOKE_PASS`, `FIXTURE_PASS`, and the existing lifecycle,
maintenance, lock, mutation, and registration regressions.

## Runtime boundary

```text
DEPLOYMENT=NOT_EXECUTED
RUNTIME_RELOAD=NOT_EXECUTED
REAL_INDEX_MUTATION=NOT_EXECUTED
REFRESH_CALLS=0
SYNC_CALLS=0
REINDEX_CALLS=0
BOOTSTRAP_CALLS=0
LOCK_MODIFICATION=0
TRUSTED_APPROVAL=NOT_ACTIVATED
```

The current canonical runtime readonly probe still reports the pre-deployment
projection (`stale`, raw added=2); source changes are not runtime-active until
the separately gated managed deployment work item.

## Next step

```text
NEXT=PCF-WI-06B.5A Managed Deployment and Native WebUI Readiness Verification
```

That next step may perform managed deployment and native readonly verification,
but still must not refresh the real index as part of this source fix.
