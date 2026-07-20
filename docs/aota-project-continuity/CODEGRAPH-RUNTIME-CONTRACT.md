# CodeGraph Runtime Contract — PCF-WI-05B

## Source closure boundary

This repository implements source-only wrappers for `@colbymchenry/codegraph` 1.1.1. Runtime activation, Node installation, deployment, reload, profile enablement and live verification are deferred to PCF-WI-05C and require a human checkpoint.

## Executable resolution

`AOTA_CODEGRAPH_EXECUTABLE` is a trusted private runtime declaration. It must be an absolute, real regular executable; it is rejected when it is inside the registered workspace or project source tree. When `AOTA_CODEGRAPH_RUNTIME_ROOT` is set, the resolved executable must remain below that root after symlink resolution. Group/world-writable executables emit a warning. NPM shims and `/usr/bin/env node` launchers are rejected.

Without the explicit declaration, the resolver only considers the deterministic bundled launcher under the managed Hermes home:

`home/lib/node_modules/@colbymchenry/codegraph/node_modules/@colbymchenry/codegraph-linux-x64/bin/codegraph`

It never uses `PATH`, a Registry path, a tool argument or a Profile-provided executable path as authority. The known npm shim is not a supported entry point because it relies on `node` in `PATH` and can exit 127.

## Side effects

The three CodeGraph commands are source-and-index read-only as observed, but append telemetry metadata to their controlled `HOME`. Every response therefore declares `telemetry_metadata_write`. They are **not strict read-only**. The wrapper neither exposes nor modifies the telemetry queue.

## Runtime limitations

The source contract does not establish the actual plugin user, mount, launcher permissions, bundled Node availability, telemetry HOME or live tool registration. PCF-WI-05C must verify those facts after explicit approval.