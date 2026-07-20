# PCF-WI-09A — Implementation Backlog

## WI-09A result boundary

WI-09A delivers only the four contract documents.  It does not change a
Profile runtime config, SOUL, skill binding, plugin/tool implementation,
routing, writer, deployment manifest, runtime service, CodeGraph index, or
Git staging/commit.

## WI-09B — Project Steward Foundation

| Item | Contracted backlog |
| --- | --- |
| Goal | Introduce the bounded Project Steward foundation without changing other role ownership |
| Likely modules | `profiles/project-steward/`, a Steward skill, project-continuity docs, capability registry, narrow project/doc writer modules, deployment manifest entries |
| New artifacts | Project Steward config/SOUL/skill, `STEWARD_RESULT.md`/card writer, bounded Project Card and docs mutation contracts |
| Tool/profile changes | Add only required read, project metadata, general-doc, and report tools; no terminal, source write, dispatch, deploy, or CodeGraph rebuild |
| Fixtures | Project match, continuity delta, allowed-doc write, denied source/control-plane/rebuild cases |
| Acceptance | Steward can produce facts/continuity card and bounded docs updates; cannot make Plan/SPEC/durable decisions or change source |
| Runtime actions | None until WI-09F activation |
| Non-goals | No generic docs editor, no source mutation, no automatic registry or CodeGraph maintenance |

## WI-09C — SPEC and Handoff Alignment

| Item | Contracted backlog |
| --- | --- |
| Goal | Implement the target `spec_kind`, frozen revision, artifact envelope, five payload schemas, and unified handoff/card contract |
| Likely modules | `_task_spec_create.py`, `_task_spec_update.py`, `_task_spec_common.py`, `_role_contracts.py`, task start/approval scope modules, all role report writers, handoff modules, traceability modules |
| New artifacts | `freeze` operation, immutable frozen SPEC revisions, `spec_id`/revision/hash task binding, stewardship payload and routing, envelope migration adapters |
| Tool/profile changes | `aota_task_spec_create/update/freeze` align on `spec_kind`; internal capability ceiling enforcement; task kind mapping docs |
| Fixtures | Each valid/invalid payload, cross-kind field rejection, frozen mutation rejection, stale binding rejection, all five cards/handoffs, card-first full-read triggers |
| Acceptance | Caller cannot select contradictory profile; frozen task binding is verified; every role produces contract-valid card/full report/handoff |
| Runtime actions | None; fixtures are isolated and must not activate Profiles |
| Non-goals | No broad workflow redesign and no deployment/activation |

## WI-09D — Existing Profile Boundary Integration

| Item | Contracted backlog |
| --- | --- |
| Goal | Make existing Profile configs, SOULs, skill bindings, capability registry, and toolsets agree with the WI-09A contract |
| Likely modules | `profiles/*/config.yaml`, `profiles/*/SOUL.md`, `skills/*`, `_capabilities.py`, plugin toolset registration, bounded mutation/command modules |
| New artifacts | Capability ceiling table, fixed command-ID registry, structured mutation receipts/audit contract |
| Tool/profile changes | Correct task-main source-write conflict; classify registry refresh as write; expose read/toolsets by role; add structured Coder mutation and fixed validator runner before terminal removal |
| Fixtures | Denied escalation, task-main source denial, registry-refresh classification, command-ID argument rejection, Coder SPEC scope/budget enforcement |
| Acceptance | All configs/registry/SOULs converge; workers cannot dispatch; task-main cannot source-write; Coder has enough bounded construction/validation capabilities |
| Runtime actions | No activation by default; separately approved configuration projection only |
| Non-goals | No unrestricted shell replacement and no deploy/restart/recreate |

## WI-09E — Isolated Workflow Fixtures

| Item | Contracted backlog |
| --- | --- |
| Goal | Prove contracts in isolated fixture storage, not against a live runtime |
| Likely modules | `fixtures/`, validators, package scripts, documentation validators |
| New artifacts | Six-role scenarios, Plan/SPEC/card/result/review/handoff fixtures, card-first consumer fixtures, negative security/routing fixtures |
| Tool/profile changes | None except test-only harness wiring |
| Fixtures | Steward facts -> task-main decision; architect challenge; coder result -> reviewer -> acceptance; debugger diagnosis -> separate implementation; full-report trigger matrix |
| Acceptance | Deterministic validation of schemas, routing, freeze immutability, capability ceilings, and document ownership boundaries |
| Runtime actions | No profile activation, service reload, or live worker |
| Non-goals | Long E2E, production data, or CodeGraph rebuild |

## WI-09F — Managed Runtime Activation

| Item | Contracted backlog |
| --- | --- |
| Goal | Project reviewed implementation into managed runtime with a reversible, operator-approved activation path |
| Likely modules | Managed deploy manifests, packaging/verification scripts, runtime source mirrors, operator runbooks |
| New artifacts | Activation/rollback evidence, capability inventory snapshot, upgrade/migration notes |
| Tool/profile changes | Deploy only the already-validated Profile/tool contract; do not mix feature design with activation |
| Fixtures | Preflight manifest checks, post-projection static inspection, rollback drill evidence |
| Acceptance | Exact deployed artifact inventory, no privilege expansion, explicit operator checkpoint, rollback available, health checks pass |
| Runtime actions | Explicit user/operator approval required for profile activation, reload, restart, recreate, or live worker |
| Non-goals | Silent activation, service recreation by default, or runtime changes outside approved package |

## Implementation ordering and gates

1. WI-09B creates the Steward boundary; WI-09C establishes compatible data
   contracts; WI-09D enforces capabilities; WI-09E proves them in isolation;
   WI-09F alone may activate them.
2. Every worker task remains a Star-topology leaf; all handoffs return to
   task-main and task-main alone records durable decision/closure.
3. No implementation work starts from this document.  The backlog is a
   construction plan, not implicit authorization.
