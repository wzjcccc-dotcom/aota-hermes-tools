# PCF-WI-05C.2 — Canonical Workspace Mount and Project Path Unification

## Decision

`CANONICAL_WORKSPACE_ROOT=/home/latios/workspace` is the only canonical
workspace authority for Host, Codex, hermes-agent, hermes-webui, and future
profile workers. The managed binding is
`/home/latios/workspace/.aota/workspaces.json` with exactly one candidate for
each of the four project IDs.

The Agent and WebUI runtime trees remain `/opt/hermes` and `/apptoo`. A managed
deploy of source/overrides does not turn either runtime tree into a canonical
project and does not activate a source update by itself.

## Contracts and source identity

All four projects have strict schema-v1 `.aota/project.yaml` contracts. Agent
and WebUI canonical directories are verified snapshots from unmounted images:

- Agent `0.18.2`, OCI revision `07271a6f628bcc6a3e8a2f921f2ab298dae2c68d`.
- WebUI `0.52.0`, OCI revision `d4e80b45498a914ce67e6b976145804638a46caf`.

The snapshots are version-locked, not upgrades. Overrides remain a separate
project and are not complete upstream sources.

## Deployment and rollback

The AOTA managed manifest includes the four contracts, canonical binding,
project-continuity documents, and an external compose source-only inventory.
The external compose backup is kept under
`/home/latios/hermes-stack/.pcf-wi-05c.2-backup-20260715/`.

Rollback restores the recorded managed backup with:

```bash
cd /home/latios/workspace/aota-hermes-tools
scripts/rollback.sh <backup-directory>
cp -p /home/latios/hermes-stack/.pcf-wi-05c.2-backup-20260715/docker-compose.yml \
  /home/latios/hermes-stack/docker-compose.yml
```

The old runtime projection and its index are retained. No Git cleanup or
source deletion is part of rollback.

## Validation boundary

Validation covers compose parsing, strict contracts, binding uniqueness and
containment, symlink boundaries, managed-file parity, project-local CodeGraph
prerequisites, and container visibility. Hermes native tool calls, Profile
tasks, live smoke, and CodeGraph mutation are explicitly not executed.

Final next-step marker:
`READY_FOR_MANUAL_HERMES_NATIVE_VERIFICATION`.
