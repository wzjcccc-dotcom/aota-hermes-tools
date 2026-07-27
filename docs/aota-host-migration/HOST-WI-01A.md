# HOST-WI-01A — Canonical Host Runtime Normalization and Vanilla Isolation Retirement

## Result

`FINAL=PASS_WITH_OPERATOR_DESKTOP_PATH_CONFIRMATION_REQUIRED`

The formal Host runtime is:

- `HOME=/home/latios`
- `HERMES_HOME=/home/latios/.hermes`
- executable: `/home/latios/.venvs/hermes-agent-host/bin/hermes`
- Hermes: `v0.19.0 (2026.7.20)`
- source: `/home/latios/workspace/hermes-agent-host`

The canonical smoke selected provider `opencode-zen` and model `deepseek-v4-flash-free`, and returned exactly `CANONICAL_HOST_RUNTIME_OK`. Six profiles were enumerated: `architect`, `coder`, `debugger`, `project-steward`, `reviewer`, and `task-main`; the default profile is present.

## Vanilla retirement

The exact Vanilla process was identified by both its Hermes command line and `HERMES_HOME`, then stopped gracefully with SIGTERM. No Vanilla process or user systemd reference remained. The wrapper `/home/latios/.local/bin/hermes-host-vanilla` was already absent. After realpath safety validation, only this exact directory was deleted:

`/home/latios/.local/state/aota-host-migration/HOST-WI-01/vanilla-hermes-home`

Pre-delete inventory: 552 files, 191 directories, 10,622,959 bytes, owner/group `latios:latios`, mode `700`. The HOST-WI-01 parent and other evidence/artifacts were preserved.

## Desktop handoff

The Host cannot read the Hermes Desktop application's saved SSH settings. Operator confirmation is required. Confirm:

- Connection mode: SSH
- Host: `100.123.10.71`
- User: `latios`
- Port: `22`
- Hermes path: `/home/latios/.venvs/hermes-agent-host/bin/hermes`

Select **儲存並重新連線**. Do not use `/home/latios/.local/bin/hermes-host-vanilla` or `vanilla-hermes-home`.

## Boundaries

No `hermes setup --portal`, config migration, Hermes update, commit, push, Docker mutation, AOTA runtime mutation, or HOST-WI-01B work was performed. Historical references in prior evidence are retained as evidence and are not active runtime settings.

Sanitized evidence: `deploy/evidence/host-migration/HOST-WI-01A/20260724T065917Z/`.
