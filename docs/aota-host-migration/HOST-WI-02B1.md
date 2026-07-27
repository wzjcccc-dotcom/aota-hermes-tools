# HOST-WI-02B1 — Canonical Host Symlink Projection Deployment Adapter

## Scope

This work item adds a fail-closed, manifest-driven adapter for the five
profile-local `aota-tools` symlinks observed by HOST-WI-02B. It does not deploy
to `/home/latios/.hermes`, change the symlink topology, reconnect Desktop, or
modify AOTA tools, profiles, Skills, Hermes source, or runtime state.

The contract is an extension of
`deploy/profile-runtime-assembly.yaml`. It declares exactly these profiles:
`task-main`, `architect`, `coder`, `debugger`, and `reviewer`; every logical
path is exact and every target is the runtime-relative
`plugins/aota-tools`. `project-steward` remains an ordinary physical managed
directory because it is not a symlink projection.

## Adapter semantics

`scripts/_host_symlink_projection.py` validates the runtime root and each
declared projection using `lstat`/`readlink`, strict `Path.resolve`,
`relative_to`, and `samefile`. It rejects undeclared links, dangling links,
chains, cycles, target mismatch, target-root links, runtime-root links,
outside-root targets, non-directory targets, ambiguous manifest paths, and
physical target collisions.

The planner keeps every logical managed path in its plan and receipt, but maps
declared profile plugin files to one canonical physical write owner. Backup and
rollback operate on that physical owner once; projection links are verified
before and after each mutation boundary. The revalidation is fail-closed but
cannot eliminate an external concurrent filesystem mutation between checks.

## Verification

The bounded fixture script covers five valid projections, exact/mismatch parity,
deduplication, one-time backup/rollback, projection verification after deploy
and rollback, undeclared/dangling/chain/circular/outside/profile/file/root/path
escape cases, manifest errors, backup symlink escape, and physical collisions.

Formal runtime assessment is read-only. The timestamped evidence is under
`deploy/evidence/host-migration/HOST-WI-02B1/`; its latest pointer is
`deploy/evidence/host-migration/HOST-WI-02B1/latest.json`.

The existing package readiness command still reports a pre-existing secret-scan
match in `scripts/capture-host-migration-docker-baseline.py`; this work item
does not alter that unrelated file. Independent review remains a gate before
HOST-WI-02B deployment resume.

Next work item: `HOST-WI-02B-DEPLOYMENT-RESUME`.
