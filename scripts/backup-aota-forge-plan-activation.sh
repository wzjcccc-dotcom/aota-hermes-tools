#!/usr/bin/env bash
set -euo pipefail

# Package-only backup.  The Human Checkpoint must run it before activation.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "${ROOT}/scripts/aota_forge_plan_package.py" backup
