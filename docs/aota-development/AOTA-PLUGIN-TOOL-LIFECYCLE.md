# AOTA Plugin Tool Lifecycle SOP

## 1. Discover before development

Answer from Compose/source evidence: which process imports the tool (Agent API
server, legacy in-process WebUI, or both), its transport (`api_server`, `cli`,
or another declared transport), host and container paths, mount access/owner,
and container-scoped environment dependencies. Unknown runtime, transport, or
path facts are a stop condition.

## 2. Contract

Record tool ID, toolset ID, purpose, closed input/output schemas, mutation
class (`readonly`, `bounded_project_mutation`, `bounded_runtime_mutation`,
`control_plane_mutation`, or `dangerous/not_allowed`), path authority,
allowed/denied Profiles, transports, activation targets, and security rejects.

## 3. Implement and register

Use a bounded Python module, structured errors, bounded output, path safety,
no secret output, and no arbitrary shell. Register the module in `__init__.py`,
declare it in `plugin.yaml`, assign a toolset, and preserve capability metadata.
A tool name is not a toolset name: `platform_toolsets` accepts the toolset.

## 4. Expose safely

Each named Profile is independently configured; do not assume root/Profile
merge. Verify `platform_toolsets.<transport>` and `agent.disabled_toolsets`.
Unknown plugin toolsets may default on, so a task-main-only tool requires
task-main explicit enable and default/root/other Profile explicit deny. Do not
use absence from another Profile as a deny. Coder `terminal` and Project
Steward source-write boundaries remain disabled unless separately authorized.

## 5. Deploy, activate, verify, rollback

Update the managed manifest and preserve backup, deploy receipt, and
source/host/container hash parity. Parity does **not** prove a process reload.
For plugin module, handler, schema, registration, `plugin.yaml`, or toolset
changes in the current legacy topology: recreate `hermes-agent` **and**
`hermes-webui`, then open a new session. Profile/SOUL changes require a new
session; managed activation normally recreates both for cache safety. Compose
env/path changes recreate the affected service.

Runtime proof is: file parity → registry → toolset resolution → Profile valid
tool names → final model definitions → exact dispatch → bounded handler result.
Keep the backup reference, rollback command, affected containers, health check,
and post-rollback inventory as evidence.

`AOTA_TOOL_LIFECYCLE_DOC_PASS`

## Scope projection and mutation telemetry

The canonical frozen SPEC scope lives under `spec.payload`; task start uses the
shared extractor and writes an atomic `scope.json` projection with a digest and
SPEC/task binding. Project file tools select the gate by operation: reads use
`read_scope`, writes and patches use `write_scope`, and `forbidden_scope` wins.
The patch helper may read only its same write-authorized target.

Project tools append locked, bounded events to the active task's
`scope-events.jsonl` using trusted worker identity. Finalizer counts those
events separately from a task-local baseline delta. Workspace-wide `git diff`
and untracked listings are observations only and cannot be current-task worker
evidence without baseline attribution.

## Active-task artifact reader

`aota_active_task_artifact_open` is a readonly `aota_active_task_context`
toolset. It derives the task directory only from the trusted
`AOTA_PROFILE_TASK_ROOT`, `AOTA_PROFILE_TASK_WORKSPACE_ID`,
`AOTA_PROFILE_TASK_ID`, `AOTA_PROFILE_TASK_START_ID`, and
`AOTA_PROFILE_TASK_PROFILE` environment. Its input is only one closed artifact
enum: `SPEC`, `SCOPE`, `BINDING`, `INPUT_MANIFEST`, `SUBJECT_CARD`, or
`SUBJECT_RESULT`. It rejects paths, IDs, task selection, symlink traversal,
binary/oversize content, and raw metadata. `BINDING` is a bounded allowlisted
summary; subject artifacts require an explicit frozen reviewer/debugger/
architect subject binding.
