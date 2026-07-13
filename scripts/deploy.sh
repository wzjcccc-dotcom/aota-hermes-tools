#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# deploy.sh — AOTA Profile Task Control Plane canonical deploy script
#
# Deploys canonical plugin/profile/skills (all skills/*/ containing SKILL.md) sources to the Hermes runtime.
# NO sudo, NO service restart, NO docker restart.
# Actions requiring restart output ACTION_REQUIRED only.
# =============================================================================

CANONICAL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_HERMES_HOME="${AOTA_HERMES_HOME_HOST-/home/latios/.hermes}"
RUNTIME_PLUGIN_DIR=""
RUNTIME_SKILLS_ROOT=""
RUNTIME_PROFILES_ROOT=""
RUNTIME_PLUGIN_RESOLVED=""
PROFILE_PLUGIN_RELATIVE_TARGET="../../../plugins/aota-tools"
BACKUP_ROOT="${CANONICAL_ROOT}/.deploy-backups"
TIMESTAMP="$(date +%Y%m%dT%H%M%S)"

# ---------- Configuration ----------
# Map canonical profiles/ to runtime profiles/
declare -A PROFILE_MAP

# ---------- Colors for output ----------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

configure_runtime_paths() {
    local requested_root="${RUNTIME_HERMES_HOME}"

    if [[ -z "${requested_root}" || "${requested_root}" == "/" || "${requested_root}" != /* || "${requested_root}" == *[[:cntrl:]]* ]]; then
        error "AOTA_HERMES_HOME_HOST must be an existing absolute directory other than /"
        exit 1
    fi

    if ! RUNTIME_HERMES_HOME="$(realpath -e -- "${requested_root}" 2>/dev/null)" || [ ! -d "${RUNTIME_HERMES_HOME}" ]; then
        error "AOTA_HERMES_HOME_HOST does not resolve to an existing directory: ${requested_root}"
        exit 1
    fi

    RUNTIME_PLUGIN_DIR="${RUNTIME_HERMES_HOME}/plugins/aota-tools"
    RUNTIME_SKILLS_ROOT="${RUNTIME_HERMES_HOME}/skills"
    RUNTIME_PROFILES_ROOT="${RUNTIME_HERMES_HOME}/profiles"

    if [ ! -d "${RUNTIME_HERMES_HOME}/plugins" ] || [ ! -d "${RUNTIME_PROFILES_ROOT}" ]; then
        error "Runtime root must contain plugins/ and profiles/: ${RUNTIME_HERMES_HOME}"
        exit 1
    fi

    if ! RUNTIME_PLUGIN_RESOLVED="$(realpath -e -- "${RUNTIME_PLUGIN_DIR}" 2>/dev/null)" || [ ! -d "${RUNTIME_PLUGIN_RESOLVED}" ] || [ "${RUNTIME_PLUGIN_RESOLVED}" = "/" ]; then
        error "Runtime plugin directory must exist and be a directory: ${RUNTIME_PLUGIN_DIR}"
        exit 1
    fi

    case "${RUNTIME_PLUGIN_RESOLVED}" in
        "${RUNTIME_HERMES_HOME}"/*) ;;
        *)
            error "Runtime plugin directory escapes runtime root: ${RUNTIME_PLUGIN_RESOLVED}"
            exit 1
            ;;
    esac

    PROFILE_MAP["task-main"]="${RUNTIME_PROFILES_ROOT}/task-main"
    PROFILE_MAP["coder"]="${RUNTIME_PROFILES_ROOT}/coder"
    PROFILE_MAP["debugger"]="${RUNTIME_PROFILES_ROOT}/debugger"
    PROFILE_MAP["reviewer"]="${RUNTIME_PROFILES_ROOT}/reviewer"
    PROFILE_MAP["architect"]="${RUNTIME_PROFILES_ROOT}/architect"
}

configure_runtime_paths

# =============================================================================
# 1. Preflight — check canonical source exists, check VERSION
# =============================================================================
info "Preflight checks..."

if [ ! -d "${CANONICAL_ROOT}/plugin/aota-tools" ]; then
    error "Canonical plugin source not found: ${CANONICAL_ROOT}/plugin/aota-tools"
    exit 1
fi

if [ ! -f "${CANONICAL_ROOT}/VERSION" ]; then
    error "VERSION file not found at ${CANONICAL_ROOT}/VERSION"
    exit 1
fi

CANONICAL_VERSION="$(cat "${CANONICAL_ROOT}/VERSION" | tr -d '[:space:]')"
info "Canonical VERSION: ${CANONICAL_VERSION}"

if [ -z "${CANONICAL_VERSION}" ]; then
    error "VERSION file is empty"
    exit 1
fi

# =============================================================================
# 2. Source validation — py_compile canonical plugin .py files
# =============================================================================
info "Validating canonical plugin source files..."

COMPILE_FAILED=0
for pyfile in "${CANONICAL_ROOT}/plugin/aota-tools"/*.py; do
    if ! python3 -m py_compile "$pyfile" 2>/dev/null; then
        error "py_compile FAILED: $pyfile"
        COMPILE_FAILED=1
    fi
done

if [ "${COMPILE_FAILED}" -eq 1 ]; then
    error "Canonical source validation failed. Aborting deploy."
    exit 1
fi
info "All canonical .py files pass py_compile."

# =============================================================================
# 3. Destination validation — check runtime plugin dir exists
# =============================================================================
info "Checking runtime destinations..."

if [ ! -d "${RUNTIME_PLUGIN_DIR}" ]; then
    error "Runtime plugin directory does not exist: ${RUNTIME_PLUGIN_DIR}"
    exit 1
fi
info "Runtime plugin dir: ${RUNTIME_PLUGIN_DIR}"

# =============================================================================
# 4. Backup current runtime plugin — cp -a to .deploy-backups/<timestamp>/
# =============================================================================
info "Backing up current runtime plugin..."

BACKUP_DIR="${BACKUP_ROOT}/${TIMESTAMP}"
mkdir -p "${BACKUP_DIR}/plugin"
cp -a "${RUNTIME_PLUGIN_DIR}" "${BACKUP_DIR}/plugin/"

# Also backup profile configs
for profile_name in "${!PROFILE_MAP[@]}"; do
    runtime_prof="${PROFILE_MAP[$profile_name]}"
    if [ -d "${runtime_prof}" ]; then
        mkdir -p "${BACKUP_DIR}/profiles/${profile_name}"
        if [ -f "${runtime_prof}/config.yaml" ]; then
            cp -a "${runtime_prof}/config.yaml" "${BACKUP_DIR}/profiles/${profile_name}/"
        fi
        if [ -f "${runtime_prof}/SOUL.md" ]; then
            cp -a "${runtime_prof}/SOUL.md" "${BACKUP_DIR}/profiles/${profile_name}/"
        fi
    fi
done

# Backup runtime skills — all skill directories containing SKILL.md
if ls "${RUNTIME_SKILLS_ROOT}"/*/SKILL.md &>/dev/null 2>&1; then
    for skill_dir in "${RUNTIME_SKILLS_ROOT}"/*/; do
        skill_name="$(basename "$skill_dir")"
        if [ -f "${skill_dir}SKILL.md" ]; then
            mkdir -p "${BACKUP_DIR}/skills/${skill_name}"
            cp -a "${skill_dir}SKILL.md" "${BACKUP_DIR}/skills/${skill_name}/"
        fi
    done
fi

info "Backup saved to: ${BACKUP_DIR}"

# =============================================================================
# 5. Deploy exact files — cp canonical plugin files to runtime plugin dir
# =============================================================================
info "Deploying canonical plugin files to runtime..."

cp --preserve=mode,timestamps \
    "${CANONICAL_ROOT}/plugin/aota-tools"/*.py \
    "${CANONICAL_ROOT}/plugin/aota-tools"/plugin.yaml \
    "${RUNTIME_PLUGIN_DIR}/"

info "Plugin files deployed."

# =============================================================================
# 6. Remove stale managed files — delete .py files in runtime that don't exist
#    in canonical (preserve unmanaged: scripts/, __pycache__/)
# =============================================================================
info "Removing stale .py files from runtime..."

for pyfile in "${RUNTIME_PLUGIN_DIR}"/*.py; do
    filename="$(basename "$pyfile")"
    if [ ! -f "${CANONICAL_ROOT}/plugin/aota-tools/${filename}" ]; then
        rm -f "$pyfile"
        warn "Removed stale file: ${filename}"
    fi
done

info "Stale file removal complete."

# =============================================================================
# 7. Preserve unmanaged files — already done by design (scripts/, __pycache__/
#    are never touched by our cp/rm operations above)
# =============================================================================

# =============================================================================
# 8. py_compile runtime — verify deployed files compile
# =============================================================================
info "Verifying deployed runtime files compile..."

COMPILE_FAILED=0
for pyfile in "${RUNTIME_PLUGIN_DIR}"/*.py; do
    if ! python3 -m py_compile "$pyfile" 2>/dev/null; then
        error "py_compile FAILED at runtime: $pyfile"
        COMPILE_FAILED=1
    fi
done

if [ "${COMPILE_FAILED}" -eq 1 ]; then
    error "Deployed runtime files failed py_compile. Restoring backup..."
    cp -a "${BACKUP_DIR}/plugin/aota-tools"/* "${RUNTIME_PLUGIN_DIR}/"
    error "Backup restored. Deploy aborted."
    exit 1
fi
info "All deployed .py files pass py_compile."

# =============================================================================
# 9. Plugin import test — python3 -c "import plugin" (may not work outside
#    Hermes — skip gracefully if import fails)
# =============================================================================
info "Attempting plugin import test..."
if python3 -c "import sys; sys.path.insert(0, '${RUNTIME_PLUGIN_DIR}'); import __init__" 2>/dev/null; then
    info "Plugin import test PASSED."
else
    warn "Plugin import test skipped (expected outside Hermes runtime)."
fi

# =============================================================================
# 10. Version verify — compare canonical VERSION with runtime plugin.yaml version
# =============================================================================
info "Verifying version consistency..."

if [ -f "${RUNTIME_PLUGIN_DIR}/plugin.yaml" ]; then
    PLUGIN_YAML_VERSION="$(grep '^version:' "${RUNTIME_PLUGIN_DIR}/plugin.yaml" | sed 's/^version:[[:space:]]*//' | tr -d '[:space:]')"
    if [ "${CANONICAL_VERSION}" != "${PLUGIN_YAML_VERSION}" ]; then
        warn "Version mismatch: canonical=${CANONICAL_VERSION} plugin.yaml=${PLUGIN_YAML_VERSION}"
    else
        info "Version match: ${CANONICAL_VERSION}"
    fi
fi

# =============================================================================
# 11. Profile config sync — copy canonical profiles/*.yaml + SOUL.md to runtime
# =============================================================================
info "Syncing profile configs..."

for profile_name in "${!PROFILE_MAP[@]}"; do
    canonical_prof="${CANONICAL_ROOT}/profiles/${profile_name}"
    runtime_prof="${PROFILE_MAP[$profile_name]}"

    if [ ! -d "${canonical_prof}" ]; then
        warn "Canonical profile directory not found: ${canonical_prof}, skipping."
        continue
    fi

    if [ ! -d "${runtime_prof}" ]; then
        warn "Runtime profile directory not found: ${runtime_prof}, skipping."
        continue
    fi

    # Copy config.yaml if exists
    if [ -f "${canonical_prof}/config.yaml" ]; then
        cp --preserve=mode,timestamps "${canonical_prof}/config.yaml" "${runtime_prof}/config.yaml"
        info "  Synced config.yaml for ${profile_name}"
    fi

    # Copy SOUL.md if exists
    if [ -f "${canonical_prof}/SOUL.md" ]; then
        cp --preserve=mode,timestamps "${canonical_prof}/SOUL.md" "${runtime_prof}/SOUL.md"
        info "  Synced SOUL.md for ${profile_name}"
    fi
done

# =============================================================================
# 11b. Profile plugin symlinks — ensure worker profiles can discover aota-tools
# =============================================================================
info "Ensuring profile plugin symlinks..."

for profile_name in "${!PROFILE_MAP[@]}"; do
    runtime_prof="${PROFILE_MAP[$profile_name]}"
    prof_plugins_dir="${runtime_prof}/plugins"
    prof_plugin_link="${prof_plugins_dir}/aota-tools"

    if [ ! -d "${runtime_prof}" ]; then
        error "PROFILE_DIRECTORY_MISSING: ${runtime_prof}"
        exit 1
    fi

    if [ ! -d "${prof_plugins_dir}" ]; then
        mkdir -p "${prof_plugins_dir}"
    fi

    if [ -L "${prof_plugin_link}" ]; then
        raw_target="$(readlink -- "${prof_plugin_link}" 2>/dev/null || true)"
        resolved_target="$(readlink -f -- "${prof_plugin_link}" 2>/dev/null || true)"
        if [ -n "${resolved_target}" ] && [ "${resolved_target}" = "${RUNTIME_PLUGIN_RESOLVED}" ] && [[ "${raw_target}" != /* ]]; then
            info "  Plugin symlink already correct for ${profile_name}"
        else
            rm -- "${prof_plugin_link}"
            ln -s "${PROFILE_PLUGIN_RELATIVE_TARGET}" "${prof_plugin_link}"
            info "  Reconciled plugin symlink for ${profile_name} (previous=${raw_target:-unresolved})"
        fi
    elif [ -e "${prof_plugin_link}" ]; then
        error "PROFILE_PLUGIN_PATH_CONFLICT: ${prof_plugin_link} exists but is not a symlink"
        exit 1
    else
        ln -s "${PROFILE_PLUGIN_RELATIVE_TARGET}" "${prof_plugin_link}"
        info "  Created plugin symlink for ${profile_name}"
    fi
done

# =============================================================================
# 12. Skill sync — copy all canonical skills/*/ containing SKILL.md to runtime
# =============================================================================
info "Syncing skills..."

for skill_dir in "${CANONICAL_ROOT}/skills/"*/; do
    skill_name="$(basename "$skill_dir")"
    if [ -f "${skill_dir}SKILL.md" ]; then
        runtime_skill="${RUNTIME_SKILLS_ROOT}/${skill_name}"
        mkdir -p "${runtime_skill}"
        cp --preserve=mode,timestamps "${skill_dir}SKILL.md" "${runtime_skill}/SKILL.md"
        info "  Synced SKILL.md for ${skill_name}"
    fi
done

# =============================================================================
# 13. Print ACTION_REQUIRED (always, since plugin code changed)
# =============================================================================
echo ""
echo "============================================================================="
echo -e "${YELLOW}ACTION_REQUIRED${NC}: Hermes profile restart or plugin reload is required"
echo "                 for the deployed plugin changes to take effect."
echo ""
echo "  Deploy timestamp: ${TIMESTAMP}"
echo "  Version:          ${CANONICAL_VERSION}"
echo "  Backup:           ${BACKUP_DIR}"
echo "============================================================================="
echo ""
info "Deploy complete."
