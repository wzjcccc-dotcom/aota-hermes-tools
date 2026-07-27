#!/usr/bin/env python3
"""Independent, read-only static review for HOST-WI-02B1."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

import _host_symlink_projection as adapter
from profile_runtime_assembly import source_errors


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    adapter_text = (ROOT / "scripts/_host_symlink_projection.py").read_text(encoding="utf-8")
    package_text = (ROOT / "scripts/aota_forge_plan_package.py").read_text(encoding="utf-8")
    assembly = yaml.safe_load((ROOT / "deploy/profile-runtime-assembly.yaml").read_text(encoding="utf-8"))
    contract = adapter.load_projection_contract(assembly)
    checks = {
        "exact_allowlist": len(contract) == 5 and {item["profile"] for item in contract} == {"task-main", "architect", "coder", "debugger", "reviewer"},
        "exact_target": all(item["target_path"] == "plugins/aota-tools" for item in contract),
        "strict_containment": all(token in adapter_text for token in ("resolve(strict=True)", "relative_to", "samefile")),
        "chain_rejection": "depth != 1" in adapter_text and "circular_symlink" in adapter_text,
        "physical_dedup": all(token in package_text for token in ("write_required", "write_owner", "deduplicated_from", "logical_projections")),
        "backup_rollback_revalidation": package_text.count("validate_contract_runtime") >= 6,
        "no_adapter_wildcard": "glob(" not in adapter_text and "rglob(" not in adapter_text,
        "no_adapter_prefix_check": "startswith(" not in adapter_text,
        "no_generic_gc": "generic_gc" not in adapter_text.lower(),
        "source_validation": not source_errors(),
    }
    fixture = subprocess.run([sys.executable, "-B", "scripts/verify-host-symlink-projection.py"], cwd=ROOT, capture_output=True, text=True)
    checks["fixture_pass"] = fixture.returncode == 0 and "HOST_SYMLINK_PROJECTION_FIXTURE_PASS" in fixture.stdout
    verdict = "PASS" if all(checks.values()) else "FAIL"
    payload = {"schema_version": 1, "reviewer": "standalone-independent-read-only-review", "verdict": verdict, "blocking_finding_count": 0 if verdict == "PASS" else sum(not value for value in checks.values()), "non_blocking_finding_count": 0, "checks": checks}
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
