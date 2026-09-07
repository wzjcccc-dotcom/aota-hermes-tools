---
name: aota-workspace-model
description: Registered filesystem authority and PCF project identity model.
category: forge
---
> **W0 Legacy Projection — Non-Authoritative**
>
> ```text
> AOTA_SKILL_CANONICAL_SOURCE=aota_forge
> AOTA_HERMES_TOOLS_SKILL_AUTHORITY=no
> CANONICAL_SOURCE=aota_forge/skills/aota-workspace-model/SKILL.md
> THIS_FILE_IS_PROJECTION=yes
> SKILL_IS_AUTHORITY=no
> ```
>
> This file remains as **thin projection / host glue / historical evidence** after M1/W0.
> Canonical semantic authority is `aota_forge/skills/aota-workspace-model/SKILL.md`.
> Do not use this file as independent authority; it is parity-checked against AF.


# AOTA Workspace Model

`workspace_id` is a canonical registry key for a filesystem authority root.
`project_id` is an identity inside that workspace and is validated by its
manifest. They may match but are never assumed equal. IDs are never generated
by workers. `/aota-runtime` stores control-plane artifacts and is not a
project-tier workspace.


---
*Projection provenance: canonical migrated to `aota_forge` (W0). Legacy retention is deployment projection, not authority.*
