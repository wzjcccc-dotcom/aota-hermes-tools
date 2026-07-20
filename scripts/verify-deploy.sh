#!/usr/bin/env bash
set -euo pipefail

# Read-only exact source/runtime hash verification.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
python3 "${ROOT}/scripts/aota_forge_plan_package.py" verify
exec python3 "${ROOT}/scripts/profile_runtime_assembly.py" pre-activation
