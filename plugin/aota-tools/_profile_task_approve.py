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
        "Record explicit human checkpoint approval for an exact frozen AOTA revision. "
        "Creates APPROVAL.json in the task directory. "
        "Only for implementation tasks (diagnosis/review do not require approval). "
        "This tool does not start execution, modify SPEC, choose profile, or create "
        "another task. Call this only when the current human reviewer has explicitly "
        "approved the exact SPEC revision/hash for execution. "
        "Approval is bound to exact revision+hash; spec updates invalidate approval.\n\n"
        "REQUIRED PARAMETERS:\n"
        "- expected_spec_hash (REQUIRED for canonical WI-09C SPECs): The exact "
        "canonical frozen SPEC hash returned by aota_task_spec_freeze. This is the "
        "spec_hash field from the freeze response (64-char hex string).\n"
        "- expected_revision (REQUIRED): The exact frozen SPEC revision number.\n"
        "- workspace_id, task_id: Task identification.\n\n"
        "DEPRECATED PARAMETER:\n"
        "- expected_spec_sha256: This is a LEGACY binding (raw SHA-256 of SPEC.md "
        "file content), NOT the canonical freeze spec_hash. Do NOT use this for "
        "canonical WI-09C SPECs. Use expected_spec_hash instead.\n\n"
        "HASH CONFLICT RESOLUTION:\n"
        "If the SPEC has been updated/re-frozen since you last read it, the hash "
        "will not match. Error: spec_hash_conflict with expected vs actual hash.\n"
        "Corrective action: re-read the SPEC from the task directory to get the "
        "current spec_hash, then re-approve with that hash. If the revision "
        "has also changed, re-read and use the current revision.\n\n"
        "VALID EXAMPLE (canonical WI-09C SPEC):\n"
        "  workspace_id: my-ws, task_id: pt_20260721T120000_abcdef01, "
        "expected_revision: 3, "
        "expected_spec_hash: 4d05d6b4cdd3907b2d3e8674f699951064b8d3e8df868704b5c83a750f141dbc\n"
        "  -> returns {status: approved, spec_sha256: <canonical spec_hash>, ...}\n\n"
        "VALID EXAMPLE (legacy non-WI-09C SPEC):\n"
        "  workspace_id: my-ws, task_id: pt_..., "
        "expected_revision: 3, "
        "expected_spec_sha256: abcd1234...\n"
        "  -> returns {status: approved, spec_sha256: <file hash>, ...}\n\n"
        "ERROR EXAMPLE (hash mismatch):\n"
        "  workspace_id: my-ws, task_id: pt_..., "
        "expected_revision: 3, expected_spec_hash: wrong_hash\n"
        "  -> error: spec_hash_conflict: expected=<wrong>, actual=<correct>. "
        "The SPEC may have been re-frozen. Re-read the current frozen SPEC hash "
        "and retry with the correct value."
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
                "description": "Expected SPEC revision number for optimistic locking. Must match the frozen SPEC's current revision exactly.",
            },
            "expected_spec_sha256": {
                "type": "string",
                "description": "DEPRECATED for canonical WI-09C SPECs. Raw SHA-256 of SPEC.md file content (legacy binding). For canonical SPECs, use expected_spec_hash instead. This is NOT the freeze return spec_hash.",
            },
            "expected_spec_hash": {
                "type": "string",
                "description": "REQUIRED for canonical WI-09C SPECs. The exact canonical frozen SPEC hash (spec_hash from aota_task_spec_freeze response). 64-char hex string. This is the authoritative hash for approval binding.",
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
SCHEMA["description"] += " For canonical WI-09C SPECs (contract_version=1), expected_spec_hash is required and expected_spec_sha256 is deprecated."
SCHEMA["parameters"]["required"] = ["workspace_id", "task_id", "expected_revision"]


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
    expected_spec_sha256: str = args.get("expected_spec_hash") or args.get("expected_spec_sha256", "")

    # 1. Locate task directory
    task_dir = get_task_dir(workspace_id, task_id)
    if not task_dir.is_dir():
        raise WorkspaceError(
            f"task_not_found: task '{task_id}' not found in workspace '{workspace_id}'"
        )

    # 2. Load meta
    meta = load_meta(task_dir)
    spec_md = load_spec_md(task_dir)
    contract = meta.get("contract_version") == 1

    # For canonical SPECs, expected_spec_hash is required; expected_spec_sha256 is legacy
    if contract and not args.get("expected_spec_hash"):
        raise WorkspaceError(
            "expected_spec_hash_required: canonical WI-09C SPECs (contract_version=1) require "
            "expected_spec_hash (the frozen spec_hash from aota_task_spec_freeze), not "
            "expected_spec_sha256 (which is the legacy raw file hash). "
            "Re-read the frozen SPEC's spec_hash from the freeze response and retry."
        )
    if contract and args.get("expected_spec_sha256") and not args.get("expected_spec_hash"):
        raise WorkspaceError(
            "expected_spec_sha256_is_legacy: expected_spec_sha256 is the legacy raw file hash "
            "binding, not the canonical frozen spec_hash. For canonical WI-09C SPECs, "
            "use expected_spec_hash with the value returned by aota_task_spec_freeze. "
            "The expected_spec_sha256 parameter is only valid for legacy non-WI-09C SPECs."
        )

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

    # 6. Status must be draft with a frozen current revision
    current_status = meta.get("status", "")
    if (contract and current_status != "frozen") or (not contract and (current_status != STATUS_DRAFT or meta.get("frozen_revision") != meta.get("revision"))):
        raise WorkspaceError(
            f"task_not_approvable: task '{task_id}' requires its current draft revision to be frozen. "
            f"For canonical SPECs: status must be 'frozen' (current: {current_status}). "
            f"For legacy SPECs: status must be 'draft' with frozen_revision matching revision."
        )

    # 7. Verify revision
    current_revision: int = meta.get("revision", 0)
    if expected_revision != current_revision:
        raise WorkspaceError(
            f"revision_conflict: expected_revision={expected_revision}, "
            f"actual_revision={current_revision}. "
            f"Re-read the current SPEC revision and retry."
        )

    # 8. Compute actual SPEC SHA-256
    actual_spec_sha256 = meta.get("spec_hash", "") if contract else compute_sha256(spec_md)

    # 8a. Verify integrity
    stored_hash = meta.get("spec_hash", "") if contract else meta.get("spec_sha256", "")
    if actual_spec_sha256 != stored_hash:
        raise WorkspaceError(
            f"artifact_integrity_mismatch: stored={stored_hash}, "
            f"actual={actual_spec_sha256}"
        )

    # 8b. Verify expected hash
    if expected_spec_sha256 != actual_spec_sha256:
        raise WorkspaceError(
            f"spec_hash_conflict: expected={expected_spec_sha256}, "
            f"actual={actual_spec_sha256}. "
            f"The SPEC may have been re-frozen since you last read it. "
            f"Re-read the current frozen SPEC revision and hash, then retry with the correct values. "
            f"Corrective action: reopen_spec_and_retry_with_current_hash."
        )

    # 9. Acquire lock
    lock_fd = acquire_lock(workspace_id, task_id, timeout=5.0)
    try:
        # 9a. Re-load under lock
        meta_after_lock = load_meta(task_dir)

        # Re-check status and frozen binding under lock
        if (contract and meta_after_lock.get("status") != "frozen") or (not contract and (meta_after_lock.get("status") != STATUS_DRAFT or meta_after_lock.get("frozen_revision") != current_revision)):
            raise WorkspaceError(
                f"task_not_approvable: task state changed during lock acquisition"
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
