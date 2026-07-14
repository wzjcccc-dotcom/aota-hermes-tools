#!/usr/bin/env bash
set -euo pipefail

# Package-only rollback.  It never restarts or recreates Hermes.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ "$#" -gt 0 ]; then
    exec python3 "${ROOT}/scripts/aota_forge_plan_package.py" rollback "$1"
fi
exec python3 "${ROOT}/scripts/aota_forge_plan_package.py" rollback
