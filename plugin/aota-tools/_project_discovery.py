"""Read-only project manifest discovery for PCF-WI-01."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ._project_common import MAX_RESULTS, ProjectError, SKIP_DIRS, is_project_scan_excluded, json_result, load_project
from ._workspace import resolve_workspace
from ._workspace import WorkspaceError

TOOL_NAME = "aota_project_scan"
TOOLSET_NAME = "aota_project_readonly"
SCHEMA = {"name": TOOL_NAME, "description": "Scan a registered workspace for bounded, valid .aota/project.yaml manifests without writing.", "parameters": {"type": "object", "properties": {"workspace_id": {"type": "string"}, "limit": {"type": "integer", "description": "1..50"}}, "required": ["workspace_id"], "additionalProperties": False}}


def _scan(root: Path, limit: int = MAX_RESULTS) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    invalid: list[dict[str, str]] = []
    seen: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not (Path(dirpath) / d).is_symlink())
        if "project.yaml" not in filenames:
            continue
        manifest = Path(dirpath) / "project.yaml"
        if Path(dirpath).name != ".aota" or manifest.is_symlink():
            continue
        if is_project_scan_excluded(root, manifest):
            continue
        try:
            project_root, data = load_project(manifest)
            project_id = data["project"]["id"]
            relative = project_root.relative_to(root).as_posix() if project_root != root else "."
            if project_id in seen:
                invalid.append({"path": relative, "error_code": "duplicate_project_id"})
                continue
            seen[project_id] = relative
            candidates.append({"project_id": project_id, "name": data["project"]["name"], "kind": data["project"]["kind"], "status": data["project"]["status"], "root": relative, "summary": data["summary"][:300], "capabilities": list(data["capabilities"])})
        except ProjectError as exc:
            invalid.append({"path": str(Path(dirpath).relative_to(root)), "error_code": exc.code})
    candidates.sort(key=lambda item: item["project_id"])
    truncated = len(candidates) > limit
    return {"projects": candidates[:limit], "project_count": min(len(candidates), limit), "invalid": sorted(invalid, key=lambda item: item["path"])[:limit], "truncated": truncated}


def handle(args: dict, **_kwargs) -> str:
    workspace_id = args.get("workspace_id", "")
    limit = args.get("limit", MAX_RESULTS)
    if not isinstance(workspace_id, str) or not workspace_id:
        return json_result({"status": "error", "error_code": "workspace_not_found"})
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > MAX_RESULTS:
        return json_result({"status": "error", "error_code": "limit_invalid"})
    try:
        root = resolve_workspace(workspace_id)
        payload = _scan(root, limit)
    except WorkspaceError as exc:
        return json_result({"status": "error", "error_code": "workspace_not_found", "detail": str(exc)[:300]})
    payload.update({"status": "ok", "workspace_id": workspace_id})
    return json_result(payload)
