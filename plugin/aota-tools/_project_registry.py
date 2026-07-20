"""Derived, rebuildable workspace Project Registry for PCF-WI-02/03."""
from __future__ import annotations

import datetime as _dt
import fcntl
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from ._project_common import MAX_RESULTS, ProjectError, SKIP_DIRS, is_project_scan_excluded, load_project
from ._workspace import WorkspaceError, resolve_workspace

SCHEMA_VERSION = 1
MAX_REGISTRY_BYTES = 512 * 1024
MAX_QUERY = 256
REGISTRY_DIR = Path(".aota") / "registry"
REGISTRY_FILE = REGISTRY_DIR / "projects.json"
LOCK_FILE = REGISTRY_DIR / "projects.lock"
TOOL_NAME_REFRESH = "aota_project_registry_refresh"
TOOL_NAME_OPEN = "aota_project_registry_open"
TOOLSET_NAME = "aota_project_readonly"
REFRESH_SCHEMA = {"name": TOOL_NAME_REFRESH, "description": "Rebuild the derived Project Registry under the registered workspace using bounded scan, lock and atomic write.", "parameters": {"type": "object", "properties": {"workspace_id": {"type": "string"}, "dry_run": {"type": "boolean"}, "include_invalid": {"type": "boolean"}}, "required": ["workspace_id"], "additionalProperties": False}}
OPEN_SCHEMA = {"name": TOOL_NAME_OPEN, "description": "Open bounded Registry metadata and deterministic stale status without refreshing it.", "parameters": {"type": "object", "properties": {"workspace_id": {"type": "string"}}, "required": ["workspace_id"], "additionalProperties": False}}


def registry_path(root: Path) -> Path:
    """Return the only registry path permitted for a resolved workspace."""
    return root / REGISTRY_FILE


def _safe_registry_path(root: Path) -> Path:
    path = registry_path(root)
    try:
        path.parent.resolve(strict=False).relative_to(root.resolve())
    except ValueError as exc:
        raise WorkspaceError("registry path escapes workspace") from exc
    return path


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tokens(*values: str) -> list[str]:
    result: set[str] = set()
    for value in values:
        result.update(re.findall(r"[a-z0-9][a-z0-9_-]{1,95}", value.casefold()))
    return sorted(result)[:64]


def _relative(root: Path, path: Path) -> str:
    return "." if path == root else path.relative_to(root).as_posix()


def _manifest_record(workspace_root: Path, manifest: Path) -> dict[str, Any]:
    project_root, data = load_project(manifest)
    project = data["project"]
    relative_root = _relative(workspace_root, project_root)
    manifest_path = _relative(workspace_root, manifest)
    warnings: list[str] = []
    for key, value in data["paths"].items():
        values = [value] if key == "source_root" else value
        for item in values:
            if not (project_root / item).exists():
                warnings.append(f"missing_path:{key}:{item}")
    active_plan_id = data["plan"]["active_plan_id"]
    if active_plan_id and not (project_root / ".aota" / "forge" / "plans" / active_plan_id).exists():
        warnings.append("active_plan_not_found")
    name = project["name"]
    kind = project["kind"]
    summary = data["summary"][:4000]
    capabilities = list(data["capabilities"])
    return {
        "project_id": project["id"],
        "root": relative_root,
        "manifest_path": manifest_path,
        "manifest_sha256": _sha256(manifest),
        "name": name,
        "kind": kind,
        "status": project["status"],
        "summary": summary,
        "capabilities": capabilities,
        "keywords": _tokens(project["id"], name, kind, summary, *capabilities),
        "relevant_paths": data["paths"],
        "commands": data["commands"],
        "active_plan_id": active_plan_id,
        "constraints": list(data["constraints"]),
        "warnings": sorted(set(warnings)),
    }


def _scan_sources(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[str], list[str]]:
    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, str]] = []
    warnings: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS and not (current / d).is_symlink()
        )
        if current.name != ".aota" or "project.yaml" not in filenames:
            continue
        manifest = current / "project.yaml"
        if is_project_scan_excluded(root, manifest):
            continue
        relative_manifest = _relative(root, manifest)
        if manifest.is_symlink():
            invalid.append({"manifest_path": relative_manifest, "error_code": "manifest_invalid"})
            continue
        try:
            valid.append(_manifest_record(root, manifest))
        except (ProjectError, OSError) as exc:
            invalid.append({
                "manifest_path": relative_manifest,
                "error_code": exc.code if isinstance(exc, ProjectError) else "manifest_invalid",
            })
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in valid:
        groups.setdefault(record["project_id"], []).append(record)
    duplicates = sorted(project_id for project_id, records in groups.items() if len(records) > 1)
    if duplicates:
        for project_id in duplicates:
            for record in groups[project_id]:
                invalid.append({
                    "manifest_path": record["manifest_path"],
                    "error_code": "duplicate_project_id",
                })
        valid = [record for record in valid if record["project_id"] not in duplicates]
    valid.sort(key=lambda item: item["project_id"])
    invalid.sort(key=lambda item: (item["manifest_path"], item["error_code"]))
    return valid, invalid, duplicates, sorted(set(warnings))


def _source_fingerprint(
    workspace_id: str,
    records: list[dict[str, Any]],
    invalid: list[dict[str, str]],
    duplicates: list[str],
) -> str:
    source = {
        "workspace_id": workspace_id,
        "manifests": [
            {
                "project_id": item["project_id"],
                "manifest_path": item["manifest_path"],
                "manifest_sha256": item["manifest_sha256"],
            }
            for item in records
        ],
        "invalid_projects": invalid,
        "duplicate_project_ids": duplicates,
    }
    encoded = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _build_registry(workspace_id: str, root: Path, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    records, invalid, duplicates, scan_warnings = _scan_sources(root)
    fingerprint = _source_fingerprint(workspace_id, records, invalid, duplicates)
    same_source = isinstance(previous, dict) and previous.get("source_fingerprint") == fingerprint
    revision = int(previous.get("registry_revision", 0)) if same_source else int(previous.get("registry_revision", 0)) + 1 if isinstance(previous, dict) else 1
    if revision < 1:
        revision = 1
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace_id": workspace_id,
        "registry_revision": revision,
        "generated_at": _now(),
        "source_fingerprint": fingerprint,
        "project_count": len(records),
        "projects": records,
        "invalid_projects": invalid,
        "duplicate_project_ids": duplicates,
        "scan_warnings": scan_warnings,
    }


def _valid_registry_shape(data: dict[str, Any]) -> bool:
    project_fields = {"project_id", "root", "manifest_path", "manifest_sha256", "name", "kind", "status", "summary", "capabilities", "keywords", "relevant_paths", "commands", "active_plan_id", "constraints", "warnings"}
    invalid_fields = {"manifest_path", "error_code"}
    if not isinstance(data.get("workspace_id"), str) or not data["workspace_id"] or not isinstance(data.get("generated_at"), str):
        return False
    if data.get("project_count") != len(data["projects"]):
        return False
    if any(not isinstance(item, dict) or set(item) != project_fields for item in data["projects"]):
        return False
    text_fields = ("project_id", "root", "manifest_path", "manifest_sha256", "name", "kind", "status", "summary")
    list_fields = ("capabilities", "keywords", "constraints", "warnings")
    for item in data["projects"]:
        if not all(isinstance(item[field], str) for field in text_fields):
            return False
        if not all(isinstance(item[field], list) and all(isinstance(value, str) for value in item[field]) for field in list_fields):
            return False
        if not isinstance(item["relevant_paths"], dict) or not isinstance(item["commands"], dict):
            return False
        if item["active_plan_id"] is not None and not isinstance(item["active_plan_id"], str):
            return False
    if any(not isinstance(item, dict) or set(item) != invalid_fields for item in data["invalid_projects"]):
        return False
    return all(isinstance(item, str) for item in data["duplicate_project_ids"] + data["scan_warnings"])


def _read_existing(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, "registry_missing"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_REGISTRY_BYTES:
            return None, "registry_invalid"
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            return None, "registry_invalid"
        required = {"schema_version", "workspace_id", "registry_revision", "generated_at", "source_fingerprint", "project_count", "projects", "invalid_projects", "duplicate_project_ids", "scan_warnings"}
        if set(data) != required or not isinstance(data["projects"], list) or not isinstance(data["invalid_projects"], list) or not isinstance(data["duplicate_project_ids"], list):
            return None, "registry_invalid"
        if not isinstance(data["registry_revision"], int) or data["registry_revision"] < 1:
            return None, "registry_invalid"
        if not isinstance(data["source_fingerprint"], str) or not re.fullmatch(r"[0-9a-f]{64}", data["source_fingerprint"]):
            return None, "registry_invalid"
        if not _valid_registry_shape(data):
            return None, "registry_invalid"
        return data, None
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return None, "registry_invalid"


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    if len(encoded.encode("utf-8")) > MAX_REGISTRY_BYTES:
        raise ProjectError("output_truncated", "registry exceeds output cap")
    fd, temp_name = tempfile.mkstemp(prefix=".projects.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise ProjectError("registry_write_failed", type(exc).__name__) from exc


def registry_status(workspace_id: str, root: Path | None = None) -> dict[str, Any]:
    if root is None:
        root = resolve_workspace(workspace_id)
    path = _safe_registry_path(root)
    existing, error = _read_existing(path)
    if error:
        return {"status": error.removeprefix("registry_"), "stale": error != "registry_missing", "registry_revision": None, "source_fingerprint": None, "project_count": 0, "invalid_count": 0, "duplicate_count": 0, "warning": error}
    records, invalid, duplicates, _ = _scan_sources(root)
    fingerprint = _source_fingerprint(workspace_id, records, invalid, duplicates)
    if existing["workspace_id"] != workspace_id:
        state = "invalid"
        warning = "registry_workspace_mismatch"
    elif existing["source_fingerprint"] != fingerprint:
        state = "stale"
        warning = "registry_stale"
    else:
        state = "fresh"
        warning = None
    return {
        "status": state,
        "stale": state != "fresh",
        "registry_revision": existing["registry_revision"],
        "source_fingerprint": existing["source_fingerprint"],
        "project_count": existing["project_count"],
        "invalid_count": len(existing["invalid_projects"]),
        "duplicate_count": len(existing["duplicate_project_ids"]),
        "warning": warning,
    }


def open_registry(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = args.get("workspace_id", "")
    if not isinstance(workspace_id, str) or not workspace_id:
        return {"status": "error", "error_code": "workspace_not_found"}
    try:
        root = resolve_workspace(workspace_id)
        state = registry_status(workspace_id, root)
        existing, error = _read_existing(_safe_registry_path(root))
        result = {"status": "ok", "workspace_id": workspace_id, "registry": state}
        if existing is not None and error is None:
            result["registry"].update({
                "schema_version": existing["schema_version"],
                "projects": existing["projects"][:MAX_RESULTS],
                "invalid_projects": existing["invalid_projects"][:MAX_RESULTS],
                "duplicate_project_ids": existing["duplicate_project_ids"][:MAX_RESULTS],
                "truncated": len(existing["projects"]) > MAX_RESULTS,
            })
        return result
    except WorkspaceError as exc:
        return {"status": "error", "error_code": "workspace_not_found", "detail": str(exc)[:300]}


def refresh_registry(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = args.get("workspace_id", "")
    dry_run = args.get("dry_run", False)
    include_invalid = args.get("include_invalid", True)
    if not isinstance(workspace_id, str) or not workspace_id:
        return {"status": "error", "error_code": "workspace_not_found"}
    if not isinstance(dry_run, bool) or not isinstance(include_invalid, bool):
        return {"status": "error", "error_code": "query_invalid"}
    try:
        root = resolve_workspace(workspace_id)
        path = _safe_registry_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_name("projects.lock").open("a+") as lock_handle:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return {"status": "error", "error_code": "registry_lock_timeout"}
            previous, previous_error = _read_existing(path)
            registry = _build_registry(workspace_id, root, previous)
            old_fingerprint = previous.get("source_fingerprint") if previous else None
            changed = previous is None or previous_error is not None or previous.get("workspace_id") != workspace_id or old_fingerprint != registry["source_fingerprint"] or previous.get("projects") != registry["projects"] or previous.get("invalid_projects") != registry["invalid_projects"] or previous.get("duplicate_project_ids") != registry["duplicate_project_ids"]
            if not dry_run and (changed or previous_error):
                _atomic_write(path, registry)
            if not include_invalid:
                registry = {**registry, "invalid_projects": [], "duplicate_project_ids": []}
            return {
                "status": "ok", "workspace_id": workspace_id, "registry_path": str(REGISTRY_FILE),
                "changed": changed, "registry_revision": registry["registry_revision"],
                "source_fingerprint": registry["source_fingerprint"], "project_count": registry["project_count"],
                "invalid_count": len(registry["invalid_projects"]), "duplicate_count": len(registry["duplicate_project_ids"]),
                "dry_run": dry_run, "warning": "registry_rebuilt_from_invalid_existing" if previous_error == "registry_invalid" else None,
            }
    except WorkspaceError as exc:
        return {"status": "error", "error_code": "workspace_not_found", "detail": str(exc)[:300]}
    except ProjectError as exc:
        return {"status": "error", "error_code": exc.code}
    except OSError as exc:
        return {"status": "error", "error_code": "registry_write_failed", "detail": type(exc).__name__}


def load_search_records(workspace_id: str, root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    state = registry_status(workspace_id, root)
    existing, error = _read_existing(_safe_registry_path(root))
    if state["status"] == "fresh" and existing is not None and error is None:
        return existing["projects"], {**state, "source": "registry"}
    records, invalid, duplicates, _ = _scan_sources(root)
    return records[:MAX_RESULTS], {**state, "source": "live_scan", "invalid_count": len(invalid), "duplicate_count": len(duplicates)}


def search_records(records: list[dict[str, Any]], query: str, limit: int, include_inactive: bool = False) -> list[dict[str, Any]]:
    terms = [term for term in re.split(r"\s+", query.casefold().strip()) if term]
    matches: list[dict[str, Any]] = []
    for record in records:
        if not include_inactive and record["status"] != "active":
            continue
        fields = {
            "project_id": record["project_id"].casefold(),
            "name": record["name"].casefold(),
            "kind": record["kind"].casefold(),
            "summary": record["summary"].casefold(),
            "capabilities": " ".join(record["capabilities"]).casefold(),
            "keywords": " ".join(record["keywords"]).casefold(),
        }
        if not all(any(term in value for value in fields.values()) for term in terms):
            continue
        score = 0
        reasons: list[str] = []
        matched_fields: set[str] = set()
        for term in terms:
            if term == fields["project_id"]:
                score += 100
                reasons.append(f"exact_project_id:{term}")
                matched_fields.add("project_id")
            elif term == fields["name"]:
                score += 80
                reasons.append(f"exact_name:{term}")
                matched_fields.add("name")
            else:
                for field, weight in (("project_id", 40), ("name", 35), ("capabilities", 25), ("kind", 20), ("summary", 10), ("keywords", 5)):
                    if term in fields[field]:
                        score += weight
                        matched_fields.add(field)
                        reasons.append(f"{field}:{term}")
        if record["status"] == "active":
            score += 3
        matches.append({**record, "score": score, "match_reasons": sorted(set(reasons)), "matched_fields": sorted(matched_fields)})
    matches.sort(key=lambda item: (-item["score"], 0 if item["status"] == "active" else 1, item["project_id"]))
    return matches[:limit]


def json_result(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def handle_open(args: dict[str, Any], **_kwargs) -> str:
    return json_result(open_registry(args))


def handle_refresh(args: dict[str, Any], **_kwargs) -> str:
    return json_result(refresh_registry(args))
