# AOTA Runtime Paths and Reloads

Current Browser traffic uses `hermes-webui`'s legacy in-process AIAgent and
`platform_toolsets.cli`; `hermes-agent` also exposes API server `:8642` through
`platform_toolsets.api_server`. They are separate Python processes with separate
PluginManager, registry snapshots, Skill catalogs, and prompt caches. Refreshing
Agent is therefore not refreshing legacy WebUI. If WebUI changes to
Gateway/API-server mode, rediscover this matrix; do not retain legacy rules.

| Resource | Source | Host deployed | Agent container | WebUI container | Access/authority |
| --- | --- | --- | --- | --- | --- |
| Plugin and `plugin.yaml` | `plugin/aota-tools` | `~/.hermes/plugins/aota-tools` | `/home/hermes/.hermes/plugins/aota-tools` | `/home/hermeswebui/.hermes/plugins/aota-tools` | Compose/inspect decides mount and owner |
| Profiles/SOUL | `profiles/<profile>` | `~/.hermes/profiles/<profile>` | `/home/hermes/.hermes/profiles/<profile>` | `/home/hermeswebui/.hermes/profiles/<profile>` | named Profile authority |
| Global Skills | `skills/<id>/SKILL.md` | `~/.hermes/skills/<id>/SKILL.md` | `/home/hermes/.hermes/skills` | `/home/hermeswebui/.hermes/skills` | global catalog only |
| Root config | source/managed config | `~/.hermes/config.yaml` | process-specific mount | process-specific mount | root is not assumed merged |
| Runtime tasks | n/a | `~/.hermes/aota-runtime` | inspect before use | inspect before use | runtime data authority |
| Attachments | n/a | `~/.hermes/webui/attachments` | `/home/hermes/.hermes/webui/attachments` | `/home/hermeswebui/.hermes/webui/attachments` | Agent read-only; WebUI owns read/write |
| Deployment evidence | `deploy/`, docs, scripts | source-only | n/a | n/a | receipts/backups are not runtime proof |

Always verify actual Compose mounts, environment, and owner before a mutation.
Never read attachment contents for this declaration check.

Project discovery treats `deploy/runtime-projects/**/.aota/project.yaml` as a
runtime projection, not a canonical candidate. The exclusion is the precise
workspace-relative root `deploy/runtime-projects`; it does not exclude general
`deploy/` content or nested canonical projects elsewhere in the workspace.

| Change | Agent recreate | WebUI recreate | New session |
| --- | --- | --- | --- |
| Plugin module, handler, schema, `__init__.py`, `plugin.yaml`, toolset | required | required | required |
| Profile toolset config | recommended | recommended | required |
| Compose env/path | affected service | affected service | required |
| Global Skill or Skill loader | required | required | required |
| Profile-local Skill | affected runtime | affected runtime | required |
| active binding or SOUL | recommended | recommended | required |

`AOTA_RUNTIME_PATH_RELOAD_DOC_PASS`

Task start also stores `workspace-baseline.json` beside the immutable
`scope.json`. The baseline records the HEAD/index identity, pre-existing dirty
tracked paths, untracked paths, and bounded content fingerprints. Finalizer
compares a postflight snapshot against that baseline; unchanged prior dirty
paths are `preexisting_workspace_state`, while new or changed paths without a
matching worker event are `unattributed_workspace_delta`.

## Global credential authority

Profile Task workers share one host authority:

```text
host:    /home/latios/.hermes/.env
host:    /home/latios/.hermes/auth.json
agent:   /home/hermes/.hermes/.env
agent:   /home/hermes/.hermes/auth.json
webui:   /home/hermeswebui/.hermes/.env
webui:   /home/hermeswebui/.hermes/auth.json
```

Agent and WebUI paths are views of the same host bind mount, not managed
secret copies. API-key providers use only the global `.env`; OAuth providers
are discovered by Hermes from global `auth.json` and are never converted to
shell variables. A worker always receives `HERMES_HOME` set to the normalized
global root and selects its Named Profile only through `hermes -p <profile>`.
Profile-local `.env`, `profiles/default/.env`, inherited parent environment,
and arbitrary caller env paths are disabled as credential authority. Resolver
logs and receipts contain only bounded metadata and existence booleans.

## Active task and scope tiers

Worker task context is read through the allowlisted
`aota_active_task_artifact_open` tool. The runtime task root is not a project
workspace and is never granted through a generic file or terminal tool. Scope
compliance classifies observed paths as `PROJECT_READ`, `PROJECT_WRITE`,
`PROJECT_METADATA`, `ACTIVE_TASK_READ`, `ACTIVE_TASK_WRITE`,
`SUBJECT_TASK_READ`, `RUNTIME_CONTROL_PLANE`, `PROFILE_RUNTIME`,
`PLUGIN_RUNTIME`, `CREDENTIAL_AUTHORITY`, `SYSTEM_DEPENDENCY`, or
`UNKNOWN_EXTERNAL`. Only explicit project-tier operations are compared with
SPEC scopes; runtime/profile/plugin/credential dependencies are reported as
ignored observations, and unknown explicit external access remains fail-closed.
