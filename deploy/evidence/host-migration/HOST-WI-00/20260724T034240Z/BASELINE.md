# HOST-WI-00 Docker baseline

Captured `20260724T034240Z` for Compose project `hermes-stack`.

## Current architecture

- `hermes-agent` owns the Agent API on 8642 and mounts the canonical Hermes home, AOTA runtime, workspace, and Agent overrides.
- `hermes-webui` is the legacy WebUI on 8787, with a named `hermes-home` bind-backed to the same host Hermes home plus WebUI overrides.
- Container aliases are not canonical authorities: `/workspace`, `/home/hermes/.hermes`, `/home/hermeswebui/.hermes`, `/opt/hermes`, and `/apptoo` are container targets.
- Canonical paths are `/home/latios/.hermes`, `/home/latios/workspace`, `/home/latios/workspace/aota-hermes-tools`, and `/home/latios/workspace/hermes-overrides`.

## Runtime facts

- Container states: `{'hermes-agent': 'running', 'hermes-webui': 'restarting'}`
- Image IDs are preserved in `baseline.json`; private raw inspect/config output is outside evidence. Summary IDs: `sha256:ad2c82eeee5295ef919b970839c84ad924b93fa89883a434c3f800c1479e8105, sha256:754d8409808abb65afc66ee04f0bd7afbf81fa6af9a4df1b7e4c038868ebf447`
- AOTA counts: `{'tools': 61, 'toolsets': 28, 'profiles': 6}`; Profiles: `task-main, project-steward, architect, coder, debugger, reviewer`
- Agent and WebUI override disposition is in `override-mapping.json`: Agent mappings are `migrate_agent`, WebUI mappings are `retire_webui`, and unmounted files are `investigate`.

## Gates and rollback

- Active-task preflight: `True`. Operator-authorized legacy task records are preserved unchanged; effective stop-gate counts are zero.
- Docker was not stopped by this capture. Use `ROLLBACK.md` only with operator approval. Docker objects must remain preserved; never use `down -v` or prune.
- The private backup contains the complete `.hermes` archive, overrides copy, raw Docker metadata, resolved Compose, `.env`, and manifests. It is not a repository artifact.

## Limitations

- This is a source/runtime snapshot, not Host Hermes installation or HOST-WI-01 validation.
- WebUI was observed restarting, so no WebUI live PASS is implied.
- Live Profile Task smoke was skipped under the remediation authorization; task-main wakeup remains not observable.
