# AOTA Tool and Skill Development

This is the mandatory entry point for AOTA Forge tool and Skill work. It is for
implementers, reviewers, operators, and AI agents that add, change, deploy,
expose, or validate an AOTA plugin tool, toolset, Profile permission, or Skill.

Before a plugin-tool change, read [the Tool Lifecycle SOP](AOTA-PLUGIN-TOOL-LIFECYCLE.md), then [the runtime paths and reload matrix](AOTA-RUNTIME-PATHS-AND-RELOADS.md), and load `aota-plugin-tool-development`. Before a Skill change, read [the Skill Lifecycle SOP](AOTA-SKILL-LIFECYCLE.md), then the runtime matrix, and load `aota-skill-development`.

## Canonical index and authority

| Need | Canonical authority |
| --- | --- |
| Human operational rules | this directory |
| AI execution rules | `skills/aota-*-development/SKILL.md` |
| Machine declarations | `deploy/aota-lifecycle-inventory.yaml` |
| Registration and Profile truth | `plugin/aota-tools/__init__.py`, `plugin.yaml`, `profiles/*/config.yaml` |
| Managed files | `deploy/aota-forge-plan-files.yaml` |
| Activation | `docs/aota-forge-plan/ACTIVATION-RUNBOOK.md` and package readiness |

Quick decision: identify the importing process and transport first; define the
contract and path authority; implement and register; explicitly allow and deny
Profiles; update inventory/manifest/fixture; run both lifecycle verifiers.
Source pass, deployment parity, runtime registry, final model visibility, and
exact dispatch are distinct states.

Required source checks:

```bash
python3 scripts/verify-aota-tool-lifecycle.py
python3 scripts/verify-aota-skill-lifecycle.py
```

Do not deploy, recreate/restart, use `/reload-skills`, or perform a real
Profile task unless the approved SPEC explicitly authorizes activation. Disk
parity is never proof that an importing process reloaded.

`AOTA_DEVELOPMENT_DOC_INDEX_PASS`

For worker task context, use `aota_active_task_artifact_open` first for the
own SPEC, SCOPE, and BINDING. Do not inject the full frozen SPEC into the child
prompt, expose generic `/aota-runtime` filesystem access, or use a terminal
fallback. Scope receipts contain bounded tiered counts and violations rather
than a flat runtime path list.
