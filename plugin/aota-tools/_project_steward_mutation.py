"""Minimal trusted Project Steward metadata and documentation mutation tools."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ._project_common import ProjectError, load_project
from ._project_registry import load_search_records
from ._spec_contract import ContractError, canonical_hash, validate_spec
from ._workspace import WorkspaceError, resolve_workspace

DOCS_TOOL_NAME = "aota_project_docs_update"
ARTIFACT_TOOL_NAME = "aota_project_artifact_link"
TOOLSET_NAME = "aota_project_steward"
_DOC_KINDS = {"readme", "changelog", "roadmap", "project_doc"}
_RELATIONS = {"plan", "spec", "result", "review", "diagnosis", "architecture_review", "steward_result", "supersedes", "follow_up"}

DOCS_SCHEMA = {"name": DOCS_TOOL_NAME, "description": "Trusted Project Steward-only bounded documentation update.", "parameters": {"type": "object", "properties": {"workspace_id": {"type": "string"}, "project_id": {"type": "string"}, "document_kind": {"type": "string", "enum": sorted(_DOC_KINDS)}, "operation": {"type": "string", "enum": ["replace", "append"]}, "content": {"type": "string", "maxLength": 32768}, "document_ref": {"type": "string", "maxLength": 256}, "expected_sha256": {"type": "string", "maxLength": 64}}, "required": ["workspace_id", "project_id", "document_kind", "operation", "content"], "additionalProperties": False}}
ARTIFACT_SCHEMA = {"name": ARTIFACT_TOOL_NAME, "description": "Trusted Project Steward-only bounded PCF artifact link mutation.", "parameters": {"type": "object", "properties": {"workspace_id": {"type": "string"}, "project_id": {"type": "string"}, "work_item_id": {"type": "string", "maxLength": 128}, "artifact_type": {"type": "string", "enum": sorted(_RELATIONS - {"supersedes", "follow_up"})}, "artifact_ref": {"type": "string", "maxLength": 512}, "relation": {"type": "string", "enum": sorted(_RELATIONS)}, "expected_index_sha256": {"type": "string", "maxLength": 64}}, "required": ["workspace_id", "project_id", "work_item_id", "artifact_type", "artifact_ref", "relation"], "additionalProperties": False}}


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try: os.unlink(temp_name)
        except OSError: pass
        raise


def _safe_text(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise ValueError(f"invalid {name}")
    return value


def _trusted_steward(workspace_id: str, project_id: str, required_operation: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    if os.environ.get("AOTA_PROFILE_TASK_PROFILE") != "project-steward":
        raise PermissionError("profile_not_allowed")
    task_id = os.environ.get("AOTA_PROFILE_TASK_ID", "")
    start_id = os.environ.get("AOTA_PROFILE_TASK_START_ID", "")
    trusted_workspace = os.environ.get("AOTA_PROFILE_TASK_WORKSPACE_ID", "")
    if not task_id or not start_id or workspace_id != trusted_workspace:
        raise PermissionError("trusted_task_context_mismatch")
    task_root = Path(os.environ.get("AOTA_PROFILE_TASK_ROOT", "/aota-runtime/profile-tasks"))
    try: meta = json.loads((task_root / workspace_id / task_id / "meta.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc: raise RuntimeError("task_meta_unavailable") from exc
    spec = meta.get("spec", {})
    if meta.get("contract_version") == 1:
        try:
            validate_spec(spec, frozen=True)
        except ContractError as exc:
            raise PermissionError("invalid_stewardship_spec") from exc
        contract = dict(spec.get("payload", {}))
        contract["project_id"] = spec.get("project_id")
        if meta.get("spec_hash") != spec.get("spec_hash") or canonical_hash(spec) != spec.get("spec_hash"):
            raise PermissionError("stewardship_spec_hash_mismatch")
    else:
        contract = spec.get("role_contract", {})
    if meta.get("status") != "running" or meta.get("spec_kind", meta.get("task_kind")) != "stewardship" or meta.get("resolved_profile", "project-steward") != "project-steward" or meta.get("execution", {}).get("start_id") != start_id or contract.get("project_id") != project_id or contract.get("operation") != required_operation:
        raise PermissionError("stewardship_spec_binding_mismatch")
    root = resolve_workspace(workspace_id)
    records, _ = load_search_records(workspace_id, root)
    record = next((item for item in records if item.get("project_id") == project_id), None)
    if record is None: raise ValueError("unregistered_project")
    project_root = (root / record["root"]).resolve(strict=False)
    try: project_root.relative_to(root.resolve())
    except ValueError as exc: raise ValueError("project_path_escape") from exc
    _, manifest = load_project(project_root / ".aota" / "project.yaml")
    return project_root, manifest, contract


def _target_for_document(project_root: Path, manifest: dict[str, Any], contract: dict[str, Any], kind: str, document_ref: Any) -> Path:
    allowed = set(contract.get("allowed_project_artifacts", []))
    if kind not in allowed: raise PermissionError("document_not_authorized_by_spec")
    names = {"readme": "README.md", "changelog": "CHANGELOG.md", "roadmap": "ROADMAP.md"}
    if kind in names:
        target = project_root / names[kind]
        scope = set(contract.get("docs_update_scope", []))
        if scope and target.relative_to(project_root).as_posix() not in scope:
            raise PermissionError("document_not_in_docs_update_scope")
        return target
    ref = _safe_text(document_ref, "document_ref", 256)
    rel = Path(ref)
    if rel.is_absolute() or ".." in rel.parts or "\\" in ref: raise ValueError("path_escape")
    target = project_root / rel
    scope = set(contract.get("docs_update_scope", []))
    if scope and target.relative_to(project_root).as_posix() not in scope:
        raise PermissionError("document_not_in_docs_update_scope")
    roots = [project_root / value for value in manifest["paths"]["docs"]]
    if not any(target.resolve(strict=False).is_relative_to(root.resolve(strict=False)) for root in roots): raise ValueError("project_doc_not_in_declared_docs_root")
    if not target.exists() or target.is_symlink() or not target.is_file(): raise ValueError("project_doc_must_be_existing_regular_file")
    return target


def handle_docs_update(args: dict, **_kwargs: Any) -> str:
    try:
        if set(args) != set(DOCS_SCHEMA["parameters"]["properties"]) - {"document_ref", "expected_sha256"} and not set(args).issubset(DOCS_SCHEMA["parameters"]["properties"]): raise ValueError("unknown field")
        workspace_id = _safe_text(args.get("workspace_id"), "workspace_id", 128); project_id = _safe_text(args.get("project_id"), "project_id", 96)
        kind = args.get("document_kind"); operation = args.get("operation"); content = args.get("content")
        if kind not in _DOC_KINDS or operation not in {"replace", "append"} or not isinstance(content, str) or len(content.encode("utf-8")) > 32768 or "\x00" in content: raise ValueError("invalid docs update")
        root, manifest, contract = _trusted_steward(workspace_id, project_id, "docs_update")
        target = _target_for_document(root, manifest, contract, kind, args.get("document_ref"))
        if target.exists() and target.is_symlink(): raise ValueError("symlink_rejected")
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        current_hash = hashlib.sha256(current.encode()).hexdigest()
        expected = args.get("expected_sha256")
        if expected is not None and (not isinstance(expected, str) or expected != current_hash): raise ValueError("expected_revision_conflict")
        _atomic_write(target, content if operation == "replace" else current + content)
        return json.dumps({"status": "updated", "document_kind": kind, "document_ref": target.relative_to(root).as_posix(), "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}, sort_keys=True)
    except (PermissionError, WorkspaceError, ProjectError, ValueError) as exc: return json.dumps({"status": "rejected", "error": str(exc)}, sort_keys=True)
    except Exception as exc: return json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True)


def handle_artifact_link(args: dict, **_kwargs: Any) -> str:
    try:
        if not set(args).issubset(ARTIFACT_SCHEMA["parameters"]["properties"]): raise ValueError("unknown field")
        workspace_id = _safe_text(args.get("workspace_id"), "workspace_id", 128); project_id = _safe_text(args.get("project_id"), "project_id", 96)
        work_item_id = _safe_text(args.get("work_item_id"), "work_item_id", 128); artifact_ref = _safe_text(args.get("artifact_ref"), "artifact_ref", 512)
        artifact_type = args.get("artifact_type"); relation = args.get("relation")
        if artifact_type not in _RELATIONS - {"supersedes", "follow_up"} or relation not in _RELATIONS: raise ValueError("invalid artifact relation")
        root, _manifest, contract = _trusted_steward(workspace_id, project_id, "artifact_link")
        if "project_metadata" not in set(contract.get("allowed_project_artifacts", [])): raise PermissionError("artifact_link_not_authorized_by_spec")
        index_path = root / ".aota" / "artifacts" / "index.json"
        if index_path.exists() and (index_path.is_symlink() or not index_path.is_file()): raise ValueError("artifact_index_unsafe")
        current = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
        expected = args.get("expected_index_sha256")
        if expected is not None and expected != hashlib.sha256(current.encode()).hexdigest(): raise ValueError("expected_revision_conflict")
        data = json.loads(current) if current else {"schema_version": 1, "project_id": project_id, "links": []}
        if not isinstance(data, dict) or set(data) != {"schema_version", "project_id", "links"} or data.get("schema_version") != 1 or data.get("project_id") != project_id or not isinstance(data["links"], list) or len(data["links"]) >= 100: raise ValueError("artifact_index_invalid")
        entry = {"work_item_id": work_item_id, "artifact_type": artifact_type, "artifact_ref": artifact_ref, "relation": relation}
        if entry not in data["links"]: data["links"].append(entry)
        _atomic_write(index_path, json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        return json.dumps({"status": "linked", "link_count": len(data["links"])}, sort_keys=True)
    except (PermissionError, WorkspaceError, ProjectError, ValueError, json.JSONDecodeError) as exc: return json.dumps({"status": "rejected", "error": str(exc)}, sort_keys=True)
    except Exception as exc: return json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True)
