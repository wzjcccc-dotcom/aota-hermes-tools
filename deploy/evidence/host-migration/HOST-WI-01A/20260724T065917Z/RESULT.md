# HOST-WI-01A Final Result

FINAL=PASS_WITH_OPERATOR_DESKTOP_PATH_CONFIRMATION_REQUIRED
SOURCE_VALIDATION=PASS

HOST_HERMES_EXECUTABLE=/home/latios/.venvs/hermes-agent-host/bin/hermes
HOST_HERMES_VERSION=v0.19.0 (2026.7.20)
HOST_SOURCE_ROOT=/home/latios/workspace/hermes-agent-host
CANONICAL_HOME=/home/latios/.hermes
CANONICAL_RUNTIME_SMOKE=PASS
PROVIDER=opencode-zen
MODEL=deepseek-v4-flash-free
ONE_SHOT_RESPONSE=CANONICAL_HOST_RUNTIME_OK
CANONICAL_CONFIG_MUTATED=no
SECRET_EXPOSED=no

PROFILE_COUNT=6
PROFILE_NAMES=architect,coder,debugger,project-steward,reviewer,task-main
DESKTOP_SSH_PATH_EXPECTED=/home/latios/.venvs/hermes-agent-host/bin/hermes
DESKTOP_PATH_OPERATOR_CONFIRMATION=NEEDS_OPERATOR_DESKTOP_PATH_CONFIRMATION

VANILLA_HOME=/home/latios/.local/state/aota-host-migration/HOST-WI-01/vanilla-hermes-home
VANILLA_HOME_REALPATH=/home/latios/.local/state/aota-host-migration/HOST-WI-01/vanilla-hermes-home
VANILLA_FILE_COUNT_BEFORE=552
VANILLA_DIRECTORY_COUNT_BEFORE=191
VANILLA_BYTES_BEFORE=10622959
VANILLA_PROCESS_COUNT_BEFORE=1
VANILLA_SYSTEMD_REFERENCE_COUNT=0
VANILLA_WRAPPER=/home/latios/.local/bin/hermes-host-vanilla
VANILLA_WRAPPER_REMOVED=not_present
REALPATH_SAFETY=PASS
VANILLA_HOME_REMOVED=yes
VANILLA_HOME_EXISTS_AFTER=no

DOCKER_RUNTIME_STATE=OFFLINE_STANDBY
DOCKER_ARTIFACTS_PRESERVED=yes
HOST_WI_00_BACKUP_PRESERVED=yes
AOTA_RUNTIME_MUTATED=no
UNRELATED_FILES_PRESERVED=yes

EVIDENCE_ROOT=deploy/evidence/host-migration/HOST-WI-01A/20260724T065917Z
LATEST_POINTER=deploy/evidence/host-migration/HOST-WI-01A/latest.json
COMMIT=no
PUSH=no

LIMITATIONS=Desktop application settings are not readable from the Host; operator must confirm the SSH executable path.
NEXT_WORK_ITEM=HOST-WI-01B

## Desktop handoff

In Hermes Desktop, confirm and use:

- Connection mode: SSH
- Host: `100.123.10.71`
- User: `latios`
- Port: `22`
- Hermes path: `/home/latios/.venvs/hermes-agent-host/bin/hermes`

Then select **儲存並重新連線**. Do not use `/home/latios/.local/bin/hermes-host-vanilla` or `vanilla-hermes-home`.
