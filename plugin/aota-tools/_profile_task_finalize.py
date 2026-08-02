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
import threading
import time
from pathlib import Path
from types import SimpleNamespace
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
_ROLE_RESULT_NAMES = {
    "coder": "RESULT.md",
    "debugger": "DIAGNOSIS.md",
    "reviewer": "REVIEW.md",
    "architect": "ARCHITECT_REVIEW.md",
    "project-steward": "STEWARD_RESULT.md",
}
_LIST_SEPARATOR = ";"
_REGISTERED_MODULE_COMPLETE = "_aota_registered_module_complete"
_SESSION_STATE_AUTHORITY_MODULE_NAME = "aota_tools._session_state_authority_finalizer"
_REGISTERED_MODULE_LOCK = threading.RLock()

# ---------------------------------------------------------------------------
# Validation helpers  (stdlib-only, usable standalone)
# ---------------------------------------------------------------------------


def _load_module_from_path_registered(module_name: str, path: Path) -> Any:
    with _REGISTERED_MODULE_LOCK:
        return _load_module_from_path_registered_unlocked(module_name, path)


def _load_module_from_path_registered_unlocked(module_name: str, path: Path) -> Any:
    """Load one deterministic module name under the import registration contract."""
    module_path = path.resolve()
    spec = importlib.util.spec_from_file_location(module_name, str(module_path))
    if not spec or not spec.loader:
        raise ImportError(f"cannot load module: {module_name}")

    prior = sys.modules.get(spec.name)
    if prior is not None:
        prior_path = getattr(prior, "__file__", None)
        if (
            prior_path
            and Path(prior_path).resolve() == module_path
            and getattr(prior, _REGISTERED_MODULE_COMPLETE, False)
        ):
            return prior
        raise ImportError(f"module name already registered: {spec.name}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if sys.modules.get(spec.name) is module:
            sys.modules.pop(spec.name, None)
        raise
    setattr(module, _REGISTERED_MODULE_COMPLETE, True)
    return module


def _projection_failure(stage: str, exc: BaseException, **evidence: Any) -> dict[str, Any]:
    return {
        "status": "failed",
        "failure_stage": stage,
        "exception_type": type(exc).__name__,
        "exception_message": str(exc)[:300],
        "retryable": False,
        **evidence,
    }


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


def _legacy_delivery_outbox_required(meta: dict[str, Any]) -> bool:
    """Keep outbox writes only for already-started legacy executions.

    New starts are terminal-background-only.  This compatibility predicate
    lets an in-flight task created by an older runtime finish consistently
    without making the retired WebUI/outbox rail part of the native path.
    """
    execution = meta.get("execution")
    if not isinstance(execution, dict):
        return False
    completion_transport = execution.get(
        "completion_transport", execution.get("transport", "")
    )
    return bool(
        completion_transport == "legacy_durable_delivery"
        and execution.get("completion_delivery_expected") is True
    )


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


def _completion_pointers(
    *,
    workspace_id: str,
    task_id: str,
    start_id: str,
    profile: str,
    task_dir: Path,
    handoff_id: str | None,
) -> tuple[str, str]:
    """Return task-root-relative bounded pointers for backend delivery.

    The handoff pointer is a small task-local marker.  The full handoff stays
    in the existing handoff rail; the marker gives the trusted backend a
    canonical, regular file under the Profile Task root without making it
    read the handoff or worker output.
    """
    result_name = _ROLE_RESULT_NAMES.get(profile, "RESULT.md")
    result_path = task_dir / result_name
    result_pointer = (
        f"{workspace_id}/{task_id}/{result_name}"
        if result_path.is_file() and not result_path.is_symlink()
        else ""
    )
    handoff_pointer = ""
    if handoff_id:
        marker_name = f"handoff.{start_id}.json"
        marker_path = task_dir / marker_name
        if not marker_path.exists():
            _atomic_write_json(
                marker_path,
                {
                    "schema_version": 1,
                    "handoff_id": handoff_id,
                    "workspace_id": workspace_id,
                    "task_id": task_id,
                    "start_id": start_id,
                    "source": "profile_task_finalizer",
                },
            )
        if marker_path.is_file() and not marker_path.is_symlink():
            handoff_pointer = f"{workspace_id}/{task_id}/{marker_name}"
    return result_pointer, handoff_pointer


def _write_session_state_pointers(
    *, workspace_id: str, task_id: str, start_id: str, profile: str,
    meta: dict[str, Any], task_dir: Path, handoff_id: str | None,
    terminal_status: str, completed_at: str,
) -> dict[str, Any]:
    """Project terminal lifecycle into the originating trusted session.

    The finalizer is launched from the control plane.  Its durable task meta
    carries the already-validated parent session binding captured at start;
    no model-provided id is consulted here.
    """
    origin = (
        meta.get("origin_session_id")
        or meta.get("parent_session_ref")
        or (meta.get("execution") or {}).get("parent_session_ref")
    )
    if not origin:
        return {
            "status": "failed",
            "failure_stage": "trusted_session_context",
            "error": "trusted_session_context_missing",
            "retryable": False,
        }
    authority_path = Path(__file__).resolve().parent / "_session_state_authority.py"
    try:
        authority = _load_module_from_path_registered(
            _SESSION_STATE_AUTHORITY_MODULE_NAME, authority_path
        )
    except BaseException as exc:
        return _projection_failure("authority_module_load", exc)

    context = SimpleNamespace(
        session_id=origin, principal="task-main", profile="task-main",
        workspace_id=workspace_id, worker_context=False,
    )
    receipt_name = f"completion.{start_id}.json"
    subject = {
        "workspace_id": workspace_id, "project_id": meta.get("project_id"),
        "task_id": task_id, "start_id": start_id, "handoff_id": handoff_id,
        "spec_revision": meta.get("revision") or (meta.get("execution") or {}).get("spec_revision"),
        "spec_hash": meta.get("spec_hash") or (meta.get("execution") or {}).get("spec_hash"),
        "spec_sha256": meta.get("spec_sha256") or (meta.get("execution") or {}).get("spec_sha256"),
        "profile": profile, "terminal_status": terminal_status,
        "outcome": (meta.get("execution") or {}).get("outcome") or terminal_status,
        "completed_at": completed_at, "receipt_ref": f"{workspace_id}/{task_id}/{receipt_name}",
        "card_ref": f"{workspace_id}/{task_id}/{_ROLE_RESULT_NAMES.get(profile, 'RESULT.md')}",
        "full_report_ref": f"{workspace_id}/{task_id}/COMPLETION.md",
        "origin_session_id": origin,
    }
    task_meta = dict(meta)
    task_meta["execution"] = dict(meta.get("execution") or {})
    task_meta["execution"]["outcome"] = subject["outcome"]
    completed_steps: dict[str, str] = {}
    try:
        active, active_path = authority.write_active_task_pointer(
            context, task_meta, start_id=start_id, state="terminal"
        )
        completed_steps["active_task_terminal"] = str(active_path)
        completion, completion_path = authority.write_current_completion_pointer(context, subject)
        completed_steps["current_completion_written"] = str(completion_path)
        handoff, handoff_path = authority.write_current_handoff_pointer(context, subject)
        completed_steps["current_handoff_written"] = str(handoff_path)
        completed, completed_path = authority.write_current_completed_task_pointer(context, subject)
        completed_steps["current_completed_task_written"] = str(completed_path)
        return {
            "status": "written", "current_completion": str(completion_path),
            "current_handoff": str(handoff_path), "current_completed_task": str(completed_path),
            "artifact_digest": completion["artifact_digest"],
            "active_task_terminal": True, "current_completion_written": True,
            "current_handoff_written": True, "completion_subject_ready": True,
        }
    except BaseException as exc:
        return _projection_failure("pointer_projection", exc, completed_steps=completed_steps)


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
    failure_stage: str = "",
    finalizer_mode: str = "primary",
    command_summary: str = "",
    global_hermes_home: str = "",
    target_profile_home: str = "",
    parent_profile: str = "",
    spec_hash: str = "",
    error_classification: str = "",
    primary_finalizer_status: str = "not_executed",
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

    # Canonical WI-09C binding is read from task metadata.  The legacy
    # SPEC.md digest remains a separate integrity field.
    try:
        binding_meta = _read_json(task_dir / "meta.json")
    except (OSError, json.JSONDecodeError):
        binding_meta = {}
    meta_execution = binding_meta.get("execution") if isinstance(binding_meta.get("execution"), dict) else {}
    completion_transport = (
        meta_execution.get("completion_transport")
        or meta_execution.get("transport")
        or "terminal_background"
    )
    canonical_spec_hash = spec_hash or binding_meta.get("spec_hash") or spec_sha256
    if not isinstance(canonical_spec_hash, str) or len(canonical_spec_hash) != 64:
        print(f"ERROR: invalid canonical spec_hash: {canonical_spec_hash!r}", file=sys.stderr)
        sys.exit(1)

    # ------------------------------------------------------------------
    # 4b. P8-C.5: Consume trusted worker-outcome JSON first, then legacy marker
    # ------------------------------------------------------------------
    # Exit code alone is not an authoritative worker outcome.  A clean worker
    # exit without a trusted outcome artifact is a failed execution.
    worker_outcome = "failed"
    outcome_evidence = False
    needs_input_reason: str | None = None

    # Precedence 1: worker-outcome.<start_id>.json (trusted control-plane tool)
    _trusted_outcome_path = task_dir / f"worker-outcome.{start_id}.json"
    if _trusted_outcome_path.exists():
        try:
            _trusted_data = json.loads(_trusted_outcome_path.read_text("utf-8"))
            _parsed_outcome = _trusted_data.get("outcome", "")
            if _parsed_outcome in ("needs_input", "completed", "partial", "failed"):
                worker_outcome = _parsed_outcome
                outcome_evidence = True
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
                        if _parsed_outcome in ("needs_input", "completed", "partial", "failed"):
                            worker_outcome = _parsed_outcome
                            outcome_evidence = True
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
    if exit_code == 0 and not outcome_evidence:
        failure_stage = failure_stage or "worker_execution"
        error_classification = "worker_outcome_missing"
    elif exit_code == 0 and worker_outcome == "completed":
        failure_stage = ""
        error_classification = ""
    outcome = worker_outcome
    completed_at = _utc_now_iso()

    receipt: dict[str, Any] = {
        "schema_version": 1,
        "task_id": task_id,
        "workspace_id": workspace_id,
        "start_id": start_id,
        "profile": profile,
        "spec_revision": spec_revision,
        "spec_hash": canonical_spec_hash,
        "spec_sha256": spec_sha256,
        "spec_id": binding_meta.get("spec_id", task_id),
        "project_id": binding_meta.get("project_id"),
        "work_item_id": binding_meta.get("work_item_id"),
        "resolved_profile": binding_meta.get("resolved_profile", profile),
        "exit_code": exit_code,
        "outcome": outcome,
        "status": "needs_input" if outcome in {"needs_input", "partial"} else ("done" if exit_code == 0 and outcome == "completed" else "failed"),
        "completed_at": completed_at,
        "transport": completion_transport,
        "failure_stage": failure_stage or None,
        "error_classification": error_classification or (
            "worker_failed" if exit_code != 0 and not failure_stage else failure_stage or None
        ),
        "command_summary": command_summary or None,
        "global_hermes_home": global_hermes_home or None,
        "target_profile_home": target_profile_home or None,
        "parent_profile": parent_profile or None,
        "primary_finalizer_status": "completed" if finalizer_mode == "primary" else "not_executed",
        "fallback_finalizer_status": "completed" if finalizer_mode == "fallback" else "not_executed",
        "primary_finalizer_executed": finalizer_mode == "primary",
        "fallback_finalizer_executed": finalizer_mode == "fallback",
        "worker_started": finalizer_mode == "primary" and failure_stage not in {"manifest_load", "binding_validation", "credential_bootstrap", "runner_resolution"},
        "redaction_applied": True,
    }
    if exit_code != 0 or failure_stage:
        receipt["diagnostics"] = _read_log_diagnostics(worker_log_path)
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
                r.get("spec_hash", r.get("spec_sha256")),
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
            elif worker_outcome in {"needs_input", "partial"}:
                new_status = "needs_input"
            elif exit_code == 0 and worker_outcome == "completed":
                new_status = "done"
            else:
                new_status = "failed"

        new_meta = dict(meta)
        new_meta["status"] = new_status

        new_execution = dict(meta_execution)
        new_execution["completed_at"] = completed_at
        new_execution["exit_code"] = exit_code
        new_execution["outcome"] = outcome
        new_execution["spec_hash"] = canonical_spec_hash
        new_execution["completion_receipt_path"] = receipt_filename
        new_execution["failure_stage"] = failure_stage or None
        new_execution["error_classification"] = receipt.get("error_classification")
        new_execution["command_summary"] = command_summary or None
        new_execution["global_hermes_home"] = global_hermes_home or None
        new_execution["target_profile_home"] = target_profile_home or None
        new_execution["parent_profile"] = parent_profile or None
        new_execution["spec_id"] = binding_meta.get("spec_id", task_id)
        new_execution["project_id"] = binding_meta.get("project_id")
        new_execution["work_item_id"] = binding_meta.get("work_item_id")
        new_execution["resolved_profile"] = binding_meta.get("resolved_profile", profile)
        new_execution["finalizer_expected_version"] = 1
        new_execution["primary_finalizer_executed"] = finalizer_mode == "primary"
        new_execution["fallback_finalizer_executed"] = finalizer_mode == "fallback"
        new_execution["worker_started"] = receipt.get("worker_started", False)
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
        scope_result: dict[str, Any] = {"scope_compliance": {"status": "unknown", "scope_source": "", "scope_digest": "", "worker_action_checked_count": 0, "worker_action_violation_count": 0, "project_read_count": 0, "project_write_count": 0, "project_metadata_count": 0, "post_write_verification_read_count": 0, "project_checked_path_count": 0, "project_violated_path_count": 0, "active_task_read_count": 0, "active_task_write_count": 0, "postflight_new_or_changed_path_count": 0, "postflight_unattributed_path_count": 0, "postflight_violation_count": 0, "preexisting_dirty_path_count": 0, "ignored_runtime_path_count": 0, "unknown_external_path_count": 0, "invalid_or_foreign_event_count": 0, "legacy_unattributed_event_count": 0, "violations": []}, "violated_paths": []}
        if workspace_root:
            ws_root = Path(workspace_root)
            if ws_root.is_dir():
                try:
                    scope_result = verify_scope(task_dir, ws_root)
                except Exception:
                    scope_result = {"scope_compliance": {"status": "unknown", "violations": []}, "violated_paths": []}
        new_execution["scope_compliance"] = scope_result.get("scope_compliance", {"status": "unknown", "violations": []})
        new_execution["scope_violated_paths"] = scope_result.get("violated_paths", [])
        for key in ("worker_action_checked_count", "worker_action_violation_count", "project_read_count", "project_write_count", "project_metadata_count", "post_write_verification_read_count", "project_checked_path_count", "project_violated_path_count", "active_task_read_count", "active_task_write_count", "postflight_new_or_changed_path_count", "postflight_unattributed_path_count", "postflight_violation_count", "preexisting_dirty_path_count", "ignored_runtime_path_count", "unknown_external_path_count", "invalid_or_foreign_event_count", "legacy_unattributed_event_count"):
            new_execution[key] = scope_result.get(key, 0)
        receipt["scope_compliance"] = new_execution["scope_compliance"]

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
            try:
                if log_file.stat().st_size > MAX_WORKER_LOG_BYTES:
                    retained_bytes = log_file.read_bytes()[-MAX_WORKER_LOG_BYTES:]
                    log_file.write_bytes(retained_bytes)
                    log_truncated = True
                    log_size = len(retained_bytes)
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

        # Complete the canonical receipt only after terminal status precedence
        # (cancel/timeout/outcome) has been evaluated.
        receipt["status"] = new_status
        receipt["worker_started"] = new_execution.get("worker_started", False)
        receipt["primary_finalizer_executed"] = finalizer_mode == "primary"
        receipt["fallback_finalizer_executed"] = finalizer_mode == "fallback"
        receipt["diagnostic_excerpt"] = receipt.get("diagnostics", "")
        _atomic_write_json(receipt_path, receipt)

        if profile == "project-steward" and new_status == "done":
            card_path = task_dir / "STEWARD_CARD.json"
            report_path = task_dir / "STEWARD_RESULT.md"
            valid = card_path.is_file() and not card_path.is_symlink() and report_path.is_file() and not report_path.is_symlink()
            if valid:
                try:
                    steward_card = _read_json(card_path)
                    expected_card_hash = meta.get("spec_hash") if meta.get("contract_version") == 1 else meta.get("spec_sha256")
                    valid = steward_card.get("role") == "project-steward" and steward_card.get("task_id") == task_id and steward_card.get("spec_id") == task_id and steward_card.get("spec_revision") == meta.get("revision") and steward_card.get("spec_hash") == expected_card_hash
                except Exception:
                    valid = False
            if not valid:
                new_status = "failed"
                new_meta["status"] = new_status
                new_execution["steward_artifact_validation"] = "missing_or_binding_mismatch"

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
                handoff_id_for_outbox: str | None = None
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
                    handoff_id_for_outbox = handoff_data.get("handoff_id")
                else:
                    handoff_id_for_outbox = existing_hid

                _result_pointer, _handoff_pointer = _completion_pointers(
                    workspace_id=workspace_id,
                    task_id=task_id,
                    start_id=start_id,
                    profile=profile,
                    task_dir=task_dir,
                    handoff_id=handoff_id_for_outbox,
                )
                _session_state_result = _write_session_state_pointers(
                    workspace_id=workspace_id, task_id=task_id, start_id=start_id,
                    profile=profile, meta=new_meta, task_dir=task_dir,
                    handoff_id=handoff_id_for_outbox, terminal_status=new_status,
                    completed_at=completed_at,
                )
                if _session_state_result.get("status") != "written":
                    print(
                        "SESSION_STATE_PROJECTION_FAILED "
                        f"failure_stage={_session_state_result.get('failure_stage', 'unknown')} "
                        f"exception_type={_session_state_result.get('exception_type', 'none')} "
                        f"exception_message={json.dumps(_session_state_result.get('exception_message', _session_state_result.get('error', '')), ensure_ascii=False)} "
                        "retryable=false "
                        f"worker_exit_code={exit_code} session_state_projection=failed "
                        "overall_completion_delivery=failed evidence="
                        + json.dumps(_session_state_result, sort_keys=True),
                        file=sys.stderr,
                    )

                # ------------------------------------------------------------------
                # Historical compatibility only: an execution that was already
                # started on the retired legacy rail may finish its outbox write.
                # Native terminal_background completion is owned by Hermes
                # ProcessRegistry and never emits a WebUI/outbox wake event.
                # ------------------------------------------------------------------
                if (
                    _session_state_result.get("status") == "written"
                    and _legacy_delivery_outbox_required(new_meta)
                ):
                    try:
                        # Load _delivery_outbox via importlib (standalone CLI compat)
                        _outbox_mod_path = (
                            Path(__file__).resolve().parent / "_delivery_outbox.py"
                        )
                        _ob_spec = importlib.util.spec_from_file_location(
                            "_delivery_outbox", str(_outbox_mod_path)
                        )
                        if _ob_spec and _ob_spec.loader:
                            _ob_mod = importlib.util.module_from_spec(_ob_spec)
                            _ob_spec.loader.exec_module(_ob_mod)
                            _origin_sid: str | None = meta.get("origin_session_id")
                            _ob_mod.write_outbox_event(
                                workspace_id=workspace_id,
                                task_id=task_id,
                                start_id=start_id,
                                profile=profile,
                                terminal_status=new_status,
                                handoff_id=handoff_id_for_outbox,
                                origin_session_id=_origin_sid,
                                needs_input_reason=needs_input_reason,
                                completed_at=completed_at,
                                parent_profile=(
                                    meta.get("parent_profile")
                                    or meta.get("execution", {}).get("parent_profile")
                                    or ""
                                ),
                                parent_session_ref=(
                                    meta.get("parent_session_ref")
                                    or meta.get("execution", {}).get("parent_session_ref")
                                    or _origin_sid
                                    or ""
                                ),
                                result_pointer=_result_pointer,
                                handoff_pointer=_handoff_pointer,
                            )
                    except Exception as e:
                        # Outbox failure is non-fatal — task state is already durable.
                        print(
                            f"OUTBOX_EVENT_FAILED: {e}",
                            file=sys.stderr,
                        )
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


def write_failure_artifacts(
    *,
    workspace_id: str,
    task_id: str,
    start_id: str,
    profile: str,
    spec_revision: int,
    spec_sha256: str,
    exit_code: int,
    failure_stage: str,
    diagnostics: str = "",
    command_summary: str = "",
    global_hermes_home: str = "",
    target_profile_home: str = "",
    parent_profile: str = "",
    spec_hash: str = "",
    error_classification: str = "",
    finalizer_mode: str = "fallback",
    lock_already_held: bool = False,
    primary_finalizer_status: str = "not_executed",
    replace_success_on_primary_failure: bool = False,
) -> bool:
    """Atomically reconcile a launcher/process failure without a worker card.

    Returns False when an existing completion receipt wins and is preserved.
    This helper is intentionally non-exiting so status reconciliation and the
    start tool can use the same canonical receipt/handoff path.
    """
    task_root = Path(os.environ.get("AOTA_PROFILE_TASK_ROOT", "/aota-runtime/profile-tasks"))
    runtime_root = Path(os.environ.get("AOTA_RUNTIME_ROOT", "/aota-runtime"))
    task_dir = task_root / workspace_id / task_id
    if not task_dir.is_dir():
        return False
    try:
        binding_meta = _read_json(task_dir / "meta.json")
    except (OSError, json.JSONDecodeError):
        binding_meta = {}
    meta_execution = binding_meta.get("execution") if isinstance(binding_meta.get("execution"), dict) else {}
    completion_transport = (
        meta_execution.get("completion_transport")
        or meta_execution.get("transport")
        or "terminal_background"
    )
    canonical_spec_hash = spec_hash or binding_meta.get("spec_hash") or spec_sha256
    receipt_path = task_dir / f"completion.{start_id}.json"
    existing_receipt: dict[str, Any] | None = None
    if receipt_path.exists():
        try:
            existing_receipt = _read_json(receipt_path)
        except (OSError, json.JSONDecodeError):
            existing_receipt = None
        # A valid success receipt is authoritative and must never be replaced
        # by a late fallback path. Non-success/partial receipts may be
        # reconciled into the canonical failure receipt below.
        if (
            existing_receipt
            and existing_receipt.get("status") == "done"
            and existing_receipt.get("exit_code") == 0
            and existing_receipt.get("outcome") == "completed"
            and not replace_success_on_primary_failure
        ):
            return False
    completed_at = _utc_now_iso()
    bounded_diag = _redact_bounded(diagnostics)
    try:
        failure_log_path = task_dir / f"worker.{start_id}.log"
        with failure_log_path.open("a", encoding="utf-8") as log:
            log.write(
                f"AOTA_FALLBACK_FINALIZER failure_stage={failure_stage} "
                f"exit_code={exit_code} diagnostics={bounded_diag}\n"
            )
        if failure_log_path.stat().st_size > 5 * 1024 * 1024:
            failure_log_path.write_bytes(failure_log_path.read_bytes()[-5 * 1024 * 1024:])
    except OSError:
        pass
    receipt = {
        "schema_version": 1,
        "task_id": task_id,
        "workspace_id": workspace_id,
        "start_id": start_id,
        "profile": profile,
        "spec_revision": spec_revision,
        "spec_hash": canonical_spec_hash,
        "spec_sha256": spec_sha256,
        "spec_id": binding_meta.get("spec_id", task_id),
        "project_id": binding_meta.get("project_id"),
        "work_item_id": binding_meta.get("work_item_id"),
        "resolved_profile": binding_meta.get("resolved_profile", profile),
        "exit_code": exit_code,
        "outcome": "failed",
        "status": "failed",
        "completed_at": completed_at,
        "transport": completion_transport,
        "failure_stage": failure_stage,
        "error_classification": error_classification or failure_stage,
        "diagnostics": bounded_diag,
        "command_summary": command_summary or None,
        "global_hermes_home": global_hermes_home or None,
        "target_profile_home": target_profile_home or None,
        "parent_profile": parent_profile or None,
        "primary_finalizer_status": primary_finalizer_status,
        "fallback_finalizer_status": "completed" if finalizer_mode == "fallback" else "not_executed",
        "primary_finalizer_executed": False,
        "fallback_finalizer_executed": finalizer_mode == "fallback",
        "primary_finalizer_error": primary_finalizer_status if primary_finalizer_status != "not_executed" else None,
        "worker_started": primary_finalizer_status != "not_executed",
        "diagnostic_excerpt": bounded_diag,
        "redaction_applied": True,
        "reconciliation_source": "fallback_finalizer",
    }
    _atomic_write_json(receipt_path, receipt)
    lock_path = Path(runtime_root) / "locks" / "task-spec" / workspace_id / f"{task_id}.lock"
    lock_fd: int | None = None
    if not lock_already_held:
        try:
            lock_fd = _acquire_lock(lock_path, timeout=5.0)
        except (TimeoutError, RuntimeError):
            return True
    try:
        meta_path = task_dir / "meta.json"
        if not meta_path.is_file():
            return True
        meta = _read_json(meta_path)
        execution = dict(meta.get("execution", {}))
        if meta.get("status") not in _TERMINAL_STATUSES:
            meta["status"] = "failed"
            execution.update({
                "completed_at": completed_at,
                "exit_code": exit_code,
                "outcome": "failed",
                "worker_outcome": "failed",
                "spec_hash": canonical_spec_hash,
                "spec_id": binding_meta.get("spec_id", task_id),
                "project_id": binding_meta.get("project_id"),
                "work_item_id": binding_meta.get("work_item_id"),
                "resolved_profile": binding_meta.get("resolved_profile", profile),
                "completion_receipt_path": receipt_path.name,
                "failure_stage": failure_stage,
                "error_classification": error_classification or failure_stage,
                "failure_diagnostics": bounded_diag,
                "reconciliation_state": "reconciled",
                "reconciliation_source": "fallback_finalizer",
                "finalizer_expected_version": 1,
                "primary_finalizer_executed": False,
                "fallback_finalizer_executed": finalizer_mode == "fallback",
                "worker_started": primary_finalizer_status != "not_executed",
            })
            meta["execution"] = execution
            _atomic_write_json(meta_path, meta)
    finally:
        if lock_fd is not None:
            _release_lock(lock_fd)

    try:
        _failure_handoff_id = _write_failure_handoff(
            workspace_id, task_id, start_id, profile, meta, completed_at, failure_stage
        )
        _failure_task_dir = task_root / workspace_id / task_id
        _failure_result_pointer, _failure_handoff_pointer = _completion_pointers(
            workspace_id=workspace_id,
            task_id=task_id,
            start_id=start_id,
            profile=profile,
            task_dir=_failure_task_dir,
            handoff_id=_failure_handoff_id,
        )
        _failure_session_state_result = _write_session_state_pointers(
            workspace_id=workspace_id, task_id=task_id, start_id=start_id,
            profile=profile, meta=meta, task_dir=_failure_task_dir,
            handoff_id=_failure_handoff_id, terminal_status="failed",
            completed_at=completed_at,
        )
        if _failure_session_state_result.get("status") != "written":
            print(
                "SESSION_STATE_PROJECTION_FAILED "
                f"failure_stage={_failure_session_state_result.get('failure_stage', 'unknown')} "
                f"exception_type={_failure_session_state_result.get('exception_type', 'none')} "
                f"exception_message={json.dumps(_failure_session_state_result.get('exception_message', _failure_session_state_result.get('error', '')), ensure_ascii=False)} "
                "retryable=false "
                f"worker_exit_code={exit_code} session_state_projection=failed "
                "overall_completion_delivery=failed evidence="
                + json.dumps(_failure_session_state_result, sort_keys=True),
                file=sys.stderr,
            )
            raise RuntimeError("session_state_projection_failed")
        if _legacy_delivery_outbox_required(meta):
            _outbox_path = Path(__file__).resolve().parent / "_delivery_outbox.py"
            _outbox_spec = importlib.util.spec_from_file_location(
                "_delivery_outbox_failure", str(_outbox_path)
            )
            if _outbox_spec and _outbox_spec.loader:
                _outbox_mod = importlib.util.module_from_spec(_outbox_spec)
                _outbox_spec.loader.exec_module(_outbox_mod)
                _outbox_mod.write_outbox_event(
                    workspace_id=workspace_id,
                    task_id=task_id,
                    start_id=start_id,
                    profile=profile,
                    terminal_status="failed",
                    handoff_id=_failure_handoff_id,
                    origin_session_id=(
                        meta.get("parent_session_ref")
                        or meta.get("execution", {}).get("parent_session_ref")
                        or meta.get("origin_session_id")
                        or ""
                    ),
                    completed_at=completed_at,
                    parent_profile=(
                        meta.get("parent_profile")
                        or meta.get("execution", {}).get("parent_profile")
                        or ""
                    ),
                    parent_session_ref=(
                        meta.get("parent_session_ref")
                        or meta.get("execution", {}).get("parent_session_ref")
                        or meta.get("origin_session_id")
                        or ""
                    ),
                    result_pointer=_failure_result_pointer,
                    handoff_pointer=_failure_handoff_pointer,
                )
    except Exception:
        pass
    return True


def _redact_bounded(value: str, limit: int = 8192) -> str:
    text = str(value or "")[:limit]
    text = re.sub(r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(api[_-]?key|token|secret|password)\s*([:=])\s*([^\s,;]+)", r"\1\2[REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED]", text)
    return text


def _read_log_diagnostics(path: str, limit: int = 8192) -> str:
    if not path:
        return ""
    try:
        data = Path(path).read_bytes()[-limit:]
    except OSError:
        return ""
    return _redact_bounded(data.decode("utf-8", errors="replace"), limit)


def _write_failure_handoff(
    workspace_id: str, task_id: str, start_id: str, profile: str,
    meta: dict[str, Any], completed_at: str, failure_stage: str,
) -> str | None:
    module_path = Path(__file__).resolve().parent / "_handoff_common.py"
    spec = importlib.util.spec_from_file_location("_handoff_common_failure", module_path)
    if not spec or not spec.loader:
        raise ImportError("cannot load _handoff_common")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.find_existing_handoff(workspace_id, task_id, start_id) is not None:
        return module.find_existing_handoff(workspace_id, task_id, start_id)
    data = module.build_handoff_data(
        workspace_id=workspace_id,
        task_id=task_id,
        start_id=start_id,
        profile=profile,
        task_kind=meta.get("task_kind", ""),
        terminal_status="failed",
        created_at=completed_at,
        subject_task_id=meta.get("subject_task_id"),
        needs_input_reason=failure_stage,
        task_dir=Path(os.environ.get("AOTA_PROFILE_TASK_ROOT", "/aota-runtime/profile-tasks")) / workspace_id / task_id,
    )
    module.write_handoff_atomic(workspace_id, data)
    return data.get("handoff_id")


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
    parser.add_argument("--failure-stage", default="")
    parser.add_argument("--finalizer-mode", choices=("primary", "fallback"), default="primary")
    parser.add_argument("--command-summary", default="")
    parser.add_argument("--global-hermes-home", default="")
    parser.add_argument("--target-profile-home", default="")
    parser.add_argument("--parent-profile", default="")
    parser.add_argument("--spec-hash", default="")
    parser.add_argument("--error-classification", default="")

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
        failure_stage=args.failure_stage,
        finalizer_mode=args.finalizer_mode,
        command_summary=args.command_summary,
        global_hermes_home=args.global_hermes_home,
        target_profile_home=args.target_profile_home,
        parent_profile=args.parent_profile,
        spec_hash=args.spec_hash,
        error_classification=args.error_classification,
    )
