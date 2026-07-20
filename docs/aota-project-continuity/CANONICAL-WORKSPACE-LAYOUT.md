# Canonical Workspace Layout

PCF-WI-05C.2 establishes one host/container-visible root:

```text
/home/latios/workspace
├── aota-hermes-tools/
├── hermes-agent/
├── hermes-webui/
└── hermes-overrides/
```

Each directory is an independent logical project with its own
`.aota/project.yaml` and project-local `.codegraph/`. Agent and WebUI are
version-locked verified image snapshots for the current runtime images; they
are not upgrades and are not the runtime execution trees.

| Project | Canonical source | Runtime execution/deploy target |
|---|---|---|
| `aota-hermes-tools` | `/home/latios/workspace/aota-hermes-tools` | managed AOTA files under `/home/latios/.hermes` |
| `hermes-agent` | `/home/latios/workspace/hermes-agent` | `/opt/hermes` |
| `hermes-webui` | `/home/latios/workspace/hermes-webui` | `/apptoo` |
| `hermes-overrides` | `/home/latios/workspace/hermes-overrides` | split targets under `/opt/hermes` and `/apptoo` |

The compose mount `/workspace` remains temporarily as a compatibility alias
for old WebUI sessions. It is not a Registry or CodeGraph authority; all PCF
bindings use the absolute canonical paths above.

The WebUI state file under `/home/latios/.hermes/webui/workspaces.json` is an
application selector with a different array schema; it is not the AOTA
workspace authority. Its entries were migrated to the same canonical paths.

The legacy projection `/home/latios/.hermes/hermes-agent-project` is retained
for rollback and compatibility evidence only. It is marked non-authoritative,
is not a canonical candidate, and is not deleted or reindexed by this work
item.

Source update, managed deploy, runtime recreate, and Hermes native/live smoke
are separate operations. CodeGraph sync/index/reindex belongs to
PCF-WI-06 and is not performed here.
