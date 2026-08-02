# AOTA Parent-Wake Workspace Authority Closure

Status: `PASS_WITH_LIMITATIONS`

## Verified

- Canonical Hermes source: `/home/latios/workspace/hermes-agent-host`.
- Active runtime import resolved to
  `/home/latios/workspace/hermes-agent-host/gateway/aota_parent_wake.py`.
- Explicit and trusted workspace authority are both `aota-hermes-tools`.
- Missing, invalid, and conflicting authority fail closed.
- Activation boundary is set to `2026-07-29T06:22:23Z`.
- Existing producer pending count before/after activation preparation: `69`.
- Processing, delivered, and failed counts after preparation: `0`, `0`, `0`.
- No historical event was claimed, moved, cleared, or resubmitted.
- Isolated claim/native-wake/delivered fixture passed, including historical
  boundary skipping.
- Forge deploy receipt:
  `.deploy-receipts/aota-forge-plan/20260729T062714Z/deployment.json`.
- Runtime env backup:
  `/home/latios/.config/hermes-host/runtime.env.backup-20260729T062223Z`.

## Not live-verified

Desktop-owned backend restart and one real task-main -> Worker -> parent wake
smoke were not run. The required `orca-ide skills get orca-cli` command was
blocked by the host's missing FUSE device. No alternate backend lifecycle or
second Worker was started.

## Next gate

After Orca is operational, use the Desktop-owned lifecycle, start exactly one
bounded P0 diagnosis Worker, and record claim -> native wake -> task-main
resume -> handoff -> decision -> acknowledgement -> closure. Re-run the
readiness verifier before activation and keep the historical backlog fenced.
