"""Freeze a canonical WI-09C SPEC revision without allowing in-place edits."""
from __future__ import annotations

import json

from ._task_spec_common import acquire_lock, atomic_write, compute_sha256, get_task_dir, load_meta, release_lock, utc_now_iso, write_json
from ._workspace import WorkspaceError
from ._spec_contract import ContractError, canonical_hash, validate_spec

TOOL_NAME = "aota_task_spec_freeze"
TOOLSET_NAME = "aota_task_spec"
SCHEMA = {"name": TOOL_NAME, "description": "Freeze one validated canonical SPEC revision. Frozen content is immutable.", "parameters": {"type": "object", "properties": {"workspace_id": {"type": "string"}, "spec_id": {"type": "string"}, "expected_revision": {"type": "integer"}}, "required": ["workspace_id", "spec_id", "expected_revision"], "additionalProperties": False}}

def handle(args: dict, **_kwargs: object) -> str:
    try:
        return _do_freeze(args)
    except WorkspaceError as exc:
        return json.dumps({"status": "rejected", "error": str(exc)}, sort_keys=True)
    except Exception as exc:
        return json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True)

def _do_freeze(args: dict) -> str:
    workspace_id, spec_id, expected = args.get("workspace_id", ""), args.get("spec_id", ""), args.get("expected_revision")
    task_dir = get_task_dir(workspace_id, spec_id)
    if not task_dir.is_dir(): raise WorkspaceError("spec not found")
    lock = acquire_lock(workspace_id, spec_id, timeout=5.0)
    try:
        meta = load_meta(task_dir)
        if meta.get("contract_version") != 1: raise WorkspaceError("legacy SPEC has no canonical freeze contract")
        if meta.get("status") == "frozen":
            if meta.get("revision") == expected: return json.dumps({"status": "frozen", "spec_id": spec_id, "revision": expected, "spec_hash": meta.get("spec_hash"), "approval_status": "required" if meta.get("spec_kind") == "implementation" else "not_required", "idempotent": True}, sort_keys=True)
            raise WorkspaceError("frozen SPEC revision mismatch")
        if meta.get("status") != "draft" or meta.get("revision") != expected: raise WorkspaceError("freeze requires the exact current draft revision")
        spec = dict(meta.get("spec", {}))
        if spec.get("workspace_context") is not None:
            from ._workspace_context import validate_workspace_context
            validate_workspace_context(spec["workspace_context"])
        spec["status"] = "frozen"; spec["frozen_at"] = utc_now_iso(); spec["updated_at"] = spec["frozen_at"]
        spec["spec_hash"] = canonical_hash(spec)
        try: validate_spec(spec, frozen=True)
        except ContractError as exc: raise WorkspaceError(str(exc)) from exc
        spec_md = json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        meta.update(spec)
        meta.update({"spec": spec, "spec_hash": spec["spec_hash"], "spec_sha256": compute_sha256(spec_md), "frozen_revision": spec["revision"]})
        atomic_write(task_dir / "SPEC.md", spec_md); write_json(task_dir / "meta.json", meta)
        return json.dumps({"status": "frozen", "spec_id": spec_id, "revision": spec["revision"], "spec_hash": spec["spec_hash"], "approval_status": "required" if spec.get("spec_kind") == "implementation" else "not_required"}, sort_keys=True)
    finally:
        release_lock(lock)
