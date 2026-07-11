#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# verify-deploy.sh — AOTA Profile Task Control Plane deploy verification
#
# Read-only verification checks for a deployed canonical source.
# NO file modifications.
# =============================================================================

CANONICAL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_PLUGIN_DIR="/home/hermeswebui/.hermes/plugins/aota-tools"

# ---------- Colors for output ----------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info()   { echo -e "${GREEN}[INFO]${NC}  $*"; }
pass()   { echo -e "${GREEN}[PASS]${NC}  $*"; }
warn()   { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()   { echo -e "${RED}[FAIL]${NC}  $*"; FAILED=1; }
header() { echo ""; echo "===== $* ====="; }

FAILED=0

# =============================================================================
# 1. Check canonical VERSION
# =============================================================================
header "Canonical VERSION"

if [ ! -f "${CANONICAL_ROOT}/VERSION" ]; then
    fail "VERSION file not found at ${CANONICAL_ROOT}/VERSION"
else
    CANONICAL_VERSION="$(cat "${CANONICAL_ROOT}/VERSION" | tr -d '[:space:]')"
    info "Canonical VERSION: ${CANONICAL_VERSION}"
    if [ -z "${CANONICAL_VERSION}" ]; then
        fail "VERSION is empty"
    else
        pass "Canonical VERSION check OK"
    fi
fi

# =============================================================================
# 2. Check runtime plugin version (from plugin.yaml)
# =============================================================================
header "Runtime Plugin Version"

if [ ! -f "${RUNTIME_PLUGIN_DIR}/plugin.yaml" ]; then
    fail "Runtime plugin.yaml not found at ${RUNTIME_PLUGIN_DIR}/plugin.yaml"
else
    RUNTIME_VERSION="$(grep '^version:' "${RUNTIME_PLUGIN_DIR}/plugin.yaml" | sed 's/^version:[[:space:]]*//' | tr -d '[:space:]')"
    if [ -z "${RUNTIME_VERSION}" ]; then
        fail "Could not extract version from runtime plugin.yaml"
    else
        info "Runtime plugin version: ${RUNTIME_VERSION}"
        pass "Runtime plugin version check OK"
    fi
fi

# =============================================================================
# 3. File manifest comparison
# =============================================================================
header "File Manifest Comparison"

CANONICAL_FILES=$(mktemp)
RUNTIME_FILES=$(mktemp)
trap 'rm -f ${CANONICAL_FILES} ${RUNTIME_FILES}' EXIT

# List canonical files (only .py and plugin.yaml)
ls -1 "${CANONICAL_ROOT}/plugin/aota-tools" | grep -E '\.py$|^plugin\.yaml$' | sort > "${CANONICAL_FILES}"
# List runtime files (only .py and plugin.yaml, not __pycache__, scripts, workspaces.json)
ls -1 "${RUNTIME_PLUGIN_DIR}" | grep -E '\.py$|^plugin\.yaml$' | sort > "${RUNTIME_FILES}"

CANONICAL_COUNT=$(wc -l < "${CANONICAL_FILES}")
RUNTIME_COUNT=$(wc -l < "${RUNTIME_FILES}")

info "Canonical files: ${CANONICAL_COUNT}"
info "Runtime files:   ${RUNTIME_COUNT}"

if [ "${CANONICAL_COUNT}" -eq "${RUNTIME_COUNT}" ]; then
    pass "File count matches: ${CANONICAL_COUNT}"
else
    fail "File count mismatch: canonical=${CANONICAL_COUNT} runtime=${RUNTIME_COUNT}"
fi

# Diff the file lists
DIFF_OUTPUT=$(diff "${CANONICAL_FILES}" "${RUNTIME_FILES}" || true)
if [ -z "${DIFF_OUTPUT}" ]; then
    pass "File manifest matches exactly"
else
    fail "File manifest differs:"
    echo "${DIFF_OUTPUT}"
fi

# =============================================================================
# 4. sha256 exact match for each .py file + plugin.yaml
# =============================================================================
header "SHA256 Content Verification"

SHA_MISMATCH=0
while IFS= read -r filename; do
    [ -z "${filename}" ] && continue
    CANONICAL_FILE="${CANONICAL_ROOT}/plugin/aota-tools/${filename}"
    RUNTIME_FILE="${RUNTIME_PLUGIN_DIR}/${filename}"

    if [ ! -f "${RUNTIME_FILE}" ]; then
        fail "Runtime file missing: ${filename}"
        SHA_MISMATCH=1
        continue
    fi

    CANONICAL_SHA=$(sha256sum "${CANONICAL_FILE}" | awk '{print $1}')
    RUNTIME_SHA=$(sha256sum "${RUNTIME_FILE}" | awk '{print $1}')

    if [ "${CANONICAL_SHA}" = "${RUNTIME_SHA}" ]; then
        pass "SHA256 match: ${filename}"
    else
        fail "SHA256 MISMATCH: ${filename}"
        info "  Canonical: ${CANONICAL_SHA}"
        info "  Runtime:   ${RUNTIME_SHA}"
        SHA_MISMATCH=1
    fi
done < "${CANONICAL_FILES}"

if [ "${SHA_MISMATCH}" -eq 0 ]; then
    pass "All files SHA256 match"
fi

# =============================================================================
# 5. Plugin import test (py_compile at minimum)
# =============================================================================
header "Plugin Compilation Test"

COMPILE_FAILED=0
for pyfile in "${RUNTIME_PLUGIN_DIR}"/*.py; do
    if ! python3 -m py_compile "$pyfile" 2>/dev/null; then
        fail "py_compile FAILED: $(basename "$pyfile")"
        COMPILE_FAILED=1
    fi
done

if [ "${COMPILE_FAILED}" -eq 0 ]; then
    pass "All runtime .py files pass py_compile"
fi

# =============================================================================
# 6. Tool count (count provides_tools in plugin.yaml)
# =============================================================================
header "Tool Count"

if [ -f "${CANONICAL_ROOT}/plugin/aota-tools/plugin.yaml" ]; then
    CANONICAL_TOOL_COUNT=$(grep -c '^  - aota_' "${CANONICAL_ROOT}/plugin/aota-tools/plugin.yaml" || true)
    info "Canonical tool count (provides_tools): ${CANONICAL_TOOL_COUNT}"
fi

if [ -f "${RUNTIME_PLUGIN_DIR}/plugin.yaml" ]; then
    RUNTIME_TOOL_COUNT=$(grep -c '^  - aota_' "${RUNTIME_PLUGIN_DIR}/plugin.yaml" || true)
    info "Runtime tool count (provides_tools):  ${RUNTIME_TOOL_COUNT}"

    if [ -n "${CANONICAL_TOOL_COUNT:-}" ] && [ "${CANONICAL_TOOL_COUNT}" -eq "${RUNTIME_TOOL_COUNT}" ]; then
        pass "Tool count matches: ${RUNTIME_TOOL_COUNT}"
    else
        fail "Tool count mismatch"
    fi
fi

# =============================================================================
# 7. Toolset count (count unique toolsets in __init__.py)
# =============================================================================
header "Toolset Count"

if [ -f "${CANONICAL_ROOT}/plugin/aota-tools/__init__.py" ]; then
    TOOLSET_COUNT=$(grep -c '^TOOLSET_' "${CANONICAL_ROOT}/plugin/aota-tools/__init__.py" || true)
    info "Unique TOOLSET_ constants in __init__.py: ${TOOLSET_COUNT}"
    pass "Toolset definitions found: ${TOOLSET_COUNT}"
fi

# =============================================================================
# 8. Profile-local aota-tools symlink check
# =============================================================================
header "Profile Symlink Check"

for profile in task-main coder debugger reviewer; do
    SYMLINK_PATH="/home/hermeswebui/.hermes/profiles/${profile}/plugins/aota-tools"
    if [ -L "${SYMLINK_PATH}" ]; then
        TARGET=$(readlink "${SYMLINK_PATH}")
        if [ "${TARGET}" = "${RUNTIME_PLUGIN_DIR}" ]; then
            pass "${profile}/plugins/aota-tools -> ${TARGET}"
        else
            warn "${profile}/plugins/aota-tools points to ${TARGET} (expected ${RUNTIME_PLUGIN_DIR})"
        fi
    elif [ -e "${SYMLINK_PATH}" ]; then
        warn "${profile}/plugins/aota-tools exists but is not a symlink"
    else
        warn "${profile}/plugins/aota-tools does not exist"
    fi
done

# =============================================================================
# 9. task-main toolsets check
# =============================================================================
header "task-main Toolsets"

if [ -f "${CANONICAL_ROOT}/profiles/task-main/config.yaml" ]; then
    TASK_MAIN_TOOLSETS=$(grep '^  - aota_' "${CANONICAL_ROOT}/profiles/task-main/config.yaml" || true)
    TASK_MAIN_COUNT=$(echo "${TASK_MAIN_TOOLSETS}" | grep -c 'aota_' || true)
    info "task-main config.yaml defines ${TASK_MAIN_COUNT} toolsets"
    echo "${TASK_MAIN_TOOLSETS}"
fi

# Also check runtime
if [ -f "/home/hermeswebui/.hermes/profiles/task-main/config.yaml" ]; then
    RT_TASK_MAIN_COUNT=$(grep -c '^  - aota_' "/home/hermeswebui/.hermes/profiles/task-main/config.yaml" || true)
    info "Runtime task-main: ${RT_TASK_MAIN_COUNT} toolsets"
fi

# =============================================================================
# 10. coder/debugger/reviewer isolation check
# =============================================================================
header "Worker Profile Isolation Check"

check_profile_isolation() {
    local profile=$1
    local runtime_cfg="/home/hermeswebui/.hermes/profiles/${profile}/config.yaml"

    if [ ! -f "${runtime_cfg}" ]; then
        warn "Runtime config not found for ${profile}"
        return
    fi

    if grep -q 'aota_profile_task' "${runtime_cfg}" 2>/dev/null; then
        fail "${profile} has aota_profile_task (should be isolated!)"
    else
        pass "${profile}: aota_profile_task NOT present (correctly isolated)"
    fi

    if grep -q 'aota_task_spec' "${runtime_cfg}" 2>/dev/null; then
        fail "${profile} has aota_task_spec (should be isolated!)"
    else
        pass "${profile}: aota_task_spec NOT present (correctly isolated)"
    fi
}

check_profile_isolation "coder"
check_profile_isolation "debugger"
check_profile_isolation "reviewer"

# =============================================================================
# 11. Runtime freshness hint (compare mtimes)
# =============================================================================
header "Runtime Freshness Hint"

if [ -f "${CANONICAL_ROOT}/plugin/aota-tools/__init__.py" ] && [ -f "${RUNTIME_PLUGIN_DIR}/__init__.py" ]; then
    CANONICAL_MTIME=$(stat -c %Y "${CANONICAL_ROOT}/plugin/aota-tools/__init__.py")
    RUNTIME_MTIME=$(stat -c %Y "${RUNTIME_PLUGIN_DIR}/__init__.py")

    if [ "${CANONICAL_MTIME}" -le "${RUNTIME_MTIME}" ]; then
        pass "Runtime is fresh (canonical mtime <= runtime mtime)"
    else
        warn "Canonical source is newer than runtime — deploy may be stale"
        info "  Canonical __init__.py mtime: $(date -d @${CANONICAL_MTIME} '+%Y-%m-%d %H:%M:%S')"
        info "  Runtime   __init__.py mtime: $(date -d @${RUNTIME_MTIME} '+%Y-%m-%d %H:%M:%S')"
    fi
fi

# =============================================================================
# Summary
# =============================================================================
echo ""
echo "============================================================================="
if [ "${FAILED}" -eq 0 ]; then
    echo -e "${GREEN}All verification checks passed.${NC}"
else
    echo -e "${RED}Some verification checks FAILED.${NC}"
fi
echo "============================================================================="

exit "${FAILED}"
