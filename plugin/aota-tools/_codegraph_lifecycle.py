"""CodeGraph lifecycle and semantic-readiness projection (PCF-WI-06A/06B.5)."""
from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
from typing import Any

from ._codegraph_readonly import (
    CodegraphError,
    _decode_json,
    _resolve_project,
    _run,
    index_present,
    resolve_codegraph_executable,
)
from ._project_common import json_result

TOOLSET_NAME = "aota_codegraph_readonly"
TOOL_NAME = "aota_codegraph_lifecycle_status"
SCHEMA_VERSION = 2
STATES = (
    "not_configured", "not_initialized", "initialized_empty", "ready", "stale",
    "refresh_recommended", "reindex_recommended", "invalid", "runtime_unavailable",
    "blocked_checkpoint",
)
_COUNTS = ("files", "nodes", "edges")
_PENDING = ("added", "modified", "removed")
_MAX_PENDING_ITEMS = 200
_MAX_PATH = 512
_MAX_IGNORE_BYTES = 32 * 1024
_SUPPORTED_EXTENSIONS = frozenset({
    ".c", ".cc", ".cpp", ".cs", ".go", ".h", ".hpp", ".java", ".js", ".jsx",
    ".json", ".kt", ".kts", ".mjs", ".php", ".py", ".rb", ".rs", ".scala",
    ".sh", ".sql", ".swift", ".ts", ".tsx", ".yaml", ".yml",
})
_SIZE_LIMIT_PROFILES = {"1.1.1": 1048576}
_SKIP_DIRS = frozenset({".git", ".codegraph", "__pycache__", "node_modules"})

SCHEMA = {
    "name": TOOL_NAME,
    "description": "Return normalized CodeGraph lifecycle and semantic readiness for one registered project. Never mutates an index; telemetry and SQLite housekeeping may occur.",
    "parameters": {"type": "object", "properties": {
        "workspace_id": {"type": "string"}, "project_id": {"type": "string"},
    }, "required": ["workspace_id", "project_id"], "additionalProperties": False},
}


def _empty_counts(names: tuple[str, ...]) -> dict[str, int]:
    return {name: 0 for name in names}


def _bounded_nonnegative(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _safe_text(value: object, limit: int) -> str:
    return str(value)[:limit] if isinstance(value, (str, int, float)) else ""


def _pending_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = [payload]
    pending_payload = payload.get("pending_changes", payload.get("pendingChanges"))
    if isinstance(pending_payload, dict):
        sources.append(pending_payload)
    raw: object = []
    for source in sources:
        raw = next((source.get(key) for key in ("pending_items", "pendingItems", "pending_files", "pendingFiles", "items") if isinstance(source.get(key), list)), [])
        if raw:
            break
    items: list[dict[str, Any]] = []
    for value in raw[:_MAX_PENDING_ITEMS]:
        if not isinstance(value, dict):
            continue
        path = value.get("relative_path", value.get("relativePath", value.get("path")))
        if not isinstance(path, str) or not path or len(path) > _MAX_PATH:
            continue
        item: dict[str, Any] = {"relative_path": path}
        for key in ("reason", "change_type", "changeType", "status"):
            if isinstance(value.get(key), str):
                item["reason"] = value[key]
                break
        for key in ("indexed", "in_index", "inIndex", "ignored", "supported", "symlink", "regular_file", "regularFile"):
            if isinstance(value.get(key), bool):
                item[key] = value[key]
        errors = value.get("errors", value.get("error"))
        if isinstance(errors, list) and errors:
            item["errors"] = [str(error)[:128] for error in errors[:8]]
        elif isinstance(errors, str) and errors:
            item["errors"] = [errors[:128]]
        items.append(item)
    return items


def _status_size_limit(payload: dict[str, Any]) -> int | None:
    candidates: list[object] = [payload]
    for key in ("index", "limits", "config", "codegraph"):
        value = payload.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    for value in candidates:
        for key in ("max_file_size_bytes", "maxFileSizeBytes", "max_file_size", "maxFileSize", "MAX_FILE_SIZE"):
            limit = _bounded_nonnegative(value.get(key)) if isinstance(value, dict) else None
            if limit is not None and limit > 0:
                return limit
    return None


def _source_exists(root: Path, data: dict[str, Any]) -> bool:
    """Find one bounded, non-hidden source file without following symlinks."""
    source_root = root / data["paths"]["source_root"]
    if not source_root.is_dir() or source_root.is_symlink():
        return False
    for path in source_root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        return True
    return False


def normalize_status(payload: object) -> dict[str, Any]:
    """Validate the bounded status contract before deriving semantics."""
    if not isinstance(payload, dict) or not isinstance(payload.get("initialized"), bool):
        raise ValueError("malformed_status")
    counts: dict[str, int] = {}
    pending: dict[str, int] = {}
    pending_payload = payload.get("pending_changes", payload.get("pendingChanges", {}))
    if not isinstance(pending_payload, dict):
        pending_payload = {}
    for name in _COUNTS:
        value = payload.get(name, {"files": "fileCount", "nodes": "nodeCount", "edges": "edgeCount"}[name])
        if isinstance(value, str):
            value = payload.get(value)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("malformed_status")
        counts[name] = value
    for name in _PENDING:
        value = payload.get(name, pending_payload.get(name, 0))
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("malformed_status")
        pending[name] = value
    version = _safe_text(payload.get("version", payload.get("codegraph_version", "unknown")), 64)
    return {
        "initialized": payload["initialized"], "counts": counts, "pending_changes": pending,
        "pending_items": _pending_items(payload),
        "max_file_size_bytes": _status_size_limit(payload),
        "reindex_recommended": bool(payload.get("reindex_recommended", payload.get("reindexRecommended", payload.get("index", {}).get("reindexRecommended", False) if isinstance(payload.get("index", {}), dict) else False))),
        "refresh_recommended": bool(payload.get("refresh_recommended", payload.get("refreshRecommended", False))),
        "version": version, "backend": _safe_text(payload.get("backend", "unknown"), 64),
        "journal_mode": _safe_text(payload.get("journal_mode", "unknown"), 32),
    }


def _ignore_patterns(root: Path) -> list[str]:
    path = root / ".gitignore"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_IGNORE_BYTES:
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#") and not line.startswith("!")][:256]


def _ignored(root: Path, relative_path: str) -> bool:
    relative = relative_path.lstrip("./")
    parts = relative.split("/")
    for pattern in _ignore_patterns(root):
        pattern = pattern.rstrip("/")
        if fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(relative, pattern.lstrip("./")):
            return True
        if "/" not in pattern and any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
    return any(part in _SKIP_DIRS for part in parts)


def _safe_file(root: Path, relative_path: str) -> Path | None:
    if not relative_path or len(relative_path) > _MAX_PATH:
        return None
    candidate = Path(relative_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    path = root / candidate
    try:
        resolved_root = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
        resolved.relative_to(resolved_root)
        current = resolved_root
        for part in candidate.parts:
            current = current / part
            if current.is_symlink():
                return None
        if not path.is_file() or path.is_symlink():
            return None
        with path.open("rb") as handle:
            handle.read(1)
        return path
    except (OSError, ValueError):
        return None


def _size_limit(normalized: dict[str, Any]) -> tuple[int | None, str, str | None]:
    configured = normalized.get("max_file_size_bytes")
    if isinstance(configured, int) and configured > 0:
        return configured, "status_configured", None
    version = normalized.get("version", "unknown")
    if version in _SIZE_LIMIT_PROFILES:
        return _SIZE_LIMIT_PROFILES[version], "version_profile", "codegraph_size_limit_from_version_profile"
    return None, "unavailable", "oversized_classification_unavailable"


def _eligible_oversized(root: Path, item: dict[str, Any], limit: int) -> dict[str, Any] | None:
    relative = item.get("relative_path", "")
    if not isinstance(relative, str) or item.get("reason", "added").lower() not in {"added", "add", "new", "missing_membership"}:
        return None
    membership = item.get("indexed", item.get("in_index"))
    if membership is not False or item.get("ignored") is True or item.get("supported") is False or item.get("symlink") is True or item.get("regular_file") is False or item.get("errors"):
        return None
    path = _safe_file(root, relative)
    if path is None or _ignored(root, relative) or path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size <= limit:
        return None
    return {
        "relative_path": Path(relative).as_posix(), "size_bytes": size, "limit_bytes": limit,
        "language_or_extension": path.suffix.lower(), "reason": "codegraph_max_file_size",
        "semantic_impact": True,
    }


def project_pending_scope(root: Path, normalized: dict[str, Any]) -> dict[str, Any]:
    """Project raw CodeGraph pending counts into effective pending plus exclusions."""
    raw = dict(normalized.get("pending_changes", _empty_counts(_PENDING)))
    effective = dict(raw)
    warnings: list[str] = []
    exclusions: list[dict[str, Any]] = []
    raw_total = sum(raw.values())
    limit, authority, authority_warning = _size_limit(normalized)
    items = normalized.get("pending_items", [])
    if raw_total and not isinstance(items, list):
        items = []
    if raw_total and not items:
        warnings.append("oversized_classification_evidence_unavailable")
    if raw_total and authority_warning:
        warnings.append(authority_warning)
    if raw_total and items and limit is not None:
        for item in items[:_MAX_PENDING_ITEMS]:
            evidence = _eligible_oversized(root, item, limit)
            if evidence is None:
                continue
            if effective["added"] <= 0:
                continue
            effective["added"] -= 1
            exclusions.append(evidence)
    if exclusions:
        warnings.append("codegraph_oversized_source_excluded")
    classified = raw_total == 0 or (sum(effective.values()) == 0 and len(exclusions) == raw_total)
    if raw_total and items and authority == "unavailable":
        classified = False
    return {
        "raw_pending": raw, "effective_pending": effective,
        "known_exclusions": {"oversized_source": len(exclusions)},
        "oversized_sources": exclusions, "classification_complete": classified,
        "semantic_coverage": "partial" if exclusions else ("complete" if classified else "unknown"),
        "coverage_complete": not exclusions and classified, "warnings": sorted(set(warnings)),
        "max_file_size_bytes": limit, "size_limit_authority": authority,
    }


def derive_codegraph_lifecycle(
    project_id: str,
    configured: bool,
    index_present: bool,
    source_exists: bool,
    normalized: dict[str, Any] | None = None,
    *,
    error: str | None = None,
    warnings: list[str] | None = None,
    projection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    warnings = list(warnings or [])
    counts = (normalized or {}).get("counts", _empty_counts(_COUNTS))
    raw_pending = (normalized or {}).get("pending_changes", _empty_counts(_PENDING))
    projection = projection or {
        "raw_pending": raw_pending, "effective_pending": raw_pending,
        "known_exclusions": {"oversized_source": 0}, "oversized_sources": [],
        "classification_complete": sum(raw_pending.values()) == 0,
        "semantic_coverage": "complete" if sum(raw_pending.values()) == 0 else "unknown",
        "coverage_complete": sum(raw_pending.values()) == 0, "warnings": [],
        "max_file_size_bytes": (normalized or {}).get("max_file_size_bytes"),
        "size_limit_authority": "none",
    }
    warnings.extend(projection.get("warnings", []))
    effective_pending = projection.get("effective_pending", raw_pending)
    if not configured:
        state, action, checkpoint = "not_configured", "none", False
    elif not index_present:
        state, action, checkpoint = "not_initialized", "bootstrap_required", True
    elif error:
        state, action, checkpoint = ("invalid" if error in {"malformed_status", "codegraph_project_mismatch"} else "runtime_unavailable"), "inspect_runtime", False
    elif not normalized or not normalized["initialized"]:
        state, action, checkpoint = "not_initialized", "bootstrap_required", True
    elif normalized.get("reindex_recommended"):
        state, action, checkpoint = "reindex_recommended", "reindex", True
    elif normalized.get("refresh_recommended"):
        state, action, checkpoint = "refresh_recommended", "refresh", True
    elif all(value == 0 for value in counts.values()) and source_exists:
        state, action, checkpoint = "initialized_empty", "bootstrap_or_reindex", True
    elif any(value > 0 for value in effective_pending.values()) or not projection.get("classification_complete", False):
        state, action, checkpoint = "stale", "refresh", True
    else:
        state, action, checkpoint = "ready", "none", False
        if counts["edges"] == 0:
            warnings.append("edge_count_zero")
    semantic_ready = state == "ready"
    if projection.get("known_exclusions", {}).get("oversized_source", 0):
        projection["semantic_coverage"] = "partial"
        projection["coverage_complete"] = False
    return {
        "schema_version": SCHEMA_VERSION, "project_id": project_id,
        "configured": configured, "state": state, "semantic_ready": semantic_ready,
        "semantic_coverage": projection.get("semantic_coverage", "unknown"),
        "coverage_complete": bool(projection.get("coverage_complete", False)),
        "index_present": index_present, "initialized": bool(normalized and normalized.get("initialized")),
        "files": counts["files"], "nodes": counts["nodes"], "edges": counts["edges"],
        "index": {"present": index_present, "initialized": bool(normalized and normalized.get("initialized")), **counts},
        "pending_changes": raw_pending, "raw_pending": projection.get("raw_pending", raw_pending),
        "effective_pending": effective_pending, "known_exclusions": projection.get("known_exclusions", {"oversized_source": 0}),
        "oversized_sources": projection.get("oversized_sources", []),
        "max_file_size_bytes": projection.get("max_file_size_bytes"),
        "size_limit_authority": projection.get("size_limit_authority", "unavailable"),
        "reindex_recommended": bool(normalized and normalized.get("reindex_recommended")),
        "recommended_action": action, "checkpoint_required": checkpoint,
        "side_effects": ["telemetry_metadata_write", "sqlite_housekeeping_possible"],
        "warnings": sorted(set(warnings)),
        **({"error_code": error} if error else {}),
    }


def inspect_lifecycle(args: dict[str, Any]) -> dict[str, Any]:
    """Inspect one registered project, invoking only the bounded status command."""
    _validate_args(args)
    try:
        _, project_id, workspace_root, root, index = _resolve_project(args)
    except CodegraphError as exc:
        return {"status": "error", "error_code": exc.code, "side_effects": ["telemetry_metadata_write"]}
    try:
        from ._project_common import load_project
        data = load_project(root / ".aota" / "project.yaml")[1]
    except Exception:
        return {"status": "error", "error_code": "manifest_invalid"}
    configured = bool(data["codegraph"]["enabled"])
    if not configured:
        result = derive_codegraph_lifecycle(project_id, False, index_present(index), False)
        result["status"] = "ok"
        return result
    if not index_present(index):
        result = derive_codegraph_lifecycle(project_id, True, False, _source_exists(root, data))
        result["status"] = "ok"
        return result
    normalized: dict[str, Any] | None = None
    try:
        executable, _source, warnings = resolve_codegraph_executable(workspace_root, root)
        raw, _ = _run(executable, ["status", "--json", str(root)], root, 10, os.environ)
        payload = _decode_json(raw, dict, "malformed_status")
        if payload.get("projectPath") and Path(str(payload["projectPath"])).resolve() != root.resolve():
            raise ValueError("codegraph_project_mismatch")
        if payload.get("indexPath") and Path(str(payload["indexPath"])).resolve() != index.resolve():
            raise ValueError("codegraph_project_mismatch")
        normalized = normalize_status(payload)
        projection = project_pending_scope(root, normalized)
        result = derive_codegraph_lifecycle(project_id, True, True, _source_exists(root, data), normalized, warnings=warnings, projection=projection)
    except ValueError as exc:
        result = derive_codegraph_lifecycle(project_id, True, True, _source_exists(root, data), normalized, error=str(exc) if str(exc) == "codegraph_project_mismatch" else "malformed_status")
    except CodegraphError as exc:
        result = derive_codegraph_lifecycle(project_id, True, True, _source_exists(root, data), normalized, error=exc.code)
    result["status"] = "ok"
    if normalized:
        result["index"]["version"] = normalized["version"]
        result["index"]["backend"] = normalized["backend"]
        result["index"]["journal_mode"] = normalized["journal_mode"]
    return result


def _validate_args(args: dict[str, Any]) -> None:
    if not isinstance(args, dict) or set(args) != {"workspace_id", "project_id"}:
        raise CodegraphError("codegraph_query_invalid")


def run_oversized_scope_smoke() -> dict[str, str]:
    """Exercise the eligibility contract in isolated temporary source roots."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="pcf-oversized-") as raw:
        root = Path(raw)
        (root / "src").mkdir()
        (root / "src" / "large.py").write_bytes(b"x" * 1048577)
        (root / "src" / "limit.py").write_bytes(b"x" * 1048576)
        (root / "src" / "small.py").write_bytes(b"x")
        (root / "ignored.py").write_bytes(b"x" * 1048577)
        (root / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
        (root / "escape.py").symlink_to(root.parent / "src", target_is_directory=True)
        normalized = {"version": "1.1.1", "max_file_size_bytes": None, "pending_changes": {"added": 2, "modified": 0, "removed": 0}, "pending_items": [{"path": "src/large.py", "reason": "added", "indexed": False}, {"path": "src/limit.py", "reason": "added", "indexed": False}]}
        normalized = {**normalized, "pending_items": [{"relative_path": "src/large.py", "reason": "added", "indexed": False}, {"relative_path": "src/limit.py", "reason": "added", "indexed": False}]}
        projection = project_pending_scope(root, normalized)
        assert projection["effective_pending"]["added"] == 1
        assert projection["known_exclusions"]["oversized_source"] == 1
        assert projection["size_limit_authority"] == "version_profile"
        (root / "api").mkdir()
        (root / "static").mkdir()
        (root / "api" / "routes.py").write_bytes(b"x" * 1056777)
        (root / "static" / "i18n.js").write_bytes(b"x" * 1645929)
        webui = {"version": "1.1.1", "pending_changes": {"added": 2, "modified": 0, "removed": 0}, "pending_items": [{"relative_path": "api/routes.py", "reason": "added", "indexed": False}, {"relative_path": "static/i18n.js", "reason": "added", "indexed": False}]}
        webui_projection = project_pending_scope(root, webui)
        webui_result = derive_codegraph_lifecycle("hermes-webui", True, True, True, {"initialized": True, "counts": {"files": 95, "nodes": 6850, "edges": 27404}, "pending_changes": webui["pending_changes"], "reindex_recommended": False}, projection=webui_projection)
        assert webui_result["state"] == "ready" and webui_result["semantic_ready"] is True
        assert webui_result["semantic_coverage"] == "partial" and webui_result["coverage_complete"] is False
        assert webui_result["known_exclusions"]["oversized_source"] == 2
        mixed = {**webui, "pending_changes": {"added": 3, "modified": 0, "removed": 0}, "pending_items": webui["pending_items"] + [{"relative_path": "src/small.py", "reason": "added", "indexed": False}]}
        mixed_result = derive_codegraph_lifecycle("fixture-project", True, True, True, {"initialized": True, "counts": {"files": 95, "nodes": 6850, "edges": 27404}, "pending_changes": mixed["pending_changes"], "reindex_recommended": False}, projection=project_pending_scope(root, mixed))
        assert mixed_result["state"] == "stale" and mixed_result["semantic_ready"] is False
        reindex = derive_codegraph_lifecycle("fixture-project", True, True, True, {"initialized": True, "counts": {"files": 95, "nodes": 6850, "edges": 27404}, "pending_changes": webui["pending_changes"], "reindex_recommended": True}, projection=webui_projection)
        assert reindex["state"] == "reindex_recommended" and reindex["semantic_ready"] is False
        rejected = {"version": "1.1.1", "pending_changes": {"added": 1, "modified": 0, "removed": 0}, "pending_items": [{"relative_path": "ignored.py", "reason": "added", "indexed": False}]}
        assert project_pending_scope(root, rejected)["known_exclusions"]["oversized_source"] == 0
        unknown = {"version": "9.9.9", "pending_changes": {"added": 1, "modified": 0, "removed": 0}, "pending_items": [{"relative_path": "src/large.py", "reason": "added", "indexed": False}]}
        assert project_pending_scope(root, unknown)["classification_complete"] is False
    return {"marker": "PCF_CODEGRAPH_OVERSIZED_CLASSIFICATION_PASS"}


def handle(args: dict, **_kwargs: Any) -> str:
    try:
        return json_result(inspect_lifecycle(args))
    except CodegraphError as exc:
        return json_result({"status": "error", "error_code": exc.code})
