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
from ._reference_resolver import ReferenceError, resolve_for_start
from ._session_active_spec_binding import trusted_session_context
from ._trusted_runtime_context import missing_context_result

TOOL_NAME = "aota_profile_task_approve"
TOOLSET_NAME = "aota_profile_task"

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Record an explicit human checkpoint decision for the current frozen SPEC. "
        "Creates APPROVAL.json in the task directory. "
        "Only for implementation tasks (diagnosis/review do not require approval). "
        "This tool does not start execution, modify SPEC, choose profile, or create "
        "another task. Call this only when the current human reviewer has explicitly "
        "approved the exact SPEC revision/hash for execution. "
        "Approval is bound to exact revision+hash; spec updates invalidate approval.\n\n"
        "CANONICAL PARAMETERS:\n"
        "- decision: approve, reject, or request_changes.\n"
        "- rationale: bounded human checkpoint rationale.\n"
        "The control plane resolves the exact "
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
        "  -> returns {status: approved, spec_hash: <canonical>, spec_sha256: <raw file hash>, ...}\n\n"
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
            "expected_spec_hash": {
                "type": "string",
                "description": "REQUIRED for canonical WI-09C SPECs. The exact canonical frozen SPEC hash (spec_hash from aota_task_spec_freeze response). 64-char hex string. This is the authoritative hash for approval binding.",
            },
        },
        "required": [
            "workspace_id",
            "task_id",
            "expected_revision",
            "expected_spec_hash",
        ],
        "additionalProperties": False,
    },
}
SCHEMA["description"] += " For canonical WI-09C SPECs (contract_version=1), expected_spec_hash is required and expected_spec_sha256 is deprecated."
# Canonical model surface is an explicit approval of the active frozen SPEC;
# exact task/revision/hash values remain handler-only compatibility fields.
SCHEMA["parameters"]["properties"] = {
    "decision": {"type": "string", "enum": ["approve", "reject", "request_changes"], "description": "The explicit human checkpoint decision."},
    "rationale": {"type": "string", "maxLength": 4000, "description": "Bounded rationale or requested change scope."},
}
SCHEMA["parameters"]["required"] = ["decision", "rationale"]
SCHEMA["description"] = (
    "Submit approve, reject, or request_changes for the current frozen implementation SPEC as an explicit human checkpoint. "
    "The control plane resolves workspace, task, revision, canonical hash and raw SHA; "
    "the model does not copy identifiers or digests. Legacy exact fields are "
    "handler-only compatibility. Only implementation SPECs require "
    "this decision. Canonical example: {decision: approve, rationale: '範圍與驗收條件已確認'}. "
    "The legacy task_ref/exact revision/hash fields are handler-only compatibility."
)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def handle(args: dict, **kwargs) -> str:
    try:
        if "decision" in args:
            context = trusted_session_context(kwargs)
            if not context.usable_for_active_spec:
                return json.dumps(missing_context_result(operation="profile_task_approve"), sort_keys=True)
            decision = args.get("decision")
            rationale = args.get("rationale")
            if decision not in {"approve", "reject", "request_changes"} or not isinstance(rationale, str) or not rationale.strip():
                raise WorkspaceError("approval_decision_invalid")
            binding = resolve_for_start(context.workspace_id, "active_frozen_spec", trusted_session_id=context.session_id, trusted_principal=context.principal)
            args = {"workspace_id": binding["workspace_id"], "task_id": binding["task_id"], "expected_revision": binding["expected_revision"], "expected_spec_hash": binding["expected_spec_hash"], "decision": decision, "rationale": rationale}
        elif args.get("task_ref"):
            context = trusted_session_context(kwargs)
            if not context.usable_for_active_spec:
                return json.dumps(missing_context_result(operation="profile_task_approve"), sort_keys=True)
            binding = resolve_for_start(
                context.workspace_id,
                args["task_ref"],
                trusted_session_id=context.session_id,
                trusted_principal=context.principal,
            )
            args = {
                "workspace_id": binding["workspace_id"],
                "task_id": binding["task_id"],
                "expected_revision": binding["expected_revision"],
                "expected_spec_hash": binding["expected_spec_hash"],
            }
        return _do_approve(args)
    except ReferenceError as e:
        return json.dumps({"status": "rejected", "operation_result": "approval", "error": e.code, "detail": e.detail, "choices": e.choices,
                           "retryable": False, "human_action_required": bool(e.choices), "next_action": "select_approval_subject" if e.choices else "stop_and_report_approval_reference_failure"}, sort_keys=True)
    except WorkspaceError as e:
        return json.dumps(
            {"status": "rejected", "operation_result": "approval", "error": str(e), "retryable": False, "human_action_required": False,
             "next_action": "refresh_current_subject" if any(token in str(e) for token in ("revision_conflict", "spec_hash_conflict", "artifact_integrity_mismatch")) else "stop_and_report_approval_failure"}, sort_keys=True
        )
    except Exception as e:
        return json.dumps(
            {"status": "failed", "operation_result": "approval", "error": str(e), "retryable": False, "human_action_required": False, "next_action": "stop_and_report_approval_failure"}, sort_keys=True
        )


def _do_approve(args: dict) -> str:
    workspace_id: str = args.get("workspace_id", "")
    task_id: str = args.get("task_id", "")
    expected_revision: int = args.get("expected_revision", 0)
    expected_spec_hash: str = args.get("expected_spec_hash", "") or ""
    expected_spec_sha256: str = args.get("expected_spec_sha256", "") or ""
    decision = args.get("decision", "approve")
    rationale = args.get("rationale", "")

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
        return json.dumps({"status": "not_required", "operation_result": "approval_not_required", "approval_binding_verified": False, "retryable": False, "human_action_required": False, "next_action": "start_active_frozen_spec"}, sort_keys=True)

    if decision != "approve":
        return json.dumps({"status": "rejected" if decision == "reject" else "changes_requested", "operation_result": "approval_decision_recorded", "decision": decision, "rationale": rationale, "revision": meta.get("revision"), "spec_hash": meta.get("spec_hash"), "approval_binding_verified": True, "retryable": False, "human_action_required": False, "next_action": "stop_and_report_approval_rejected" if decision == "reject" else "update_current_draft_spec"}, sort_keys=True)

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
    actual_spec_hash = meta.get("spec_hash", "") if contract else ""
    actual_spec_sha256 = compute_sha256(spec_md)

    # 8a. Verify integrity
    stored_hash = meta.get("spec_hash", "") if contract else meta.get("spec_sha256", "")
    if (contract and (actual_spec_hash != stored_hash or meta.get("spec_sha256") != actual_spec_sha256)) or (not contract and actual_spec_sha256 != stored_hash):
        raise WorkspaceError(
            f"artifact_integrity_mismatch: stored={stored_hash}, "
            f"actual={actual_spec_hash if contract else actual_spec_sha256}"
        )

    # 8b. Verify expected hash
    expected_binding = expected_spec_hash if contract else expected_spec_sha256
    actual_binding = actual_spec_hash if contract else actual_spec_sha256
    if expected_binding != actual_binding:
        raise WorkspaceError(
            f"spec_hash_conflict: expected={expected_binding}, "
            f"actual={actual_binding}. "
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
            expected_existing_hash = actual_spec_hash if contract else actual_spec_sha256
            if existing_rev == current_revision and existing_hash in {expected_existing_hash, actual_spec_sha256}:
                # Idempotent: same revision/hash already approved
                return json.dumps(
                    {
                        "status": "already_approved",
                        "task_id": task_id,
                        "workspace_id": workspace_id,
                        "revision": current_revision,
                        "spec_hash": actual_spec_hash or None,
                        "spec_sha256": actual_spec_sha256,
                        "approval_id": existing_approval.get("approval_id", ""),
                        "approved_at": existing_approval.get("approved_at", ""),
                        "approval_source": "explicit_tool_invocation", "operation_result": "approval_already_recorded", "approval_binding_verified": True, "retryable": False, "human_action_required": False, "next_action": "start_active_frozen_spec",
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
            "spec_hash": actual_spec_hash or None,
            "spec_sha256": actual_spec_sha256,
            "approved_at": now,
            "approval_source": "explicit_tool_invocation",
            "task_kind": task_kind, "decision": "approve", "rationale": rationale,
        }

        # Atomic write
        write_json(approval_path, approval)

        return json.dumps(
            {
                "status": "approved",
                "task_id": task_id,
                "workspace_id": workspace_id,
                "revision": current_revision,
                "spec_hash": actual_spec_hash or None,
                "spec_sha256": actual_spec_sha256,
                "approval_id": approval_id,
                "approved_at": now,
                "approval_source": "explicit_tool_invocation", "operation_result": "approval_recorded", "approval_binding_verified": True, "retryable": False, "human_action_required": False, "next_action": "start_active_frozen_spec",
            },
            sort_keys=True,
        )
    finally:
        release_lock(lock_fd)
