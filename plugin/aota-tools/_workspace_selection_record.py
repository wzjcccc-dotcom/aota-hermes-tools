"""task-main's durable workspace-selection writer using the decision store."""
from __future__ import annotations

import json
from typing import Any

from ._orchestration_common import (acquire_decision_lock, atomic_write_json, generate_decision_id, get_decision_dir, get_decision_filename, release_decision_lock, utc_now_iso)
from ._workspace import WorkspaceError
from ._workspace_context import resolve_workspace_context

TOOL_NAME = "aota_workspace_selection_record"
TOOLSET_NAME = "aota_orchestration"
SCHEMA = {"name": TOOL_NAME, "description": "Record task-main's only durable workspace/project selection after registry and manifest validation.", "parameters": {"type": "object", "properties": {"workspace_id": {"type": "string"}, "project_id": {"type": "string"}, "source_type": {"type": "string", "enum": ["user_supplied", "steward_recommendation", "prior_decision"]}, "relationship": {"type": "string", "enum": ["existing", "adjacent", "new_project", "new_workspace"]}, "recommendation_id": {"type": ["string", "null"]}, "user_message_ref": {"type": ["string", "null"]}}, "required": ["workspace_id", "project_id", "source_type", "relationship"], "additionalProperties": False}}

def handle(args: dict[str, Any], **kwargs: Any) -> str:
    if kwargs.get("profile") not in {None, "task-main"}:
        return json.dumps({"status": "error", "error_code": "workspace_selection_authority_denied"}, sort_keys=True)
    try:
        context = resolve_workspace_context(args.get("workspace_id", ""), args.get("project_id", ""))
        relationship = args.get("relationship")
        if relationship not in {"existing", "adjacent", "new_project", "new_workspace"}:
            raise WorkspaceError("relationship_invalid")
        decision_id = generate_decision_id(); now = utc_now_iso()
        context.update({"relationship": relationship, "source_type": args.get("source_type"), "recommendation_id": args.get("recommendation_id"), "decision_id": decision_id, "frozen_at": now, "frozen_by": "task-main"})
        record = {"schema_version": 1, "decision_id": decision_id, "decision_type": "workspace_selection", "selected": {"workspace_id": context["workspace_id"], "project_id": context["project_id"], "canonical_root": context["canonical_root"], "project_root": context["project_root"], "manifest_path": context["manifest_path"]}, "source": {"type": args.get("source_type"), "recommendation_id": args.get("recommendation_id"), "user_message_ref": args.get("user_message_ref")}, "validation": {"workspace_registered": True, "project_exists": True, "project_belongs_to_workspace": True, "manifest_valid": True}, "relationship": {"type": relationship}, "registry": {"workspace_registry_digest": context["workspace_registry_digest"], "project_registry_revision": context["project_registry_revision"], "project_manifest_digest": context["project_manifest_digest"]}, "workspace_context": context, "decided_by": "task-main", "decided_at": now}
        lock = acquire_decision_lock(context["workspace_id"], decision_id)
        try:
            directory = get_decision_dir(context["workspace_id"]); directory.mkdir(parents=True, exist_ok=True)
            atomic_write_json(directory / get_decision_filename(decision_id), record)
        finally:
            release_decision_lock(lock)
        return json.dumps({"status": "recorded", "decision_id": decision_id, "workspace_context": context}, ensure_ascii=False, sort_keys=True)
    except WorkspaceError as exc:
        return json.dumps({"status": "error", "error_code": str(exc)}, sort_keys=True)
