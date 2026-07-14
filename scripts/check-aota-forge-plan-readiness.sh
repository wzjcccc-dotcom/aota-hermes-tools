#!/usr/bin/env bash
set -euo pipefail

# Read-only source/package readiness check.  It does not write pyc, receipts,
# backups, runtime files, containers, or service state.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "${ROOT}/scripts/aota_forge_plan_package.py" readiness
