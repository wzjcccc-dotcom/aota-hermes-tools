"""Registry-backed project search and identity-based project card operations."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ._project_common import MAX_QUERY, MAX_RESULTS, ProjectError, json_result, load_observed, load_project, project_card
from ._project_discovery import _scan
from ._project_registry import load_search_records, search_records
from ._workspace import WorkspaceError, resolve_workspace

TOOL_NAME_SEARCH = "aota_project_search"
TOOL_NAME_OPEN = "aota_project_open"
TOOLSET_NAME = "aota_project_readonly"
SEARCH_SCHEMA = {"name": TOOL_NAME_SEARCH, "description": "Search a fresh derived Project Registry with deterministic ranking; use a clearly marked live-scan fallback when the Registry is unavailable.", "parameters": {"type": "object", "properties": {"workspace_id": {"type": "string"}, "query": {"type": "string"}, "limit": {"type": "integer", "description": "1..50"}, "include_inactive": {"type": "boolean"}}, "required": ["workspace_id", "query"], "additionalProperties": False}}
OPEN_SCHEMA = {"name": TOOL_NAME_OPEN, "description": "Open a compact Project Card by trusted project_id; never accepts an arbitrary path.", "parameters": {"type": "object", "properties": {"workspace_id": {"type": "string"}, "project_id": {"type": "string"}, "include_observed": {"type": "boolean"}}, "required": ["workspace_id", "project_id"], "additionalProperties": False}}


def _validate_common(args: dict) -> tuple[str, Any] | tuple[None, dict[str, str]]:
    workspace_id = args.get("workspace_id", "")
    if not isinstance(workspace_id, str) or not workspace_id:
        return None, {"status": "error", "error_code": "workspace_not_found"}
    try:
        root = resolve_workspace(workspace_id)
    except WorkspaceError as exc:
        return None, {"status": "error", "error_code": "workspace_not_found", "detail": str(exc)[:300]}
    return workspace_id, root


def handle_search(args: dict, **_kwargs) -> str:
    workspace_id, root_or_error = _validate_common(args)
    if workspace_id is None:
        return json_result(root_or_error)
    query = args.get("query", "")
    limit = args.get("limit", 10)
    include_inactive = args.get("include_inactive", False)
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY:
        return json_result({"status": "error", "error_code": "query_invalid"})
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > MAX_RESULTS:
        return json_result({"status": "error", "error_code": "limit_invalid"})
    if not isinstance(include_inactive, bool):
        return json_result({"status": "error", "error_code": "include_inactive_invalid"})
    records, registry = load_search_records(workspace_id, root_or_error)
    matches = search_records(records, query, MAX_RESULTS + 1, include_inactive)
    results = matches[:limit]
    warnings = [] if registry["status"] == "fresh" else [registry.get("warning") or "registry_stale"]
    return json_result({
        "status": "ok", "workspace_id": workspace_id, "query": query,
        "results": results, "result_count": len(results), "truncated": len(matches) > limit,
        "registry_status": registry["status"], "registry_revision": registry.get("registry_revision"),
        "source": registry["source"], "warnings": warnings,
    })


def _find_manifest(root: Path, project_id: str) -> tuple[Path, dict[str, Any]] | None:
    scanned = _scan(root, MAX_RESULTS)
    record = next((item for item in scanned["projects"] if item["project_id"] == project_id), None)
    if record is None:
        return None
    project_root = root if record["root"] == "." else root / record["root"]
    manifest = project_root / ".aota" / "project.yaml"
    return manifest, load_project(manifest)[1]


def handle_open(args: dict, **_kwargs) -> str:
    workspace_id, root_or_error = _validate_common(args)
    if workspace_id is None:
        return json_result(root_or_error)
    project_id = args.get("project_id", "")
    include_observed = args.get("include_observed", True)
    if not isinstance(project_id, str) or not project_id or len(project_id) > 96 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", project_id):
        return json_result({"status": "error", "error_code": "project_not_found", "project_id": project_id})
    if not isinstance(include_observed, bool):
        return json_result({"status": "error", "error_code": "include_observed_invalid"})
    try:
        found = _find_manifest(root_or_error, project_id)
        if found is None:
            return json_result({"status": "error", "error_code": "project_not_found", "project_id": project_id})
        manifest, data = found
        project_root = manifest.parent.parent
        observed = None
        observed_status = "missing"
        if include_observed and (project_root / ".aota" / "observed.json").exists():
            try:
                observed = load_observed(project_root / ".aota" / "observed.json")
                observed_status = "valid"
            except ProjectError:
                observed_status = "invalid"
        card = project_card(project_root, data, observed)
        if observed_status == "invalid" and "observed_invalid" not in card["warnings"]:
            card["warnings"].append("observed_invalid")
            card["warnings"].sort()
        return json_result({"status": "ok", "workspace_id": workspace_id, "project_id": project_id, "manifest_validation": "valid", "observed_validation": observed_status, "card": card})
    except (ProjectError, OSError) as exc:
        code = exc.code if isinstance(exc, ProjectError) else "manifest_invalid"
        return json_result({"status": "error", "error_code": code, "project_id": project_id})
