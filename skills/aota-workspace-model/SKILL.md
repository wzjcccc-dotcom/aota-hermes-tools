---
name: aota-workspace-model
description: Registered filesystem authority and PCF project identity model.
category: forge
---

# AOTA Workspace Model

`workspace_id` is a canonical registry key for a filesystem authority root.
`project_id` is an identity inside that workspace and is validated by its
manifest. They may match but are never assumed equal. IDs are never generated
by workers. A workspace may contain multiple projects; `workspace_id` is not a
filesystem path and `project_id` is not an arbitrary folder name.
`/aota-runtime` stores control-plane artifacts and is not a project-tier
workspace. For example, `workspace_id=main-workspace` and
`project_id=aota-hermes-tools` intentionally identify different things.

Model-facing tools do not ask the model to copy these control-plane IDs when a
trusted session/workspace context exists. The runtime projection normalizes
Hermes registry kwargs, transport ContextVars, and trusted environment values;
missing context fails closed.

Never invent, copy, or retry control-plane identifiers, paths, revisions,
hashes, sessions, profiles, or lifecycle bindings. Model-facing calls use
semantic references; existing authority resolvers supply internal scope and
identity and return a deterministic next action on failure.

## Phase 3 current-project authority

Artifact/project tools resolve `current_project` only through trusted runtime
workspace context, canonical workspace selection, exact registry binding, and
validated `.aota/project.yaml`. They never infer a project from cwd, basename,
mtime, first candidate, or latest candidate. Zero matches return a deterministic
initialization/registration action; multiple matches return bounded semantic
choices. The model does not provide a canonical root or registry path.
