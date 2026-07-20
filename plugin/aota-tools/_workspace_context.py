"""Registered workspace discovery and immutable project binding helpers.

This module is deliberately read-only except for callers which persist a
selection in the existing orchestration decision store.  A workspace id is a
registry key, never a path supplied by a model or worker.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ._workspace import WorkspaceError, _load_registry, _registry_path, resolve_workspace
from ._project_registry import _scan_sources, registry_status

TOOLSET_NAME = "aota_workspace_readonly"
LIST_TOOL_NAME = "aota_workspace_list"
OPEN_TOOL_NAME = "aota_workspace_open"
MAX_ITEMS = 50

LIST_SCHEMA = {"name": LIST_TOOL_NAME, "description": "List bounded summaries of registered canonical workspaces. This tool accepts no registry path or filesystem path.", "parameters": {"type": "object", "properties": {"status": {"type": "string", "enum": ["active", "inactive", "all"]}, "max_items": {"type": "integer", "minimum": 1, "maximum": MAX_ITEMS}}, "additionalProperties": False}}
OPEN_SCHEMA = {"name": OPEN_TOOL_NAME, "description": "Open one bounded registered workspace and its project summaries. This tool accepts a registry id, never a path.", "parameters": {"type": "object", "properties": {"workspace_id": {"type": "string", "maxLength": 128}}, "required": ["workspace_id"], "additionalProperties": False}}


def _digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise WorkspaceError("workspace_registry_unavailable") from exc


def workspace_registry_digest() -> str:
    return _digest(_registry_path())


def _entry_status(entry: Any) -> str:
    return entry.get("status", "active") if isinstance(entry, dict) else "invalid"


def _summary(workspace_id: str, entry: Any) -> dict[str, Any]:
    warnings: list[str] = []
    try:
        root = resolve_workspace(workspace_id)
        canonical_root = str(root)
        valid = True
        if root == Path("/aota-runtime"):
            valid = False; warnings.append("runtime_root_not_project_workspace")
    except WorkspaceError:
        root = None; canonical_root = None; valid = False; warnings.append("workspace_root_unavailable")
    records: list[dict[str, Any]] = []
    state: dict[str, Any] = {"status": "unavailable", "registry_revision": None}
    if root is not None and valid:
        try:
            records, _, _, scan_warnings = _scan_sources(root)
            state = registry_status(workspace_id, root)
            warnings.extend(scan_warnings)
        except (OSError, WorkspaceError):
            warnings.append("project_registry_unavailable")
    return {"workspace_id": workspace_id, "canonical_root": canonical_root,
            "status": _entry_status(entry), "registry_valid": valid,
            "project_registry_status": state.get("status", "unavailable"),
            "project_count": len(records), "primary_project_ids": [r["project_id"] for r in records[:10]],
            "project_kinds": sorted({r["kind"] for r in records})[:10],
            "summary": (entry.get("summary", "") if isinstance(entry, dict) else "")[:500],
            "last_project_registry_refresh": state.get("generated_at"), "warnings": sorted(set(warnings))}


def list_workspaces(args: dict[str, Any]) -> dict[str, Any]:
    status = args.get("status", "active")
    limit = args.get("max_items", MAX_ITEMS)
    if status not in {"active", "inactive", "all"} or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_ITEMS:
        return {"status": "error", "error_code": "workspace_list_request_invalid"}
    registry = _load_registry()
    if not registry:
        return {"status": "error", "error_code": "workspace_registry_unavailable", "workspaces": []}
    items = []
    for key in sorted(registry):
        if not isinstance(key, str) or len(key) > 128:
            continue
        entry = registry[key]
        if status != "all" and _entry_status(entry) != status:
            continue
        items.append(_summary(key, entry))
        if len(items) == limit:
            break
    return {"status": "ok", "registry_digest": workspace_registry_digest(), "workspaces": items, "truncated": len(items) < len(registry)}


def open_workspace(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = args.get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id or len(workspace_id) > 128:
        return {"status": "error", "error_code": "workspace_context_missing", "next_actions": next_actions()}
    registry = _load_registry(); entry = registry.get(workspace_id)
    if entry is None:
        return {"status": "error", "error_code": "workspace_not_found", "next_actions": next_actions()}
    summary = _summary(workspace_id, entry)
    projects: list[dict[str, Any]] = []
    try:
        root = resolve_workspace(workspace_id)
        records, _, _, _ = _scan_sources(root)
        projects = [{"project_id": r["project_id"], "status": r["status"], "kind": r["kind"], "root": r["root"], "summary": r["summary"][:500]} for r in records[:MAX_ITEMS]]
    except WorkspaceError:
        pass
    return {"status": "ok", "workspace_id": workspace_id, "canonical_root": summary["canonical_root"], "registry_entry": {"status": _entry_status(entry)}, "registry_status": "valid" if summary["registry_valid"] else "invalid", "project_registry_status": summary["project_registry_status"], "projects": projects, "warnings": summary["warnings"]}


def next_actions() -> list[str]:
    return ["use active Profile Task context", "use frozen SPEC context", "provide user-supplied workspace_id", "call aota_workspace_list", "request Project Steward relationship resolution"]


def resolve_workspace_context(workspace_id: str, project_id: str) -> dict[str, Any]:
    """Return the complete frozen binding or raise a stable WorkspaceError."""
    if not workspace_id:
        raise WorkspaceError("workspace_context_missing")
    if not project_id:
        raise WorkspaceError("project_not_found")
    root = resolve_workspace(workspace_id)
    if root == Path("/aota-runtime"):
        raise WorkspaceError("runtime_root_not_project_workspace")
    records, _, duplicates, _ = _scan_sources(root)
    if project_id in duplicates:
        raise WorkspaceError("project_id_ambiguous")
    project = next((record for record in records if record["project_id"] == project_id), None)
    if project is None:
        raise WorkspaceError("project_not_found")
    state = registry_status(workspace_id, root)
    return {"schema_version": 1, "workspace_id": workspace_id, "canonical_root": str(root),
            "workspace_registry_digest": workspace_registry_digest(), "project_id": project_id,
            "project_root": str((root / project["root"]).resolve()), "manifest_path": project["manifest_path"],
            "project_manifest_digest": project["manifest_sha256"], "project_registry_revision": state.get("registry_revision"),
            "relationship": None, "source_type": None, "recommendation_id": None, "decision_id": None}


def validate_workspace_context(context: Any) -> dict[str, Any]:
    if not isinstance(context, dict):
        raise WorkspaceError("workspace_context_required")
    required = {"schema_version", "workspace_id", "canonical_root", "workspace_registry_digest", "project_id", "project_root", "manifest_path", "project_manifest_digest", "project_registry_revision", "relationship", "source_type", "recommendation_id", "decision_id"}
    frozen = required | {"frozen_at", "frozen_by"}
    if set(context) not in {frozenset(required), frozenset(frozen)} or context.get("schema_version") != 1:
        raise WorkspaceError("binding_invalid")
    actual = resolve_workspace_context(context["workspace_id"], context["project_id"])
    for key in ("canonical_root", "workspace_registry_digest", "project_root", "manifest_path", "project_manifest_digest", "project_registry_revision"):
        if actual[key] != context.get(key):
            raise WorkspaceError("registry_revision_mismatch" if key.endswith("revision") or key.endswith("digest") else "binding_invalid")
    return context


def context_from_selection(workspace_id: str, project_id: str, decision_id: str) -> dict[str, Any]:
    """Read and revalidate a task-main workspace_selection decision."""
    from ._orchestration_common import find_decision_path, read_decision
    path = find_decision_path(workspace_id, decision_id)
    if path is None:
        raise WorkspaceError("workspace_decision_not_found")
    record = read_decision(path)
    if record.get("decision_type") != "workspace_selection" or record.get("decided_by") != "task-main":
        raise WorkspaceError("workspace_selection_authority_denied")
    context = record.get("workspace_context")
    if not isinstance(context, dict) or context.get("project_id") != project_id:
        raise WorkspaceError("context_binding_mismatch")
    return validate_workspace_context(context)


def handle_list(args: dict[str, Any], **_kwargs: Any) -> str:
    return json.dumps(list_workspaces(args), ensure_ascii=False, sort_keys=True)


def handle_open(args: dict[str, Any], **_kwargs: Any) -> str:
    return json.dumps(open_workspace(args), ensure_ascii=False, sort_keys=True)
