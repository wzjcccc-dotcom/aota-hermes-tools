"""Standalone finalizer for AOTA Profile Tasks (P6: Durable Lifecycle Reconciliation).

Works as both:
- Plugin module import (from ._profile_task_finalize import run_finalize)
- Standalone script execution (python _profile_task_finalize.py --workspace-id ...)

stdlib only. No third-party dependencies.
"""

from __future__ import annotations

import argparse
import datetime
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Local import: verify_scope from sibling module
# Works both as plugin module and standalone script.
# ---------------------------------------------------------------------------

import importlib.util

_scope_mod_path = Path(__file__).resolve().parent / "_profile_task_scope.py"
_scope_spec = importlib.util.spec_from_file_location(
    "_profile_task_scope", str(_scope_mod_path)
)
if _scope_spec and _scope_spec.loader:
    _scope_mod = importlib.util.module_from_spec(_scope_spec)
    _scope_spec.loader.exec_module(_scope_mod)
    verify_scope = _scope_mod.verify_scope
else:
    raise ImportError("cannot import verify_scope from _profile_task_scope")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TASK_ID_RE = re.compile(r"^pt_\d{8}T\d{6}_[a-f0-9]{8}$")
_TASK_ID_MAX_LENGTH = 64
_FORBIDDEN_CHARS = ("\x00", "/", "\\", " ")
_TERMINAL_STATUSES = {"done", "failed", "cancelled", "timeout", "needs_input", "scope_violation"}
_LIST_SEPARATOR = ";"

# ---------------------------------------------------------------------------
# Validation helpers  (stdlib-only, usable standalone)
# ---------------------------------------------------------------------------


def _validate_task_id(task_id: str) -> Optional[str]:
    """Validate task_id format. Returns None if valid, error string otherwise."""
    if not task_id:
        return "task_id is empty"
    if len(task_id) > _TASK_ID_MAX_LENGTH:
        return f"task_id exceeds {_TASK_ID_MAX_LENGTH} characters"
    for ch in _FORBIDDEN_CHARS:
        if ch in task_id:
            return f"task_id contains forbidden character: {ch!r}"
    if ".." in task_id:
        return "task_id contains '..' segment"
    if not _TASK_ID_RE.match(task_id):
        return (
            f"invalid task_id format: expected pt_<timestamp>_<random>, "
            f"got {task_id!r}"
        )
    return None


def _validate_start_id(start_id: str) -> Optional[str]:
    """start_id follows same format as task_id in P5 convention."""
    return _validate_task_id(start_id)


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


def _validate_profile(profile: str) -> Optional[str]:
    """Validate profile name (no path traversal, no NUL)."""
    if not profile:
        return "profile is empty"
    for ch in _FORBIDDEN_CHARS:
        if ch in profile:
            return f"profile contains forbidden character: {ch!r}"
    if ".." in profile:
        return "profile contains '..' segment"
    if len(profile) > 128:
        return "profile exceeds 128 characters"
    return None


# ---------------------------------------------------------------------------
# Crypto / I/O helpers
# ---------------------------------------------------------------------------


def _compute_sha256(content: str) -> str:
    """Compute SHA-256 hex digest of content (UTF-8 encoded)."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    """Return current UTC time in ISO 8601 format (Z suffix)."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Atomically write a JSON dict to *path* via temp sibling + os.replace."""
    content = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    fd, tmp_path_str = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.tmp_",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path_str, str(path))
    except Exception:
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> dict[str, Any]:
    """Read and parse a JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Lock helpers  (fcntl.flock, 5s timeout)
# ---------------------------------------------------------------------------


def _acquire_lock(lock_path: Path, timeout: float = 5.0) -> int:
    """Acquire exclusive flock on *lock_path* with bounded timeout.

    Returns an open file descriptor the caller **must** release via
    :func:`_release_lock`.

    Raises TimeoutError if lock cannot be acquired within *timeout* seconds.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as e:
        raise RuntimeError(f"failed to open lock file: {e}") from e

    start = time.monotonic()
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() - start >= timeout:
                os.close(fd)
                raise TimeoutError(
                    f"could not acquire lock for {lock_path} within {timeout}s"
                )
            time.sleep(0.1)
        except OSError as e:
            os.close(fd)
            raise RuntimeError(f"flock error: {e}") from e


def _release_lock(fd: int) -> None:
    """Release an acquired flock and close the file descriptor."""
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except (OSError, ValueError):
        pass
    try:
        os.close(fd)
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------------------
# Main finalizer logic
# ---------------------------------------------------------------------------


def run_finalize(
    workspace_id: str,
    task_id: str,
    start_id: str,
    profile: str,
    spec_revision: int,
    spec_sha256: str,
    exit_code: int,
    workspace_root: str = "",
    timeout_seconds: int = 0,
    worker_log_path: str = "",
) -> None:
    """Run the finalizer for an AOTA profile task.

    This function:
      1. Validates all inputs
      2. Writes a completion receipt atomically
      3. Acquires the task lock
      4. Under lock: validates meta, computes SPEC SHA-256, reconciles meta
      5. Releases lock

    Called either via Python import or standalone CLI.
    On success exits with code 0.
    """
    # ------------------------------------------------------------------
    # 1. Validate all inputs
    # ------------------------------------------------------------------
    err = _validate_task_id(task_id)
    if err:
        print(f"ERROR: invalid task_id: {err}", file=sys.stderr)
        sys.exit(1)

    err = _validate_start_id(start_id)
    if err:
        print(f"ERROR: invalid start_id: {err}", file=sys.stderr)
        sys.exit(1)

    err = _validate_workspace_id(workspace_id)
    if err:
        print(f"ERROR: invalid workspace_id: {err}", file=sys.stderr)
        sys.exit(1)

    err = _validate_profile(profile)
    if err:
        print(f"ERROR: invalid profile: {err}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(spec_revision, int) or spec_revision < 0:
        print(
            f"ERROR: invalid spec_revision: {spec_revision!r}", file=sys.stderr
        )
        sys.exit(1)

    if not spec_sha256 or not isinstance(spec_sha256, str) or len(spec_sha256) != 64:
        print(f"ERROR: invalid spec_sha256: {spec_sha256!r}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(exit_code, int):
        print(f"ERROR: invalid exit_code: {exit_code!r}", file=sys.stderr)
        sys.exit(1)

    # ------------------------------------------------------------------
    # 2. Derive paths from environment
    # ------------------------------------------------------------------
    profile_task_root = os.environ.get(
        "AOTA_PROFILE_TASK_ROOT", "/aota-runtime/profile-tasks"
    )
    runtime_root = os.environ.get("AOTA_RUNTIME_ROOT", "/aota-runtime")

    # ------------------------------------------------------------------
    # 3. Construct task_dir
    # ------------------------------------------------------------------
    task_dir = Path(profile_task_root) / workspace_id / task_id

    # ------------------------------------------------------------------
    # 4. Verify task_dir exists
    # ------------------------------------------------------------------
    if not task_dir.is_dir():
        print(f"ERROR: task directory not found: {task_dir}", file=sys.stderr)
        sys.exit(1)

    # ------------------------------------------------------------------
    # 4b. P8-C.5: Consume trusted worker-outcome JSON first, then legacy marker
    # ------------------------------------------------------------------
    worker_outcome = "completed" if exit_code == 0 else "failed"
    needs_input_reason: str | None = None

    # Precedence 1: worker-outcome.<start_id>.json (trusted control-plane tool)
    _trusted_outcome_path = task_dir / f"worker-outcome.{start_id}.json"
    if _trusted_outcome_path.exists():
        try:
            _trusted_data = json.loads(_trusted_outcome_path.read_text("utf-8"))
            _parsed_outcome = _trusted_data.get("outcome", "")
            if _parsed_outcome in ("needs_input", "completed", "failed"):
                worker_outcome = _parsed_outcome
                needs_input_reason = _trusted_data.get("reason") or None
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass

    # Precedence 2: legacy .worker_outcome_marker (backward compat)
    _outcome_marker_path = task_dir / ".worker_outcome_marker"
    if worker_outcome != "needs_input":  # only override if not already needs_input
        if _outcome_marker_path.exists():
            try:
                _marker_text = _outcome_marker_path.read_text("utf-8").strip()
                for _line in _marker_text.split("\n"):
                    _line = _line.strip()
                    if _line.startswith("AOTA_PROFILE_TASK_OUTCOME="):
                        _parsed_outcome = _line.split("=", 1)[1].strip()
                        if _parsed_outcome in ("needs_input", "completed", "failed"):
                            worker_outcome = _parsed_outcome
                    elif _line.startswith("AOTA_PROFILE_TASK_NEEDS_INPUT_REASON="):
                        needs_input_reason = _line.split("=", 1)[1].strip() or None
            except (OSError, UnicodeDecodeError):
                pass
            # Clean up marker after reading
            try:
                _outcome_marker_path.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # 5. Render receipt JSON
    # ------------------------------------------------------------------
    outcome = worker_outcome  # trusted outcome from worker marker or exit code
    completed_at = _utc_now_iso()

    receipt: dict[str, Any] = {
        "schema_version": 1,
        "task_id": task_id,
        "workspace_id": workspace_id,
        "start_id": start_id,
        "profile": profile,
        "spec_revision": spec_revision,
        "spec_sha256": spec_sha256,
        "exit_code": exit_code,
        "outcome": outcome,
        "completed_at": completed_at,
        "transport": "terminal_background",
    }
    # P11-A: Add timeout info to receipt when configured
    if timeout_seconds > 0:
        receipt["timeout_seconds"] = timeout_seconds

    receipt_filename = f"completion.{start_id}.json"
    receipt_path = task_dir / receipt_filename

    # ------------------------------------------------------------------
    # 6. Write receipt atomically
    # 7. If receipt already exists: compare (idempotent or conflict)
    # ------------------------------------------------------------------
    if receipt_path.exists():
        existing_receipt = _read_json(receipt_path)
        # Compare fields except completed_at (timestamp differs on re-run)
        def _receipt_key(r: dict[str, Any]) -> tuple:
            return (
                r.get("schema_version"),
                r.get("task_id"),
                r.get("workspace_id"),
                r.get("start_id"),
                r.get("profile"),
                r.get("spec_revision"),
                r.get("spec_sha256"),
                r.get("exit_code"),
                r.get("outcome"),
                r.get("transport"),
            )

        if _receipt_key(existing_receipt) == _receipt_key(receipt):
            # Idempotent: same parameters, reuse existing completed_at
            completed_at = existing_receipt.get("completed_at", completed_at)
            receipt["completed_at"] = completed_at
            pass
        else:
            # Conflict: different exit_code or params
            print(
                "COMPLETION_RECEIPT_CONFLICT",
                file=sys.stderr,
            )
            sys.exit(2)
    else:
        _atomic_write_json(receipt_path, receipt)

    # ------------------------------------------------------------------
    # 8. Acquire task lock  (/aota-runtime/locks/task-spec/<ws>/<tid>.lock)
    # ------------------------------------------------------------------
    lock_path = (
        Path(runtime_root) / "locks" / "task-spec" / workspace_id / f"{task_id}.lock"
    )
    try:
        lock_fd = _acquire_lock(lock_path, timeout=5.0)
    except TimeoutError as e:
        # Receipt is written, but meta NOT reconciled.
        # Status tool will reconcile from receipt later.
        print(f"ERROR: lock timeout: {e}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"ERROR: lock error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        # ------------------------------------------------------------------
        # 9. Under lock: load meta.json, validate fields, compute SPEC SHA-256
        # ------------------------------------------------------------------
        meta_path = task_dir / "meta.json"
        if not meta_path.exists():
            print(f"ERROR: meta.json not found in {task_dir}", file=sys.stderr)
            sys.exit(1)

        meta = _read_json(meta_path)

        # Validate meta.task_id
        if meta.get("task_id") != task_id:
            print(
                f"ERROR: meta.task_id mismatch: "
                f"expected {task_id}, got {meta.get('task_id')}",
                file=sys.stderr,
            )
            sys.exit(1)

        # Validate meta.workspace_id
        if meta.get("workspace_id") != workspace_id:
            print(
                f"ERROR: meta.workspace_id mismatch: "
                f"expected {workspace_id}, got {meta.get('workspace_id')}",
                file=sys.stderr,
            )
            sys.exit(1)

        execution = meta.get("execution", {})

        # Validate meta.execution.start_id
        meta_start_id = execution.get("start_id", "")
        if meta_start_id != start_id:
            print(
                f"ERROR: meta.execution.start_id mismatch: "
                f"expected {start_id}, got {meta_start_id}",
                file=sys.stderr,
            )
            sys.exit(1)

        # Validate meta.execution.profile
        meta_profile = execution.get("profile", "")
        if meta_profile != profile:
            print(
                f"ERROR: meta.execution.profile mismatch: "
                f"expected {profile}, got {meta_profile}",
                file=sys.stderr,
            )
            sys.exit(1)

        # Validate meta.execution.spec_revision
        meta_revision = execution.get("spec_revision", -1)
        if meta_revision != spec_revision:
            print(
                f"ERROR: meta.execution.spec_revision mismatch: "
                f"expected {spec_revision}, got {meta_revision}",
                file=sys.stderr,
            )
            sys.exit(1)

        # Validate meta.execution.spec_sha256
        meta_sha256 = execution.get("spec_sha256", "")
        if meta_sha256 != spec_sha256:
            print(
                f"ERROR: meta.execution.spec_sha256 mismatch: "
                f"expected {spec_sha256}, got {meta_sha256}",
                file=sys.stderr,
            )
            sys.exit(1)

        # Compute actual SPEC.md SHA-256 and verify
        spec_path = task_dir / "SPEC.md"
        if not spec_path.exists():
            print(f"ERROR: SPEC.md not found in {task_dir}", file=sys.stderr)
            sys.exit(1)
        with open(spec_path, "r", encoding="utf-8") as f:
            actual_spec_content = f.read()
        actual_sha256 = _compute_sha256(actual_spec_content)
        if actual_sha256 != spec_sha256:
            print(
                f"ERROR: SPEC.md SHA-256 mismatch: "
                f"expected {spec_sha256}, actual {actual_sha256}",
                file=sys.stderr,
            )
            sys.exit(1)

        # ------------------------------------------------------------------
        # 10. If meta.status is already terminal with same start_id → idempotent
        # ------------------------------------------------------------------
        meta_status = meta.get("status", "")
        meta_execution = meta.get("execution", {})

        if meta_status in _TERMINAL_STATUSES:
            existing_start_id = meta_execution.get("start_id", "")
            if existing_start_id == start_id:
                # Same task, same start_id, already terminal → idempotent
                sys.exit(0)
            else:
                # Already terminal with different start_id → conflict
                print(
                    f"ERROR: task already terminal with different start_id: "
                    f"{existing_start_id}",
                    file=sys.stderr,
                )
                sys.exit(3)

        # ------------------------------------------------------------------
        # 11. meta.status MUST be "running"
        # ------------------------------------------------------------------
        if meta_status != "running":
            print(
                f"ERROR: meta.status is '{meta_status}', expected 'running'",
                file=sys.stderr,
            )
            sys.exit(3)

        # ------------------------------------------------------------------
        # 12. Reconcile meta — P11-A: Cancel/Timeout/Completion precedence
        # ------------------------------------------------------------------
        # Precedence:
        #   A. Already terminal → don't change (handled above)
        #   B. Cancel intent durable (signal_sent/confirmed) → cancelled
        #   C. Timeout triggered (SIGTERM exit + timeout configured) → timeout
        #   D. Worker completion receipt → done/failed/needs_input based on outcome
        #
        # Never allow: same task with both cancelled+timeout
        termination = meta_execution.get("termination")
        defer_terminalization = False
        new_status: str = "running"

        # Check B: Cancel intent takes precedence over timeout
        cancel_confirmed = False
        if termination:
            term_reason = termination.get("reason")
            term_state = termination.get("state", "requested")
            term_process_session_id = termination.get("process_session_id")
            term_start_id = termination.get("start_id")
            ids_match = (
                term_process_session_id == execution.get("process_session_id")
                and term_start_id == execution.get("start_id")
            )
            if (
                term_reason == "user_cancel"
                and ids_match
                and term_state in ("signal_sent", "confirmed")
            ):
                cancel_confirmed = True
                new_status = "cancelled"
                termination["state"] = "confirmed"
                termination["confirmed_at"] = completed_at
            elif (
                term_reason == "user_cancel"
                and ids_match
                and term_state == "requested"
            ):
                defer_terminalization = True
                new_status = "running"

        # Check C: Timeout (only if not cancelled)
        timeout_triggered = False
        if not cancel_confirmed and not defer_terminalization:
            signal_exit_codes = {143, 137}  # SIGTERM, SIGKILL
            if timeout_seconds > 0 and exit_code in signal_exit_codes:
                timeout_triggered = True
                new_status = "timeout"
            elif worker_outcome == "needs_input":
                new_status = "needs_input"
            else:
                new_status = "done" if exit_code == 0 else "failed"

        new_meta = dict(meta)
        new_meta["status"] = new_status

        new_execution = dict(meta_execution)
        new_execution["completed_at"] = completed_at
        new_execution["exit_code"] = exit_code
        new_execution["outcome"] = outcome
        new_execution["completion_receipt_path"] = receipt_filename
        # P8-C: durable needs_input metadata
        new_execution["worker_outcome"] = worker_outcome
        if needs_input_reason:
            new_execution["needs_input_reason"] = needs_input_reason
            new_execution["needs_input_at"] = completed_at
        else:
            new_execution["needs_input_reason"] = None
            new_execution["needs_input_at"] = None

        # P11-A: Add timeout metadata when timeout triggered
        if timeout_triggered:
            new_execution["timeout_triggered"] = True
            new_execution["timeout_at"] = completed_at
            new_execution["timeout_signal_sent"] = True
            new_execution["timeout_kill_escalated"] = (exit_code == 137)
            new_execution["timeout_exit_code"] = exit_code
            new_execution["process_tree_stopped"] = True
        elif timeout_seconds > 0 and not timeout_triggered:
            # Timeout was configured but not triggered (worker finished on time)
            new_execution["timeout_triggered"] = False

        if defer_terminalization:
            new_execution["reconciliation_state"] = "awaiting_termination_result"
            new_execution["reconciliation_source"] = "trusted_finalizer_deferred"
        else:
            new_execution["reconciliation_state"] = "reconciled"
            new_execution["reconciliation_source"] = "trusted_finalizer"

        # ------------------------------------------------------------------
        # 12b. P8-A: Postflight scope verification
        # ------------------------------------------------------------------
        scope_result: dict[str, Any] = {"scope_compliance": "unknown", "violated_paths": []}
        if workspace_root:
            ws_root = Path(workspace_root)
            if ws_root.is_dir():
                try:
                    scope_result = verify_scope(task_dir, ws_root)
                except Exception:
                    scope_result = {"scope_compliance": "unknown", "violated_paths": []}
        new_execution["scope_compliance"] = scope_result.get("scope_compliance", "unknown")
        new_execution["scope_violated_paths"] = scope_result.get("violated_paths", [])

        # ------------------------------------------------------------------
        # 12c. P11-B: Worker log handling — truncate if needed, add metadata
        # ------------------------------------------------------------------
        MAX_WORKER_LOG_BYTES = 5 * 1024 * 1024  # 5MB
        log_truncated = False
        log_size = 0
        original_bytes_estimate = 0
        if worker_log_path:
            log_file = Path(worker_log_path)
            if log_file.exists():
                try:
                    log_size = log_file.stat().st_size
                    if log_size > MAX_WORKER_LOG_BYTES:
                        original_bytes_estimate = log_size
                        # Truncate to last MAX_WORKER_LOG_BYTES bytes
                        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read()
                        # Keep last MAX_WORKER_LOG_BYTES chars (rough byte approximation)
                        retained = content[-MAX_WORKER_LOG_BYTES:]
                        with open(log_file, "w", encoding="utf-8") as f:
                            f.write(retained)
                            f.flush()
                            os.fsync(f.fileno())
                        log_truncated = True
                        log_size = len(retained.encode("utf-8"))
                except (OSError, IOError):
                    pass

        # Add finalizer summary to worker log
        if worker_log_path:
            log_file = Path(worker_log_path)
            try:
                summary_line = (
                    f"\n--- AOTA FINALIZER SUMMARY ---\n"
                    f"finalized_at={completed_at}\n"
                    f"exit_code={exit_code}\n"
                    f"outcome={outcome}\n"
                    f"status={new_status}\n"
                    f"--- END FINALIZER SUMMARY ---\n"
                )
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(summary_line)
                    f.flush()
                    os.fsync(f.fileno())
            except (OSError, IOError):
                pass

        # Write worker_log metadata to execution block
        new_execution["worker_log"] = {
            "path": f"worker.{start_id}.log",
            "size_bytes": log_size,
            "truncated": log_truncated,
        }
        if log_truncated:
            new_execution["worker_log"]["original_bytes_estimate"] = original_bytes_estimate
            new_execution["worker_log"]["retained_bytes"] = log_size
            new_execution["worker_log"]["log_truncated"] = True

        new_meta["execution"] = new_execution

        # ------------------------------------------------------------------
        # 13. Atomic meta write: temp sibling → flush → fsync → os.replace
        # ------------------------------------------------------------------
        _atomic_write_json(meta_path, new_meta)

        # ------------------------------------------------------------------
        # P8-D: Create durable handoff after terminal state is durable
        # ------------------------------------------------------------------
        if new_status not in ("running",):
            try:
                # Load handoff_common via importlib for standalone compatibility
                _handoff_common_path = Path(__file__).resolve().parent / "_handoff_common.py"
                _hc_spec = importlib.util.spec_from_file_location(
                    "_handoff_common", str(_handoff_common_path)
                )
                _hc_mod = None
                if _hc_spec and _hc_spec.loader:
                    _hc_mod = importlib.util.module_from_spec(_hc_spec)
                    _hc_spec.loader.exec_module(_hc_mod)
                if _hc_mod:
                    build_handoff_data = _hc_mod.build_handoff_data
                    find_existing_handoff = _hc_mod.find_existing_handoff
                    write_handoff_atomic = _hc_mod.write_handoff_atomic
                else:
                    raise ImportError("cannot load _handoff_common")

                existing_hid = find_existing_handoff(
                    workspace_id, task_id, start_id
                )
                if existing_hid is None:
                    task_kind = meta.get("task_kind", "")
                    subject_task_id = meta.get("subject_task_id")
                    handoff_data = build_handoff_data(
                        workspace_id=workspace_id,
                        task_id=task_id,
                        start_id=start_id,
                        profile=profile,
                        task_kind=task_kind,
                        terminal_status=new_status,
                        created_at=completed_at,
                        subject_task_id=subject_task_id,
                        needs_input_reason=needs_input_reason,
                        task_dir=task_dir,
                    )
                    write_handoff_atomic(workspace_id, handoff_data)
            except Exception as e:
                # Handoff write failure → task remains terminal, log but don't revert
                print(
                    f"HANDOFF_CREATION_FAILED: {e}",
                    file=sys.stderr,
                )

    finally:
        # ------------------------------------------------------------------
        # 14. Release lock
        # ------------------------------------------------------------------
        _release_lock(lock_fd)

    # ------------------------------------------------------------------
    # 15. Exit 0
    # ------------------------------------------------------------------
    sys.exit(0)


# ---------------------------------------------------------------------------
# Standalone CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AOTA Profile Task Finalizer (P6)",
    )
    parser.add_argument("--workspace-id", required=True, help="Workspace ID")
    parser.add_argument("--task-id", required=True, help="Task ID")
    parser.add_argument("--start-id", required=True, help="Start ID (P5: task_id)")
    parser.add_argument("--profile", required=True, help="Named Profile")
    parser.add_argument(
        "--spec-revision", type=int, required=True, help="SPEC revision number"
    )
    parser.add_argument("--spec-sha256", required=True, help="SPEC SHA-256 hex digest")
    parser.add_argument(
        "--exit-code", type=int, required=True, help="Worker process exit code"
    )
    parser.add_argument(
        "--workspace-root", default="", help="Workspace root path for scope verification"
    )
    parser.add_argument(
        "--timeout-seconds", type=int, default=0,
        help="Optional timeout in seconds (0 = disabled)",
    )
    parser.add_argument(
        "--worker-log-path", default="",
        help="Path to worker.log for finalizer summary",
    )

    args = parser.parse_args()

    run_finalize(
        workspace_id=args.workspace_id,
        task_id=args.task_id,
        start_id=args.start_id,
        profile=args.profile,
        spec_revision=args.spec_revision,
        spec_sha256=args.spec_sha256,
        exit_code=args.exit_code,
        workspace_root=args.workspace_root,
        timeout_seconds=args.timeout_seconds,
        worker_log_path=args.worker_log_path,
    )
