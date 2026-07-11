"""aota_profile_task_cancel — request cancellation of a running AOTA Profile Task.

P7-A: Cancel & Timeout Control (Cancel Only).
Uses durable termination intent in meta.json and the Hermes ProcessRegistry
kill primitive. Three-phase transaction: lock-validate-intent, registry kill,
lock-persist-result.

Does NOT implement timeout_seconds (blocked — no background deadline mechanism exists
in the terminal background rail; timeout on terminal_tool is foreground-only).
"""

from __future__ import annotations

import datetime
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any, Optional

from ._profile_task_common import validate_task_id
from ._task_spec_common import (
    acquire_lock,
    get_task_dir,
    load_meta,
    release_lock,
    utc_now_iso,
)
from ._workspace import WorkspaceError, resolve_workspace

TOOL_NAME = "aota_profile_task_cancel"
TOOLSET_NAME = "aota_profile_task"

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Request cancellation of one currently running AOTA Profile Task. "
        "Validates durable execution metadata, records durable user-cancel "
        "termination intent, and uses the existing Hermes ProcessRegistry "
        "termination primitive to signal the worker process. "
        "Does not accept arbitrary process IDs or signals. "
        "Safely coordinates with the trusted finalizer to ensure "
        "cancelled state is recognized even if the process exits concurrently. "
        "Does not: retry, restart, timeout another task, cancel arbitrary OS "
        "processes, or modify SPEC.md."
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
                "description": "Existing AOTA task ID to cancel",
            },
        },
        "required": [
            "workspace_id",
            "task_id",
        ],
        "additionalProperties": False,
    },
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_request_id() -> str:
    """Generate a unique termination request ID: tr_<UTC_TIMESTAMP>_<random_hex_8>."""
    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    rand = secrets.token_hex(4)  # 8 hex chars
    return f"tr_{ts}_{rand}"


def _atomic_commit_meta(meta_path: Path, meta: dict[str, Any]) -> None:
    """Atomically write meta.json via temp sibling + os.replace."""
    content = json.dumps(meta, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    fd, tmp_path_str = tempfile.mkstemp(
        dir=str(meta_path.parent),
        prefix=f".{meta_path.name}.tmp_",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path_str, str(meta_path))
    except Exception:
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise


def _query_process_registry(process_session_id: str) -> tuple[Optional[Any], str]:
    """Query ProcessRegistry for a session. Returns (session, state_str)."""
    try:
        from tools.process_registry import process_registry  # type: ignore[import-untyped]
    except ImportError:
        return None, "unavailable"
    try:
        session = process_registry.get(process_session_id)
    except Exception:
        return None, "unavailable"
    if session is None:
        return None, "not_found"
    if session.exited:
        return session, "exited"
    return session, "running"


def _kill_process_registry(process_session_id: str) -> tuple[bool, str]:
    """Attempt to kill a process via ProcessRegistry."""
    try:
        from tools.process_registry import process_registry  # type: ignore[import-untyped]
    except ImportError:
        return False, "unavailable"
    try:
        result = process_registry.kill_process(
            process_session_id, source="aota_profile_task_cancel"
        )
    except Exception:
        return False, "error"
    if not isinstance(result, dict):
        return False, "error"
    status = result.get("status", "")
    if status == "killed":
        return True, "killed"
    elif status == "already_exited":
        return True, "already_exited"
    elif status == "not_found":
        return False, "not_found"
    else:
        return False, status


def _validate_cancel_workspace_id(workspace_id: str) -> Optional[str]:
    """Validate workspace_id (no path traversal, no NUL)."""
    if not workspace_id:
        return "workspace_id is empty"
    if len(workspace_id) > 128:
        return "workspace_id exceeds 128 characters"
    for ch in ("\x00", "/", "\\", " "):
        if ch in workspace_id:
            return f"workspace_id contains forbidden character: {ch!r}"
    if ".." in workspace_id:
        return "workspace_id contains '..' segment"
    return None


def _build_cancel_output(
    meta: dict[str, Any],
    task_id: str,
    workspace_id: str,
    *,
    registry_state: str = "not_applicable",
) -> dict[str, Any]:
    """Build compact output for a task that has been cancelled."""
    execution = meta.get("execution", {})
    termination = execution.get("termination", {})
    return {
        "task_id": task_id,
        "workspace_id": workspace_id,
        "status": meta.get("status", ""),
        "profile": execution.get("profile", ""),
        "process_session_id": execution.get("process_session_id", ""),
        "start_id": execution.get("start_id", ""),
        "termination_request_id": termination.get("request_id", ""),
        "termination_reason": termination.get("reason", ""),
        "termination_state": termination.get("state", ""),
        "requested_at": termination.get("requested_at", ""),
        "registry_state": registry_state,
    }


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------


def handle(args: dict, **_kwargs) -> str:
    """Handle aota_profile_task_cancel tool invocation."""
    try:
        result_dict = _do_cancel(args)
        return json.dumps(result_dict, sort_keys=True)
    except WorkspaceError as e:
        return json.dumps({"status": "error", "error": str(e)}, sort_keys=True)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, sort_keys=True)


def _do_cancel(args: dict) -> dict[str, Any]:
    workspace_id: str = args.get("workspace_id", "")
    task_id: str = args.get("task_id", "")

    # ------------------------------------------------------------------
    # Phase 0 — Input validation
    # ------------------------------------------------------------------
    err = _validate_cancel_workspace_id(workspace_id)
    if err:
        return {"status": "unknown_workspace", "error": f"invalid_workspace_id: {err}"}

    err = validate_task_id(task_id)
    if err:
        return {"status": "invalid_task_id", "error": f"invalid_task_id: {err}"}

    # Resolve workspace (live)
    try:
        resolve_workspace(workspace_id)
    except WorkspaceError:
        return {
            "status": "unknown_workspace",
            "error": f"workspace '{workspace_id}' not found",
        }

    # Locate task directory
    task_dir = get_task_dir(workspace_id, task_id)
    if not task_dir.is_dir():
        return {
            "status": "task_not_found",
            "error": f"task '{task_id}' not found in workspace '{workspace_id}'",
        }

    meta_path = task_dir / "meta.json"

    # ------------------------------------------------------------------
    # Phase A — Durable Intent (under lock)
    # ------------------------------------------------------------------
    try:
        lock_fd = acquire_lock(workspace_id, task_id, timeout=5.0)
    except TimeoutError:
        return {"status": "lock_timeout", "error": "could not acquire task lock within 5s"}
    except WorkspaceError as e:
        return {"status": "lock_timeout", "error": str(e)}

    # Variables populated in Phase A, consumed in Phase B/C
    process_session_id: str = ""
    start_id: str = ""
    request_id: str = ""
    now: str = ""
    profile: str = ""
    meta_status: str = ""

    try:
        try:
            meta = load_meta(task_dir)
        except WorkspaceError:
            return {
                "status": "task_not_found",
                "error": "meta.json not found",
            }

        # Validate meta matches inputs
        if meta.get("task_id") != task_id:
            return {
                "status": "artifact_integrity_mismatch",
                "error": "meta.task_id mismatch",
            }
        if meta.get("workspace_id") != workspace_id:
            return {
                "status": "artifact_integrity_mismatch",
                "error": "meta.workspace_id mismatch",
            }

        meta_status = meta.get("status", "")
        execution = meta.get("execution", {})

        # If already cancelled — compact idempotent return
        if meta_status == "cancelled":
            return _build_cancel_output(
                meta, task_id, workspace_id, registry_state="not_applicable"
            )

        # If already timed out — cancel cannot override a terminal timeout
        if meta_status == "timeout":
            return {
                "status": "already_timeout",
                "task_id": task_id,
                "workspace_id": workspace_id,
                "error": (
                    f"Task {task_id} has already timed out. "
                    f"Cancel cannot override a terminal timeout."
                ),
            }

        # Gate: must be running
        if meta_status != "running":
            return {
                "status": "task_not_cancellable",
                "error": f"task status is '{meta_status}', expected 'running'",
            }

        process_session_id = execution.get("process_session_id", "")
        start_id = execution.get("start_id", "")
        profile = execution.get("profile", "")

        if not process_session_id:
            return {
                "status": "execution_metadata_missing",
                "error": "execution.process_session_id is empty",
            }
        if not start_id:
            return {
                "status": "execution_metadata_missing",
                "error": "execution.start_id is empty",
            }

        # Idempotency: existing termination with reason=user_cancel
        existing_termination = execution.get("termination")
        if existing_termination and existing_termination.get("reason") == "user_cancel":
            existing_request_id = existing_termination.get("request_id", "")
            existing_state = existing_termination.get("state", "requested")
            existing_requested_at = existing_termination.get("requested_at", "")
            _, reg_state = _query_process_registry(process_session_id)
            return {
                "task_id": task_id,
                "workspace_id": workspace_id,
                "status": "running",
                "profile": profile,
                "process_session_id": process_session_id,
                "start_id": start_id,
                "termination_request_id": existing_request_id,
                "termination_reason": "user_cancel",
                "termination_state": existing_state,
                "requested_at": existing_requested_at,
                "registry_state": reg_state,
            }

        # Generate new termination request
        now = utc_now_iso()
        request_id = _generate_request_id()

        termination: dict[str, Any] = {
            "schema_version": 1,
            "request_id": request_id,
            "reason": "user_cancel",
            "state": "requested",
            "requested_at": now,
            "signal_sent_at": None,
            "confirmed_at": None,
            "source": "aota_profile_task_cancel",
            "process_session_id": process_session_id,
            "start_id": start_id,
        }

        # Atomic meta commit
        new_meta = dict(meta)
        new_execution = dict(execution)
        new_execution["termination"] = termination
        new_meta["execution"] = new_execution
        _atomic_commit_meta(meta_path, new_meta)

    finally:
        release_lock(lock_fd)

    # ------------------------------------------------------------------
    # Phase B — ProcessRegistry Control (NO lock held)
    # ------------------------------------------------------------------
    _, reg_state = _query_process_registry(process_session_id)

    termination_state = "requested"
    signal_sent_at: Optional[str] = None
    control_result: str = ""   # ← ADD THIS LINE

    if reg_state == "not_found":
        termination_state = "unresolved"
        control_result = "not_found"
    elif reg_state == "exited":
        # Process already finished — cannot confirm cancel caused exit
        termination_state = "unresolved"
        control_result = "already_exited"
    elif reg_state == "running":
        kill_ok, kill_status = _kill_process_registry(process_session_id)
        if kill_ok:
            termination_state = "signal_sent"
            signal_sent_at = utc_now_iso()
            control_result = "killed"
        elif kill_status == "already_exited":
            # Process exited before signal — explicit not_sent
            termination_state = "not_sent"
            control_result = "already_exited"
        elif kill_status in ("not_found", "unavailable"):
            termination_state = "unresolved"
            control_result = kill_status
        else:
            # Any error is unresolved, never cancelled
            termination_state = "unresolved"
            control_result = kill_status
    else:
        termination_state = "unresolved"
        control_result = reg_state

    # ------------------------------------------------------------------
    # Phase C — Persist Result (re-acquire lock)
    # ------------------------------------------------------------------
    try:
        lock_fd2 = acquire_lock(workspace_id, task_id, timeout=5.0)
    except (TimeoutError, WorkspaceError):
        # Best-effort: cannot re-acquire lock, return what we have
        return {
            "task_id": task_id,
            "workspace_id": workspace_id,
            "status": meta_status or "running",
            "profile": profile,
            "process_session_id": process_session_id,
            "start_id": start_id,
            "termination_request_id": request_id,
            "termination_reason": "user_cancel",
            "termination_state": termination_state,
            "requested_at": now,
            "registry_state": reg_state,
        }

    try:
        try:
            meta = load_meta(task_dir)
        except WorkspaceError:
            return {
                "task_id": task_id,
                "workspace_id": workspace_id,
                "status": meta_status or "running",
                "profile": profile,
                "process_session_id": process_session_id,
                "start_id": start_id,
                "termination_request_id": request_id,
                "termination_reason": "user_cancel",
                "termination_state": termination_state,
                "requested_at": now,
                "registry_state": reg_state,
            }

        meta_status = meta.get("status", "")

        # If finalizer already terminalized — respect it
        _TERMINAL = frozenset({"done", "failed", "cancelled", "timeout"})
        if meta_status in _TERMINAL:
            return _build_cancel_output(
                meta, task_id, workspace_id, registry_state="not_applicable"
            )

        # Update termination state + control_result atomically
        if meta_status == "running":
            execution = meta.get("execution", {})
            start_id_val = execution.get("start_id", "")
            new_meta = dict(meta)
            new_execution = dict(execution)
            existing_termination = dict(new_execution.get("termination", {}))
            existing_termination["state"] = termination_state
            existing_termination["control_result"] = control_result
            if signal_sent_at:
                existing_termination["signal_sent_at"] = signal_sent_at

            # --- Receipt-aware reconciliation ---
            # Check if finalizer already wrote a completion receipt
            # (finalizer defers on requested state, keeps running)
            receipt_path = task_dir / f"completion.{start_id_val}.json"
            receipt_data = None
            if receipt_path.exists():
                try:
                    receipt_data = json.loads(receipt_path.read_text())
                except Exception:
                    pass

            if receipt_data and receipt_data.get("start_id") == start_id_val:
                # Receipt exists — finalizer wrote it, awaiting Phase C
                receipt_exit_code = receipt_data.get("exit_code", -1)
                receipt_completed_at = receipt_data.get("completed_at", utc_now_iso())

                if termination_state == "signal_sent":
                    # Kill confirmed + receipt exists → reconcile to cancelled NOW
                    new_meta["status"] = "cancelled"
                    existing_termination["state"] = "confirmed"
                    existing_termination["confirmed_at"] = utc_now_iso()
                    new_execution["completed_at"] = receipt_completed_at
                    new_execution["exit_code"] = receipt_exit_code
                    new_execution["outcome"] = receipt_data.get("outcome", "failed")
                    new_execution["reconciliation_state"] = "reconciled"
                    new_execution["reconciliation_source"] = "cancel_phase_c_with_completion_receipt"
                    new_execution["completion_receipt_path"] = f"completion.{start_id_val}.json"
                elif termination_state in ("not_sent", "unresolved"):
                    # No kill signal — reconcile using receipt exit code
                    new_status = "done" if receipt_exit_code == 0 else "failed"
                    new_meta["status"] = new_status
                    new_execution["completed_at"] = receipt_completed_at
                    new_execution["exit_code"] = receipt_exit_code
                    new_execution["outcome"] = receipt_data.get("outcome", "failed")
                    new_execution["reconciliation_state"] = "reconciled"
                    new_execution["reconciliation_source"] = "cancel_phase_c_with_completion_receipt"
                    new_execution["completion_receipt_path"] = f"completion.{start_id_val}.json"
                else:
                    # requested or other — keep running, finalizer will handle
                    pass
            # else: no receipt yet, just persist signal_sent; finalizer will terminalize

            new_execution["termination"] = existing_termination
            new_meta["execution"] = new_execution
            _atomic_commit_meta(meta_path, new_meta)

        return {
            "task_id": task_id,
            "workspace_id": workspace_id,
            "status": meta_status,
            "profile": profile,
            "process_session_id": process_session_id,
            "start_id": start_id,
            "termination_request_id": request_id,
            "termination_reason": "user_cancel",
            "termination_state": termination_state,
            "requested_at": now,
            "registry_state": reg_state,
        }

    finally:
        release_lock(lock_fd2)
