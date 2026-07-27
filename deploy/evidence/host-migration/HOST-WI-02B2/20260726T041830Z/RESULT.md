# HOST-WI-02B2 Final Result

FINAL=FAIL_STATE_DATABASE_INTEGRITY
SOURCE_VALIDATION=PASS

PROVIDER=opencode-zen
MODEL=deepseek-v4-flash-free
PROVIDER_CONFIGURATION_SOURCE=canonical_hermes_env_plus_model_alias_plus_bundled_provider_plugin
PROVIDER_CONFIGURATION_RESOLUTION=PASS
PROVIDER_REQUIRED_KEY_COUNT=1
PROVIDER_PRESENT_KEY_COUNT=2
PROVIDER_MISSING_KEYS=none
CREDENTIAL_EXPOSED=no

PROVIDER_ERROR_BEFORE=APIConnectionError:Connection error
PROVIDER_ERROR_CLASSIFICATION=PROXY_FAILURE
PROVIDER_FIX_REQUIRED=no
PROVIDER_FIX_APPLIED=no
PROVIDER_CONNECTIVITY_AFTER=UNAUTHENTICATED_ENDPOINT_REACHABLE_AUTH_NOT_RUN
PROVIDER_RETRY_COUNT=0

HOST_LAUNCHER=/home/latios/.local/bin/hermes-host
HOST_LAUNCHER_MUTATED=no
PRIVATE_RUNTIME_ENV_MUTATED=no
OFFICIAL_HERMES_EXECUTABLE_MUTATED=no

HOST_CLI_VERSION_SMOKE=PASS
HOST_CLI_DOCTOR=FAIL_GLOBAL_STATE_DB_MALFORMED
PROFILE_DISCOVERY=PASS
AOTA_PLUGIN_DISCOVERY=PASS_FROM_PREVIOUS_EVIDENCE
AOTA_TOOL_DISCOVERY=PASS_FROM_PREVIOUS_EVIDENCE
HOST_CLI_ONE_SHOT=NOT_RUN
ONE_SHOT_RESPONSE=NOT_OBTAINED

GLOBAL_STATE_DB=/home/latios/.hermes/state.db
GLOBAL_STATE_DB_INTEGRITY=FAIL
PROFILE_STATE_DB_COUNT=6
PROFILE_STATE_DB_INTEGRITY=PASS
ZERO_BYTE_DATABASE_COUNT=0
CORRUPTION_MARKER_COUNT=11

STATE_DB_TARGETED_BY_DEPLOYER=no
STATE_DB_REPLACED_BY_DEPLOYER=no_evidence
STATE_DB_REMOVED_BY_DEPLOYER=no_evidence
EXPECTED_RUNTIME_MUTATION=yes
PREEXISTING_SESSIONS_REMOVED=NOT_PROVABLE_GLOBAL_DB_MALFORMED
NEW_RUNTIME_SESSION_COUNT=NOT_COMPUTABLE
RUNTIME_PRESERVATION=FAIL_GLOBAL_STATE_DB_INTEGRITY

DESKTOP_HERMES_PATH=/home/latios/.local/bin/hermes-host
DESKTOP_RECONNECT=NOT_STARTED
DESKTOP_GATEWAY_STATUS=NOT_STARTED
DESKTOP_PROFILE_VISIBILITY=NOT_STARTED
AOTA_RUNTIME_INFO=NOT_STARTED
AOTA_RUNTIME_AVAILABLE=NOT_VERIFIED
AOTA_RUNTIME_ROOT=/home/latios/.hermes/aota-runtime
AOTA_PROFILE_TASK_ROOT=/home/latios/.hermes/aota-runtime/profile-tasks
DESKTOP_PROVIDER_SMOKE=NOT_STARTED

KNOWN_WORKSPACE_ID=aota-hermes-tools
KNOWN_WORKSPACE_READ_SMOKE=NOT_STARTED
WORKSPACE_RESOLUTION_LIMITATION=NOT_ASSESSED
WORKSPACE_TOOL_MODIFIED=no

CANONICAL_DEPLOYMENT_STATUS=PASS
STATIC_RUNTIME_PARITY=PASS
DOCKER_RUNTIME_STATE=OFFLINE_STANDBY
PROFILE_TASK_STARTED=no
DELEGATE_TASK_STARTED=no
CODEGRAPH_MUTATION=no
AMF_MUTATION=no

EVIDENCE_ROOT=deploy/evidence/host-migration/HOST-WI-02B2/20260726T041830Z
LATEST_POINTER=/home/latios/workspace/aota-hermes-tools/deploy/evidence/host-migration/HOST-WI-02B2/latest.json
HOST_WI_02B_CLOSURE_POINTER=/home/latios/workspace/aota-hermes-tools/deploy/evidence/host-migration/HOST-WI-02B/latest.json
GIT_STATUS_BEFORE="M deploy/profile-runtime-assembly.yaml; M plugin/aota-tools/_followup_task_create.py; M scripts/aota_forge_plan_package.py; M scripts/profile_runtime_assembly.py; ?? deploy/evidence/host-migration/; ?? docs/aota-host-migration/; ?? scripts/_host_symlink_projection.py; ?? scripts/capture-host-migration-docker-baseline.py; ?? scripts/capture-host-wi-02b-deployment-resume.py; ?? scripts/capture-host-wi-02b.py; ?? scripts/capture-host-wi-02b1.py; ?? scripts/review-host-wi-02b1.py; ?? scripts/verify-host-symlink-projection.py"
GIT_STATUS_AFTER="M deploy/profile-runtime-assembly.yaml
 M plugin/aota-tools/_followup_task_create.py
 M scripts/aota_forge_plan_package.py
 M scripts/profile_runtime_assembly.py
?? deploy/evidence/host-migration/
?? docs/aota-host-migration/
?? scripts/_host_symlink_projection.py
?? scripts/capture-host-migration-docker-baseline.py
?? scripts/capture-host-wi-02b-deployment-resume.py
?? scripts/capture-host-wi-02b.py
?? scripts/capture-host-wi-02b1.py
?? scripts/review-host-wi-02b1.py
?? scripts/verify-host-symlink-projection.py
?? scripts/verify-host-wi-02b2.py"
COMMIT=no
PUSH=no

LIMITATIONS=Global state.db is malformed at messages_fts; no repair or DB mutation was authorized. Authenticated provider test, one-shot, Desktop reconnect/live smoke remain pending.
NEXT_WORK_ITEM=HOST-WI-02C0
