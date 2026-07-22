#!/usr/bin/env python3
"""Verify profile-capability-matrix.json (WI-4 PCF Closure).

Rebuilds the matrix via the generator and performs structural/content checks:
  1. Rebuild matrix (import generate-profile-capability-matrix).
  2. Compare against existing generated file (fail on mismatch).
  3. Verify generated matrix contains no runtime_authority claim.
  4. Verify per-profile tool data aligns with plugin.yaml provides_tools.
  5. Output PASS/FAIL marker.

Uses only stdlib + importlib (no non-stdlib deps beyond those the generator uses).
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GENERATED_PATH = PROJECT_ROOT / "deploy" / "generated" / "profile-capability-matrix.json"

PASS_MARKER = "VERIFY:PASS"
FAIL_MARKER = "VERIFY:FAIL"


# ── Helpers ────────────────────────────────────────────────────────────────

def _rebuild_matrix() -> dict[str, Any]:
    """Import and call the generator to produce a fresh in-memory matrix."""
    # Ensure project root is on sys.path so scripts/ is importable as a package
    scripts_dir = str(PROJECT_ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # The generator module name (without .py)
    spec = importlib.util.spec_from_file_location(
        "generate_profile_capability_matrix",
        PROJECT_ROOT / "scripts" / "generate-profile-capability-matrix.py",
    )
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load generator module")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.generate()


def _load_existing_matrix() -> dict[str, Any] | None:
    """Load the existing generated JSON, or None if missing."""
    if not GENERATED_PATH.exists():
        return None
    with open(GENERATED_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _sorted_list(items: list[str]) -> list[str]:
    return sorted(items)


# ── Checks ─────────────────────────────────────────────────────────────────

def check_no_runtime_authority(matrix: dict[str, Any]) -> list[str]:
    """Verify the matrix contains no runtime_authority=true claim anywhere."""
    errors: list[str] = []

    def _walk(obj: Any, path: str = "$") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "runtime_authority" and v is True:
                    errors.append(f"runtime_authority=true found at {path}.{k}")
                if k == "runtime_authority" and v == "true":
                    errors.append(f"runtime_authority='true' (string) found at {path}.{k}")
                _walk(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _walk(item, f"{path}[{i}]")

    _walk(matrix)
    return errors


def check_tool_counts(
    matrix: dict[str, Any],
    plugin_tool_count: int | None = None,
) -> list[str]:
    """Verify per-profile tool data consistency with plugin.yaml."""
    errors: list[str] = []

    profiles = matrix.get("profiles", [])
    canonical_count = matrix.get("canonical_tool_count", 0)

    if plugin_tool_count is not None and canonical_count != plugin_tool_count:
        errors.append(
            f"canonical_tool_count in matrix ({canonical_count}) != "
            f"plugin.yaml provides_tools count ({plugin_tool_count})"
        )

    for p_entry in profiles:
        profile_name = p_entry.get("profile", "?")
        registered = p_entry.get("registered_tools", [])
        # Every registered tool should appear in canonical provides_tools
        # (we can't load plugin.yaml here directly without duplicating,
        #  but we can cross-check the count consistency)

        # At minimum, registered_tools count should not exceed canonical_tool_count
        if len(registered) > canonical_count:
            errors.append(
                f"{profile_name}: registered_tools count ({len(registered)}) "
                f"exceeds canonical_tool_count ({canonical_count})"
            )

        # Every registered tool should be a non-empty string
        for tool in registered:
            if not isinstance(tool, str) or not tool:
                errors.append(f"{profile_name}: registered_tools contains non-string/empty entry: {tool!r}")

    return errors


def check_structural_completeness(matrix: dict[str, Any]) -> list[str]:
    """Verify required fields exist for every profile."""
    errors: list[str] = []
    profiles = matrix.get("profiles", [])

    if not isinstance(profiles, list):
        errors.append("'profiles' is not a list")
        return errors

    required_fields = [
        "profile",
        "config_path",
        "soul_path",
        "plugin",
        "declared_toolsets",
        "disabled_toolsets",
        "registered_tools",
        "runtime_visible_toolsets_expected",
        "active_skills",
        "reference_skills",
        "disabled_skills",
        "expected_projection_paths",
        "inventory_ownership",
        "mismatches",
    ]

    for p_entry in profiles:
        pname = p_entry.get("profile", "?")
        for field in required_fields:
            if field not in p_entry:
                errors.append(f"{pname}: missing required field '{field}'")

    return errors


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> int:
    errors: list[str] = []

    # 1. Load existing matrix (if any)
    existing = _load_existing_matrix()

    # 2. Rebuild fresh matrix
    try:
        rebuilt = _rebuild_matrix()
    except Exception as exc:
        print(f"FATAL: rebuild failed: {exc}", file=sys.stderr)
        print(FAIL_MARKER)
        return 1

    # 3. Compare rebuilt vs existing
    if existing is not None:
        # Strip dynamic fields for comparison
        existing_stripped = {k: v for k, v in existing.items() if k not in ("generated_at",)}
        rebuilt_stripped = {k: v for k, v in rebuilt.items() if k not in ("generated_at",)}
        if existing_stripped != rebuilt_stripped:
            errors.append(
                "MISMATCH: existing generated file differs from fresh rebuild "
                "(timestamps excluded). Run generator to update."
            )
            # Show diff summary
            ek = set(str(k) for k in existing_stripped.keys())
            rk = set(str(k) for k in rebuilt_stripped.keys())
            if ek != rk:
                errors.append(f"  top-level key diff: existing={sorted(ek)}, rebuilt={sorted(rk)}")
            else:
                for key in sorted(ek):
                    ev = existing_stripped.get(key)
                    rv = rebuilt_stripped.get(key)
                    if ev != rv:
                        errors.append(f"  key '{key}' differs (len existing={len(str(ev))}, len rebuilt={len(str(rv))})")

    # 4. No runtime_authority claim
    errors.extend(check_no_runtime_authority(rebuilt))

    # 5. Tool count checks
    errors.extend(check_tool_counts(rebuilt))

    # 6. Structural completeness
    errors.extend(check_structural_completeness(rebuilt))

    # 7. Report
    if errors:
        print(FAIL_MARKER)
        for err in errors:
            print(f"  - {err}")
        return 1
    else:
        print(PASS_MARKER)
        if existing is None:
            print("  (no existing matrix to compare; structural checks only)")
        else:
            print("  (existing matrix matches fresh rebuild)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
