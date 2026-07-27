# HOST-WI-02B3 Final Result

FINAL=NEEDS_CONTROLLED_GLOBAL_DATABASE_RECOVERY
SOURCE_VALIDATION=PASS
RUNTIME_QUIESCENT=yes

GLOBAL_STATE_DB=/home/latios/.hermes/state.db
GLOBAL_STATE_DB_EXISTS=yes
GLOBAL_STATE_DB_SIZE=497434624
GLOBAL_STATE_DB_WAL_PRESENT=yes
GLOBAL_STATE_DB_SHM_PRESENT=yes

SYSTEM_SQLITE_VERSION=3.45.1
SYSTEM_SQLITE_FTS5=PASS
SYSTEM_SQLITE_TRIGRAM=PASS
SYSTEM_PYTHON_SQLITE_VERSION=3.45.1
SYSTEM_PYTHON_FTS5=PASS
SYSTEM_PYTHON_TRIGRAM=PASS
HERMES_PYTHON_SQLITE_VERSION=3.45.1
HERMES_PYTHON_FTS5=PASS
HERMES_PYTHON_TRIGRAM=PASS
FTS5_MEMORY_FIXTURE=PASS
TRIGRAM_MEMORY_FIXTURE=PASS

GLOBAL_SCHEMA_VERSION=23
EXPECTED_SCHEMA_VERSION=23
MIGRATION_STATE=SCHEMA_CURRENT

GLOBAL_CORE_SCHEMA_READABLE=yes
SESSIONS_TABLE_READABLE=no
MESSAGES_TABLE_READABLE=no
CORE_TABLE_READABILITY=FAIL
CORE_DATA_LOSS_INDICATOR=CORE_READ_FAILURE

MESSAGES_FTS_PRESENT=yes
MESSAGES_FTS_CONSTRUCTOR=FAIL_VTABLE_CONSTRUCTOR
MESSAGES_FTS_COUNT=FAIL_DATABASE_DISK_IMAGE_MALFORMED
MESSAGES_FTS_MATCH=FAIL_VTABLE_CONSTRUCTOR
MESSAGES_FTS_SHADOW_TABLES=present_but_unreadable

MESSAGES_FTS_TRIGRAM_PRESENT=yes
MESSAGES_FTS_TRIGRAM_CONSTRUCTOR=FAIL_VTABLE_CONSTRUCTOR
MESSAGES_FTS_TRIGRAM_COUNT=FAIL_DATABASE_DISK_IMAGE_MALFORMED
MESSAGES_FTS_TRIGRAM_MATCH=FAIL_VTABLE_CONSTRUCTOR
MESSAGES_FTS_TRIGRAM_SHADOW_TABLES=present_but_unreadable

SYSTEM_SQLITE_INTEGRITY_CHECK=FAIL_VTABLE_CONSTRUCTOR
SYSTEM_PYTHON_INTEGRITY_CHECK=FAIL_VTABLE_CONSTRUCTOR
HERMES_PYTHON_INTEGRITY_CHECK=FAIL_VTABLE_CONSTRUCTOR
HERMES_PYTHON_QUICK_CHECK=FAIL_VTABLE_CONSTRUCTOR

PROFILE_STATE_DB_COUNT=6
PROFILE_STATE_DB_INTEGRITY=PASS
GLOBAL_VS_PROFILE_SCHEMA_COMPARISON=PASS

FAILURE_CLASSIFICATION=CORE_DATABASE_CORRUPTION
VERIFIER_FALSE_POSITIVE=no
FTS_COMPATIBILITY_FAILURE=no
FTS_SCHEMA_MISMATCH=no
FTS_SHADOW_TABLE_DAMAGE=CONFIRMED
FTS_INDEX_INCONSISTENCY=CONFIRMED
MIGRATION_RECOVERY_REQUIRED=no_evidence
CORE_DATABASE_CORRUPTION=CONFIRMED

RECOMMENDED_RECOVERY_OPTION=E
RECOVERY_MUTATION_REQUIRED=yes
OFFICIAL_HERMES_RECOVERY_PATH=repair_state_db_schema_after_controlled_snapshot
BACKUP_REQUIRED=yes
ROLLBACK_REQUIRED=yes

VERIFIER_MODIFIED=no
DB_MUTATED=no
WAL_MUTATED=no
SHM_MUTATED=no
SESSION_CONTENT_EXPOSED=no
MESSAGE_CONTENT_EXPOSED=no

INDEPENDENT_REVIEW_TASK_ID=HOST-WI-02B3-INDEPENDENT-REVIEW
INDEPENDENT_REVIEW_VERDICT=PASS
BLOCKING_FINDING_COUNT=0
NON_BLOCKING_FINDING_COUNT=1

EVIDENCE_ROOT=deploy/evidence/host-migration/HOST-WI-02B3/20260726T051020Z
LATEST_POINTER=/home/latios/workspace/aota-hermes-tools/deploy/evidence/host-migration/HOST-WI-02B3/latest.json
HOST_WI_02B_CLOSURE_POINTER=/home/latios/workspace/aota-hermes-tools/deploy/evidence/host-migration/HOST-WI-02B/latest.json
GIT_STATUS_BEFORE="M deploy/profile-runtime-assembly.yaml; M plugin/aota-tools/_followup_task_create.py; M scripts/aota_forge_plan_package.py; M scripts/profile_runtime_assembly.py; ?? deploy/evidence/host-migration/; ?? docs/aota-host-migration/; ?? scripts/_host_symlink_projection.py; ?? scripts/capture-host-migration-docker-baseline.py; ?? scripts/capture-host-wi-02b-deployment-resume.py; ?? scripts/capture-host-wi-02b.py; ?? scripts/capture-host-wi-02b1.py; ?? scripts/review-host-wi-02b1.py; ?? scripts/verify-host-symlink-projection.py"
GIT_STATUS_AFTER="M deploy/profile-runtime-assembly.yaml; M plugin/aota-tools/_followup_task_create.py; M scripts/aota_forge_plan_package.py; M scripts/profile_runtime_assembly.py; ?? deploy/evidence/host-migration/; ?? docs/aota-host-migration/; ?? scripts/_host_symlink_projection.py; ?? scripts/capture-host-migration-docker-baseline.py; ?? scripts/capture-host-wi-02b-deployment-resume.py; ?? scripts/capture-host-wi-02b.py; ?? scripts/capture-host-wi-02b1.py; ?? scripts/review-host-wi-02b1.py; ?? scripts/verify-host-symlink-projection.py; ?? scripts/verify-host-wi-02b2.py"
COMMIT=no
PUSH=no

LIMITATIONS=Read-only diagnostics confirm malformed core b-trees and FTS failures, but cannot identify recoverable row extent without controlled snapshot/repair work. Website session-storage documentation says schema 21 while hermes_state.py source says 23.
NEXT_WORK_ITEM=HOST-WI-02B3R Controlled Global Database Recovery
