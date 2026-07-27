"""Fail-closed, manifest-driven Host symlink projection support.

The adapter only applies to exact profile-local plugin projections declared in
``profile-runtime-assembly.yaml``.  It never turns a general symlink into a
deployable target and it keeps the logical projection visible in plans and
receipts while allowing one physical write owner.
"""

from __future__ import annotations

import os
import hashlib
import unicodedata
from pathlib import Path
from typing import Any


CONTRACT_KEY = "plugin_projections"
PROJECTION_TYPE = "profile_plugin_global_projection"
WRITE_MODE = "canonical_target_only"
REQUIRED_FIELDS = {"type", "profile", "logical_path", "target_path", "write_mode", "verify_logical_path"}
FORBIDDEN_PATTERN_CHARS = frozenset("*?[]{}")


def _relative(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError(f"projection {field} must be a non-empty relative path")
    path = Path(value)
    if "\\" in value or any(part in {"", "."} for part in path.parts):
        raise ValueError(f"projection {field} has ambiguous separators")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"projection {field} has ambiguous Unicode normalization")
    if any(part == ".." for part in path.parts):
        raise ValueError(f"projection {field} has path traversal")
    if any(char in value for char in FORBIDDEN_PATTERN_CHARS):
        raise ValueError(f"projection {field} contains wildcard syntax")
    return path.as_posix()


def load_projection_contract(assembly: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate and return exact projection declarations from the assembly."""
    if assembly.get("projection_contract_schema_version") != 1:
        raise ValueError("projection contract schema version must be 1")
    raw = assembly.get(CONTRACT_KEY, [])
    if not isinstance(raw, list):
        raise ValueError("plugin_projections must be a list")
    profiles = assembly.get("profiles")
    if not isinstance(profiles, dict):
        raise ValueError("assembly profiles must be a mapping")
    seen_logical: set[str] = set()
    result: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or set(item) - REQUIRED_FIELDS:
            unknown = set(item) - REQUIRED_FIELDS if isinstance(item, dict) else {"shape"}
            raise ValueError(f"projection {index} has unknown fields: {sorted(unknown)}")
        if set(item) != REQUIRED_FIELDS:
            raise ValueError(f"projection {index} fields are not exact")
        if item["type"] != PROJECTION_TYPE or item["write_mode"] != WRITE_MODE or item["verify_logical_path"] is not True:
            raise ValueError(f"projection {index} has unsupported contract values")
        profile = item["profile"]
        if not isinstance(profile, str) or profile not in profiles:
            raise ValueError(f"projection {index} has unknown profile")
        logical_path = _relative(item["logical_path"], "logical_path")
        target_path = _relative(item["target_path"], "target_path")
        expected_logical = f"profiles/{profile}/plugins/aota-tools"
        if logical_path != expected_logical:
            raise ValueError(f"projection {index} logical path does not bind to profile")
        if target_path != "plugins/aota-tools":
            raise ValueError(f"projection {index} target path is not canonical global plugin root")
        if logical_path in seen_logical:
            raise ValueError(f"duplicate projection logical path: {logical_path}")
        seen_logical.add(logical_path)
        result.append({
            "projection_id": f"{PROJECTION_TYPE}:{profile}",
            "type": item["type"],
            "profile": profile,
            "logical_path": logical_path,
            "target_path": target_path,
            "write_mode": item["write_mode"],
            "verify_logical_path": True,
        })
    if len({item["target_path"] for item in result}) > 1:
        raise ValueError("projection targets must be canonical and identical")
    return result


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _parent_chain_has_symlink(path: Path, root: Path) -> bool:
    current = path.parent
    root = root.absolute()
    while True:
        if current == root:
            return False
        if current.is_symlink():
            return True
        if not _within(current, root):
            return True
        current = current.parent


def _symlink_depth(path: Path, root: Path) -> tuple[int, bool]:
    """Count link hops without resolving away the original link first."""
    current = path
    seen: set[Path] = set()
    depth = 0
    while current.is_symlink():
        current_abs = current.absolute()
        if current_abs in seen:
            return depth + 1, True
        seen.add(current_abs)
        depth += 1
        raw = os.readlink(current)
        current = (current.parent / raw) if not Path(raw).is_absolute() else Path(raw)
        if not _within(current.absolute(), root.absolute()):
            return depth, False
    return depth, False


def validate_projection(runtime_root: Path, projection: dict[str, Any]) -> dict[str, Any]:
    """Validate one live projection and return sanitized assessment metadata."""
    runtime_root = Path(runtime_root)
    logical = runtime_root / projection["logical_path"]
    expected = runtime_root / projection["target_path"]
    result: dict[str, Any] = {
        "projection_id": projection["projection_id"],
        "profile": projection["profile"],
        "logical_path": str(logical),
        "path_type": "missing",
        "raw_link_target": None,
        "resolved_target": None,
        "expected_target": str(expected),
        "target_exists": False,
        "target_is_directory": False,
        "target_within_runtime_root": False,
        "manifest_declared": True,
        "current_deployer_decision": "SYMLINK_PROJECTION_INVALID",
        "valid": False,
        "reason": None,
    }
    if runtime_root.is_symlink():
        result["reason"] = "runtime_root_is_symlink"
        return result
    if not logical.is_symlink():
        result["path_type"] = "directory" if logical.is_dir() else "file" if logical.is_file() else "missing"
        result["reason"] = "logical_path_is_not_symlink"
        return result
    if _parent_chain_has_symlink(logical, runtime_root.resolve(strict=True)):
        result["reason"] = "logical_parent_contains_symlink"
        return result
    result["path_type"] = "symlink"
    result["raw_link_target"] = os.readlink(logical)
    depth, circular = _symlink_depth(logical, runtime_root)
    if circular:
        result["reason"] = "circular_symlink"
        return result
    if depth != 1:
        result["reason"] = "symlink_chain" if depth > 1 else "symlink_resolution"
        return result
    try:
        resolved = logical.resolve(strict=True)
    except (FileNotFoundError, RuntimeError, OSError):
        result["reason"] = "dangling_or_circular_symlink"
        return result
    result["resolved_target"] = str(resolved)
    result["target_exists"] = resolved.exists()
    result["target_is_directory"] = resolved.is_dir()
    result["target_within_runtime_root"] = _within(resolved, runtime_root.resolve(strict=True))
    if not result["target_exists"]:
        result["reason"] = "dangling_target"
        return result
    if not result["target_is_directory"]:
        result["reason"] = "target_is_not_directory"
        return result
    if not result["target_within_runtime_root"]:
        result["reason"] = "target_outside_runtime_root"
        return result
    if resolved != expected.resolve(strict=True):
        result["reason"] = "target_mismatch"
        return result
    if expected.is_symlink():
        result["reason"] = "target_root_is_symlink"
        return result
    if _parent_chain_has_symlink(expected, runtime_root.resolve(strict=True)):
        result["reason"] = "target_parent_contains_symlink"
        return result
    if not os.path.samefile(logical, expected):
        result["reason"] = "samefile_mismatch"
        return result
    result["current_deployer_decision"] = "SYMLINK_PROJECTION_VALID"
    result["valid"] = True
    result["reason"] = None
    return result


def validate_contract_runtime(runtime_root: Path, assembly: dict[str, Any]) -> list[dict[str, Any]]:
    projections = load_projection_contract(assembly)
    results = [validate_projection(runtime_root, projection) for projection in projections]
    invalid = [item for item in results if not item["valid"]]
    if invalid:
        details = ", ".join(f"{item['profile']}:{item['reason']}" for item in invalid)
        raise ValueError(f"SYMLINK_PROJECTION_INVALID: {details}")
    declared = {item["logical_path"] for item in projections}
    profiles_root = runtime_root / "profiles"
    if profiles_root.is_dir() and not profiles_root.is_symlink():
        for profile_root in profiles_root.iterdir():
            candidate = profile_root / "plugins" / "aota-tools"
            if candidate.is_symlink() and candidate.relative_to(runtime_root).as_posix() not in declared:
                raise ValueError(f"SYMLINK_PROJECTION_INVALID: undeclared:{candidate}")
    return results


def apply_projection_plan(entries: list[dict[str, Any]], assembly: dict[str, Any], runtime_root: Path) -> list[dict[str, Any]]:
    """Map declared logical plugin entries to one physical target per file."""
    projections = load_projection_contract(assembly)
    by_profile = {item["profile"]: item for item in projections}
    by_destination: dict[Path, dict[str, Any]] = {}
    for entry in entries:
        if entry["kind"] != "copy":
            continue
        prior = by_destination.get(entry["destination"])
        if prior is not None and prior["source"].absolute() != entry["source"].absolute():
            raise ValueError(f"physical target collision with different sources: {entry['destination']}")
        by_destination[entry["destination"]] = entry
    for entry in entries:
        profile = entry.get("profile")
        if entry["kind"] != "copy" or profile not in by_profile or "/plugin/" not in entry["id"]:
            continue
        projection = by_profile[profile]
        logical = entry["destination"]
        relative = entry["relative_path"]
        physical = runtime_root / projection["target_path"] / relative
        owner = by_destination.get(physical)
        if owner is None:
            raise ValueError(f"projection physical target has no canonical owner: {physical}")
        entry["logical_runtime_path"] = str(logical)
        entry["projection_id"] = projection["projection_id"]
        entry["resolved_physical_target"] = str(physical)
        entry["write_required"] = False
        entry["write_owner"] = owner["id"]
        entry["deduplicated_from"] = [entry["id"]]
        entry["projection_verification_required"] = True
        owner.setdefault("logical_projections", []).append({
            "profile": profile,
            "logical_path": str(logical),
            "projection_id": projection["projection_id"],
        })
        owner.setdefault("deduplicated_from", []).append(entry["id"])
    for entry in entries:
        entry.setdefault("write_required", entry["kind"] == "copy")
        entry.setdefault("logical_runtime_path", str(entry["destination"]))
        entry.setdefault("resolved_physical_target", str(entry["destination"]))
        entry.setdefault("deduplicated_from", [])
        entry.setdefault("projection_verification_required", False)
        entry.setdefault("logical_projections", [])
        entry.setdefault("write_owner", entry["id"] if entry["write_required"] else None)
    return entries


def projection_plan_summary(entries: list[dict[str, Any]]) -> dict[str, int]:
    logical = [entry for entry in entries if entry["kind"] == "copy"]
    physical = {entry["destination"] for entry in logical if entry.get("write_required")}
    projections = [entry for entry in logical if entry.get("projection_verification_required")]
    return {
        "logical_managed_path_count": len(logical),
        "physical_target_count": len(physical),
        "symlink_projection_count": len({entry.get("projection_id") for entry in projections}),
        "deduplicated_write_count": len([entry for entry in logical if not entry.get("write_required")]),
    }


def _files(root: Path) -> list[Path]:
    result: list[Path] = []
    if not root.is_dir() or root.is_symlink():
        return result
    for child in sorted(root.iterdir()):
        if child.is_dir() and not child.is_symlink():
            result.extend(_files(child))
        elif child.is_file() and not child.is_symlink():
            result.append(child)
    return result


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify_projection_parity(runtime_root: Path, source_root: Path, assembly: dict[str, Any]) -> list[dict[str, Any]]:
    """Classify each declared projection without writing either tree."""
    assessments = validate_contract_runtime(runtime_root, assembly)
    source_files = {path.relative_to(source_root).as_posix(): path for path in _files(source_root)}
    target_root = runtime_root / "plugins" / "aota-tools"
    target_files = {path.relative_to(target_root).as_posix(): path for path in _files(target_root)}
    classification = "PROJECTION_CONTENT_EXACT_MATCH" if source_files.keys() == target_files.keys() and all(_hash(source_files[key]) == _hash(target_files[key]) for key in source_files) else "PROJECTION_CONTENT_MISMATCH"
    return [
        {**assessment, "parity_classification": classification}
        for assessment in assessments
    ]
