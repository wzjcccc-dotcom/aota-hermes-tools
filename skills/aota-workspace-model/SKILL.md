---
name: aota-workspace-model
description: Registered filesystem authority and PCF project identity model.
category: forge
---

# AOTA Workspace Model

`workspace_id` is a canonical registry key for a filesystem authority root.
`project_id` is an identity inside that workspace and is validated by its
manifest. They may match but are never assumed equal. IDs are never generated
by workers. `/aota-runtime` stores control-plane artifacts and is not a
project-tier workspace.
