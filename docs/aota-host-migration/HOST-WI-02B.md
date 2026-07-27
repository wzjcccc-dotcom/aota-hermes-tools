# HOST-WI-02B — AOTA Forge Host Runtime Parity

## Result

`FINAL=NEEDS_CANONICAL_HOST_DEPLOY_ADAPTER`

The canonical source and Host Hermes target were inspected read-only. The Host
target resolves to `/home/latios/.hermes`; no Docker target, Docker volume, or
vanilla test `HERMES_HOME` was used.

The deployment was intentionally not attempted. Five profile-local plugin
roots are symlinks to the global plugin:

```text
profiles/{architect,coder,debugger,reviewer,task-main}/plugins/aota-tools
    -> ../../../plugins/aota-tools
```

The existing manifest deployer checks the final file path but not this
symlinked parent root. Continuing would risk writing profile projections into
the global plugin. Manual `cp`, `rsync`, wildcard replacement, runtime-tree
cleanup, or symlink bypass is outside this work item.

## Canonical counts

The actual canonical counts are `TOOLS=61`, `TOOLSETS=28`, and `PROFILES=6`.
The six profiles are `task-main`, `coder`, `debugger`, `reviewer`,
`architect`, and `project-steward`. The active/reference Skill assignments are
derived from `deploy/profile-runtime-assembly.yaml`; no Skill content was
changed.

## Verification

- Source validation: PASS.
- Existing package fixture: PASS.
- Existing profile assembly fixture: PASS.
- Host launcher version, profile discovery, and AOTA toolset discovery: PASS.
- Existing runtime parity: blocked by the five symlinked profile plugin roots
  and one preserved non-manifest reference file.
- Official Hermes executable, Host launcher, private runtime env, profile
  `state.db` files, sessions, and AOTA runtime artifacts: unchanged.
- Desktop reconnect and live AOTA smoke: pending operator checkpoint; no
  Desktop reconnect was assumed.

## Evidence

See the timestamped evidence directory selected by
`deploy/evidence/host-migration/HOST-WI-02B/latest.json`. It contains source
and runtime inventories, managed parity classification, deployment plan,
preservation metadata, CLI smoke, verification, and `RESULT.md`.

The next permitted work item remains `HOST-WI-02C`; the required deployment
adapter must be resolved before claiming Host runtime parity.
