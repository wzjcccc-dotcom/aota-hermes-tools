#!/usr/bin/env python3
"""PF-WI-07-2 manifest, readiness, backup, deploy and rollback primitives.

The command surface is deliberately package-oriented.  It never starts a
service, invokes Docker, reads private .env values, or performs Git writes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

import _host_symlink_projection as projection


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "deploy" / "aota-forge-plan-files.yaml"
LIFECYCLE_INVENTORY_PATH = REPO_ROOT / "deploy" / "aota-lifecycle-inventory.yaml"
ASSEMBLY_PATH = REPO_ROOT / "deploy" / "profile-runtime-assembly.yaml"
ORPHAN_EVIDENCE_PATH = REPO_ROOT / "deploy" / "evidence" / "orphaned-profile-skill-runtime-files-20260722.json"
PLUGIN_YAML_PATH = REPO_ROOT / "plugin" / "aota-tools" / "plugin.yaml"
DEFAULT_HERMES_HOME = "/home/latios/.hermes"
DEFAULT_HERMES_STACK = "/home/latios/hermes-stack"
TRUSTED_KEYS = (
    "AOTA_TRUSTED_PRINCIPAL",
    "AOTA_TRUSTED_AUTHORITIES",
    "AOTA_TRUSTED_WORKSPACE_ID",
)
WORKER_PROFILES = ("architect", "reviewer", "coder", "debugger", "project-steward")
PLAN_TOOLSETS = ("aota_work_intake", "aota_plan_read", "aota_plan_write")
ORPHAN_CLEANUP_GROUP = "orphan_profile_skill_cleanup"
ORPHAN_AUTHORIZED_COUNT = 11
ORPHAN_OPERATOR_AUTHORIZATION = "GRANTED_EXACT_11_PATHS"
KNOWN_SECRET_SCAN_FINDINGS = frozenset({
    "scripts/capture-host-migration-docker-baseline.py:871",
})


# -- Canonical count resolver -------------------------------------------------
# Derives deployment receipt counts from canonical repository sources:
#   tools    <- plugin/aota-tools/plugin.yaml provides_tools
#   toolsets <- unique toolset names from the lifecycle inventory tools mapping
#   profiles <- deploy/profile-runtime-assembly.yaml profiles keys
# No hardcoded counts remain; all are derived from canonical sources.

def canonical_counts() -> dict[str, int]:
    """Return {tools, toolsets, profiles} derived from canonical sources."""
    plugin_data = yaml.safe_load(PLUGIN_YAML_PATH.read_text(encoding="utf-8"))
    tools_list = plugin_data.get("provides_tools", [])
    if not isinstance(tools_list, list):
        raise ValueError("plugin.yaml provides_tools must be a list")
    tools_count = len(tools_list)

    inventory_data = yaml.safe_load(LIFECYCLE_INVENTORY_PATH.read_text(encoding="utf-8"))
    inv_tools = inventory_data.get("tools", {})
    if not isinstance(inv_tools, dict):
        raise ValueError("lifecycle inventory tools must be a mapping")
    toolsets_count = len({str(item.get("toolset")) for item in inv_tools.values() if isinstance(item, dict)})

    assembly_data = yaml.safe_load(ASSEMBLY_PATH.read_text(encoding="utf-8"))
    if not isinstance(assembly_data, dict) or assembly_data.get("schema_version") != 1:
        raise ValueError("profile runtime assembly schema_version must be 1")
    runner = assembly_data.get("profile_task_runner")
    if not isinstance(runner, dict) or runner.get("default_runner") != "/home/latios/.local/bin/hermes-host" or runner.get("path_fallback_allowed") is not False or runner.get("shell_execution_allowed") is not False:
        raise ValueError("profile task runner contract is not canonical")
    profiles_count = len(assembly_data.get("profiles", {}))

    return {
        "tools": tools_count,
        "toolsets": toolsets_count,
        "profiles": profiles_count,
    }


# -- Canonical Skill tree expansion (shared with profile_runtime_assembly) ----
# Enumerates SKILL.md plus canonical Skill-local regular files (including
# nested references/**), excludes caches/temporary/editor-backup/runtime-
# generated files, rejects path traversal and symlink escapes.

_EXCLUDE_DIRS = frozenset({
    "__pycache__", ".git", ".cache", "node_modules",
    ".mypy_cache", ".ruff_cache", ".pytest_cache",
})
_EXCLUDE_SUFFIXES = frozenset({".pyc", ".pyo", ".pyd"})
_EXCLUDE_NAMES = frozenset({".DS_Store", "Thumbs.db"})


def _is_excluded_canonical(rel: str) -> bool:
    parts = Path(rel).parts
    if any(part in _EXCLUDE_DIRS for part in parts):
        return True
    name = Path(rel).name
    if name in _EXCLUDE_NAMES:
        return True
    if Path(rel).suffix in _EXCLUDE_SUFFIXES:
        return True
    if name.endswith("~") or name.endswith(".bak") or name.endswith(".swp") or name.endswith(".tmp"):
        return True
    if name.startswith(".#") or (name.startswith("#") and name.endswith("#")):
        return True
    return False


def expand_skill_tree(root: Path, *, include_escapes: bool = False) -> dict[str, Path]:
    """Enumerate canonical Skill-local regular files under *root*.

    Includes SKILL.md and nested references/** regular files.
    Excludes __pycache__, *.pyc, temporary/editor backup files, runtime
    snapshots, receipts, generated caches, and empty directories.
    Symlink escapes and path traversal are rejected (excluded) unless
    *include_escapes* is True, in which case escaping symlinks are included
    so that runtime verification can detect and report them.
    """
    root = root.resolve(strict=False)
    if not root.is_dir():
        return {}
    result: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            continue
        if not path.is_file() and not path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        if ".." in Path(rel).parts:
            continue
        if _is_excluded_canonical(rel):
            continue
        if path.is_symlink():
            resolved = path.resolve(strict=False)
            try:
                resolved.relative_to(root)
            except ValueError:
                if not include_escapes:
                    continue
        result[rel] = path
    return result


def expand(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        defaults = {
            "AOTA_SOURCE_ROOT": str(REPO_ROOT),
            "AOTA_HERMES_HOME_HOST": os.environ.get("AOTA_HERMES_HOME_HOST", DEFAULT_HERMES_HOME),
            "AOTA_HERMES_STACK_ROOT": os.environ.get("AOTA_HERMES_STACK_ROOT", DEFAULT_HERMES_STACK),
            "AOTA_CANONICAL_WORKSPACE_ROOT": os.environ.get("AOTA_CANONICAL_WORKSPACE_ROOT", "/home/latios/workspace"),
        }
        return defaults.get(name, os.environ.get(name, match.group(0)))

    return re.sub(r"\$\{([A-Z0-9_]+)\}", replace, value)


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("managed manifest schema_version must be 1")
    assembly_ref = data.get("profile_runtime_assembly")
    if assembly_ref:
        assembly_path = path.parent.parent / str(assembly_ref)
        assembly = yaml.safe_load(assembly_path.read_text(encoding="utf-8"))
        if not isinstance(assembly, dict) or assembly.get("schema_version") != 1:
            raise ValueError("profile runtime assembly schema_version must be 1")
        if assembly.get("profile_task_runner", {}).get("default_runner") != "/home/latios/.local/bin/hermes-host":
            raise ValueError("profile task runner contract is not canonical")
        projection.load_projection_contract(assembly)
        data["groups"] = dict(data.get("groups", {}))
        data["groups"]["profile_runtime_assembly"] = {
            "kind": "copy",
            "source_root": "${AOTA_SOURCE_ROOT}",
            "runtime_root": "${AOTA_HERMES_HOME_HOST}",
            "files": [],
            "remove_entries": [],
        }
        group = data["groups"]["profile_runtime_assembly"]
        for profile, spec in assembly.get("profiles", {}).items():
            # Runtime profile mapping explicit: logical task-main → runtime aota-task-main
            runtime_profile = spec.get("hermes_runtime_profile") if isinstance(spec, dict) else None
            if profile == "task-main" and runtime_profile != "aota-task-main":
                # Enforce explicit mapping; fallback to alias map
                runtime_profile = "aota-task-main"
            else:
                runtime_profile = runtime_profile or profile
            source_root = "${AOTA_SOURCE_ROOT}/plugin/aota-tools"
            runtime_root = f"${{AOTA_HERMES_HOME_HOST}}/profiles/{runtime_profile}/plugins/aota-tools"
            group["files"].extend([
                {"pattern": "*.py", "destination": "{relative_path}", "file_type": "python", "required": True, "managed": True, "_source_root": source_root, "_runtime_root": runtime_root},
                {"source": "plugin.yaml", "destination": "plugin.yaml", "file_type": "yaml", "required": True, "managed": True, "_source_root": source_root, "_runtime_root": runtime_root},
                {"pattern": "dashboard/**/*", "destination": "{relative_path}", "file_type": "asset", "required": True, "managed": True, "_source_root": source_root, "_runtime_root": runtime_root},
            ])
            for item in group["files"][-3:]:
                item["_id_prefix"] = f"profile_runtime_assembly/{profile}/plugin"
                item["_profile"] = profile
                item["_runtime_profile"] = runtime_profile
            for activation_class, skills in (("active", spec.get("active_skills", [])), ("reference", spec.get("reference_skills", []))):
                for skill in skills:
                    group["files"].append({
                        "pattern": "**/*",
                        "destination": f"profiles/{runtime_profile}/skills/{skill}/{{relative_path}}",
                        "file_type": "markdown",
                        "required": True,
                        "managed": True,
                        "_source_root": f"${{AOTA_SOURCE_ROOT}}/skills/{skill}",
                        "_runtime_root": "${AOTA_HERMES_HOME_HOST}",
                        "_profile": profile,
                        "_runtime_profile": runtime_profile,
                        "_skill": skill,
                        "_activation_class": activation_class,
                        "_operation": "create_or_update",
                        "_id_prefix": f"profile_runtime_assembly/{profile}/{activation_class}-skill/{skill}",
                    })
                    # The old categorized tree is a migration source only.  It
                    # is backed up and removed after the direct projection is
                    # deployed; it is never a new AOTA destination.
                    if profile == "task-main" and activation_class == "reference":
                        legacy_root = absolute_root("${AOTA_HERMES_HOME_HOST}") / "profiles" / runtime_profile / "skills" / "orchestration" / skill
                        if legacy_root.is_dir():
                            for legacy_file in sorted(path for path in legacy_root.rglob("*") if path.is_file() or path.is_symlink()):
                                relative = legacy_file.relative_to(absolute_root("${AOTA_HERMES_HOME_HOST}")).as_posix()
                                group["remove_entries"].append({
                                    "path": relative,
                                    "_profile": profile,
                                    "_skill": skill,
                                    "_activation_class": activation_class,
                                    "_source_path": f"${{AOTA_SOURCE_ROOT}}/skills/{skill}/SKILL.md",
                                    "_operation": "remove",
                                    "_id_prefix": f"profile_runtime_assembly/{profile}/legacy-reference-remove/{skill}",
                                })
    if ORPHAN_EVIDENCE_PATH.is_file():
        evidence = load_orphan_evidence()
        cleanup = {
            "kind": "copy",
            "source_root": "${AOTA_HERMES_HOME_HOST}",
            "runtime_root": "${AOTA_HERMES_HOME_HOST}",
            "files": [],
            "remove_entries": [],
        }
        for item in evidence["files"]:
            relative = safe_relative(str(item["relative_runtime_path"]))
            cleanup["remove_entries"].append({
                "path": relative,
                "file_type": "file",
                "managed": True,
                "_orphan_cleanup": True,
                "_evidence_id": evidence["evidence_id"],
                "_operator_authorization": evidence["operator_authorization"],
                "_original_sha256": item["sha256"],
                "_profile": item["profile"],
                "_skill": item["skill"],
                "_source_path": item["canonical_source_path"],
                "_reason": "operator-authorized orphaned profile-local Skill runtime cleanup",
                "_id_prefix": f"{ORPHAN_CLEANUP_GROUP}/{evidence['evidence_id']}",
            })
        data["groups"] = dict(data.get("groups", {}))
        data["groups"][ORPHAN_CLEANUP_GROUP] = cleanup
    return data


def load_orphan_evidence(path: Path | None = None) -> dict[str, Any]:
    path = path or ORPHAN_EVIDENCE_PATH
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("orphan evidence artifact unreadable") from exc
    if (
        not isinstance(evidence, dict)
        or evidence.get("schema_version") != 1
        or evidence.get("evidence_id") != "orphaned-profile-skill-runtime-files-20260722"
        or evidence.get("project_id") != "aota-hermes-tools"
        or evidence.get("operator_classification") != "ORPHANED_PROFILE_LOCAL_SKILL_RUNTIME_FILES"
        or evidence.get("operator_authorization") != ORPHAN_OPERATOR_AUTHORIZATION
        or evidence.get("authorized_path_count") != ORPHAN_AUTHORIZED_COUNT
        or not isinstance(evidence.get("files"), list)
        or len(evidence["files"]) != ORPHAN_AUTHORIZED_COUNT
    ):
        raise ValueError("orphan evidence artifact authority/count invalid")
    paths = set()
    for item in evidence["files"]:
        if not isinstance(item, dict):
            raise ValueError("orphan evidence entry invalid")
        required = {"absolute_runtime_path", "relative_runtime_path", "profile", "skill", "sha256", "regular_file", "is_symlink", "allowed_root", "canonical_source_exists", "package_plan_declares", "assembly_expected_declares"}
        if not required.issubset(item) or item["relative_runtime_path"] in paths:
            raise ValueError("orphan evidence entry incomplete/duplicated")
        paths.add(item["relative_runtime_path"])
    return evidence


def lifecycle_activation_targets() -> tuple[str, ...]:
    """Return the source-declared services, never a hard-coded activation plan."""
    data = yaml.safe_load(LIFECYCLE_INVENTORY_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("lifecycle inventory schema_version must be 1")
    targets: set[str] = set()
    for item in list(data.get("tools", {}).values()) + list(data.get("skills", {}).values()):
        if isinstance(item, dict):
            targets.update(str(target) for target in item.get("activation_targets", []))
    allowed = {"hermes-agent", "hermes-webui", "new-session"}
    if not targets or not targets.issubset(allowed):
        raise ValueError("lifecycle inventory has invalid activation targets")
    return tuple(sorted(targets))


def lifecycle_verifier_errors() -> list[str]:
    errors: list[str] = []
    for name in ("verify-aota-tool-lifecycle.py", "verify-aota-skill-lifecycle.py"):
        result = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / name)], capture_output=True, text=True)
        if result.returncode:
            errors.append(f"lifecycle:{name}")
    return errors


def absolute_root(value: str) -> Path:
    expanded = Path(expand(value))
    return expanded if expanded.is_absolute() else REPO_ROOT / expanded


def safe_relative(value: str) -> str:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe manifest relative path: {value}")
    return path.as_posix()


def within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def safe_target(path: Path, root: Path) -> None:
    root_resolved = root.resolve(strict=False)
    if path.is_symlink():
        resolved = path.resolve(strict=False)
    else:
        resolved = path.parent.resolve(strict=False) / path.name
    if not within(resolved, root_resolved):
        raise ValueError(f"managed target escapes root: {path}")


def format_destination(spec: str, relative: str) -> str:
    rel = Path(relative)
    return spec.format(relative_path=rel.as_posix(), name=rel.name, stem=rel.stem)


def build_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for group_name, group in manifest.get("groups", {}).items():
        if not isinstance(group, dict):
            raise ValueError(f"manifest group is not a mapping: {group_name}")
        kind = group.get("kind")
        if kind not in {"copy", "source_only"}:
            raise ValueError(f"unsupported manifest group kind: {group_name}")
        source_root = absolute_root(str(group["source_root"]))
        runtime_root = absolute_root(str(group["runtime_root"]))
        for spec in group.get("files", []):
            if not isinstance(spec, dict):
                raise ValueError(f"manifest file entry is not a mapping: {group_name}")
            spec_source_root = absolute_root(str(spec.get("_source_root", source_root)))
            spec_runtime_root = absolute_root(str(spec.get("_runtime_root", runtime_root)))
            destination_spec = str(spec.get("destination", "{relative_path}"))
            if "pattern" in spec:
                pattern = safe_relative(str(spec["pattern"]))
                matches = sorted(path for path in spec_source_root.glob(pattern) if path.is_file() or path.is_symlink())
                if not matches and spec.get("required", False):
                    raise FileNotFoundError(f"required manifest pattern has no files: {group_name}/{pattern}")
                for source in matches:
                    relative = source.relative_to(spec_source_root).as_posix()
                    destination = spec_runtime_root / format_destination(destination_spec, relative)
                    entries.append(_entry(group_name, kind, source, destination, spec_runtime_root, spec_source_root, relative, spec))
            elif "source" in spec:
                relative = safe_relative(str(spec["source"]))
                source = spec_source_root / relative
                destination = spec_runtime_root / format_destination(destination_spec, relative)
                entries.append(_entry(group_name, kind, source, destination, spec_runtime_root, spec_source_root, relative, spec))
            else:
                raise ValueError(f"manifest file entry needs source or pattern: {group_name}")
        for removed in group.get("removed_files", []):
            relative = safe_relative(str(removed))
            destination = runtime_root / relative
            entry = _entry(group_name, "remove", source_root / relative, destination, runtime_root, source_root, relative,
                           {"file_type": "file", "required": False, "expected_mode": "preserve", "managed": True})
            entries.append(entry)
        for removal in group.get("remove_entries", []):
            if not isinstance(removal, dict) or "path" not in removal:
                raise ValueError(f"manifest remove entry needs path: {group_name}")
            relative = safe_relative(str(removal["path"]))
            destination = runtime_root / relative
            entry = _entry(group_name, "remove", source_root / relative, destination, runtime_root, source_root, relative, removal)
            entries.append(entry)
    if not entries:
        raise ValueError("managed manifest has no files")
    if "profile_runtime_assembly" in manifest.get("groups", {}):
        assembly = yaml.safe_load(ASSEMBLY_PATH.read_text(encoding="utf-8"))
        projection.apply_projection_plan(
            entries,
            assembly,
            absolute_root("${AOTA_HERMES_HOME_HOST}"),
        )
    return entries


def _entry(group: str, kind: str, source: Path, destination: Path, root: Path, source_root: Path, relative: str, spec: dict[str, Any]) -> dict[str, Any]:
    safe_relative(relative)
    safe_target(destination, root)
    return {
        "id": f"{spec.get('_id_prefix', group)}/{relative}",
        "group": group,
        "kind": kind,
        "source": source,
        "destination": destination,
        "root": root,
        "source_root": source_root,
        "relative_path": relative,
        "file_type": str(spec.get("file_type", "file")),
        "required": bool(spec.get("required", False)),
        "expected_mode": str(spec.get("expected_mode", "preserve")),
        "managed": bool(spec.get("managed", False)),
        "profile": spec.get("_profile"),
        "skill_name": spec.get("_skill"),
        "activation_class": spec.get("_activation_class"),
        "operation": str(spec.get("_operation", "remove" if kind == "remove" else "create_or_update")),
        "source_path": absolute_root(str(spec["_source_path"])) if spec.get("_source_path") else source,
        "orphan_cleanup": bool(spec.get("_orphan_cleanup", False)),
        "evidence_id": spec.get("_evidence_id"),
        "operator_authorization": spec.get("_operator_authorization"),
        "original_sha256": spec.get("_original_sha256"),
        "reason": spec.get("_reason"),
    }


def sha256(path: Path) -> str:
    if path.is_symlink():
        return hashlib.sha256(f"symlink:{os.readlink(path)}".encode()).hexdigest()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_type(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_file():
        return "file"
    if path.exists():
        return "directory"
    return "missing"


def record_path(path: Path) -> dict[str, Any]:
    kind = file_type(path)
    if kind == "missing":
        return {"file_type": kind, "mode": None, "symlink_target": None, "sha256": None}
    if kind == "directory":
        raise ValueError(f"managed path must be a file, not directory: {path}")
    stat = path.lstat()
    return {
        "file_type": kind,
        "mode": oct(stat.st_mode & 0o777),
        "symlink_target": os.readlink(path) if path.is_symlink() else None,
        "sha256": sha256(path),
    }


def copy_preserving(path: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        if destination.exists() or destination.is_symlink():
            destination.unlink()
        destination.symlink_to(os.readlink(path))
    else:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(prefix=f".{destination.name}.", dir=destination.parent, delete=False) as handle:
                temporary = Path(handle.name)
            shutil.copyfile(path, temporary)
            shutil.copystat(path, temporary, follow_symlinks=False)
            os.replace(temporary, destination)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()


def safe_backup_path(path: Path, root: Path) -> None:
    """Reject backup roots/destinations that could redirect through a link."""
    root = root.absolute()
    path = path.absolute()
    if root.is_symlink() or not within(path, root):
        raise ValueError(f"backup path escapes canonical backup root: {path}")
    ancestor = root.parent
    while ancestor != ancestor.parent:
        if ancestor.is_symlink():
            raise ValueError(f"backup root ancestor is symlink: {ancestor}")
        ancestor = ancestor.parent
    current = path
    while current != root:
        if current.is_symlink():
            raise ValueError(f"backup path contains symlink: {current}")
        current = current.parent
    if path.exists() and path.is_symlink():
        raise ValueError(f"backup destination is symlink: {path}")


def backup_package(entries: list[dict[str, Any]], root: Path, quiet: bool = False) -> Path:
    if root.exists() and root.is_symlink():
        raise ValueError(f"backup root is symlink: {root}")
    root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = root / timestamp
    suffix = 0
    while backup_dir.exists():
        suffix += 1
        backup_dir = root / f"{timestamp}-{suffix:02d}"
    backup_dir.mkdir()
    safe_backup_path(backup_dir, root)
    records: list[dict[str, Any]] = []
    entry_by_id = {entry["id"]: entry for entry in entries}
    for entry in entries:
        if entry["kind"] == "copy" and not entry.get("write_required", True):
            owner = entry_by_id.get(entry.get("write_owner"))
            if owner is None or "_backup_path" not in owner:
                raise ValueError(f"projection backup owner missing: {entry['id']}")
            entry["_backup_path"] = owner["_backup_path"]
            continue
        original = entry["source"] if entry["kind"] == "source_only" else entry["destination"]
        safe_target(original, entry["source_root"] if entry["kind"] == "source_only" else entry["root"])
        relative_backup = Path("files") / entry["id"]
        backup_path = backup_dir / relative_backup
        safe_backup_path(backup_path, backup_dir)
        entry["_backup_path"] = str(relative_backup)
        state = record_path(original)
        existed = state["file_type"] != "missing"
        if existed:
            copy_preserving(original, backup_path)
        records.append({
            "id": entry["id"],
            "group": entry["group"],
            "kind": entry["kind"],
            "original_path": str(original),
            "destination_path": str(entry["destination"]),
            "root_path": str(entry["root"]),
            "source_root_path": str(entry["source_root"]),
            "backup_path": str(relative_backup),
            "existed_before": existed,
            "managed": entry["managed"],
            "file_type": state["file_type"],
            "mode": state["mode"],
            "symlink_target": state["symlink_target"],
            "sha256": state["sha256"],
        })
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "package": "aota-forge-plan",
        "managed_file_inventory": [
            {"id": e["id"], "source": str(e["source"]), "destination": str(e["destination"]), "logical_runtime_path": e.get("logical_runtime_path"), "resolved_physical_target": e.get("resolved_physical_target"), "kind": e["kind"], "file_type": e["file_type"], "managed": e["managed"], "write_required": e.get("write_required", True), "write_owner": e.get("write_owner")}
            for e in entries
        ],
        "files": records,
        "projection_contract": any(e.get("projection_verification_required") for e in entries),
        "projection_runtime_root": str(Path(next((e["resolved_physical_target"] for e in entries if e.get("projection_verification_required")), "")).parents[2]) if any(e.get("projection_verification_required") for e in entries) else None,
    }
    manifest_path = backup_dir / "backup-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checksum_lines = []
    for path in sorted(p for p in (backup_dir / "files").rglob("*") if p.is_file() or p.is_symlink()):
        checksum_lines.append(f"{sha256(path)}  {path.relative_to(backup_dir).as_posix()}")
    checksum_lines.append(f"{sha256(manifest_path)}  backup-manifest.json")
    (backup_dir / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
    if not quiet:
        print(f"BACKUP_CREATED {backup_dir}")
    return backup_dir


def restore_file(record: dict[str, Any], backup_dir: Path) -> None:
    destination = Path(record["original_path"] if record["kind"] == "source_only" else record["destination_path"])
    if not record.get("root_path"):
        raise ValueError(f"rollback record has no managed root: {record['id']}")
    safe_root = record.get("source_root_path") if record["kind"] == "source_only" else record["root_path"]
    safe_target(destination, Path(safe_root))
    backup_path = backup_dir / record["backup_path"]
    safe_backup_path(backup_dir, backup_dir.parent)
    safe_backup_path(backup_path, backup_dir)
    if record["existed_before"]:
        if not backup_path.exists() and not backup_path.is_symlink():
            raise ValueError(f"backup payload missing: {record['id']}")
        if destination.exists() or destination.is_symlink():
            if destination.is_dir() and not destination.is_symlink():
                raise ValueError(f"refusing to replace directory: {destination}")
            destination.unlink()
        copy_preserving(backup_path, destination)
        if record["mode"] and not destination.is_symlink():
            destination.chmod(int(record["mode"], 8))
    elif destination.exists() or destination.is_symlink():
        if destination.is_dir() and not destination.is_symlink():
            raise ValueError(f"refusing to remove directory: {destination}")
        destination.unlink()


def verify_backup_parity(manifest: dict[str, Any], backup_dir: Path) -> None:
    for record in manifest["files"]:
        destination = Path(record["original_path"] if record["kind"] == "source_only" else record["destination_path"])
        if record["existed_before"]:
            if not destination.exists() and not destination.is_symlink():
                raise ValueError(f"rollback parity missing: {record['id']}")
            if file_type(destination) != record["file_type"] or sha256(destination) != record["sha256"]:
                raise ValueError(f"rollback parity mismatch: {record['id']}")
        elif destination.exists() or destination.is_symlink():
                raise ValueError(f"rollback left new managed file: {record['id']}")


def _receipt_entry(entry: dict[str, Any], backup_dir: Path, source_hash: str | None, runtime_hash: str | None) -> dict[str, Any]:
    receipt = {
        "profile": entry.get("profile"),
        "skill_name": entry.get("skill_name"),
        "activation_class": entry.get("activation_class"),
        "source_path": str(entry.get("source_path", entry["source"])),
        "destination_path": str(entry["destination"]),
        "source_hash": source_hash,
        "runtime_hash": runtime_hash,
        "operation": entry.get("operation", "remove" if entry["kind"] == "remove" else "create_or_update"),
        "backup_path": str(backup_dir / "files" / entry.get("_backup_path", entry["id"])),
        "entry_id": entry["id"],
        "logical_runtime_path": entry.get("logical_runtime_path", str(entry["destination"])),
        "resolved_physical_target": entry.get("resolved_physical_target", str(entry["destination"])),
        "projection_id": entry.get("projection_id"),
        "write_required": entry.get("write_required", True),
        "write_owner": entry.get("write_owner"),
        "deduplicated_from": entry.get("deduplicated_from", []),
        "logical_projections": entry.get("logical_projections", []),
        "projection_verification_required": entry.get("projection_verification_required", False),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if entry.get("orphan_cleanup"):
        receipt.update({
            "evidence_id": entry["evidence_id"],
            "operator_authorization": entry["operator_authorization"],
            "original_path": str(entry["destination"]),
            "original_sha256": entry["original_sha256"],
            "reason": entry["reason"],
        })
    return receipt


def _canonical_package_destinations() -> set[Path]:
    """Return package/assembly destinations, excluding generated cleanup entries."""
    return {
        entry["destination"]
        for entry in build_entries(load_manifest())
        if entry["group"] != ORPHAN_CLEANUP_GROUP and entry["kind"] in {"copy", "remove"}
    }


def _canonical_assembly_destinations(runtime_root: Path) -> set[Path]:
    assembly = yaml.safe_load(ASSEMBLY_PATH.read_text(encoding="utf-8"))
    expected: set[Path] = set()
    for profile, spec in assembly.get("profiles", {}).items():
        # Runtime profile mapping explicit: logical task-main → runtime aota-task-main
        runtime_profile = spec.get("hermes_runtime_profile") if isinstance(spec, dict) else None
        if profile == "task-main" and runtime_profile == "aota-task-main":
            runtime_profile = "aota-task-main"
        else:
            runtime_profile = runtime_profile or profile
        for skill in (*spec.get("active_skills", []), *spec.get("reference_skills", [])):
            source_root = REPO_ROOT / "skills" / str(skill)
            target_root = runtime_root / "profiles" / str(runtime_profile) / "skills" / str(skill)
            expected.update(target_root / relative for relative in expand_skill_tree(source_root))
    return expected


def revalidate_orphan_evidence(entries: list[dict[str, Any]], runtime_root: Path | None = None) -> dict[str, Any]:
    """Revalidate exactly the persisted allowlist before any orphan removal."""
    evidence = load_orphan_evidence()
    runtime_root = runtime_root or absolute_root("${AOTA_HERMES_HOME_HOST}")
    cleanup = [entry for entry in entries if entry.get("orphan_cleanup")]
    # Handle runtime profile mapping: evidence still uses logical task-main path,
    # but runtime after repair is aota-task-main. Accept either.
    expected_rel = {str(item["relative_runtime_path"]) for item in evidence["files"]}
    expected_rel_alt = {p.replace("profiles/task-main/", "profiles/aota-task-main/", 1) if p.startswith("profiles/task-main/") else p for p in expected_rel}
    actual_rel = {str(entry["relative_path"]) for entry in cleanup}
    if len(cleanup) != ORPHAN_AUTHORIZED_COUNT or (actual_rel != expected_rel and actual_rel != expected_rel_alt):
        raise ValueError("ORPHAN_EVIDENCE_DRIFT: exact allowlist mismatch")
    package_paths = _canonical_package_destinations()
    assembly_paths = _canonical_assembly_destinations(runtime_root)
    for item in evidence["files"]:
        rel = safe_relative(str(item["relative_runtime_path"]))
        profile = str(item["profile"])
        skill = str(item["skill"])
        runtime_profile = "aota-task-main" if profile == "task-main" else profile
        alt_rel = rel.replace("profiles/task-main/", "profiles/aota-task-main/", 1) if profile == "task-main" else rel
        # Prefer runtime alias path if it exists or original is absent
        path = runtime_root / rel
        allowed_root = runtime_root / "profiles" / profile / "skills" / skill
        alt_path = runtime_root / alt_rel
        alt_allowed_root = runtime_root / "profiles" / runtime_profile / "skills" / skill
        if profile == "task-main" and (alt_path.exists() or alt_path.is_symlink() or (not path.exists() and alt_path.parent.exists())):
            # Use runtime alias for checks (disposable or after repair)
            if alt_path.relative_to(runtime_root).as_posix() != alt_rel:
                raise ValueError(f"ORPHAN_EVIDENCE_DRIFT: path binding {rel}")
            path = alt_path
            allowed_root = alt_allowed_root
            rel = alt_rel
        else:
            # Check relative binding (disposable-safe; absolute not required)
            if path.relative_to(runtime_root).as_posix() != rel:
                raise ValueError(f"ORPHAN_EVIDENCE_DRIFT: path binding {rel}")
            # For production, also verify absolute matches when runtime_root is production
            if runtime_root.resolve() == absolute_root("${AOTA_HERMES_HOME_HOST}").resolve():
                # Production: allow both original and alias absolute, but check relative already
                # Only enforce absolute if file exists at original location
                if path.exists() or Path(item["absolute_runtime_path"]).exists():
                    if Path(item["absolute_runtime_path"]) != path and Path(item["absolute_runtime_path"]).as_posix().replace("profiles/task-main/", "profiles/aota-task-main/") != path.as_posix():
                        # For alias, absolute will differ; accept alt absolute if it matches alt_path
                        if alt_path.exists() and Path(item["absolute_runtime_path"]).as_posix().replace("profiles/task-main/", "profiles/aota-task-main/") != alt_path.as_posix():
                            raise ValueError(f"ORPHAN_EVIDENCE_DRIFT: path binding {rel}")
        if not path.exists() and not path.is_symlink():
            # An exact orphan allowlist member may already have been removed
            # by a prior approved run.  It is safe to keep it absent; any
            # dangling link, directory, or changed regular file remains a
            # fail-closed evidence drift below.
            continue
        if not path.exists() or path.is_symlink() or not path.is_file():
            raise ValueError(f"ORPHAN_EVIDENCE_DRIFT: file safety {rel}")
        safe_target(path, allowed_root)
        if not within(path.resolve(strict=True), allowed_root.resolve(strict=True)):
            raise ValueError(f"ORPHAN_EVIDENCE_DRIFT: root boundary {rel}")
        source = Path(str(item["canonical_source_path"]))
        if source.exists() or source.is_symlink() or item["canonical_source_exists"]:
            raise ValueError(f"ORPHAN_EVIDENCE_DRIFT: canonical source reappeared {rel}")
        if path in package_paths or item["package_plan_declares"]:
            raise ValueError(f"ORPHAN_EVIDENCE_DRIFT: package declaration reappeared {rel}")
        if path in assembly_paths or item["assembly_expected_declares"]:
            raise ValueError(f"ORPHAN_EVIDENCE_DRIFT: assembly declaration reappeared {rel}")
        if sha256(path) != item["sha256"] or sha256(path) != item.get("captured_sha256", item["sha256"]):
            raise ValueError(f"ORPHAN_EVIDENCE_DRIFT: hash changed {rel}")
    return evidence


def receipt_payload(backup_dir: Path, copied: list[str], source_hashes: dict[str, str], runtime_hashes: dict[str, str], entries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    counts = canonical_counts()
    receipt = {
        "schema_version": 1,
        "source_version": "0.17.6",
        "tools": counts["tools"],
        "toolsets": counts["toolsets"],
        "profiles": counts["profiles"],
        "source_root": str(REPO_ROOT),
        "runtime_root": expand("${AOTA_HERMES_HOME_HOST}"),
        "files": copied,
        "source_hashes": source_hashes,
        "runtime_hashes": runtime_hashes,
        "managed_entries": entries or [],
        "compose_changed": False,
        "restart_executed": False,
        "live_smoke_executed": False,
        "backup_path": str(backup_dir),
    }
    external_compose_backup = os.environ.get("AOTA_EXTERNAL_COMPOSE_BACKUP", "").strip()
    if external_compose_backup:
        receipt["external_compose_backup"] = external_compose_backup
    return receipt


def rollback_package(backup_dir: Path, quiet: bool = False) -> None:
    manifest_path = backup_dir / "backup-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assembly = None
    if manifest.get("projection_contract"):
        assembly = yaml.safe_load(ASSEMBLY_PATH.read_text(encoding="utf-8"))
        projection.validate_contract_runtime(Path(manifest["projection_runtime_root"]), assembly)
    for record in manifest["files"]:
        if not record.get("managed"):
            raise ValueError(f"unmanaged rollback record: {record['id']}")
        restore_file(record, backup_dir)
    verify_backup_parity(manifest, backup_dir)
    if assembly is not None:
        projection.validate_contract_runtime(Path(manifest["projection_runtime_root"]), assembly)
    if not quiet:
        print(f"ROLLBACK_VERIFIED {backup_dir}")


def source_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def readiness(
    manifest_path: Path = MANIFEST_PATH,
    quiet: bool = False,
    allow_known_secret_false_positive: bool = False,
) -> int:
    errors: list[str] = []
    try:
        manifest = load_manifest(manifest_path)
        entries = build_entries(manifest)
        lifecycle_activation_targets()
    except Exception as exc:  # noqa: BLE001 - readiness must report one stable verdict
        print("READINESS_BLOCKED")
        print(f"manifest: {type(exc).__name__}")
        return 1

    version_path = REPO_ROOT / "VERSION"
    plugin_yaml = REPO_ROOT / "plugin" / "aota-tools" / "plugin.yaml"
    if version_path.read_text(encoding="utf-8").strip() != "0.17.6":
        errors.append("VERSION")
    try:
        plugin_data = yaml.safe_load(source_text(plugin_yaml))
    except Exception:
        plugin_data = None
        errors.append("plugin.yaml.parse")
    if not isinstance(plugin_data, dict) or str(plugin_data.get("version")) != "0.17.6":
        errors.append("plugin.yaml.version")
    required_tools = {"aota_project_steward_report", "aota_project_docs_update", "aota_project_artifact_link", "aota_project_file_read", "aota_project_file_write", "aota_project_file_patch", "aota_project_command_run"}
    if not isinstance(plugin_data, dict) or not required_tools.issubset(set(plugin_data.get("provides_tools", []))):
        errors.append("steward-tools")
    init_text = source_text(REPO_ROOT / "plugin" / "aota-tools" / "__init__.py")
    if "TOOLSET_PROJECT_STEWARD_ARTIFACT" not in init_text or "TOOLSET_PROJECT_STEWARD" not in init_text:
        errors.append("steward-toolsets")
    if "TOOLSET_CODER_FILE_MUTATION" not in init_text or "TOOLSET_CODER_COMMAND" not in init_text:
        errors.append("coder-bounded-toolsets")
    errors.extend(lifecycle_verifier_errors())
    assembly_check = subprocess.run(
        [sys.executable, "-B", str(REPO_ROOT / "scripts" / "profile_runtime_assembly.py"), "source"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if assembly_check.returncode:
        errors.append("profile-runtime-assembly")

    for entry in entries:
        if entry["required"] and not entry["source"].is_file() and entry["group"] != "compose":
            errors.append(f"missing:{entry['id']}")
        if entry["kind"] == "copy":
            try:
                safe_target(entry["destination"], entry["root"])
            except ValueError:
                errors.append(f"target:{entry['id']}")
    for entry in entries:
        if not entry["source"].is_file():
            continue
        try:
            if entry["file_type"] == "python":
                compile(source_text(entry["source"]), str(entry["source"]), "exec")
            elif entry["file_type"] == "json":
                json.loads(source_text(entry["source"]))
            elif entry["file_type"] in {"yaml", "compose"}:
                yaml.safe_load(source_text(entry["source"]))
            elif entry["file_type"] == "shell":
                result = subprocess.run(["bash", "-n", str(entry["source"])], capture_output=True, text=True)
                if result.returncode:
                    errors.append(f"shell:{entry['id']}")
        except Exception:
            errors.append(f"parse:{entry['id']}")

    start = REPO_ROOT / "plugin" / "aota-tools" / "_profile_task_start.py"
    start_text = source_text(start)
    if sum(start_text.count(key) for key in TRUSTED_KEYS) < 6:
        errors.append("trusted-keys")
    if "for key in _TRUSTED_ORCHESTRATOR_ENV_KEYS:" not in start_text or "child_env.pop(key, None)" not in start_text:
        errors.append("worker-sanitizer")
    if "child_env = dict(parent_env)" not in start_text:
        errors.append("parent-env-copy")
    # W3 Thin-Host: legacy WORKER_PROFILES (coder etc.) remain deny-checked,
    # but task-main and shared aota-worker now use thin-host (all native disabled via "all").
    # For thin-host profiles, "all" covers every toolset, so explicit PLAN_TOOLSETS subset check is satisfied via "all".
    for profile in WORKER_PROFILES:
        try:
            config = yaml.safe_load(source_text(REPO_ROOT / "profiles" / profile / "config.yaml"))
            disabled = set(config.get("agent", {}).get("disabled_toolsets", []))
            # Thin-host shortcut: "all" or "*" in disabled means every toolset is disabled
            if "all" in disabled or "*" in disabled:
                continue
            if not set(PLAN_TOOLSETS).issubset(disabled):
                errors.append(f"worker-deny:{profile}")
        except Exception:
            errors.append(f"worker-profile:{profile}")
    try:
        coder = yaml.safe_load(source_text(REPO_ROOT / "profiles" / "coder" / "config.yaml"))
        coder_tools = set(coder.get("toolsets", []))
        coder_disabled = set(coder.get("agent", {}).get("disabled_toolsets", []))
        # Thin-host coder still has file/terminal disabled via "all" if migrated; treat "all" as covering
        if not ({"all", "*" } & coder_disabled):
            if not {"aota_coder_file_mutation", "aota_coder_command"}.issubset(coder_tools) or not {"file", "terminal"}.issubset(coder_disabled):
                errors.append("coder-terminal-boundary")
    except Exception:
        errors.append("coder-terminal-boundary")
    try:
        task_main = yaml.safe_load(source_text(REPO_ROOT / "profiles" / "task-main" / "config.yaml"))
        # W3 Thin-Host: task-main is now thin host (model+session+executor+MCP only).
        # Legacy PLAN_TOOLSETS (aota_work_intake etc.) are no longer task-main tool surface;
        # they are disabled via "all" and not required to be in available. Task-main thin-host
        # is valid when agent.disabled_toolsets contains "all" (or "*") and mcp_servers.aota.enabled=true
        # and HERMES_IS_THIN_HOST semantics apply.
        task_disabled = set(task_main.get("agent", {}).get("disabled_toolsets", []))
        task_mcp = task_main.get("mcp_servers", {}).get("aota", {}).get("enabled") if isinstance(task_main.get("mcp_servers"), dict) else None
        if {"all", "*"} & task_disabled and task_mcp is True:
            pass  # thin-host valid
        else:
            available = set(task_main.get("toolsets", []))
            if not set(PLAN_TOOLSETS).issubset(available):
                errors.append("task-main-retain")
    except Exception:
        errors.append("task-main")
    security_text = source_text(REPO_ROOT / "plugin" / "aota-tools" / "_orchestrator_security_context.py")
    mutation_text = source_text(REPO_ROOT / "plugin" / "aota-tools" / "_plan_mutation.py")
    if "PLAN_WRITE_FORBIDDEN_FOR_WORKER" not in security_text + mutation_text:
        errors.append("worker-marker-defense")

    deploy_text = source_text(REPO_ROOT / "scripts" / "deploy.sh")
    if "aota-forge-plan-files.yaml" not in deploy_text or "aota_forge_plan_package.py" not in deploy_text:
        errors.append("deploy-manifest")
    for required_script in ("deploy.sh", "backup-aota-forge-plan-activation.sh", "rollback-aota-forge-plan-activation.sh"):
        if not (REPO_ROOT / "scripts" / required_script).is_file():
            errors.append(f"script:{required_script}")

    compose_path = Path(expand("${AOTA_HERMES_STACK_ROOT}")) / "docker-compose.yml"
    if compose_path.is_file():
        try:
            compose = yaml.safe_load(source_text(compose_path))
            services = compose.get("services", {})
            agent_env = services.get("hermes-agent", {}).get("environment", {})
            webui_env = services.get("hermes-webui", {}).get("environment", {})
            for key in TRUSTED_KEYS:
                if key not in agent_env:
                    errors.append(f"compose-agent:{key}")
                if key in webui_env:
                    errors.append(f"compose-webui:{key}")
            if not isinstance(agent_env, dict) or not isinstance(webui_env, dict):
                errors.append("compose-environment-shape")
        except Exception:
            errors.append("compose.parse")
    else:
        print("EXTERNAL_PACKAGE_READINESS=NOT_REASSESSED")

    secret_errors = secret_scan_errors()
    if allow_known_secret_false_positive and set(secret_errors) == KNOWN_SECRET_SCAN_FINDINGS:
        print("SECRET_SCAN=PASS_WITH_KNOWN_FALSE_POSITIVE")
        print("KNOWN_FALSE_POSITIVE_REUSED=scripts/capture-host-migration-docker-baseline.py:871")
    else:
        errors.extend(secret_errors)
    status = "READINESS_BLOCKED" if errors else "READINESS_PASS"
    if not errors:
        try:
            dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.strip())
        except subprocess.SubprocessError:
            dirty = True
        if dirty:
            status = "READINESS_PASS_WITH_UNCOMMITTED_CHANGES"
    print(status)
    if not errors:
        print("AOTA_READINESS_LIFECYCLE_GATE_PASS")
    if errors and not quiet:
        for error in sorted(set(errors)):
            print(error)
    return 1 if errors else 0


def secret_scan_errors() -> list[str]:
    errors: list[str] = []
    roots = [REPO_ROOT / "deploy", REPO_ROOT / "scripts", REPO_ROOT / "docs" / "aota-forge-plan", REPO_ROOT / "README.md"]
    paths: list[Path] = []
    for root in roots:
        if root.is_file():
            paths.append(root)
        elif root.exists():
            paths.extend(p for p in root.rglob("*") if p.is_file())
    patterns = [
        re.compile(r"sk-[A-Za-z0-9]{16,}"),
        re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}", re.IGNORECASE),
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        re.compile(r"\b(?:OPENAI_API_KEY|HERMES_API_SERVER_KEY|DATABASE_URL|PASSWORD)\s*=\s*(?!$|#|\$\{|replace-with|your[-_]|<)[^\s]+"),
        re.compile(r"\bAOTA_TRUSTED_(?:PRINCIPAL|AUTHORITIES|WORKSPACE_ID)\s*=\s*(?!$|#|\$\{|replace-with|absent|<)[^\s]+"),
    ]
    for path in paths:
        if (
            path.name == ".env"
            or path.suffix in {".pyc", ".pyo", ".pyd"}
            or "__pycache__" in path.parts
            or ".deploy-backups" in path.parts
            or ".deploy-receipts" in path.parts
        ):
            continue
        try:
            text = source_text(path)
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            if any(pattern.search(line) for pattern in patterns):
                errors.append(f"{path.relative_to(REPO_ROOT)}:{line_no}")
    return errors


def deploy_package(manifest_path: Path = MANIFEST_PATH, *, allow_known_secret_false_positive: bool = False) -> int:
    if readiness(manifest_path, quiet=True, allow_known_secret_false_positive=allow_known_secret_false_positive):
        return 1
    entries = build_entries(load_manifest(manifest_path))
    runtime_root = absolute_root("${AOTA_HERMES_HOME_HOST}")
    assembly = yaml.safe_load(ASSEMBLY_PATH.read_text(encoding="utf-8"))
    projection.validate_contract_runtime(runtime_root, assembly)
    # The allowlist is checked before any managed mutation and again after the
    # canonical projection is copied.  It is never expanded from a new scan.
    revalidate_orphan_evidence(entries, runtime_root)
    projection.validate_contract_runtime(runtime_root, assembly)
    backup_dir = backup_package(entries, REPO_ROOT / ".deploy-backups", quiet=True)
    source_hashes: dict[str, str] = {}
    runtime_hashes: dict[str, str] = {}
    copied: list[str] = []
    receipt_entries: list[dict[str, Any]] = []
    try:
        # Deploy all canonical files first so source/runtime parity can be
        # checked before the exact orphan transaction is attempted.
        for entry in entries:
            if entry["kind"] != "copy" or not entry.get("write_required", True):
                continue
            if not entry["source"].is_file():
                raise ValueError(f"source disappeared during deploy: {entry['id']}")
            safe_target(entry["destination"], entry["root"])
            if entry["destination"].is_symlink():
                raise ValueError(f"refusing symlink deployment target: {entry['id']}")
            projection.validate_contract_runtime(runtime_root, assembly)
            copy_preserving(entry["source"], entry["destination"])
            projection.validate_contract_runtime(runtime_root, assembly)
            source_hashes[entry["id"]] = sha256(entry["source"])
            runtime_hashes[entry["id"]] = sha256(entry["destination"])
            copied.append(entry["id"])
            receipt_entries.append(_receipt_entry(entry, backup_dir, source_hashes[entry["id"]], runtime_hashes[entry["id"]]))
        for entry in entries:
            if entry["kind"] != "copy" or entry.get("write_required", True):
                continue
            if not entry["source"].is_file() or not entry["destination"].is_file():
                raise ValueError(f"projection target disappeared during deploy: {entry['id']}")
            source_hashes[entry["id"]] = sha256(entry["source"])
            runtime_hashes[entry["id"]] = sha256(entry["destination"])
            copied.append(entry["id"])
            receipt_entries.append(_receipt_entry(entry, backup_dir, source_hashes[entry["id"]], runtime_hashes[entry["id"]]))
        projection.validate_contract_runtime(runtime_root, assembly)
        copy_mismatches = [
            entry["id"] for entry in entries
            if entry["kind"] == "copy"
            and entry.get("write_required", True)
            and (not entry["source"].is_file() or not entry["destination"].is_file()
                 or sha256(entry["source"]) != sha256(entry["destination"]))
        ]
        if copy_mismatches:
            raise ValueError("canonical source/runtime parity failed before orphan cleanup")
        revalidate_orphan_evidence(entries, runtime_root)
        projection.validate_contract_runtime(runtime_root, assembly)
        # Remove entries are processed only after every exact allowlist member
        # has passed the same fail-closed validation in one transaction.
        for entry in entries:
            if entry["kind"] != "remove":
                continue
            safe_target(entry["destination"], entry["root"])
            if entry["destination"].exists() or entry["destination"].is_symlink():
                if entry["destination"].is_dir() and not entry["destination"].is_symlink():
                    raise ValueError(f"refusing to remove managed directory: {entry['id']}")
                entry["destination"].unlink()
            copied.append(entry["id"])
            receipt_entries.append(_receipt_entry(entry, backup_dir, None, None))
        for entry in entries:
            if entry["kind"] == "remove" and (entry["destination"].exists() or entry["destination"].is_symlink()):
                raise ValueError(f"exact cleanup absence failed: {entry['id']}")
        for directory in sorted(
            {entry["destination"].parent for entry in entries if entry["kind"] == "remove" and "/skills/orchestration/" in str(entry["destination"])},
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            while directory.name != "skills" and directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
                directory = directory.parent
        projection.validate_contract_runtime(runtime_root, assembly)
        assembly_check = subprocess.run(
            [sys.executable, "-B", str(REPO_ROOT / "scripts" / "profile_runtime_assembly.py"), "pre-activation", str(runtime_root)],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        if assembly_check.returncode:
            raise ValueError("PROFILE_ASSEMBLY_PRE_ACTIVATION_FAILED")
        receipt_dir = REPO_ROOT / ".deploy-receipts" / "aota-forge-plan"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        receipt_dir = receipt_dir / timestamp
        receipt_dir.mkdir(parents=True, exist_ok=False)
        receipt = receipt_payload(backup_dir, copied, source_hashes, runtime_hashes, receipt_entries)
        (receipt_dir / "deployment.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        rollback_package(backup_dir, quiet=True)
        raise
    targets = lifecycle_activation_targets()
    print("ACTION_REQUIRED")
    print("Human activation targets from lifecycle inventory: " + ", ".join(targets))
    print(f"backup: {backup_dir}")
    print(f"receipt: {receipt_dir / 'deployment.json'}")
    return 0


def verify_deployment(manifest_path: Path = MANIFEST_PATH) -> int:
    entries = build_entries(load_manifest(manifest_path))
    assembly = yaml.safe_load(ASSEMBLY_PATH.read_text(encoding="utf-8"))
    try:
        projection.validate_contract_runtime(absolute_root("${AOTA_HERMES_HOME_HOST}"), assembly)
    except ValueError as exc:
        print("DEPLOY_VERIFY_BLOCKED")
        print(str(exc))
        return 1
    mismatches = []
    for entry in entries:
        if entry["kind"] == "remove":
            if entry["destination"].exists() or entry["destination"].is_symlink():
                mismatches.append(entry["id"])
            continue
        if entry["kind"] != "copy":
            continue
        if entry.get("write_required", True) and (not entry["source"].is_file() or not entry["destination"].is_file() or sha256(entry["source"]) != sha256(entry["destination"])):
            mismatches.append(entry["id"])
    if mismatches:
        print("DEPLOY_VERIFY_BLOCKED")
        for item in mismatches:
            print(item)
        return 1
    print("DEPLOY_VERIFY_PASS")
    return 0


def fixture() -> int:
    if lifecycle_activation_targets() != ("hermes-agent", "hermes-webui", "new-session"):
        raise AssertionError("lifecycle activation matrix does not require both importing processes and a new session")
    with tempfile.TemporaryDirectory(prefix="aota-forge-plan-fixture-") as temp:
        root = Path(temp)
        source = root / "source"
        runtime = root / "runtime"
        (source / "plugin").mkdir(parents=True)
        (source / "scripts").mkdir(parents=True)
        (runtime / "plugin").mkdir(parents=True)
        (source / "plugin" / "new.py").write_text("new\n", encoding="utf-8")
        (source / "plugin" / "new2.py").write_text("new2\n", encoding="utf-8")
        (source / "scripts" / "deploy.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (runtime / "plugin" / "new.py").write_text("old\n", encoding="utf-8")
        (runtime / "plugin" / "old.py").write_text("stale\n", encoding="utf-8")
        manifest = {
            "schema_version": 1,
            "groups": {
                "plugin": {"kind": "copy", "source_root": str(source / "plugin"), "runtime_root": str(runtime / "plugin"), "files": [{"pattern": "*.py", "destination": "{relative_path}", "file_type": "python", "managed": True}], "removed_files": ["old.py"]},
                "scripts": {"kind": "source_only", "source_root": str(source / "scripts"), "runtime_root": str(source / "scripts"), "files": [{"source": "deploy.sh", "destination": "deploy.sh", "file_type": "shell", "managed": True}]},
            },
        }
        entries = build_entries(manifest)
        backup_dir = backup_package(entries, root / "backups", quiet=True)
        (runtime / "plugin" / "new2.py").write_text("created-after-backup\n", encoding="utf-8")
        for entry in entries:
            if entry["kind"] == "copy":
                copy_preserving(entry["source"], entry["destination"])
            elif entry["kind"] == "remove" and entry["destination"].exists():
                entry["destination"].unlink()
        assert not (runtime / "plugin" / "old.py").exists()
        rollback_package(backup_dir, quiet=True)
        assert (runtime / "plugin" / "old.py").read_text(encoding="utf-8") == "stale\n"
        assert (runtime / "plugin" / "new.py").read_text(encoding="utf-8") == "old\n"
        assert not (runtime / "plugin" / "new2.py").exists()
        assert (backup_dir / "checksums.sha256").is_file()
        receipt_path = root / "deployment.json"
        receipt = receipt_payload(backup_dir, ["plugin/new.py"], {"plugin/new.py": sha256(source / "plugin" / "new.py")}, {"plugin/new.py": sha256(runtime / "plugin" / "new.py")})
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        loaded_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        assert loaded_receipt["schema_version"] == 1
        assert not any(key in loaded_receipt for key in ("env", "api_key", "raw_logs"))
        try:
            bad = dict(manifest)
            bad["groups"] = {"bad": {"kind": "copy", "source_root": str(source), "runtime_root": str(runtime), "files": [{"source": "../escape", "destination": "../escape", "managed": True}]}}
            build_entries(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("path traversal fixture was accepted")
        outside = root / "outside"
        outside.write_text("outside\n", encoding="utf-8")
        link = runtime / "plugin" / "escape.py"
        link.symlink_to(outside)
        try:
            safe_target(link, runtime / "plugin")
        except ValueError:
            pass
        else:
            raise AssertionError("symlink escape fixture was accepted")
        bad_backup = root / "bad-backup"
        shutil.copytree(backup_dir, bad_backup)
        bad_manifest_path = bad_backup / "backup-manifest.json"
        bad_manifest = json.loads(bad_manifest_path.read_text(encoding="utf-8"))
        bad_manifest["files"][0]["destination_path"] = str(outside)
        bad_manifest_path.write_text(json.dumps(bad_manifest), encoding="utf-8")
        try:
            rollback_package(bad_backup, quiet=True)
        except ValueError:
            pass
        else:
            raise AssertionError("rollback manifest-root escape was accepted")
    # -- Receipt count parity fixtures ----------------------------------------
    # Verify canonical count resolver derives 61 tools / 28 toolsets / 6 profiles
    # from canonical sources, and detects the old 59 baseline.
    counts = canonical_counts()
    assert counts["tools"] == 61, f"Expected 61 tools, got {counts['tools']}"
    assert counts["toolsets"] == 28, f"Expected 28 toolsets, got {counts['toolsets']}"
    assert counts["profiles"] == 6, f"Expected 6 profiles, got {counts['profiles']}"
    assert counts["tools"] != 59, "Must detect old 59 baseline"
    # Verify receipt_payload uses the canonical counts, not hardcoded values
    assert receipt["tools"] == 61, f"Receipt tools must be 61, got {receipt['tools']}"
    assert receipt["toolsets"] == 28, f"Receipt toolsets must be 28, got {receipt['toolsets']}"
    assert receipt["profiles"] == 6, f"Receipt profiles must be 6, got {receipt['profiles']}"
    print("RECEIPT_COUNT_PARITY_PASS")

    # Verify expand_skill_tree enumerates SKILL.md + nested references
    # and excludes __pycache__, *.pyc, editor backup files
    with tempfile.TemporaryDirectory(prefix="aota-skill-tree-expand-") as st_temp:
        st_root = Path(st_temp) / "skill"
        (st_root / "references" / "deep").mkdir(parents=True)
        (st_root / "SKILL.md").write_text("test\n", encoding="utf-8")
        (st_root / "references" / "ref.md").write_text("ref\n", encoding="utf-8")
        (st_root / "references" / "deep" / "nested.md").write_text("deep\n", encoding="utf-8")
        (st_root / "__pycache__").mkdir()
        (st_root / "__pycache__" / "cache.pyc").write_text("cache\n", encoding="utf-8")
        (st_root / "backup.md~").write_text("backup\n", encoding="utf-8")
        st_tree = expand_skill_tree(st_root)
        assert "SKILL.md" in st_tree
        assert "references/ref.md" in st_tree
        assert "references/deep/nested.md" in st_tree
        assert not any(r.startswith("__pycache__/") for r in st_tree)
        assert "backup.md~" not in st_tree
        print("SKILL_TREE_EXPANSION_PASS")

    print("AOTA_PACKAGE_ACTIVATION_MATRIX_PASS")
    print("FIXTURE_PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("readiness", "backup", "deploy", "rollback", "verify", "fixture"))
    parser.add_argument("path", nargs="?", help="backup directory for rollback")
    parser.add_argument(
        "--allow-known-secret-false-positive",
        action="store_true",
        help="continue deploy only when the exact approved test-fixture finding is the sole scan result",
    )
    args = parser.parse_args()
    try:
        if args.command == "readiness":
            return readiness(allow_known_secret_false_positive=args.allow_known_secret_false_positive)
        if args.command == "backup":
            entries = build_entries(load_manifest())
            backup_package(entries, REPO_ROOT / ".deploy-backups")
            return 0
        if args.command == "deploy":
            return deploy_package(allow_known_secret_false_positive=args.allow_known_secret_false_positive)
        if args.command == "verify":
            return verify_deployment()
        if args.command == "fixture":
            return fixture()
        backup_root = REPO_ROOT / ".deploy-backups"
        backup_dir = Path(args.path) if args.path else max(backup_root.iterdir(), key=lambda p: p.name)
        rollback_package(backup_dir)
        print("ACTION_REQUIRED")
        print("Human activation targets from lifecycle inventory: " + ", ".join(lifecycle_activation_targets()))
        return 0
    except Exception as exc:  # noqa: BLE001 - shell package gets stable non-secret failure
        print(f"PACKAGE_BLOCKED: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
