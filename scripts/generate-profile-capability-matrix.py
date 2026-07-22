#!/usr/bin/env python3
"""Deterministic profile-capability-matrix generator (WI-4 PCF Closure).

Reads from 4 source authorities and produces a single read-only JSON matrix.
Never modifies any existing source file.

Source authorities (read-only):
  1. deploy/profile-runtime-assembly.yaml  — active/reference skills per profile
  2. profiles/<profile>/config.yaml        — toolsets, disabled_toolsets, disabled skills
  3. plugin/aota-tools/plugin.yaml         — provides_tools (canonical tool registry)
  4. deploy/aota-lifecycle-inventory.yaml  — tool profile policies, skill ownership

Output:
  deploy/generated/profile-capability-matrix.json

Deterministic: sorted keys, alphabetically sorted arrays, stable ordering.
No non-stdlib dependencies beyond PyYAML.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required. Install with: pip install pyyaml")
    sys.exit(1)

# ── Paths ──────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent

ASSEMBLY_PATH = PROJECT_ROOT / "deploy" / "profile-runtime-assembly.yaml"
INVENTORY_PATH = PROJECT_ROOT / "deploy" / "aota-lifecycle-inventory.yaml"
PLUGIN_PATH = PROJECT_ROOT / "plugin" / "aota-tools" / "plugin.yaml"
PROFILES_DIR = PROJECT_ROOT / "profiles"
OUTPUT_DIR = PROJECT_ROOT / "deploy" / "generated"
OUTPUT_PATH = OUTPUT_DIR / "profile-capability-matrix.json"

PROFILE_NAMES = [
    "task-main",
    "architect",
    "reviewer",
    "coder",
    "debugger",
    "project-steward",
]

HEADER_TEXT = (
    "此檔案由 scripts/generate-profile-capability-matrix.py 自動生成，不可手動編輯。"
    "Source authority 為 deploy/profile-runtime-assembly.yaml, "
    "profiles/<profile>/config.yaml, plugin/aota-tools/plugin.yaml, "
    "deploy/aota-lifecycle-inventory.yaml"
)


# ── Helpers ────────────────────────────────────────────────────────────────

def _sorted_list(items: list[str]) -> list[str]:
    """Return a sorted copy of the list (deterministic)."""
    return sorted(items)


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file, returning {} on missing file (with warning)."""
    if not path.exists():
        print(f"WARNING: {path} not found — treating as empty", file=sys.stderr)
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}


# ── Source 1: profile-runtime-assembly.yaml ───────────────────────────────

def load_assembly() -> dict[str, dict[str, Any]]:
    """Return mapping: profile_name -> {config, soul, plugin, active_skills, reference_skills}."""
    raw = _load_yaml(ASSEMBLY_PATH)
    profiles_raw = raw.get("profiles", {})
    result: dict[str, dict[str, Any]] = {}
    for name in PROFILE_NAMES:
        entry = profiles_raw.get(name, {})
        result[name] = {
            "config_path": entry.get("config", ""),
            "soul_path": entry.get("soul", ""),
            "plugin": entry.get("plugin", ""),
            "active_skills": _sorted_list(entry.get("active_skills", [])),
            "reference_skills": _sorted_list(entry.get("reference_skills", [])),
        }
    return result


# ── Source 2: profiles/<profile>/config.yaml ──────────────────────────────

def load_profile_config(profile_name: str) -> dict[str, Any]:
    """Return {toolsets, disabled_toolsets, disabled_skills} for one profile."""
    config_path = PROFILES_DIR / profile_name / "config.yaml"
    raw = _load_yaml(config_path)
    agent = raw.get("agent", {})
    skills = raw.get("skills", {})

    toolsets: list[str] = raw.get("toolsets", [])
    disabled_toolsets: list[str] = agent.get("disabled_toolsets", [])
    disabled_skills: list[str] = skills.get("disabled", [])

    return {
        "toolsets": _sorted_list(toolsets),
        "disabled_toolsets": _sorted_list(disabled_toolsets),
        "disabled_skills": _sorted_list(disabled_skills),
    }


# ── Source 3: plugin/aota-tools/plugin.yaml ────────────────────────────────

def load_plugin() -> list[str]:
    """Return the canonical provides_tools list (sorted)."""
    raw = _load_yaml(PLUGIN_PATH)
    tools: list[str] = raw.get("provides_tools", [])
    return _sorted_list(tools)


# ── Source 4: deploy/aota-lifecycle-inventory.yaml ────────────────────────

def load_inventory_tools() -> dict[str, dict[str, Any]]:
    """Return mapping: tool_name -> {allowed_profiles, denied_profiles, toolset, ...}."""
    raw = _load_yaml(INVENTORY_PATH)
    tools = raw.get("tools", {})
    return tools if isinstance(tools, dict) else {}


def load_inventory_skills() -> dict[str, dict[str, Any]]:
    """Return mapping: skill_name -> {owning_profiles, source_path, ...}."""
    raw = _load_yaml(INVENTORY_PATH)
    skills = raw.get("skills", {})
    # Filter out comments (YAML comments parsed as None keys)
    result: dict[str, dict[str, Any]] = {}
    for k, v in skills.items():
        if isinstance(v, dict):
            result[k] = v
    return result


# ── Compute derived fields ─────────────────────────────────────────────────

def _profile_has_tool_access(
    profile: str,
    tool_entry: dict[str, Any],
) -> bool:
    """Check if *profile* is allowed to use this tool per lifecycle inventory policy."""
    allowed = tool_entry.get("allowed_profiles")
    denied = tool_entry.get("denied_profiles")

    if allowed is not None:
        if not isinstance(allowed, list):
            allowed = [allowed]
        if profile not in allowed:
            return False

    if denied is not None:
        if not isinstance(denied, list):
            denied = [denied]
        if profile in denied:
            return False

    return True


def compute_tool_fields(
    profile: str,
    inventory_tools: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Return (registered_tools, runtime_visible_toolsets_expected) for a profile.

    Both lists are sorted for determinism.
    """
    registered: list[str] = []
    toolsets: set[str] = set()

    for tool_name, tool_entry in inventory_tools.items():
        if not isinstance(tool_entry, dict):
            continue
        if _profile_has_tool_access(profile, tool_entry):
            registered.append(tool_name)
            ts: str = tool_entry.get("toolset", "")
            if ts:
                toolsets.add(ts)

    return _sorted_list(registered), _sorted_list(list(toolsets))


def compute_skill_fields(
    profile: str,
    inventory_skills: dict[str, dict[str, Any]],
) -> tuple[dict[str, list[str]], list[str]]:
    """Return (inventory_ownership, expected_projection_paths) for a profile.

    inventory_ownership: {skill_name: owning_profiles} for skills this profile owns.
    expected_projection_paths: source_path values (sorted, unique).
    """
    ownership: dict[str, list[str]] = {}
    projection_paths: set[str] = set()

    for skill_name, skill_entry in inventory_skills.items():
        owning: list[str] = skill_entry.get("owning_profiles", [])
        if not isinstance(owning, list):
            owning = [owning] if owning else []
        if profile in owning:
            ownership[skill_name] = _sorted_list(owning)
            sp: str = skill_entry.get("source_path", "")
            if sp:
                projection_paths.add(sp)

    return ownership, _sorted_list(list(projection_paths))


# ── Compute mismatches ────────────────────────────────────────────────────

def compute_mismatches(
    profile: str,
    assembly: dict[str, Any],
    config: dict[str, Any],
    registered_tools: list[str],
    runtime_toolsets: list[str],
    inventory_ownership: dict[str, list[str]],
    plugin_tools: list[str],
) -> list[str]:
    """Return a list of mismatch descriptions (empty = no mismatch)."""
    mismatches: list[str] = []

    declared_ts = set(config["toolsets"])
    runtime_ts = set(runtime_toolsets)

    # 1. Toolsets declared in config but NOT visible per lifecycle inventory
    extra_declared = declared_ts - runtime_ts
    if extra_declared:
        mismatches.append(
            f"toolsets declared in config but NOT in lifecycle inventory tool assignments: "
            f"{_sorted_list(list(extra_declared))}"
        )

    # 2. Toolsets visible per inventory but NOT declared in config
    missing_declared = runtime_ts - declared_ts
    if missing_declared:
        mismatches.append(
            f"toolsets in lifecycle inventory but NOT declared in profile config: "
            f"{_sorted_list(list(missing_declared))}"
        )

    # 3. Skills in assembly (active+reference) but NOT owned per inventory
    assembly_skills = set(assembly["active_skills"]) | set(assembly["reference_skills"])
    inventory_skill_names = set(inventory_ownership.keys())
    assembly_not_in_inventory = assembly_skills - inventory_skill_names
    if assembly_not_in_inventory:
        mismatches.append(
            f"skills in assembly (active+reference) but NOT owned per lifecycle inventory: "
            f"{_sorted_list(list(assembly_not_in_inventory))}"
        )

    # 4. Skills owned per inventory but NOT in assembly (active or reference)
    inventory_not_in_assembly = inventory_skill_names - assembly_skills
    if inventory_not_in_assembly:
        mismatches.append(
            f"skills owned per lifecycle inventory but NOT in assembly active/reference: "
            f"{_sorted_list(list(inventory_not_in_assembly))}"
        )

    # 5. Tool count for this profile vs plugin provides_tools
    # Tool count mismatch is per-profile registered vs plugin canonical
    # (intentionally per-profile — the verifier cross-checks with plugin.yaml)

    # 6. Disabled toolsets vs declared toolsets sanity
    disabled_ts = set(config["disabled_toolsets"])
    overlap = disabled_ts & declared_ts
    if overlap:
        mismatches.append(
            f"toolsets appear in BOTH declared and disabled: "
            f"{_sorted_list(list(overlap))}"
        )

    return mismatches


# ── Main ───────────────────────────────────────────────────────────────────

def generate() -> dict[str, Any]:
    """Build the full capability matrix as a deterministic dict."""
    assembly = load_assembly()
    configs = {p: load_profile_config(p) for p in PROFILE_NAMES}
    plugin_tools = load_plugin()
    inventory_tools = load_inventory_tools()
    inventory_skills = load_inventory_skills()

    profiles_output: list[dict[str, Any]] = []

    for profile_name in PROFILE_NAMES:
        prof_assembly = assembly[profile_name]
        prof_config = configs[profile_name]
        registered_tools, runtime_toolsets = compute_tool_fields(
            profile_name, inventory_tools
        )
        inventory_ownership, projection_paths = compute_skill_fields(
            profile_name, inventory_skills
        )
        mismatches = compute_mismatches(
            profile_name,
            prof_assembly,
            prof_config,
            registered_tools,
            runtime_toolsets,
            inventory_ownership,
            plugin_tools,
        )

        profiles_output.append({
            "profile": profile_name,
            "config_path": prof_assembly["config_path"],
            "soul_path": prof_assembly["soul_path"],
            "plugin": prof_assembly["plugin"],
            "declared_toolsets": prof_config["toolsets"],
            "disabled_toolsets": prof_config["disabled_toolsets"],
            "registered_tools": registered_tools,
            "runtime_visible_toolsets_expected": runtime_toolsets,
            "active_skills": prof_assembly["active_skills"],
            "reference_skills": prof_assembly["reference_skills"],
            "disabled_skills": prof_config["disabled_skills"],
            "expected_projection_paths": projection_paths,
            "inventory_ownership": inventory_ownership,
            "mismatches": mismatches,
        })

    return {
        "_header": HEADER_TEXT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "scripts/generate-profile-capability-matrix.py",
        "source_authorities": [
            "deploy/profile-runtime-assembly.yaml",
            "profiles/<profile>/config.yaml",
            "plugin/aota-tools/plugin.yaml",
            "deploy/aota-lifecycle-inventory.yaml",
        ],
        "canonical_tool_count": len(plugin_tools),
        "profile_count": len(PROFILE_NAMES),
        "profiles": profiles_output,
    }


def main() -> int:
    matrix = generate()

    # Ensure output directory exists
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Write deterministic JSON (sorted_keys=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(matrix, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")

    print(f"Generated: {OUTPUT_PATH}")
    print(f"Profiles: {len(matrix['profiles'])}")
    print(f"Canonical tool count: {matrix['canonical_tool_count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
