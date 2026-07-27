# HOST-WI-00 Docker rollback runbook

Snapshot: `20260724T033807Z`. This runbook is documentation only; HOST-WI-00 does not execute rollback.

1. Confirm ports 8642 and 8787 are free for Docker Hermes, and confirm no incompatible Host Hermes has modified `/home/latios/.hermes`.
2. Inspect the private backup at the recorded path. Do not copy secrets into the repository or command line.
3. Restore `docker-stack/docker-compose.yml` and `.env` from the private backup with owner/group `latios` and mode 600 for `.env`.
4. Restore the `.hermes` archive and `hermes-overrides` only after preserving any newer host state and checking the private manifests. Preserve symlinks; do not follow an external target.
5. Restore only the named-volume archives recorded in `manifests/volumes.json`; the bind-backed Hermes home points at the host path and must not be duplicated.
6. From `/home/latios/hermes-stack`, use the recorded Compose project and `docker compose up -d` (never `down -v`, `prune`, `volume rm`, `image rm`, or `container rm`).
7. Verify Agent health on 8642, WebUI health on 8787, API auth without printing the key, Profiles/Tools/Toolsets, and the read-only Profile Task/delegate-runtime endpoints.
8. If a port collides, stop the conflicting approved process or choose an operator-approved rollback plan; do not alter `.env` secrets in this work item.
