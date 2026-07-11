"""aota_task_spec_update — revise an existing draft AOTA task specification.

Requires optimistic revision matching. Does not approve or start execution.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ._task_spec_common import (
    MUTABLE_SPEC_FIELDS,
    TASK_KIND_PROFILE_HINT,
    HUMAN_CHECKPOINT_POLICY_MAP,
    SOURCE_MUTATION_POLICY_MAP,
    STATUS_DRAFT,
    STATUS_NEEDS_INPUT,
    utc_now_iso,
    compute_sha256,
    validate_scope_expressions,
    validate_semantic_rules,
    check_scope_overlap,
    validate_field_limits,
    render_spec_md,
    build_spec_dict,
    write_json,
    atomic_write,
    acquire_lock,
    release_lock,
    get_task_dir,
    load_meta,
    load_spec_md,
    WorkspaceError,
)

TOOL_NAME = "aota_task_spec_update"
TOOLSET_NAME = "aota_task_spec"

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Use to revise an existing draft AOTA task specification. "
        "Requires optimistic revision matching. "
        "Does not approve or start execution."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {
                "type": "string",
                "description": "Registered workspace identifier",
            },
            "task_id": {
                "type": "string",
                "description": "Existing task ID to update",
            },
            "expected_revision": {
                "type": "integer",
                "description": "Expected current revision for optimistic locking",
            },
            "title": {
                "type": "string",
                "description": "Updated title (max 200 characters)",
            },
            "goal": {
                "type": "string",
                "description": "Updated goal (max 8000 characters)",
            },
            "risk_level": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Updated risk level",
            },
            "known_inputs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Updated known inputs",
            },
            "read_scope": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Updated read scope globs",
            },
            "write_scope": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Updated write scope globs",
            },
            "forbidden_scope": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Updated forbidden scope globs",
            },
            "acceptance_criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Updated acceptance criteria",
            },
            "validation_policy": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Updated validation policy items",
            },
            "stop_conditions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Updated stop conditions",
            },
            "evidence_required": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Updated evidence required items",
            },
        },
        "required": ["workspace_id", "task_id", "expected_revision"],
        "additionalProperties": False,
    },
}


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handle(args: dict, **_kwargs) -> str:
    try:
        return _do_update(args)
    except WorkspaceError as e:
        return json.dumps(
            {"status": "rejected", "error": str(e)}, sort_keys=True
        )
    except Exception as e:
        return json.dumps(
            {"status": "failed", "error": str(e)}, sort_keys=True
        )


def _do_update(args: dict) -> str:
    # ------------------------------------------------------------------
    # Extract params
    # ------------------------------------------------------------------
    workspace_id: str = args.get("workspace_id", "")
    task_id: str = args.get("task_id", "")
    expected_revision: int = args.get("expected_revision", 0)

    # ------------------------------------------------------------------
    # 1. Resolve workspace
    # ------------------------------------------------------------------
    from ._workspace import resolve_workspace

    workspace_root = resolve_workspace(workspace_id)

    task_dir = get_task_dir(workspace_id, task_id)

    if not task_dir.is_dir():
        raise WorkspaceError(f"task '{task_id}' not found in workspace '{workspace_id}'")

    # ------------------------------------------------------------------
    # 2. Load existing meta.json + SPEC.md
    # ------------------------------------------------------------------
    existing_meta = load_meta(task_dir)
    existing_spec_md = load_spec_md(task_dir)

    # ------------------------------------------------------------------
    # 3. Check status == "draft" or "needs_input"
    # ------------------------------------------------------------------
    current_status = existing_meta.get("status", "")
    update_allowed = current_status in (STATUS_DRAFT, STATUS_NEEDS_INPUT)
    if not update_allowed:
        raise WorkspaceError(
            f"task_not_updatable: task '{task_id}' has status "
            f"'{current_status}', expected '{STATUS_DRAFT}' or '{STATUS_NEEDS_INPUT}'"
        )

    # ------------------------------------------------------------------
    # 4. Integrity check: compute current SPEC.md SHA-256
    # ------------------------------------------------------------------
    current_hash = compute_sha256(existing_spec_md)
    stored_hash = existing_meta.get("spec_sha256", "")
    if current_hash != stored_hash:
        raise WorkspaceError(
            f"artifact_integrity_mismatch: SPEC.md hash mismatch "
            f"(stored={stored_hash}, actual={current_hash})"
        )

    # ------------------------------------------------------------------
    # 5. Revision check
    # ------------------------------------------------------------------
    current_revision: int = existing_meta.get("revision", 0)
    if expected_revision != current_revision:
        raise WorkspaceError(
            f"revision_conflict: expected revision {expected_revision}, "
            f"actual revision {current_revision}"
        )

    # ------------------------------------------------------------------
    # 6. Acquire lock
    # ------------------------------------------------------------------
    lock_fd = acquire_lock(workspace_id, task_id, timeout=5.0)

    try:
        # ------------------------------------------------------------------
        # 7. Inside lock: re-load + re-validate
        # ------------------------------------------------------------------
        meta_after_lock = load_meta(task_dir)
        spec_md_after_lock = load_spec_md(task_dir)

        # Re-check integrity and revision inside lock
        hash_after_lock = compute_sha256(spec_md_after_lock)
        if hash_after_lock != meta_after_lock.get("spec_sha256", ""):
            raise WorkspaceError(
                "artifact_integrity_mismatch: SPEC.md changed between "
                "initial read and lock acquisition"
            )
        rev_after_lock: int = meta_after_lock.get("revision", 0)
        if rev_after_lock != current_revision:
            raise WorkspaceError(
                f"revision_conflict: revision changed from {current_revision} "
                f"to {rev_after_lock} during lock acquisition"
            )

        # ------------------------------------------------------------------
        # 8. Build patched spec dict
        # ------------------------------------------------------------------
        existing_spec = meta_after_lock["spec"]
        patched_spec = dict(existing_spec)

        # Also track top-level meta changes
        meta_updates: dict = {}
        if "risk_level" in args and args["risk_level"] is not None:
            meta_updates["risk_level"] = args["risk_level"]

        change_detected = False
        for key in MUTABLE_SPEC_FIELDS:
            if key in args and args[key] is not None:
                if patched_spec.get(key) != args[key]:
                    patched_spec[key] = args[key]
                    change_detected = True

        # Also apply risk_level to patched_spec for re-validation
        # (risk_level appears in SPEC.md Identity section but not in spec dict)
        # Actually, risk_level is in meta only, not in spec dict.
        # It does appear in SPEC.md though, so we need it for rendering.
        new_risk_level = meta_updates.get("risk_level") or existing_meta.get("risk_level", "low")

        # Check that at least one mutable field changed
        if not change_detected and "risk_level" not in meta_updates:
            raise WorkspaceError(
                f"no_change: no mutable field was actually changed"
            )

        # ------------------------------------------------------------------
        # 9. Validate scope expressions on changed scope fields
        # ------------------------------------------------------------------
        scope_fields = {
            "read_scope": patched_spec.get("read_scope", []),
            "write_scope": patched_spec.get("write_scope", []),
            "forbidden_scope": patched_spec.get("forbidden_scope", []),
        }
        for key, items in scope_fields.items():
            err = validate_scope_expressions(items)
            if err:
                raise WorkspaceError(f"{key}: {err}")

        # ------------------------------------------------------------------
        # 10. Full semantic re-validation
        # ------------------------------------------------------------------
        task_kind: str = existing_meta.get("task_kind", "")
        new_write_scope = patched_spec.get("write_scope", [])
        # subject_task_id cannot change via update, use existing
        subject_task_id = existing_meta.get("subject_task_id")

        err = validate_semantic_rules(task_kind, new_write_scope, subject_task_id)
        if err:
            raise WorkspaceError(err)

        # Scope overlap check
        new_forbidden_scope = patched_spec.get("forbidden_scope", [])
        err = check_scope_overlap(new_write_scope, new_forbidden_scope)
        if err:
            raise WorkspaceError(err)

        # ------------------------------------------------------------------
        # 11. Validate field limits
        # ------------------------------------------------------------------
        err = validate_field_limits(
            title=patched_spec.get("title", ""),
            goal=patched_spec.get("goal", ""),
            known_inputs=patched_spec.get("known_inputs", []),
            read_scope=patched_spec.get("read_scope", []),
            write_scope=new_write_scope,
            forbidden_scope=new_forbidden_scope,
            acceptance_criteria=patched_spec.get("acceptance_criteria", []),
            validation_policy=patched_spec.get("validation_policy", []),
            stop_conditions=patched_spec.get("stop_conditions", []),
            evidence_required=patched_spec.get("evidence_required", []),
        )
        if err:
            raise WorkspaceError(err)

        # ------------------------------------------------------------------
        # 12. Render new SPEC.md
        # ------------------------------------------------------------------
        profile_hint = TASK_KIND_PROFILE_HINT.get(task_kind, "coder")
        new_revision = current_revision + 1
        new_status = STATUS_DRAFT
        now = utc_now_iso()
        created_at = existing_meta.get("created_at", now)

        new_spec_md = render_spec_md(
            task_id=task_id,
            workspace_id=workspace_id,
            task_kind=task_kind,
            profile_hint=profile_hint,
            risk_level=new_risk_level,
            revision=new_revision,
            status=new_status,
            goal=patched_spec.get("goal", ""),
            known_inputs=patched_spec.get("known_inputs", []),
            read_scope=patched_spec.get("read_scope", []),
            write_scope=new_write_scope,
            forbidden_scope=new_forbidden_scope,
            acceptance_criteria=patched_spec.get("acceptance_criteria", []),
            validation_policy=patched_spec.get("validation_policy", []),
            stop_conditions=patched_spec.get("stop_conditions", []),
            evidence_required=patched_spec.get("evidence_required", []),
            subject_task_id=subject_task_id,
            parent_task_id=existing_meta.get("parent_task_id"),
        )

        # Size check
        spec_md_bytes = len(new_spec_md.encode("utf-8"))
        if spec_md_bytes > 65536:
            raise WorkspaceError(
                f"SPEC.md exceeds 65536 bytes (got {spec_md_bytes})"
            )

        # ------------------------------------------------------------------
        # 13. Compute new hash
        # ------------------------------------------------------------------
        new_spec_sha256 = compute_sha256(new_spec_md)

        # ------------------------------------------------------------------
        # 14. Build updated meta
        # ------------------------------------------------------------------
        human_checkpoint_policy = HUMAN_CHECKPOINT_POLICY_MAP.get(
            task_kind, "required_before_start"
        )
        source_mutation_policy = SOURCE_MUTATION_POLICY_MAP.get(
            task_kind, "forbidden"
        )

        new_meta = dict(meta_after_lock)
        new_meta["revision"] = new_revision
        new_meta["updated_at"] = now
        new_meta["risk_level"] = new_risk_level
        new_meta["spec_sha256"] = new_spec_sha256
        new_meta["spec"] = patched_spec
        new_meta["status"] = new_status

        # Preserve created_at
        new_meta["created_at"] = created_at

        # ------------------------------------------------------------------
        # 14a. P8-C: If transitioning from needs_input → draft,
        # clear needs_input authoritative fields and invalidate old approval
        # ------------------------------------------------------------------
        if current_status == STATUS_NEEDS_INPUT:
            # Preserve execution as history but clear active fields
            exec_copy = dict(new_meta.get("execution", {}))
            exec_copy.pop("worker_outcome", None)
            exec_copy.pop("needs_input_reason", None)
            exec_copy.pop("needs_input_at", None)
            new_meta["execution"] = exec_copy if exec_copy else None
            # Remove stale APPROVAL.json
            approval_path = task_dir / "APPROVAL.json"
            try:
                approval_path.unlink()
            except OSError:
                pass

        # ------------------------------------------------------------------
        # 15. Atomic write
        # ------------------------------------------------------------------
        spec_path = task_dir / "SPEC.md"
        meta_path = task_dir / "meta.json"
        atomic_write(spec_path, new_spec_md)
        write_json(meta_path, new_meta)

    finally:
        release_lock(lock_fd)

    # ------------------------------------------------------------------
    # 16. Return compact output
    # ------------------------------------------------------------------
    return json.dumps(
        {
            "status": "updated",
            "task_id": task_id,
            "workspace_id": workspace_id,
            "task_kind": task_kind,
            "profile_hint": profile_hint,
            "risk_level": new_risk_level,
            "revision": new_revision,
            "spec_path": str(spec_path),
            "meta_path": str(meta_path),
            "spec_sha256": new_spec_sha256,
            "human_checkpoint_policy": human_checkpoint_policy,
            "error": None,
        },
        sort_keys=True,
    )
