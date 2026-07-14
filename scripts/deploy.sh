#!/usr/bin/env bash
set -euo pipefail

# PF-WI-07-2 source/package deployment.  It reads deploy/aota-forge-plan-files.yaml
# through aota_forge_plan_package.py and never restarts or recreates Hermes.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "${ROOT}/scripts/aota_forge_plan_package.py" deploy
