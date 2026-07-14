#!/usr/bin/env bash
set -euo pipefail

# Read-only exact source/runtime hash verification.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "${ROOT}/scripts/aota_forge_plan_package.py" verify
