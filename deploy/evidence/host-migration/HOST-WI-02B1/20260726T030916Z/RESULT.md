# HOST-WI-02B1 Final Result

FINAL=PASS_CANONICAL_HOST_SYMLINK_PROJECTION_ADAPTER_WITH_LIMITATIONS
SOURCE_VALIDATION=PASS

CANONICAL_SOURCE_ROOT=/home/latios/workspace/aota-hermes-tools
CANONICAL_RUNTIME_ROOT=/home/latios/.hermes
ADAPTER_IMPLEMENTED=yes
ADAPTER_FILES=scripts/_host_symlink_projection.py,scripts/aota_forge_plan_package.py,scripts/profile_runtime_assembly.py,scripts/verify-host-symlink-projection.py,scripts/capture-host-wi-02b1.py

PROJECTION_CONTRACT_SOURCE=deploy/profile-runtime-assembly.yaml
PROJECTION_CONTRACT_SCHEMA_VERSION=1
PROJECTION_CONTRACT_VALIDATION=PASS
MANIFEST_DRIVEN=yes
EXACT_PROFILE_ALLOWLIST=yes
EXACT_TARGET_REQUIRED=yes

PROFILE_COUNT=6
LEGAL_PROJECTION_COUNT=5
INVALID_PROJECTION_COUNT=0
SYMLINK_REJECTED_BEFORE=5
SYMLINK_PROJECTION_VALID_AFTER_ADAPTER=5

CANONICAL_GLOBAL_PLUGIN_TARGET=/home/latios/.hermes/plugins/aota-tools
PHYSICAL_GLOBAL_PLUGIN_TARGET_COUNT=1
LOGICAL_MANAGED_PATH_COUNT=708
PHYSICAL_TARGET_COUNT=243
DEDUPLICATED_WRITE_COUNT=465

SYMLINK_CHAIN_ALLOWED=no
DANGLING_SYMLINK_ALLOWED=no
OUTSIDE_RUNTIME_TARGET_ALLOWED=no
SOURCE_WORKSPACE_TARGET_ALLOWED=no
PROFILE_TO_PROFILE_TARGET_ALLOWED=no
TARGET_TYPE_VALIDATION=PASS

PATH_TRAVERSAL_PROTECTION=PASS
SYMLINK_PROTECTION=PASS
CONTAINMENT_VALIDATION=PASS
TOCTOU_REVALIDATION=PASS

BACKUP_DEDUPLICATION=PASS
ROLLBACK_DEDUPLICATION=PASS
LOGICAL_PROJECTION_RECEIPTS=PASS
RECEIPT_SCHEMA_VALIDATION=PASS

EXISTING_FIXTURE_REGRESSION=PASS
NEW_PROJECTION_PASS_FIXTURES=PASS
NEW_PROJECTION_FAIL_FIXTURES=PASS
BACKUP_FIXTURE=PASS
ROLLBACK_FIXTURE=PASS
DEDUPLICATION_FIXTURE=PASS
DRY_RUN_RUNTIME_ASSESSMENT=PASS

FORMAL_RUNTIME_DEPLOY_EXECUTED=no
RUNTIME_MUTATED=no
GLOBAL_PLUGIN_MUTATED=no
PROFILE_SYMLINKS_MUTATED=no

AOTA_TOOL_SOURCE_MODIFIED=no
PROFILE_CONTENT_MODIFIED=no
SKILL_CONTENT_MODIFIED=no
HERMES_AGENT_SOURCE_MODIFIED=no
OFFICIAL_HERMES_EXECUTABLE_MUTATED=no
HOST_LAUNCHER_MUTATED=no
PRIVATE_RUNTIME_ENV_MUTATED=no

PROFILE_STATE_DATABASES_PRESERVED=yes
AOTA_RUNTIME_ARTIFACTS_PRESERVED=yes
UNRELATED_FILES_PRESERVED=yes

INDEPENDENT_REVIEW_TASK_ID=PENDING_EXTERNAL_REVIEW
INDEPENDENT_REVIEW_VERDICT=PENDING_EXTERNAL_REVIEW
BLOCKING_FINDING_COUNT=PENDING_EXTERNAL_REVIEW
NON_BLOCKING_FINDING_COUNT=PENDING_EXTERNAL_REVIEW

EVIDENCE_ROOT=deploy/evidence/host-migration/HOST-WI-02B1/20260726T030916Z
LATEST_POINTER=deploy/evidence/host-migration/HOST-WI-02B1/latest.json
GIT_STATUS_BEFORE=["## main...origin/main", " M deploy/profile-runtime-assembly.yaml", " M plugin/aota-tools/_followup_task_create.py", " M scripts/aota_forge_plan_package.py", " M scripts/profile_runtime_assembly.py", "?? deploy/evidence/host-migration/", "?? docs/aota-host-migration/", "?? scripts/_host_symlink_projection.py", "?? scripts/capture-host-migration-docker-baseline.py", "?? scripts/capture-host-wi-02b.py", "?? scripts/capture-host-wi-02b1.py", "?? scripts/verify-host-symlink-projection.py"]
GIT_STATUS_AFTER=["## main...origin/main", " M deploy/profile-runtime-assembly.yaml", " M plugin/aota-tools/_followup_task_create.py", " M scripts/aota_forge_plan_package.py", " M scripts/profile_runtime_assembly.py", "?? deploy/evidence/host-migration/", "?? docs/aota-host-migration/", "?? scripts/_host_symlink_projection.py", "?? scripts/capture-host-migration-docker-baseline.py", "?? scripts/capture-host-wi-02b.py", "?? scripts/capture-host-wi-02b1.py", "?? scripts/verify-host-symlink-projection.py"]
COMMIT=no
PUSH=no

LIMITATIONS=Existing package readiness remains blocked by a pre-existing secret-scan match in scripts/capture-host-migration-docker-baseline.py; independent review is required before formal deployment resume. The adapter fail-closed revalidation cannot eliminate external concurrent filesystem mutation between checks.
NEXT_WORK_ITEM=HOST-WI-02B-DEPLOYMENT-RESUME
