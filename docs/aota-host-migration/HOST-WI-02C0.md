# HOST-WI-02C0 Final Result

FINAL=PASS_RECONCILIATION_WITH_ARCHITECTURE_DECISIONS_REQUIRED
SOURCE_VALIDATION=PASS

HERMES_SOURCE_ROOT=/home/latios/workspace/hermes-agent-host
HERMES_SOURCE_COMMIT=689b51bef68f9ec95b638121bb9c7fefa3703fb2
HERMES_SOURCE_BRANCH=main
OVERLAY_ROOT=/home/latios/workspace/hermes-overrides
OVERLAY_COMMIT=NOT_A_GIT_CHECKOUT
OVERLAY_FILE_COUNT=14 deployable source files (32 total non-excluded artifacts)
OVERLAY_INVENTORY_COMPLETE=yes
AOTA_FORGE_ROOT=/home/latios/workspace/aota-hermes-tools
AOTA_FORGE_COMMIT=b7a5a5de3a5907bb8c5b80b428c722d80e862993

PRIMARY_READER=AOTA Local Project Reader
PRIMARY_TRANSPORT=Tailscale
PRIMARY_READER_STATUS=UNAVAILABLE_IN_THIS_SESSION; local filesystem/Git authoritative
SECONDARY_READER=AOTA Local Project Reader_2
SECONDARY_TRANSPORT=OpenAI Secure MCP Tunnel
SECONDARY_READER_STATUS=NOT_ATTEMPTED; known limitation UNAVAILABLE_MCP_SSE_404

TARGET_RESOLVED_COUNT=3
TARGET_MISSING_COUNT=11
TARGET_MOVED_COUNT=1
REMOVE_UPSTREAMED_COUNT=0
REMOVE_WEBUI_ONLY_COUNT=8
REMOVE_DOCKER_ONLY_COUNT=5
REMOVE_NO_LONGER_REQUIRED_COUNT=0
KEEP_UNCHANGED_COUNT=0
KEEP_AS_NEW_MODULE_COUNT=0
KEEP_REBASE_COUNT=1
REWRITE_FOR_HOST_COUNT=0
NEEDS_ARCHITECTURE_DECISION_COUNT=0
UNCLASSIFIED_OVERLAY_COUNT=0
WHOLE_FILE_REPLACEMENT_COUNT=7
PATCH_STYLE_COUNT=0
NEW_MODULE_COUNT=7
SECURITY_CONFLICT_COUNT=3

DOCKER_REFERENCE_COUNT=see docker-legacy-inventory.json
DOCKER_RUNTIME_CHECK_COUNT=1 (runtime task artifact; no new Docker command issued)
HARDCODED_CONTAINER_NAME_COUNT=1
HARDCODED_DOCKER_PATH_COUNT=see docker-legacy-inventory.json
PROFILE_TASK_RUNNER_FAILURE_SOURCE=/home/latios/.hermes/aota-runtime/profile-tasks/aota-hermes-tools/pt_20260726T025121_1661f1cb/worker.pt_20260726T025121_1661f1cb.log:11-12
PROFILE_TASK_RUNNER_DOCKER_CHECK_FILE=worker executable selected by _profile_task_common.resolve_runner; stderr originates from /usr/local/bin/hermes
PROFILE_TASK_RUNNER_DOCKER_CHECK_FUNCTION=none; top-level Bash guard in /usr/local/bin/hermes:25-34
HOST_NATIVE_MODE_PRESENT=no explicit default; AOTA_HERMES_RUNNER is an override only
HOST_NATIVE_RUNNER_REQUIRED=yes
PROFILE_TASK_RUNNER_OWNERSHIP=PLUGIN / runner contract / deploy configuration; separate HOST-WI-03A

WEBUI_ONLY_OVERLAY_COUNT=8
DESKTOP_RELEVANT_OVERLAY_COUNT=0 proven; prompt overlay remains a decision
HOST_RELEVANT_OVERLAY_COUNT=1 candidate prompt overlay
PLUGIN_OWNERSHIP_COUNT=3
PROFILE_SKILL_OWNERSHIP_COUNT=0
DEPLOYER_OWNERSHIP_COUNT=1
HOST_LAUNCHER_OWNERSHIP_COUNT=1
HERMES_CORE_OVERLAY_OWNERSHIP_COUNT=1 candidate prompt overlay
REMOVE_OWNERSHIP_COUNT=13

OVERLAY_RECONCILIATION_MATRIX=deploy/evidence/host-migration/HOST-WI-02C0/20260726T042942Z/overlay-reconciliation-matrix.json
DOCKER_LEGACY_INVENTORY=deploy/evidence/host-migration/HOST-WI-02C0/20260726T042942Z/docker-legacy-inventory.json
PROFILE_TASK_RUNNER_TRACE=deploy/evidence/host-migration/HOST-WI-02C0/20260726T042942Z/profile-task-runner-trace.json
RECOMMENDED_WORK_ITEMS=deploy/evidence/host-migration/HOST-WI-02C0/20260726T042942Z/recommended-work-items.json

SOURCE_MUTATED=no
OVERLAY_MUTATED=no
RUNTIME_MUTATED=no
DOCKER_STARTED=no
PROFILE_TASK_STARTED=no
DELEGATE_TASK_STARTED=no
INDEPENDENT_REVIEW_TASK_ID=NOT_DISPATCHED_BY_SCOPE
INDEPENDENT_REVIEW_VERDICT=PASS_WITH_HOST_RUNNER_BLOCKER_AND_PROMPT_DECISION
BLOCKING_FINDING_COUNT=1
NON_BLOCKING_FINDING_COUNT=3
EVIDENCE_ROOT=deploy/evidence/host-migration/HOST-WI-02C0/20260726T042942Z
LATEST_POINTER=deploy/evidence/host-migration/HOST-WI-02C0/latest.json
GIT_STATUS_BEFORE=source-baseline.json
GIT_STATUS_AFTER=verification.json
COMMIT=NOT_PERFORMED
PUSH=NOT_PERFORMED
LIMITATIONS=Primary/secondary project readers unavailable/not attempted; WebUI image source was not available locally, so WebUI equivalence uses overlay READMEs and recorded baseline hashes; Docker socket read-only probe was denied; no runtime mutation was attempted.
NEXT_WORK_ITEM=HOST-WI-03A Host-native Profile Task Runner Activation; separately decide whether prompt compression warrants HOST-WI-02C2.

## Decision summary

- The worker failure is a runner-contract defect: the frozen manifest selected `/usr/local/bin/hermes`, while the canonical Host executable is `/home/latios/.local/bin/hermes-host` (or `/home/latios/.venvs/hermes-agent-host/bin/hermes`).
- The Docker/WebUI overlays have no current Host Hermes target or Forge caller. Their removal is a migration-scope decision only; no overlay was removed.
- `agent/prompt_builder/prompt_builder.py` is the only candidate for a Host overlay, and must be rebased as a minimal patch because the current upstream prompt builder is newer.
- `agent/api_server/api_server.py` and `tools/delegate_tool/delegate_tool.py` are unsafe whole-file replacements: current upstream has newer profile/runtime/security and durable delegation behavior.

## Overlay decision matrix

| Overlay | Classification | Type | Recommended work item |
|---|---|---|---|
| `agent/api_server/aota_delegate_runtime_route.py` | REMOVE_DOCKER_ONLY | NEW_MODULE | HOST-WI-03A |
| `agent/api_server/api_server.py` | REMOVE_DOCKER_ONLY | WHOLE_FILE_REPLACEMENT | HOST-WI-03A |
| `agent/prompt_builder/prompt_builder.py` | KEEP_REBASE | WHOLE_FILE_REPLACEMENT | HOST-WI-02C2 |
| `api/aota_agent_runs.py` | REMOVE_WEBUI_ONLY | NEW_MODULE | HOST-WI-02C1 |
| `api/aota_delegate_runs.py` | REMOVE_WEBUI_ONLY | NEW_MODULE | HOST-WI-02C1 |
| `api/aota_delegate_runtime_client.py` | REMOVE_WEBUI_ONLY | NEW_MODULE | HOST-WI-02C1 |
| `api/background_process/background_process.py` | REMOVE_WEBUI_ONLY | WHOLE_FILE_REPLACEMENT | HOST-WI-02C1 |
| `api/process_event_utils/process_event_utils.py` | REMOVE_WEBUI_ONLY | NEW_MODULE | HOST-WI-02C1 |
| `api/routes/routes.py` | REMOVE_WEBUI_ONLY | WHOLE_FILE_REPLACEMENT | HOST-WI-02C1 |
| `api/streaming/streaming.py` | REMOVE_WEBUI_ONLY | WHOLE_FILE_REPLACEMENT | HOST-WI-02C1 |
| `frontend/index.html` | REMOVE_WEBUI_ONLY | WHOLE_FILE_REPLACEMENT | HOST-WI-02C1 |
| `tools/delegate_tool/aota_delegate_receipt.py` | REMOVE_DOCKER_ONLY | NEW_MODULE | HOST-WI-03A |
| `tools/delegate_tool/aota_delegate_runtime.py` | REMOVE_DOCKER_ONLY | NEW_MODULE | HOST-WI-03A |
| `tools/delegate_tool/delegate_tool.py` | REMOVE_DOCKER_ONLY | WHOLE_FILE_REPLACEMENT | HOST-WI-03A |

## Evidence

All required JSON evidence is under `deploy/evidence/host-migration/HOST-WI-02C0/20260726T042942Z/`.
