# CodeGraph Project Layout

PCF uses one project root to one project-local CodeGraph location:

| Project | Index path | State in PCF-WI-05C.2 | Authority |
|---|---|---|---|
| `aota-hermes-tools` | `/home/latios/workspace/aota-hermes-tools/.codegraph` | existing index; status prerequisite only | authoritative for this project |
| `hermes-agent` | `/home/latios/workspace/hermes-agent/.codegraph` | `NOT_INITIALIZED`; layout marker only | canonical path, no index yet |
| `hermes-webui` | `/home/latios/workspace/hermes-webui/.codegraph` | `NOT_INITIALIZED`; layout marker only | canonical path, no index yet |
| `hermes-overrides` | `/home/latios/workspace/hermes-overrides/.codegraph` | existing index; status prerequisite only | authoritative for this project |

The Agent and WebUI indexes are never shared. The override project is not an
upstream source index. The legacy index at
`/home/latios/.hermes/hermes-agent-project/.codegraph` remains untouched and
non-authoritative; it is not copied into the Agent project.

Allowed operations in this work item are path existence, SQLite readability,
read-only `status --json`, and source/index-root comparison. `sync`, `init`,
`index`, `reindex`, and `daemon` are deferred to PCF-WI-06.
