#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# rollback.sh — AOTA Profile Task Control Plane rollback script
#
# Restores the most recent deployment backup to the Hermes runtime.
# NO sudo, NO service restart, NO docker restart.
# Actions requiring restart output ACTION_REQUIRED only.
# =============================================================================

CANONICAL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_PLUGIN_DIR="/home/hermeswebui/.hermes/plugins/aota-tools"
RUNTIME_SKILL_DIR="/home/hermeswebui/.hermes/skills/aota-profile-task-orchestration"
BACKUP_ROOT="${CANONICAL_ROOT}/.deploy-backups"

# ---------- Configuration ----------
declare -A PROFILE_MAP
PROFILE_MAP["task-main"]="/home/hermeswebui/.hermes/profiles/task-main"
PROFILE_MAP["coder"]="/home/hermeswebui/.hermes/profiles/coder"
PROFILE_MAP["debugger"]="/home/hermeswebui/.hermes/profiles/debugger"
PROFILE_MAP["reviewer"]="/home/hermeswebui/.hermes/profiles/reviewer"

# ---------- Colors for output ----------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

# =============================================================================
# 1. List latest backup in .deploy-backups/
# =============================================================================
if [ ! -d "${BACKUP_ROOT}" ]; then
    error "No backup directory found: ${BACKUP_ROOT}"
    exit 1
fi

# Find the most recent backup by timestamp directory name (YYYYMMDDTHHMMSS)
LATEST_BACKUP="$(ls -1 "${BACKUP_ROOT}" | sort | tail -1)"

if [ -z "${LATEST_BACKUP}" ]; then
    error "No backups found in ${BACKUP_ROOT}"
    exit 1
fi

BACKUP_DIR="${BACKUP_ROOT}/${LATEST_BACKUP}"
info "Found latest backup: ${BACKUP_DIR}"

# Verify backup has plugin content
if [ ! -d "${BACKUP_DIR}/plugin/aota-tools" ]; then
    error "Backup is incomplete — missing plugin/aota-tools/ in ${BACKUP_DIR}"
    exit 1
fi

# =============================================================================
# 2. Restore exact runtime plugin/profile/skill files from backup
# =============================================================================
info "Restoring plugin files..."
cp -a "${BACKUP_DIR}/plugin/aota-tools"/* "${RUNTIME_PLUGIN_DIR}/"
info "  Plugin files restored."

# Restore profile configs
info "Restoring profile configs..."
for profile_name in "${!PROFILE_MAP[@]}"; do
    backup_prof="${BACKUP_DIR}/profiles/${profile_name}"
    runtime_prof="${PROFILE_MAP[$profile_name]}"

    if [ -d "${backup_prof}" ]; then
        if [ ! -d "${runtime_prof}" ]; then
            warn "Runtime profile directory missing: ${runtime_prof}, skipping."
            continue
        fi

        if [ -f "${backup_prof}/config.yaml" ]; then
            cp -a "${backup_prof}/config.yaml" "${runtime_prof}/config.yaml"
            info "  Restored config.yaml for ${profile_name}"
        fi

        if [ -f "${backup_prof}/SOUL.md" ]; then
            cp -a "${backup_prof}/SOUL.md" "${runtime_prof}/SOUL.md"
            info "  Restored SOUL.md for ${profile_name}"
        fi
    fi
done

# Restore skill
if [ -d "${BACKUP_DIR}/skills/aota-profile-task-orchestration" ]; then
    info "Restoring skill..."
    mkdir -p "${RUNTIME_SKILL_DIR}"
    cp -a "${BACKUP_DIR}/skills/aota-profile-task-orchestration"/* "${RUNTIME_SKILL_DIR}/"
    info "  Skill files restored."
fi

# =============================================================================
# 3. Verify — py_compile restored files
# =============================================================================
info "Verifying restored plugin files compile..."

COMPILE_FAILED=0
for pyfile in "${RUNTIME_PLUGIN_DIR}"/*.py; do
    if ! python3 -m py_compile "$pyfile" 2>/dev/null; then
        error "py_compile FAILED: $pyfile"
        COMPILE_FAILED=1
    fi
done

if [ "${COMPILE_FAILED}" -eq 1 ]; then
    error "Restored files failed py_compile. Rollback may be incomplete."
    exit 1
fi
info "All restored .py files pass py_compile."

# =============================================================================
# 4. Print ACTION_REQUIRED (restart needed)
# =============================================================================
echo ""
echo "============================================================================="
echo -e "${YELLOW}ACTION_REQUIRED${NC}: Hermes profile restart or plugin reload is required"
echo "                 for the restored plugin changes to take effect."
echo ""
echo "  Rollback timestamp: ${LATEST_BACKUP}"
echo "  Backup restored:    ${BACKUP_DIR}"
echo "============================================================================="
echo ""
info "Rollback complete."
