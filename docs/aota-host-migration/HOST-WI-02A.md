# HOST-WI-02A — Canonical Host Launcher and AOTA Runtime Environment Alignment

## Scope

This work item aligns the Docker-era AOTA runtime variables with the canonical
Host Hermes installation. It creates the Host launcher and its private runtime
environment, while preserving the official venv entry point and the existing
Hermes/AOTA runtime artifacts.

## Canonical Host contract

- launcher: `/home/latios/.local/bin/hermes-host`
- official executable: `/home/latios/.venvs/hermes-agent-host/bin/hermes`
- private runtime env: `/home/latios/.config/hermes-host/runtime.env`
- `HERMES_HOME`: `/home/latios/.hermes`
- `AOTA_RUNTIME_ROOT`: `/home/latios/.hermes/aota-runtime`
- `AOTA_PROFILE_TASK_ROOT`: `/home/latios/.hermes/aota-runtime/profile-tasks`
- `AOTA_CANONICAL_WORKSPACE_ROOT`: `/home/latios/workspace`
- `AOTA_WORKSPACE_REGISTRY_PATH`: `/home/latios/workspace/.aota/workspaces.json`
- `AMF_BASE_URL`: `http://127.0.0.1:8791`

The launcher validates the official executable and private env file, exports
the bounded runtime assignments, and uses `exec` with the original argv. It
does not contain credential values. Provider credentials remain in the
existing `/home/latios/.hermes/.env`, which Hermes loads from `HERMES_HOME`.

## Docker-to-Host disposition

The Docker-only `/aota-runtime`, `/home/hermes*`, `/workspace`, and
`host.docker.internal` forms are not used by the Host launcher. API-server and
WebUI attachment variables are intentionally not migrated because Desktop SSH
uses `hermes serve` rather than the retired Docker API server/WebUI path.

The CodeGraph executable/runtime are the existing Host-managed paths resolved
from the Docker declaration, Host CodeGraph configuration, and installed
runtime. The managed executable is inside the declared runtime root so the
AOTA readonly CodeGraph containment check remains valid.

## Verification boundary

Static launcher checks, official version/doctor checks, CodeGraph version
smoke, provider one-shot, permissions, official executable hash, and canonical
config hash were verified. `aota_runtime_info` and workspace readonly smoke
remain Desktop live checks because there is no safe standalone CLI tool-call
subcommand. No Profile Task was created or started, no source/plugin override
was applied, and no task artifact was reconciled or deleted.
