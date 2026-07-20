"""PCF-WI-01 project contract, observed state, and compact card helpers."""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path
from typing import Any

import yaml

SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 256 * 1024
MAX_SUMMARY = 4000
MAX_QUERY = 256
MAX_RESULTS = 50
MAX_CARD_BYTES = 64 * 1024
PROJECT_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
STATUS_VALUES = {"active", "maintenance", "planned", "archived"}
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".codegraph", ".pytest_cache", ".deploy-receipts", ".deploy-backups", "fixtures", "runtime", "deployment"}
# These are retained runtime projections, not canonical project sources.  Keep
# this path-specific so ordinary deploy content remains eligible for walking.
PROJECT_SCAN_EXCLUDED_RELATIVE_ROOTS = (Path("deploy/runtime-projects"),)
TOP_LEVEL = {"schema_version", "project", "summary", "capabilities", "paths", "commands", "runtime", "codegraph", "plan", "constraints"}
PROJECT_FIELDS = {"id", "name", "kind", "status"}
PATH_FIELDS = {"source_root", "source", "docs", "scripts", "profiles", "skills", "tests"}
COMMAND_FIELDS = {"validate", "deploy", "verify_deploy"}

class ProjectError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail[:300]
        super().__init__(self.code)

class _StrictLoader(yaml.SafeLoader):
    pass


def is_project_scan_excluded(workspace_root: Path, candidate: Path) -> bool:
    """Return whether a candidate path is under a non-canonical projection."""
    try:
        relative = candidate.relative_to(workspace_root)
    except ValueError:
        return True
    return any(
        relative == excluded_root or excluded_root in relative.parents
        for excluded_root in PROJECT_SCAN_EXCLUDED_RELATIVE_ROOTS
    )

def _strict_mapping(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise ProjectError("manifest_invalid", "mapping keys must be strings")
        if key in mapping:
            raise ProjectError("manifest_invalid", f"duplicate key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping

_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _strict_mapping)

def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProjectError("manifest_invalid", f"{name} must be an object")
    return value

def _fields(value: dict[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ProjectError("manifest_invalid", f"unknown {name} field: {unknown[0]}")

def _required(value: dict[str, Any], names: set[str], name: str) -> None:
    missing = sorted(names - set(value))
    if missing:
        raise ProjectError("manifest_invalid", f"missing {name} field: {missing[0]}")

def _string(value: Any, name: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ProjectError("manifest_invalid", f"{name} must be a bounded string")
    return value

def _relative(value: Any, name: str) -> str:
    value = _string(value, name, 512)
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value or "\x00" in value:
        raise ProjectError("path_escape", f"{name} must be a safe relative path")
    return path.as_posix()

def _list(value: Any, name: str, item_kind: type = str, maximum: int = 64) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum or not all(isinstance(item, item_kind) for item in value):
        raise ProjectError("manifest_invalid", f"{name} must be a bounded list")
    return value

def load_yaml(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ProjectError("manifest_invalid", "manifest is unsafe or too large")
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=_StrictLoader)
    except ProjectError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ProjectError("manifest_invalid", type(exc).__name__) from exc
    return _mapping(data, "manifest")

def validate_project(data: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    _fields(data, TOP_LEVEL, "top-level")
    _required(data, TOP_LEVEL, "manifest")
    if data["schema_version"] != SCHEMA_VERSION:
        raise ProjectError("manifest_invalid", "schema_version must be 1")
    project = _mapping(data["project"], "project")
    _fields(project, PROJECT_FIELDS, "project")
    _required(project, PROJECT_FIELDS, "project")
    project_id = _string(project["id"], "project.id", 96)
    if not PROJECT_ID_RE.fullmatch(project_id):
        raise ProjectError("manifest_invalid", "project.id must be kebab-case")
    _string(project["name"], "project.name", 200)
    kind = _string(project["kind"], "project.kind", 96)
    if not PROJECT_ID_RE.fullmatch(kind):
        raise ProjectError("manifest_invalid", "project.kind must be kebab-case")
    if project["status"] not in STATUS_VALUES:
        raise ProjectError("manifest_invalid", "project.status is invalid")
    _string(data["summary"], "summary", MAX_SUMMARY)
    caps = _list(data["capabilities"], "capabilities", str, 32)
    if any(not PROJECT_ID_RE.fullmatch(item) or len(item) > 96 for item in caps):
        raise ProjectError("manifest_invalid", "capabilities must be kebab-case")
    paths = _mapping(data["paths"], "paths")
    _fields(paths, PATH_FIELDS, "paths")
    _required(paths, PATH_FIELDS, "paths")
    for key, value in paths.items():
        if key == "source_root":
            _relative(value, f"paths.{key}")
        else:
            for item in _list(value, f"paths.{key}", str, 64):
                _relative(item, f"paths.{key}")
    commands = _mapping(data["commands"], "commands")
    _fields(commands, COMMAND_FIELDS, "commands")
    _required(commands, COMMAND_FIELDS, "commands")
    for key, value in commands.items():
        for item in _list(value, f"commands.{key}", str, 16):
            _relative(item, f"commands.{key}")
    runtime = _mapping(data["runtime"], "runtime")
    _fields(runtime, {"deployment_type", "requires_human_checkpoint"}, "runtime")
    _required(runtime, {"deployment_type", "requires_human_checkpoint"}, "runtime")
    _string(runtime["deployment_type"], "runtime.deployment_type", 96)
    if not isinstance(runtime["requires_human_checkpoint"], bool):
        raise ProjectError("manifest_invalid", "runtime.requires_human_checkpoint must be boolean")
    codegraph = _mapping(data["codegraph"], "codegraph")
    _fields(codegraph, {"enabled", "index_location"}, "codegraph")
    _required(codegraph, {"enabled", "index_location"}, "codegraph")
    if not isinstance(codegraph["enabled"], bool):
        raise ProjectError("manifest_invalid", "codegraph.enabled must be boolean")
    _relative(codegraph["index_location"], "codegraph.index_location")
    plan = _mapping(data["plan"], "plan")
    _fields(plan, {"active_plan_id"}, "plan")
    _required(plan, {"active_plan_id"}, "plan")
    if plan["active_plan_id"] is not None and (not isinstance(plan["active_plan_id"], str) or not re.fullmatch(r"plan_[a-z0-9]+(?:-[a-z0-9]+)*", plan["active_plan_id"])):
        raise ProjectError("manifest_invalid", "plan.active_plan_id is invalid")
    _list(data["constraints"], "constraints", str, 32)
    if root is not None:
        _validate_paths_exist(data, root)
    return data

def _validate_paths_exist(data: dict[str, Any], root: Path) -> None:
    for key, value in data["paths"].items():
        values = [value] if key == "source_root" else value
        for item in values:
            candidate = root / item
            try:
                resolved = candidate.resolve(strict=False)
                resolved.relative_to(root.resolve())
            except ValueError as exc:
                raise ProjectError("path_escape", f"paths.{key} escapes project root") from exc

def load_project(manifest: Path) -> tuple[Path, dict[str, Any]]:
    if manifest.parent.is_symlink() or manifest.parent.parent.is_symlink():
        raise ProjectError("path_escape", "project root or .aota directory is a symlink")
    root = manifest.parent.parent
    try:
        data = validate_project(load_yaml(manifest), root)
    except ProjectError:
        raise
    return root, data

def load_observed(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ProjectError("observed_invalid", "observed.json is unsafe or too large")
        data = json.loads(path.read_text(encoding="utf-8"))
    except ProjectError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectError("observed_invalid", type(exc).__name__) from exc
    if not isinstance(data, dict) or set(data) != {"schema_version", "project_id", "observed_at", "filesystem", "git", "codegraph", "plan"}:
        raise ProjectError("observed_invalid", "observed.json fields are invalid")
    if data["schema_version"] != 1 or not isinstance(data["project_id"], str) or not PROJECT_ID_RE.fullmatch(data["project_id"]):
        raise ProjectError("observed_invalid", "observed identity is invalid")
    try:
        _dt.datetime.fromisoformat(str(data["observed_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProjectError("observed_invalid", "observed_at must be RFC3339") from exc
    fs = _mapping(data["filesystem"], "filesystem")
    if set(fs) != {"root_exists", "manifest_exists"} or not all(isinstance(fs[k], bool) for k in fs):
        raise ProjectError("observed_invalid", "filesystem observation is invalid")
    git = _mapping(data["git"], "git")
    if set(git) != {"available", "branch", "head", "dirty"} or not isinstance(git["available"], bool) or not all(isinstance(git[k], str) for k in ("branch", "head")) or not isinstance(git["dirty"], bool):
        raise ProjectError("observed_invalid", "git observation is invalid")
    cg = _mapping(data["codegraph"], "codegraph")
    if set(cg) != {"configured", "index_present", "runtime_verified"} or not all(isinstance(cg[k], bool) for k in cg):
        raise ProjectError("observed_invalid", "codegraph observation is invalid")
    plan = _mapping(data["plan"], "plan")
    if set(plan) != {"active_plan_id", "revision", "sha256"} or (plan["active_plan_id"] is not None and not isinstance(plan["active_plan_id"], str)) or (plan["revision"] is not None and not isinstance(plan["revision"], int)) or (plan["sha256"] is not None and not isinstance(plan["sha256"], str)):
        raise ProjectError("observed_invalid", "plan observation is invalid")
    return data

def _warnings(data: dict[str, Any], root: Path, observed: dict[str, Any] | None) -> list[str]:
    warnings: list[str] = []
    for key, value in data["paths"].items():
        values = [value] if key == "source_root" else value
        for item in values:
            if not (root / item).exists():
                warnings.append(f"missing_path:{key}:{item}")
    if data["plan"]["active_plan_id"] and not (root / ".aota" / "forge" / "plans" / data["plan"]["active_plan_id"]).exists():
        warnings.append("active_plan_not_found")
    if observed is None and (root / ".aota" / "observed.json").exists():
        warnings.append("observed_invalid")
    if observed is not None:
        if observed["project_id"] != data["project"]["id"]:
            warnings.append("observed_project_id_conflict")
        if observed["plan"]["active_plan_id"] != data["plan"]["active_plan_id"]:
            warnings.append("observed_active_plan_conflict")
    return sorted(set(warnings))

def project_card(root: Path, data: dict[str, Any], observed: dict[str, Any] | None = None) -> dict[str, Any]:
    card: dict[str, Any] = {
        "project_id": data["project"]["id"], "name": data["project"]["name"], "kind": data["project"]["kind"], "status": data["project"]["status"], "summary": data["summary"], "capabilities": list(data["capabilities"]), "root": ".", "relevant_paths": data["paths"], "commands": data["commands"], "git": observed.get("git") if observed else None, "codegraph": {**data["codegraph"], "observed": observed.get("codegraph") if observed else None}, "active_plan": {"declared": data["plan"]["active_plan_id"], "observed": observed.get("plan") if observed else None}, "constraints": list(data["constraints"]), "warnings": _warnings(data, root, observed),
    }
    encoded = json.dumps(card, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_CARD_BYTES:
        raise ProjectError("output_truncated", "project card exceeds output cap")
    return card

def json_result(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
