"""aota_profile_task_approve — record explicit human checkpoint approval for a draft task.

P8-C: Human Checkpoint Enforcement.
Creates APPROVAL.json for implementation tasks only.
Does not start execution, modify SPEC, choose profile, or create another task.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from ._task_spec_common import (
    STATUS_DRAFT,
    TASK_KIND_PROFILE_HINT,
    acquire_lock,
    compute_sha256,
    get_task_dir,
    load_meta,
    load_spec_md,
    release_lock,
    utc_now_iso,
    write_json,
)
from ._workspace import WorkspaceError

TOOL_NAME = "aota_profile_task_approve"
TOOLSET_NAME = "aota_profile_task"

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Record explicit human checkpoint approval for a draft AOTA task. "
        "Creates APPROVAL.json in the task directory. "
        "Only for implementation tasks (diagnosis/review do not require approval). "
        "This tool does not start execution, modify SPEC, choose profile, or create "
        "another task. Call this only when the current human reviewer has explicitly "
        "approved the exact SPEC revision/hash for execution. "
        "Approval is bound to exact revision+hash; spec updates invalidate approval."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {
                "type": "string",
                "description": "Registered workspace identifier (e.g. 'aota-runtime')",
            },
            "task_id": {
                "type": "string",
                "description": "Existing AOTA task ID to approve",
            },
            "expected_revision": {
                "type": "integer",
                "description": "Expected SPEC revision number for optimistic locking",
            },
            "expected_spec_sha256": {
                "type": "string",
                "description": "Expected SHA-256 hex digest of the exact SPEC.md content",
            },
        },
        "required": [
            "workspace_id",
            "task_id",
            "expected_revision",
            "expected_spec_sha256",
        ],
        "additionalProperties": False,
    },
}


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def handle(args: dict, **_kwargs) -> str:
    try:
        return _do_approve(args)
    except WorkspaceError as e:
        return json.dumps(
            {"status": "rejected", "error": str(e)}, sort_keys=True
        )
    except Exception as e:
        return json.dumps(
            {"status": "failed", "error": str(e)}, sort_keys=True
        )


def _do_approve(args: dict) -> str:
    workspace_id: str = args.get("workspace_id", "")
    task_id: str = args.get("task_id", "")
    expected_revision: int = args.get("expected_revision", 0)
    expected_spec_sha256: str = args.get("expected_spec_sha256", "")

    # 1. Locate task directory
    task_dir = get_task_dir(workspace_id, task_id)
    if not task_dir.is_dir():
        raise WorkspaceError(
            f"task_not_found: task '{task_id}' not found in workspace '{workspace_id}'"
        )

    # 2. Load meta
    meta = load_meta(task_dir)
    spec_md = load_spec_md(task_dir)

    # 3. Verify meta.task_id matches
    if meta.get("task_id") != task_id:
        raise WorkspaceError("task_id_mismatch: meta.task_id does not match input")

    # 4. Verify meta.workspace_id matches
    if meta.get("workspace_id") != workspace_id:
        raise WorkspaceError("workspace_id_mismatch: meta.workspace_id does not match input")

    # 5. Only implementation tasks require approval
    task_kind = meta.get("task_kind", "")
    if task_kind != "implementation":
        raise WorkspaceError(
            f"approval_not_required: task_kind={task_kind} does not require approval "
            f"(only implementation tasks require human checkpoint)"
        )

    # 6. Status must be draft
    current_status = meta.get("status", "")
    if current_status != STATUS_DRAFT:
        raise WorkspaceError(
            f"task_not_approvable: task '{task_id}' has status "
            f"'{current_status}', expected '{STATUS_DRAFT}'"
        )

    # 7. Verify revision
    current_revision: int = meta.get("revision", 0)
    if expected_revision != current_revision:
        raise WorkspaceError(
            f"revision_conflict: expected_revision={expected_revision}, "
            f"actual_revision={current_revision}"
        )

    # 8. Compute actual SPEC SHA-256
    actual_spec_sha256 = compute_sha256(spec_md)

    # 8a. Verify integrity
    stored_hash = meta.get("spec_sha256", "")
    if actual_spec_sha256 != stored_hash:
        raise WorkspaceError(
            f"artifact_integrity_mismatch: stored={stored_hash}, "
            f"actual={actual_spec_sha256}"
        )

    # 8b. Verify expected hash
    if expected_spec_sha256 != actual_spec_sha256:
        raise WorkspaceError(
            f"spec_hash_conflict: expected={expected_spec_sha256}, "
            f"actual={actual_spec_sha256}"
        )

    # 9. Acquire lock
    lock_fd = acquire_lock(workspace_id, task_id, timeout=5.0)
    try:
        # 9a. Re-load under lock
        meta_after_lock = load_meta(task_dir)

        # Re-check status under lock
        if meta_after_lock.get("status") != STATUS_DRAFT:
            raise WorkspaceError(
                f"task_not_approvable: status changed to "
                f"'{meta_after_lock.get('status')}' during lock acquisition"
            )

        # 9b. Check if APPROVAL.json already exists for same revision/hash
        approval_path = task_dir / "APPROVAL.json"
        now = utc_now_iso()

        if approval_path.exists():
            try:
                existing_approval = json.loads(approval_path.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                # Corrupt — overwrite
                existing_approval = {}

            existing_rev = existing_approval.get("revision")
            existing_hash = existing_approval.get("spec_sha256")
            if existing_rev == current_revision and existing_hash == actual_spec_sha256:
                # Idempotent: same revision/hash already approved
                return json.dumps(
                    {
                        "status": "already_approved",
                        "task_id": task_id,
                        "workspace_id": workspace_id,
                        "revision": current_revision,
                        "spec_sha256": actual_spec_sha256,
                        "approval_id": existing_approval.get("approval_id", ""),
                        "approved_at": existing_approval.get("approved_at", ""),
                        "approval_source": "explicit_tool_invocation",
                    },
                    sort_keys=True,
                )

        # 9c. Create approval artifact
        approval_id = f"ap_{utc_now_iso().replace(':', '').replace('-', '').replace('Z', '')}_{secrets.token_hex(4)}"

        approval = {
            "schema_version": 1,
            "approval_id": approval_id,
            "workspace_id": workspace_id,
            "task_id": task_id,
            "revision": current_revision,
            "spec_sha256": actual_spec_sha256,
            "approved_at": now,
            "approval_source": "explicit_tool_invocation",
            "task_kind": task_kind,
        }

        # Atomic write
        write_json(approval_path, approval)

        return json.dumps(
            {
                "status": "approved",
                "task_id": task_id,
                "workspace_id": workspace_id,
                "revision": current_revision,
                "spec_sha256": actual_spec_sha256,
                "approval_id": approval_id,
                "approved_at": now,
                "approval_source": "explicit_tool_invocation",
            },
            sort_keys=True,
        )
    finally:
        release_lock(lock_fd)
