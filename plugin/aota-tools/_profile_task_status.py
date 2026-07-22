"""aota_profile_task_status — query AOTA profile task status with lifecycle reconciliation.

P6: Durable Lifecycle Reconciliation & Profile Task Status.
Returns compact task state, terminal status, receipt status, registry status,
and reconciliation metadata. For running tasks, attempts to reconcile via
completion receipt or process registry.

PCF-WI-PROFILE-TASK-PROGRESS-POLLING-ENFORCEMENT-MINIMUM:
Adds retrieval_reason optional parameter and enforcement guards to prevent
progress polling of running tasks in wakeup-capable sessions. Terminal/draft
task queries are unaffected (retrieval_reason is not required for them).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from ._profile_task_common import validate_task_id
from ._task_spec_common import (
    acquire_lock,
    compute_sha256,
    get_task_dir,
    load_meta,
    read_json,
    release_lock,
    utc_now_iso,
    write_json,
)
from ._workspace import WorkspaceError

TOOL_NAME = "aota_profile_task_status"
TOOLSET_NAME = "aota_profile_task"

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Query the status of an AOTA Profile Task with lifecycle reconciliation. "
        "Returns current task state, terminal status, receipt status, registry "
        "status, and reconciliation metadata. For running tasks, attempts to "
        "reconcile via completion receipt or process registry. "
        "Idempotent: does not mutate terminal tasks."
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
                "description": "Existing AOTA task ID to query",
            },
            "retrieval_reason": {
                "type": "string",
                "description": (
                    "Optional typed reason for querying a running task. "
                    "Required for running tasks to prevent progress polling. "
                    "Ignored for terminal/draft tasks. "
                    "Must be one of: completion_notification_timeout, "
                    "lost_notification, suspected_runtime_failure, "
                    "non_wakeup_reentry, user_requested, isolated_probe."
                ),
            },
        },
        "required": [
            "workspace_id",
            "task_id",
        ],
        "additionalProperties": False,
    },
}

#: Valid retrieval_reason values for running task status queries.
RETRIEVAL_REASONS = frozenset(
    {
        "completion_notification_timeout",
        "lost_notification",
        "suspected_runtime_failure",
        "non_wakeup_reentry",
        "user_requested",
        "isolated_probe",
    }
)

#: Minimum interval (seconds) between running-task status queries before a
#: repeated_progress_poll_forbidden rejection is returned.
_REPEAT_POLL_MIN_INTERVAL_SECONDS = 30.0

#: Rejection reason for missing retrieval_reason on a running task query.
_REJECT_NORMAL_PATH_PROGRESS_POLL_FORBIDDEN = "normal_path_progress_poll_forbidden"

#: Rejection reason for short-interval repeated running-task status queries.
_REJECT_REPEATED_PROGRESS_POLL_FORBIDDEN = "repeated_progress_poll_forbidden"

_TERMINAL_STATUSES = frozenset(
    {
        "done",
        "failed",
        "needs_input",
        "scope_violation",
        "cancelled",
        "timeout",
    }
)

_FORBIDDEN_CHARS = ("\x00", "/", "\\", " ")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_retrieval_reason(retrieval_reason: str) -> Optional[str]:
    """Validate retrieval_reason value."""
    if not retrieval_reason:
        return "retrieval_reason is empty"
    if retrieval_reason not in RETRIEVAL_REASONS:
        return f"retrieval_reason must be one of {sorted(RETRIEVAL_REASONS)}, got {retrieval_reason!r}"
    return None


def _validate_workspace_id(workspace_id: str) -> Optional[str]:
    """Validate workspace_id (no path traversal, no NUL)."""
    if not workspace_id:
        return "workspace_id is empty"
    if len(workspace_id) > 128:
        return "workspace_id exceeds 128 characters"
    for ch in _FORBIDDEN_CHARS:
        if ch in workspace_id:
            return f"workspace_id contains forbidden character: {ch!r}"
    if ".." in workspace_id:
        return "workspace_id contains '..' segment"
    return None


# ---------------------------------------------------------------------------
# Status query history (anti-poll enforcement)
# ---------------------------------------------------------------------------


def _record_status_query(
    meta: dict[str, Any],
    task_dir: Path,
    retrieval_reason: str,
) -> list[dict[str, Any]]:
    """Record a status query in meta.json execution.status_queries.

    Appends a new entry with timestamp and retrieval_reason. Returns the
    updated status_queries list. Writes the updated meta atomically.
    """
    execution = meta.get("execution", {})
    status_queries: list[dict[str, Any]] = execution.get("status_queries", [])
    now = utc_now_iso()
    entry = {
        "queried_at": now,
        "retrieval_reason": retrieval_reason,
        "epoch_seconds": time.time(),
    }
    status_queries = status_queries + [entry]
    new_execution = dict(execution)
    new_execution["status_queries"] = status_queries
    new_meta = dict(meta)
    new_meta["execution"] = new_execution
    try:
        write_json(task_dir / "meta.json", new_meta)
    except Exception:
        pass  # Best-effort recording
    return status_queries


def _detect_repeated_poll(
    status_queries: list[dict[str, Any]],
    *,
    min_interval_seconds: float = _REPEAT_POLL_MIN_INTERVAL_SECONDS,
) -> bool:
    """Detect short-interval repeated status queries.

    Returns True if the most recent query (before the current one) was
    within min_interval_seconds of the current query, indicating polling.
    """
    if len(status_queries) < 2:
        return False
    # Compare the last two entries by epoch_seconds
    last = status_queries[-1].get("epoch_seconds", 0)
    prev = status_queries[-2].get("epoch_seconds", 0)
    if not isinstance(last, (int, float)) or not isinstance(prev, (int, float)):
        return False
    if last <= 0 or prev <= 0:
        return False
    interval = last - prev
    return 0 <= interval < min_interval_seconds


# ---------------------------------------------------------------------------
# Artifact manifest helper
# ---------------------------------------------------------------------------

_KNOWN_ARTIFACTS = frozenset(
    {
        "CARD.json",
        "RESULT.md",
        "DIAGNOSIS_CARD.json",
        "DIAGNOSIS.md",
        "REVIEW_CARD.json",
        "REVIEW.md",
        "scope.json",
    }
)


def _build_artifact_manifest(task_dir: Path, start_id: str) -> dict[str, dict[str, bool | int]]:
    """Build a manifest of known artifact files in the task directory.

    Returns a dict mapping artifact filename to {exists: true/false}.
    Only checks exact known filenames within task_dir; no arbitrary patterns.
    """
    manifest: dict[str, dict[str, bool | int]] = {}

    # Static known filenames
    for name in _KNOWN_ARTIFACTS:
        manifest[name] = {"exists": (task_dir / name).is_file()}

    # Dynamic filenames based on start_id
    if start_id:
        for dynamic_name in (
            f"completion.{start_id}.json",
            f"worker-outcome.{start_id}.json",
        ):
            manifest[dynamic_name] = {"exists": (task_dir / dynamic_name).is_file()}

        # P11-B: Worker log
        worker_log_name = f"worker.{start_id}.log"
        worker_log_path = task_dir / worker_log_name
        worker_log_entry: dict[str, bool | int] = {
            "exists": worker_log_path.is_file(),
            "path": worker_log_name,
        }
        if worker_log_entry["exists"]:
            try:
                worker_log_entry["size_bytes"] = worker_log_path.stat().st_size
            except OSError:
                worker_log_entry["size_bytes"] = 0
        manifest[worker_log_name] = worker_log_entry

    return manifest


# ---------------------------------------------------------------------------
# Status dict builder
# ---------------------------------------------------------------------------


def _build_status_dict(
    meta: dict[str, Any],
    task_id: str,
    workspace_id: str,
    *,
    registry_state: str = "unavailable",
    receipt_present: bool = False,
    reconciliation_state: str = "not_applicable",
    reconciliation_source: str = "meta",
) -> dict[str, Any]:
    """Build a compact status dictionary from task meta and enrichment data."""
    execution = meta.get("execution", {})

    result: dict[str, Any] = {
        "task_id": task_id,
        "workspace_id": workspace_id,
        "task_kind": meta.get("task_kind", ""),
        "profile": execution.get("profile", meta.get("profile_hint", "")),
        "status": meta.get("status", ""),
        "revision": meta.get("revision", 0),
        "spec_hash": meta.get("spec_hash", ""),
        "spec_sha256": meta.get("spec_sha256", ""),
        "spec_id": meta.get("spec_id", task_id),
        "project_id": meta.get("project_id"),
        "work_item_id": meta.get("work_item_id"),
        "resolved_profile": meta.get("resolved_profile", execution.get("profile", meta.get("profile_hint", ""))),
        "process_session_id": execution.get("process_session_id", ""),
        "start_id": execution.get("start_id", ""),
        "started_at": execution.get("started_at", ""),
        "completed_at": execution.get("completed_at"),
        "exit_code": execution.get("exit_code"),
        "registry_state": registry_state,
        "receipt_present": receipt_present,
        "reconciliation_state": reconciliation_state,
        "reconciliation_source": reconciliation_source,
        "human_checkpoint_policy": meta.get("human_checkpoint_policy", ""),
    }

    # P7: termination fields
    termination = execution.get("termination")
    if termination:
        result["termination_reason"] = termination.get("reason")
        result["termination_state"] = termination.get("state")
        result["termination_request_id"] = termination.get("request_id")

    # P7: timeout fields
    result["timeout_seconds"] = execution.get("timeout_seconds")
    result["deadline_at"] = execution.get("timeout_deadline_at")
    result["timeout_triggered"] = execution.get("timeout_triggered", False)
    result["timeout_at"] = execution.get("timeout_at")
    result["timeout_exit_code"] = execution.get("timeout_exit_code")

    # P8-A: scope postflight
    scope_compliance = execution.get("scope_compliance", {"status": "unknown", "violations": []})
    result["scope_compliance"] = scope_compliance
    violated_paths: list[str] = execution.get("scope_violated_paths", [])
    result["violated_path_count"] = execution.get("project_violated_path_count", len(violated_paths))
    result["project_checked_path_count"] = execution.get("project_checked_path_count", 0)
    result["project_violated_path_count"] = execution.get("project_violated_path_count", len(violated_paths))
    result["active_task_read_count"] = execution.get("active_task_read_count", 0)
    result["active_task_write_count"] = execution.get("active_task_write_count", 0)
    result["ignored_runtime_path_count"] = execution.get("ignored_runtime_path_count", 0)
    result["unknown_external_path_count"] = execution.get("unknown_external_path_count", 0)

    # P8-C: needs_input metadata
    result["worker_outcome"] = execution.get("worker_outcome")
    result["needs_input_reason"] = execution.get("needs_input_reason")
    result["failure_stage"] = execution.get("failure_stage")
    result["error_classification"] = execution.get("error_classification")
    result["finalizer_expected_version"] = execution.get("finalizer_expected_version", execution.get("finalizer_version"))
    result["primary_finalizer_executed"] = execution.get("primary_finalizer_executed", False)
    result["fallback_finalizer_executed"] = execution.get("fallback_finalizer_executed", False)

    # P10: artifact manifest
    result["artifacts"] = _build_artifact_manifest(
        get_task_dir(workspace_id, task_id),
        execution.get("start_id", ""),
    )

    # P8-D: handoff metadata
    handoff_meta: dict[str, Any] = {"exists": False, "recoverable": False}
    try:
        from ._handoff_common import find_existing_handoff

        task_dir_for_handoff = get_task_dir(workspace_id, task_id)
        existing_hid = find_existing_handoff(
            workspace_id, task_id, execution.get("start_id", "")
        )
        if existing_hid:
            from ._handoff_common import find_handoff_path, read_handoff

            hpath = find_handoff_path(workspace_id, existing_hid)
            if hpath:
                handoff_data = read_handoff(hpath)
                handoff_meta = {
                    "exists": True,
                    "handoff_id": existing_hid,
                    "state": handoff_data.get("state", "unknown"),
                    "acknowledged": handoff_data.get("state") == "acknowledged",
                    "decision": None,
                }
                if handoff_meta["acknowledged"]:
                    try:
                        from ._handoff_common import read_ack_artifact
                        ack_data = read_ack_artifact(workspace_id, existing_hid)
                        if ack_data and "decision" in ack_data:
                            handoff_meta["decision"] = ack_data["decision"]
                    except Exception:
                        pass
            else:
                handoff_meta = {"exists": False, "recoverable": True}
        elif meta.get("status") in _TERMINAL_STATUSES:
            # Check if there's a receipt but no handoff → recoverable
            receipt_path_str: str = execution.get("completion_receipt_path", "")
            if receipt_path_str and _validate_receipt_basename(receipt_path_str):
                if (task_dir_for_handoff / receipt_path_str).exists():
                    try:
                        from ._handoff_common import build_handoff_data, write_handoff_atomic
                        profile = execution.get("profile", "")
                        task_kind = meta.get("task_kind", "")
                        terminal_status = meta.get("status", "")
                        created_at = execution.get("completed_at", utc_now_iso())
                        needs_input_reason = execution.get("needs_input_reason")
                        subject_task_id = meta.get("subject_task_id")
                        start_id = execution.get("start_id", "")
                        handoff_data = build_handoff_data(
                            workspace_id=workspace_id,
                            task_id=task_id,
                            start_id=start_id,
                            profile=profile,
                            task_kind=task_kind,
                            terminal_status=terminal_status,
                            created_at=created_at,
                            subject_task_id=subject_task_id,
                            needs_input_reason=needs_input_reason,
                            task_dir=task_dir_for_handoff,
                        )
                        write_handoff_atomic(workspace_id, handoff_data)
                        new_hid = handoff_data["handoff_id"]
                        handoff_meta = {
                            "exists": True,
                            "handoff_id": new_hid,
                            "state": "pending",
                            "acknowledged": False,
                            "decision": None,
                            "recovered": True,
                        }
                    except Exception:
                        handoff_meta = {"exists": False, "recoverable": True, "recovery_attempted": True}
    except Exception:
        handoff_meta = {"exists": False, "recoverable": False}

    result["handoff"] = handoff_meta

    # P8-E: orchestration decision metadata (compact)
    orchestration_meta: dict[str, Any] = {"decision_exists": False}
    try:
        from ._orchestration_common import find_decision_path, read_decision, get_decision_dir
        import os as _os

        if handoff_meta.get("exists") and handoff_meta.get("handoff_id"):
            hid = handoff_meta["handoff_id"]
            dec_dir = get_decision_dir(workspace_id)
            if dec_dir.is_dir():
                for _entry in _os.listdir(str(dec_dir)):
                    if not _entry.startswith("DECISION.") or not _entry.endswith(".json"):
                        continue
                    try:
                        _dec = read_decision(dec_dir / _entry)
                    except (OSError, json.JSONDecodeError):
                        continue
                    if _dec.get("handoff_id") == hid:
                        dec_state = _dec.get("state", "")
                        dec_resume = _dec.get("resume", {})
                        orchestration_meta = {
                            "decision_exists": True,
                            "decision_id": _dec.get("decision_id", ""),
                            "decision": _dec.get("decision", ""),
                            "decision_state": dec_state,
                            "followup_task_id": _dec.get("followup", {}).get("task_id"),
                            "awaiting_user": dec_state == "awaiting_user",
                            "resumed": bool(dec_resume.get("resumed")),
                        }
                        break
    except Exception:
        pass

    result["orchestration"] = orchestration_meta

    return result


# ---------------------------------------------------------------------------
# Receipt validation helper
# ---------------------------------------------------------------------------


def _validate_receipt_basename(receipt_path_str: str) -> bool:
    """Validate that a receipt path string is a safe basename.

    Rejects: absolute paths, directory separators, '..' segments, NUL bytes.
    """
    if not receipt_path_str:
        return False
    if "/" in receipt_path_str or "\\" in receipt_path_str:
        return False
    if ".." in receipt_path_str:
        return False
    if "\x00" in receipt_path_str:
        return False
    return True


# ---------------------------------------------------------------------------
# Reconciliation implementations
# ---------------------------------------------------------------------------


def _reconcile_from_receipt(
    meta: dict[str, Any],
    task_dir: Path,
) -> Optional[dict[str, Any]]:
    """Attempt to reconcile meta from a completion receipt.

    Returns reconciled meta dict on success, None if receipt is absent,
    invalid, or mismatched.
    """
    execution = meta.get("execution", {})
    receipt_path_str: str = execution.get("completion_receipt_path", "")
    if not receipt_path_str:
        return None

    if not _validate_receipt_basename(receipt_path_str):
        return None

    receipt_file = task_dir / receipt_path_str
    if not receipt_file.exists():
        return None

    try:
        receipt_data = read_json(receipt_file)
    except (json.JSONDecodeError, OSError):
        return None

    # Validate receipt fields match meta
    task_id = meta.get("task_id", "")
    workspace_id = meta.get("workspace_id", "")
    start_id = execution.get("start_id", "")
    profile = execution.get("profile", "")
    spec_revision = execution.get("spec_revision", -1)
    spec_sha256 = execution.get("spec_sha256", "")
    spec_hash = execution.get("spec_hash", meta.get("spec_hash", ""))

    if receipt_data.get("task_id") != task_id:
        return None
    if receipt_data.get("workspace_id") != workspace_id:
        return None
    if receipt_data.get("start_id") != start_id:
        return None
    if receipt_data.get("profile") != profile:
        return None
    if receipt_data.get("spec_revision") != spec_revision:
        return None
    if receipt_data.get("spec_sha256") != spec_sha256:
        return None
    if spec_hash and receipt_data.get("spec_hash", receipt_data.get("spec_sha256")) != spec_hash:
        return None

    # Receipt is valid — reconcile
    receipt_exit_code = receipt_data.get("exit_code", -1)
    receipt_outcome = receipt_data.get("outcome", "failed")
    receipt_completed_at = receipt_data.get("completed_at", utc_now_iso())

    # Evidence-driven termination check
    termination = execution.get("termination")
    if termination and termination.get("reason") == "user_cancel":
        term_state = termination.get("state", "requested")
        term_start_id = termination.get("start_id", "")
        ids_match = (
            termination.get("process_session_id") == execution.get("process_session_id")
            and term_start_id == execution.get("start_id")
        )
        if ids_match and term_state in ("signal_sent", "confirmed"):
            new_status = "cancelled"
            # Update termination to confirmed
            termination["state"] = "confirmed"
            termination["confirmed_at"] = utc_now_iso()
        elif ids_match and term_state == "requested":
            # Phase B/C still pending — keep running, defer
            new_status = "running"
        elif ids_match and term_state in ("unresolved", "not_sent"):
            # No signal evidence → use exit code
            new_status = _authoritative_receipt_status(receipt_data)
        else:
            new_status = _authoritative_receipt_status(receipt_data)
    else:
        new_status = _authoritative_receipt_status(receipt_data)
    new_meta = dict(meta)
    new_meta["status"] = new_status
    new_execution = dict(execution)
    new_execution["completed_at"] = receipt_completed_at
    new_execution["exit_code"] = receipt_exit_code
    new_execution["outcome"] = receipt_outcome
    new_execution["spec_hash"] = receipt_data.get("spec_hash", spec_hash)
    new_execution["failure_stage"] = receipt_data.get("failure_stage")
    new_execution["error_classification"] = receipt_data.get("error_classification")
    new_execution["primary_finalizer_executed"] = receipt_data.get("primary_finalizer_status") == "completed"
    new_execution["fallback_finalizer_executed"] = receipt_data.get("fallback_finalizer_status") == "completed"
    new_execution["finalizer_expected_version"] = 1
    new_execution["reconciliation_state"] = "reconciled"
    new_execution["reconciliation_source"] = "completion_receipt"
    new_execution["completion_receipt_path"] = receipt_path_str
    new_meta["execution"] = new_execution

    return new_meta


def _authoritative_receipt_status(receipt: dict[str, Any]) -> str:
    """Project terminal state only from a valid canonical receipt outcome."""
    status = receipt.get("status")
    outcome = receipt.get("outcome")
    exit_code = receipt.get("exit_code")
    if status == "done" and exit_code == 0 and outcome == "completed":
        return "done"
    if status == "needs_input" and outcome in {"needs_input", "partial"}:
        return "needs_input"
    return "failed"


def _reconcile_from_registry(
    meta: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Attempt to reconcile meta from ProcessRegistry.

    Returns reconciled meta dict on success, None if process is still
    running or registry is unavailable.
    """
    execution = meta.get("execution", {})
    process_session_id: str = execution.get("process_session_id", "")
    if not process_session_id:
        return None

    try:
        from tools.process_registry import process_registry  # type: ignore[import-untyped]
    except ImportError:
        return None

    try:
        session = process_registry.get(process_session_id)
    except Exception:
        return None

    if session is None:
        return None

    if not session.exited:
        return None

    exit_code = session.exit_code
    if exit_code is None:
        return None

    # ProcessRegistry is only a signal, not durable truth.  If the process is
    # gone and the launcher never produced a completion receipt, reconcile via
    # the canonical fallback finalizer before projecting registry state.
    task_dir = get_task_dir(meta.get("workspace_id", ""), meta.get("task_id", ""))
    receipt_name = execution.get("completion_receipt_path", "")
    receipt_missing = not receipt_name or not _validate_receipt_basename(receipt_name) or not (task_dir / receipt_name).is_file()
    if receipt_missing:
        try:
            from ._profile_task_finalize import write_failure_artifacts
            write_failure_artifacts(
                workspace_id=meta.get("workspace_id", ""),
                task_id=meta.get("task_id", ""),
                start_id=execution.get("start_id", meta.get("task_id", "")),
                profile=execution.get("profile", meta.get("profile_hint", "")),
                spec_revision=execution.get("spec_revision", meta.get("revision", 0)),
                spec_sha256=execution.get("spec_sha256", meta.get("spec_sha256", "")),
                spec_hash=execution.get("spec_hash", meta.get("spec_hash", "")),
                exit_code=exit_code,
                failure_stage="status_reconciliation_missing_receipt",
                diagnostics="process registry reported exited without completion receipt",
                command_summary=f"status reconciliation task_id={meta.get('task_id', '')}",
                global_hermes_home=execution.get("global_hermes_home", ""),
                target_profile_home=execution.get("target_profile_home", ""),
                parent_profile=execution.get("parent_profile", ""),
                lock_already_held=True,
            )
            refreshed = load_meta(task_dir)
            if refreshed.get("status") in _TERMINAL_STATUSES:
                return refreshed
        except Exception:
            pass

    reg_outcome = "completed" if exit_code == 0 else "failed"
    reg_completed_at = utc_now_iso()

    # Evidence-driven termination check
    termination = execution.get("termination")
    if termination and termination.get("reason") == "user_cancel":
        term_state = termination.get("state", "requested")
        term_start_id = termination.get("start_id", "")
        ids_match = (
            termination.get("process_session_id") == execution.get("process_session_id")
            and term_start_id == execution.get("start_id")
        )
        if ids_match and term_state in ("signal_sent", "confirmed"):
            new_status = "cancelled"
            # Update termination to confirmed
            termination["state"] = "confirmed"
            termination["confirmed_at"] = utc_now_iso()
        elif ids_match and term_state == "requested":
            # Phase B/C still pending — keep running, defer
            new_status = "running"
        elif ids_match and term_state in ("unresolved", "not_sent"):
            # No signal evidence → use exit code
            new_status = "failed" if receipt_missing else ("done" if exit_code == 0 else "failed")
        else:
            new_status = "failed" if receipt_missing else ("done" if exit_code == 0 else "failed")
    else:
        new_status = "failed" if receipt_missing else ("done" if exit_code == 0 else "failed")
    new_meta = dict(meta)
    new_meta["status"] = new_status
    new_execution = dict(execution)
    new_execution["completed_at"] = reg_completed_at
    new_execution["exit_code"] = exit_code
    new_execution["outcome"] = reg_outcome
    new_execution["reconciliation_state"] = "reconciled"
    new_execution["reconciliation_source"] = "process_registry"
    new_execution["completion_receipt_path"] = execution.get(
        "completion_receipt_path", ""
    )
    new_meta["execution"] = new_execution

    return new_meta


# ---------------------------------------------------------------------------
# Lock helper for status tool
# ---------------------------------------------------------------------------


def _try_acquire_status_lock(
    workspace_id: str, task_id: str, timeout: float = 5.0
) -> Optional[int]:
    """Try to acquire task lock, returning fd or None on timeout/error."""
    try:
        return acquire_lock(workspace_id, task_id, timeout=timeout)
    except (TimeoutError, WorkspaceError):
        return None


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def handle(args: dict, **_kwargs) -> str:
    """Handle aota_profile_task_status tool invocation."""
    try:
        result_dict = _do_status(args)
        return json.dumps(result_dict, sort_keys=True)
    except WorkspaceError as e:
        return json.dumps({"status": "error", "error": str(e)}, sort_keys=True)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, sort_keys=True)


def _do_status(args: dict) -> dict[str, Any]:
    """Core status logic: validate, locate, load, reconcile, return."""
    workspace_id: str = args.get("workspace_id", "")
    task_id: str = args.get("task_id", "")
    retrieval_reason: str = args.get("retrieval_reason", "")

    # 1. Validate inputs
    err = _validate_workspace_id(workspace_id)
    if err:
        return {"status": "error", "error": f"invalid_workspace_id: {err}"}

    err = validate_task_id(task_id)
    if err:
        return {"status": "error", "error": f"invalid_task_id: {err}"}

    # Validate retrieval_reason if provided (even if not yet needed)
    if retrieval_reason:
        err = _validate_retrieval_reason(retrieval_reason)
        if err:
            return {"status": "error", "error": f"invalid_retrieval_reason: {err}"}

    # 2. Locate task_dir
    task_dir = get_task_dir(workspace_id, task_id)

    # 3. If not exists
    if not task_dir.is_dir():
        return {
            "status": "not_found",
            "task_id": task_id,
            "workspace_id": workspace_id,
        }

    # 4. Load meta.json
    meta = load_meta(task_dir)

    # 5. Validate meta.task_id and meta.workspace_id match input
    if meta.get("task_id") != task_id:
        return {
            "status": "error",
            "error": (
                f"meta.task_id mismatch: "
                f"expected {task_id}, got {meta.get('task_id')}"
            ),
        }
    if meta.get("workspace_id") != workspace_id:
        return {
            "status": "error",
            "error": (
                f"meta.workspace_id mismatch: "
                f"expected {workspace_id}, got {meta.get('workspace_id')}"
            ),
        }

    meta_status = meta.get("status", "")

    # 6. Terminal status — meta is authority (retrieval_reason NOT required)
    if meta_status in _TERMINAL_STATUSES:
        execution = meta.get("execution", {})
        receipt_path_str: str = execution.get("completion_receipt_path", "")
        receipt_present = bool(
            receipt_path_str
            and _validate_receipt_basename(receipt_path_str)
            and (task_dir / receipt_path_str).exists()
        )
        return _build_status_dict(
            meta,
            task_id,
            workspace_id,
            registry_state="not_applicable",
            receipt_present=receipt_present,
            reconciliation_state=execution.get(
                "reconciliation_state", "not_applicable"
            ),
            reconciliation_source=execution.get("reconciliation_source", "meta"),
        )

    # 7. Draft status (retrieval_reason NOT required)
    if meta_status == "draft":
        return _build_status_dict(
            meta,
            task_id,
            workspace_id,
            registry_state="not_applicable",
            receipt_present=False,
            reconciliation_state="not_applicable",
            reconciliation_source="meta",
        )

    # 8. Running status — enforce anti-poll guards, then attempt reconciliation
    if meta_status == "running":
        return _handle_running_status(meta, task_id, workspace_id, task_dir, retrieval_reason)

    # Unknown status
    return _build_status_dict(
        meta,
        task_id,
        workspace_id,
        registry_state="not_applicable",
        receipt_present=False,
        reconciliation_state="unknown_status",
        reconciliation_source="meta",
    )


def _handle_running_status(
    meta: dict[str, Any],
    task_id: str,
    workspace_id: str,
    task_dir: Path,
    retrieval_reason: str = "",
) -> dict[str, Any]:
    """Handle status query for a running task with reconciliation attempts.

    Enforces anti-poll guards before reconciliation:
    - retrieval_reason missing → normal_path_progress_poll_forbidden rejection
    - short-interval repeated query → repeated_progress_poll_forbidden rejection

    8a. Enforce retrieval_reason guard
    8b. Acquire task lock
    8c. Reload meta under lock
    8d. If now terminal → return
    8e. Record status query and detect repeated polling
    8f. Check completion receipt → reconcile if valid
    8g. Query ProcessRegistry → reconcile if exited
    8h. Release lock
    """
    execution = meta.get("execution", {})

    # 8a. Enforce retrieval_reason guard for running tasks
    if not retrieval_reason:
        return {
            "status": "rejected",
            "reject_reason": _REJECT_NORMAL_PATH_PROGRESS_POLL_FORBIDDEN,
            "task_id": task_id,
            "workspace_id": workspace_id,
            "message": (
                "Querying a running task without retrieval_reason is forbidden. "
                "Provide one of: "
                + ", ".join(sorted(RETRIEVAL_REASONS))
            ),
        }

    # 8b. Acquire lock (bounded 5s timeout)
    lock_fd = _try_acquire_status_lock(workspace_id, task_id, timeout=5.0)

    if lock_fd is None:
        # Lock unavailable — return running without reconciliation
        return _build_status_dict(
            meta,
            task_id,
            workspace_id,
            registry_state="lock_unavailable",
            receipt_present=False,
            reconciliation_state="unresolved",
            reconciliation_source="meta",
        )

    try:
        # 8c. Reload meta under lock (finalizer may have reconciled)
        meta = load_meta(task_dir)
        meta_status = meta.get("status", "")
        execution = meta.get("execution", {})

        # 8d. If now terminal — retrieval_reason not needed for terminal tasks
        if meta_status in _TERMINAL_STATUSES:
            return _build_status_dict(
                meta,
                task_id,
                workspace_id,
                registry_state="not_applicable",
                receipt_present=bool(
                    execution.get("completion_receipt_path")
                    and _validate_receipt_basename(execution.get("completion_receipt_path", ""))
                    and (task_dir / execution.get("completion_receipt_path", "")).is_file()
                ),
                reconciliation_state=execution.get(
                    "reconciliation_state", "not_applicable"
                ),
                reconciliation_source=execution.get("reconciliation_source", "meta"),
            )

        if meta_status != "running":
            return _build_status_dict(
                meta,
                task_id,
                workspace_id,
                registry_state="not_applicable",
                receipt_present=False,
                reconciliation_state="unexpected",
                reconciliation_source="meta",
            )

        # 8e. Record status query and detect repeated polling
        status_queries = _record_status_query(meta, task_dir, retrieval_reason)
        if _detect_repeated_poll(status_queries):
            return {
                "status": "rejected",
                "reject_reason": _REJECT_REPEATED_PROGRESS_POLL_FORBIDDEN,
                "task_id": task_id,
                "workspace_id": workspace_id,
                "message": (
                    "Repeated status query within "
                    f"{_REPEAT_POLL_MIN_INTERVAL_SECONDS:.0f}s is forbidden. "
                    "This is progress polling, not authorized recovery."
                ),
            }

        # 8f. Check for completion receipt
        reconciled = _reconcile_from_receipt(meta, task_dir)
        if reconciled is not None:
            # Receipt valid — reconcile meta atomically
            try:
                write_json(task_dir / "meta.json", reconciled)
            except Exception:
                pass  # Best-effort reconciliation

            return _build_status_dict(
                reconciled,
                task_id,
                workspace_id,
                registry_state="not_applicable",
                receipt_present=True,
                reconciliation_state="reconciled",
                reconciliation_source="completion_receipt",
            )

        # Receipt not present or invalid → check receipt file presence
        receipt_path_str: str = execution.get("completion_receipt_path", "")
        receipt_present = bool(
            receipt_path_str
            and _validate_receipt_basename(receipt_path_str)
            and (task_dir / receipt_path_str).exists()
        )

        # 8g. No valid receipt — query ProcessRegistry
        registry_state = "unavailable"
        try:
            from tools.process_registry import process_registry  # type: ignore[import-untyped]

            registry_state = "available"
        except ImportError:
            registry_state = "unavailable"

        if registry_state == "available":
            reconciled = _reconcile_from_registry(meta)
            if reconciled is not None:
                try:
                    write_json(task_dir / "meta.json", reconciled)
                except Exception:
                    pass

                return _build_status_dict(
                    reconciled,
                    task_id,
                    workspace_id,
                    registry_state=registry_state,
                    receipt_present=receipt_present,
                    reconciliation_state="reconciled",
                    reconciliation_source=reconciled.get("execution", {}).get("reconciliation_source", "process_registry"),
                )

            # Registry available but process not exited yet or not found
            process_session_id = execution.get("process_session_id", "")
            if not process_session_id:
                registry_state = "no_session_id"

            return _build_status_dict(
                meta,
                task_id,
                workspace_id,
                registry_state=registry_state,
                receipt_present=receipt_present,
                reconciliation_state="unresolved",
                reconciliation_source="process_registry",
            )

        # Registry unavailable
        return _build_status_dict(
            meta,
            task_id,
            workspace_id,
            registry_state=registry_state,
            receipt_present=receipt_present,
            reconciliation_state="unresolved",
            reconciliation_source="meta",
        )

    finally:
        # 8h. Release lock
        if lock_fd is not None:
            release_lock(lock_fd)