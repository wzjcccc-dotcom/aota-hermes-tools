"""Freeze a canonical WI-09C SPEC revision without allowing in-place edits."""
from __future__ import annotations

import json

from ._task_spec_common import acquire_lock, atomic_write, compute_sha256, get_task_dir, load_meta, release_lock, utc_now_iso, write_json
from ._workspace import WorkspaceError
from ._spec_contract import ContractError, canonical_hash, validate_spec

TOOL_NAME = "aota_task_spec_freeze"
TOOLSET_NAME = "aota_task_spec"
SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Freeze one validated canonical SPEC revision. Frozen content is immutable.\n\n"
        "CANONICAL vs LEGACY SPEC:\n"
        "- Canonical SPEC: has contract_version=1 in meta.json. Uses WI-09C "
        "envelope with spec_id, spec_kind, spec_hash, workspace_context. "
        "CAN be frozen. After freeze, spec_hash is computed via canonical_hash() "
        "and the status becomes 'frozen'.\n"
        "- Legacy SPEC: does NOT have contract_version=1. Uses older "
        "task_kind-based envelope without spec_hash. CANNOT be frozen by this "
        "tool. When attempting to freeze a legacy SPEC, error includes "
        "detected_format=legacy and required_action=create_or_migrate_canonical_spec.\n\n"
        "FREEZE REQUIREMENTS:\n"
        "- SPEC must be a canonical SPEC (contract_version=1)\n"
        "- SPEC must have status='draft'\n"
        "- expected_revision must match the current revision\n"
        "- If already frozen at the same revision, returns idempotent success\n\n"
        "VALID EXAMPLE:\n"
        '  {"workspace_id": "my-ws", "spec_id": "pt_20260721T120000_abcdef01", '
        '"expected_revision": 3}\n'
        "  → returns {status: frozen, spec_hash: <64-char hex>, ...}\n\n"
        "LEGACY ERROR EXAMPLE:\n"
        '  {"workspace_id": "my-ws", "spec_id": "pt_...", "expected_revision": 1}\n'
        "  → error: legacy SPEC has no canonical freeze contract. "
        "detected_format=legacy. required_action=create_or_migrate_canonical_spec. "
        "Create a new canonical SPEC and migrate content."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {"type": "string", "description": "Registered workspace identifier."},
            "spec_id": {"type": "string", "description": "Canonical SPEC ID to freeze. Must be a WI-09C SPEC with contract_version=1."},
            "expected_revision": {"type": "integer", "description": "Expected current revision number for optimistic locking. Must match the SPEC's current revision."},
        },
        "required": ["workspace_id", "spec_id", "expected_revision"],
        "additionalProperties": False,
    },
}

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
    if not task_dir.is_dir(): raise WorkspaceError("spec not found: " + spec_id)
    lock = acquire_lock(workspace_id, spec_id, timeout=5.0)
    try:
        meta = load_meta(task_dir)
        if meta.get("contract_version") != 1:
            raise WorkspaceError(
                "legacy SPEC has no canonical freeze contract. "
                "detected_format=legacy. "
                "contract_version=" + str(meta.get("contract_version", "missing")) + ". "
                "required_action=create_or_migrate_canonical_spec. "
                "Create a new canonical SPEC with spec_kind and migrate content, "
                "then freeze the new canonical SPEC."
            )
        if meta.get("status") == "frozen":
            if meta.get("revision") == expected:
                return json.dumps({
                    "status": "frozen", "spec_id": spec_id, "revision": expected,
                    "spec_hash": meta.get("spec_hash"),
                    "approval_status": "required" if meta.get("spec_kind") == "implementation" else "not_required",
                    "idempotent": True,
                }, sort_keys=True)
            raise WorkspaceError(
                "frozen SPEC revision mismatch: already frozen at revision=" +
                str(meta.get("revision")) + ", expected=" + str(expected) +
                ". This SPEC is already frozen at a different revision."
            )
        if meta.get("status") != "draft" or meta.get("revision") != expected:
            raise WorkspaceError(
                "freeze requires the exact current draft revision. "
                "status=" + str(meta.get("status")) + ", "
                "revision=" + str(meta.get("revision")) + ", "
                "expected=" + str(expected) + ". "
                "Ensure the SPEC is in draft status and expected_revision matches."
            )
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
