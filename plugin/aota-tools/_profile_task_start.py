"""aota_profile_task_start — start one existing validated AOTA draft task.

P5: Profile Task Start Foundation.
This tool verifies exact revision + SPEC SHA-256, derives the profile from
task_kind, uses the existing Hermes terminal background completion rail,
and transitions the task lifecycle from draft to running.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import tempfile
from pathlib import Path

from ._profile_task_common import (
    validate_task_id,
    derive_profile,
    resolve_runner,
    generate_worker_prompt,
    build_worker_command,
)
from ._task_spec_common import (
    PROFILE_TASK_ROOT,
    TASK_KINDS,
    TASK_KIND_PROFILE_HINT,
    STATUS_DRAFT,
    compute_sha256,
    acquire_lock,
    release_lock,
    read_json,
    write_json,
    load_meta,
    load_spec_md,
    get_task_dir,
    get_lock_path,
    utc_now_iso,
)
from ._workspace import WorkspaceError, resolve_workspace

TOOL_NAME = "aota_profile_task_start"
TOOLSET_NAME = "aota_profile_task"

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Start one existing validated AOTA draft task using its fixed derived Named Profile. "
        "This tool verifies exact revision and SPEC SHA-256, derives profile from task_kind, "
        "and uses the existing Hermes background completion rail. "
        "It does not accept arbitrary commands or profiles. "
        "For implementation tasks: call only after explicit human approval of the exact revision/hash. "
        "This tool does not create a SPEC, approve a task, query status, cancel, or retry."
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
                "description": "Existing AOTA task ID to start",
            },
            "expected_revision": {
                "type": "integer",
                "description": "Expected SPEC revision number for optimistic concurrency",
            },
            "expected_spec_sha256": {
                "type": "string",
                "description": "Expected SHA-256 hex digest of the exact SPEC.md content",
            },
            "timeout_seconds": {
                "type": "integer",
                "minimum": 30,
                "maximum": 86400,
                "description": (
                    "Optional hard timeout for the worker process in seconds (30-86400). "
                    "Worker process tree receives SIGTERM at deadline, then SIGKILL after 10s grace. "
                    "On timeout, status is set to 'timeout' with durable metadata. "
                    "Default: null (no timeout)."
                ),
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
        return _do_start(args)
    except WorkspaceError as e:
        return json.dumps(
            {"status": "rejected", "error": str(e)}, sort_keys=True
        )
    except Exception as e:
        return json.dumps(
            {"status": "failed", "error": str(e)}, sort_keys=True
        )


def _do_start(args: dict) -> str:
    # ------------------------------------------------------------------
    # Extract parameters
    # ------------------------------------------------------------------
    workspace_id: str = args.get("workspace_id", "")
    task_id: str = args.get("task_id", "")
    expected_revision: int = args.get("expected_revision", 0)
    expected_spec_sha256: str = args.get("expected_spec_sha256", "")
    timeout_seconds_raw = args.get("timeout_seconds")

    # Validate timeout_seconds
    timeout_seconds: int | None = None
    if timeout_seconds_raw is not None:
        if not isinstance(timeout_seconds_raw, int) or isinstance(timeout_seconds_raw, bool):
            raise WorkspaceError(
                f"invalid_timeout_seconds: expected integer, got {type(timeout_seconds_raw).__name__}"
            )
        if timeout_seconds_raw < 30 or timeout_seconds_raw > 86400:
            raise WorkspaceError(
                f"invalid_timeout_seconds: value {timeout_seconds_raw} out of range [30, 86400]"
            )
        timeout_seconds = timeout_seconds_raw

    # ------------------------------------------------------------------
    # 1. Validate task_id format
    # ------------------------------------------------------------------
    err = validate_task_id(task_id)
    if err:
        raise WorkspaceError(f"invalid_task_id: {err}")

    # ------------------------------------------------------------------
    # 2. Resolve workspace (live, not from creation-time snapshot)
    # ------------------------------------------------------------------
    workspace_root = resolve_workspace(workspace_id)

    # ------------------------------------------------------------------
    # 3. Locate task directory
    # ------------------------------------------------------------------
    task_dir = get_task_dir(workspace_id, task_id)
    if not task_dir.is_dir():
        raise WorkspaceError(
            f"task_not_found: task '{task_id}' not found in workspace '{workspace_id}'"
        )

    meta_path = task_dir / "meta.json"
    spec_path = task_dir / "SPEC.md"

    # ------------------------------------------------------------------
    # 4. Acquire same P4 task lock
    # ------------------------------------------------------------------
    lock_fd = acquire_lock(workspace_id, task_id, timeout=5.0)
    try:
        return _do_start_locked(
            workspace_id=workspace_id,
            task_id=task_id,
            expected_revision=expected_revision,
            expected_spec_sha256=expected_spec_sha256,
            timeout_seconds=timeout_seconds,
            workspace_root=workspace_root,
            task_dir=task_dir,
            meta_path=meta_path,
            spec_path=spec_path,
            lock_fd=lock_fd,
        )
    finally:
        release_lock(lock_fd)


def _do_start_locked(
    workspace_id: str,
    task_id: str,
    expected_revision: int,
    expected_spec_sha256: str,
    timeout_seconds: int | None,
    workspace_root: Path,
    task_dir: Path,
    meta_path: Path,
    spec_path: Path,
    lock_fd: int,
) -> str:
    # ------------------------------------------------------------------
    # 5. Load meta.json + SPEC.md
    # ------------------------------------------------------------------
    existing_meta = load_meta(task_dir)
    existing_spec_md = load_spec_md(task_dir)

    # ------------------------------------------------------------------
    # 6. Verify meta.task_id == input task_id
    # ------------------------------------------------------------------
    if existing_meta.get("task_id") != task_id:
        raise WorkspaceError(
            "task_id_mismatch: meta.task_id does not match input task_id"
        )

    # ------------------------------------------------------------------
    # 7. Verify meta.workspace_id == input workspace_id
    # ------------------------------------------------------------------
    if existing_meta.get("workspace_id") != workspace_id:
        raise WorkspaceError(
            "workspace_id_mismatch: meta.workspace_id does not match input workspace_id"
        )

    # ------------------------------------------------------------------
    # 8. Status gate: only allow draft
    # ------------------------------------------------------------------
    current_status = existing_meta.get("status", "")
    if current_status != STATUS_DRAFT:
        raise WorkspaceError(
            f"task_not_startable: task '{task_id}' has status "
            f"'{current_status}', expected '{STATUS_DRAFT}'"
        )

    # ------------------------------------------------------------------
    # 9. Revision check
    # ------------------------------------------------------------------
    current_revision: int = existing_meta.get("revision", 0)
    if expected_revision != current_revision:
        raise WorkspaceError(
            f"revision_conflict: expected_revision={expected_revision}, "
            f"actual_revision={current_revision}"
        )

    # ------------------------------------------------------------------
    # 10. Compute actual SPEC SHA-256
    # ------------------------------------------------------------------
    actual_spec_sha256 = compute_sha256(existing_spec_md)

    # 10a. Verify meta hash matches actual file
    stored_hash = existing_meta.get("spec_sha256", "")
    if actual_spec_sha256 != stored_hash:
        raise WorkspaceError(
            f"artifact_integrity_mismatch: stored={stored_hash}, "
            f"actual={actual_spec_sha256}"
        )

    # 10b. Verify expected hash matches actual
    if expected_spec_sha256 != actual_spec_sha256:
        raise WorkspaceError(
            f"spec_hash_conflict: expected={expected_spec_sha256}, "
            f"actual={actual_spec_sha256}"
        )

    # ------------------------------------------------------------------
    # 11. Validate task_kind
    # ------------------------------------------------------------------
    task_kind = existing_meta.get("task_kind", "")
    if task_kind not in TASK_KINDS:
        raise WorkspaceError(
            f"unknown task_kind in meta: {task_kind!r}"
        )

    # ------------------------------------------------------------------
    # 11b. P8-C: Approval enforcement for implementation tasks
    # ------------------------------------------------------------------
    if task_kind == "implementation":
        approval_path = task_dir / "APPROVAL.json"
        if not approval_path.exists():
            raise WorkspaceError(
                "human_checkpoint_required: implementation tasks require "
                "explicit approval before start. Use aota_profile_task_approve "
                "with exact revision/hash."
            )
        try:
            import json as _json
            approval = _json.loads(approval_path.read_text("utf-8"))
        except Exception:
            raise WorkspaceError(
                "human_checkpoint_required: APPROVAL.json is unreadable. "
                "Re-approve the task with aota_profile_task_approve."
            )
        approval_rev = approval.get("revision")
        approval_hash = approval.get("spec_sha256")
        if approval_rev != current_revision or approval_hash != actual_spec_sha256:
            raise WorkspaceError(
                f"approval_stale: APPROVAL.json is for rev={approval_rev} "
                f"but current rev={current_revision}. "
                f"Re-approve the updated SPEC with aota_profile_task_approve."
            )

    # ------------------------------------------------------------------
    # 12. Derive profile from task_kind
    # ------------------------------------------------------------------
    derived_profile = derive_profile(task_kind)

    # 13. Verify meta.profile_hint == derived profile (no fallback)
    profile_hint = existing_meta.get("profile_hint", "")
    if profile_hint != derived_profile:
        raise WorkspaceError(
            f"profile_routing_mismatch: meta.profile_hint={profile_hint!r}, "
            f"derived={derived_profile!r}"
        )

    # ------------------------------------------------------------------
    # 14. Profile availability preflight
    # ------------------------------------------------------------------
    _verify_profile_available(derived_profile)

    # ------------------------------------------------------------------
    # 14b. Review preflight: verify subject task
    # ------------------------------------------------------------------
    subject_task_id: str | None = existing_meta.get("subject_task_id")
    if task_kind == "review":
        if not subject_task_id:
            raise WorkspaceError(
                "review_missing_subject: review task requires subject_task_id"
            )
        _verify_subject_reviewable(workspace_id, subject_task_id)

    # ------------------------------------------------------------------
    # 15. Runner availability preflight
    # ------------------------------------------------------------------
    try:
        runner = resolve_runner()
    except WorkspaceError:
        raise WorkspaceError("runner_unavailable: no hermes runner found")

    # ------------------------------------------------------------------
    # 15. Generate worker prompt
    # ------------------------------------------------------------------
    spec = existing_meta.get("spec", {})
    read_scope_list: list[str] = spec.get("read_scope", [])
    write_scope_list: list[str] = spec.get("write_scope", [])
    forbidden_scope_list: list[str] = spec.get("forbidden_scope", [])

    worker_prompt = generate_worker_prompt(
        task_id=task_id,
        workspace_id=workspace_id,
        task_kind=task_kind,
        derived_profile=derived_profile,
        meta_path=str(meta_path),
        spec_path=str(spec_path),
        read_scope=read_scope_list,
        write_scope=write_scope_list,
        forbidden_scope=forbidden_scope_list,
        subject_task_id=subject_task_id,
    )

    # --------------------------------------------------------------------------
    # 17. Write scope.json — immutable scope manifest
    # --------------------------------------------------------------------------
    scope_manifest = {
        "workspace_id": workspace_id,
        "task_id": task_id,
        "read_scope": read_scope_list,
        "write_scope": write_scope_list,
        "forbidden_scope": forbidden_scope_list,
        "created_at": utc_now_iso(),
        "spec_sha256": actual_spec_sha256,
        "revision": current_revision,
    }
    write_json(task_dir / "scope.json", scope_manifest)

    # --------------------------------------------------------------------------
    # 18. Build worker command
    # --------------------------------------------------------------------------
    worker_cmd = build_worker_command(
        runner=runner,
        profile=derived_profile,
        worker_prompt=worker_prompt,
    )

    # --------------------------------------------------------------------------
    # 17b. Append finalizer to command chain (P6)
    # --------------------------------------------------------------------------
    finalizer_path = Path(__file__).parent / "_profile_task_finalize.py"
    python_interp = shlex.quote(sys.executable)
    finalizer_quoted = shlex.quote(str(finalizer_path))
    q_ws = shlex.quote(workspace_id)
    q_tid = shlex.quote(task_id)
    q_profile = shlex.quote(derived_profile)
    q_sha = shlex.quote(expected_spec_sha256)
    q_wr = shlex.quote(str(workspace_root))

    # P8-C.5: Inject trusted execution context into worker env
    # Also pass API keys + base URL from Hermes delegation config for worker auth.
    # opencode-go ProviderConfig resolves:
    #   api_key from OPENCODE_GO_API_KEY env var
    #   base_url from OPENCODE_GO_BASE_URL env var (default: https://opencode.ai/zen/go/v1)
    # The profile config's `api` and `key_env` fields are NOT read by
    # the hardcoded PROVIDER_REGISTRY entry — only these env vars matter.
    _opencode_key = ""
    _opencode_base_url = ""
    _hermes_config_path = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))) / "config.yaml"
    if _hermes_config_path.exists():
        try:
            import yaml as _yaml
            _cfg = _yaml.safe_load(_hermes_config_path.read_text("utf-8"))
            _del = _cfg.get("delegation", {})
            _opencode_key = _del.get("api_key", "")
            _opencode_base_url = _del.get("base_url", "")
        except Exception:
            pass
    _opencode_export = (
        f"export OPENCODE_GO_API_KEY={shlex.quote(_opencode_key)}; "
        f"export OPENCODE_GO_BASE_URL={shlex.quote(_opencode_base_url)}; "
        f"export AMF_PROXY_AGNES_KEY={shlex.quote(_opencode_key)}; "
        if _opencode_key else ""
    )
    timeout_iso_deadline: str | None = None
    timeout_quoted: str = ""
    if timeout_seconds is not None:
        # Compute deadline ISO timestamp
        import datetime as _dt
        deadline_dt = _dt.datetime.now(_dt.timezone.utc)
        deadline_ts = deadline_dt.timestamp() + timeout_seconds
        timeout_iso_deadline = _dt.datetime.fromtimestamp(
            deadline_ts, tz=_dt.timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        timeout_quoted = shlex.quote(str(timeout_seconds))

    # Worker log path
    worker_log_path = task_dir / f"worker.{task_id}.log"
    q_worker_log = shlex.quote(str(worker_log_path))

    env_exports = (
        f"export AOTA_PROFILE_TASK_WORKSPACE_ID={shlex.quote(workspace_id)}; "
        f"export AOTA_PROFILE_TASK_ID={shlex.quote(task_id)}; "
        f"export AOTA_PROFILE_TASK_START_ID={shlex.quote(task_id)}; "
        f"export AOTA_PROFILE_TASK_PROFILE={shlex.quote(derived_profile)};"
        f"{_opencode_export}"
        f"export AOTA_PROFILE_TASK_DIR={shlex.quote(str(task_dir))};"
    )
    if timeout_seconds is not None:
        env_exports += (
            f"export AOTA_PROFILE_TASK_TIMEOUT_SECONDS={timeout_quoted};"
            f"export AOTA_PROFILE_TASK_TIMEOUT_DEADLINE_AT={shlex.quote(timeout_iso_deadline or '')};"
        )

    # Build command with optional timeout watchdog and worker.log
    if timeout_seconds is not None:
        # Watchdog runs in background, kills process group on deadline
        watchdog_block = (
            f"( trap '' TERM; sleep {timeout_quoted}; "
            f"WORKER_PGID=$(ps -o pgid= -p $WORKER_PID 2>/dev/null | tr -d ' '); "
            f"kill -TERM -$WORKER_PGID 2>/dev/null; "
            f"sleep 10; "
            f"kill -KILL -$WORKER_PGID 2>/dev/null ) &"
        )
        command = (
            f"set +e"
            f"; {env_exports}"
            # Capture main shell PGID for watchdog
            f" MAIN_PGID=$$;"
            # Put worker in its own process group via set -m and background
            f" set -m;"
            f" ({worker_cmd} 2>&1; echo AOTA_WORKER_EXIT_CODE=$?) | "
            f" tee -a {q_worker_log} &"
            f" WORKER_PID=$!;"
            f" set +m;"
            # Start watchdog
            f" {watchdog_block}"
            f" WATCHDOG_PID=$!;"
            # Wait for worker pipeline to finish
            f" wait $WORKER_PID 2>/dev/null;"
            # Get exit code from the marker
            f" worker_rc=$(grep -o 'AOTA_WORKER_EXIT_CODE=[0-9]*' {q_worker_log} 2>/dev/null | tail -1 | cut -d= -f2);"
            f" worker_rc=${{worker_rc:-$?}};"
            # Kill watchdog
            f" kill -KILL $WATCHDOG_PID 2>/dev/null;"
            f" wait $WATCHDOG_PID 2>/dev/null;"
            # Finalizer
            f" {python_interp} {finalizer_quoted}"
            f" --workspace-id {q_ws}"
            f" --task-id {q_tid}"
            f" --start-id {q_tid}"
            f" --profile {q_profile}"
            f" --spec-revision {current_revision}"
            f" --spec-sha256 {q_sha}"
            f" --workspace-root {q_wr}"
            f' --exit-code "$worker_rc"'
            f" --timeout-seconds {timeout_quoted}"
            f" --worker-log-path {q_worker_log}"
            f"; finalizer_rc=$?"
            f'; if [ "$finalizer_rc" -ne 0 ]; then'
            f' echo "AOTA_PROFILE_TASK_FINALIZE_FAILED task_id={q_tid} start_id={q_tid}" >&2'
            f"; fi"
            f'; exit "$worker_rc"'
        )
    else:
        command = (
            f"set +e"
            f"; {env_exports}"
            # Worker with tee to log file — use PIPESTATUS to get worker exit code
            f" {worker_cmd} 2>&1 | tee -a {q_worker_log};"
            f" worker_rc=${{PIPESTATUS[0]}}"
            f"; {python_interp} {finalizer_quoted}"
            f" --workspace-id {q_ws}"
            f" --task-id {q_tid}"
            f" --start-id {q_tid}"
            f" --profile {q_profile}"
            f" --spec-revision {current_revision}"
            f" --spec-sha256 {q_sha}"
            f" --workspace-root {q_wr}"
            f' --exit-code "$worker_rc"'
            f" --timeout-seconds 0"
            f" --worker-log-path {q_worker_log}"
            f"; finalizer_rc=$?"
            f'; if [ "$finalizer_rc" -ne 0 ]; then'
            f' echo "AOTA_PROFILE_TASK_FINALIZE_FAILED task_id={q_tid} start_id={q_tid}" >&2'
            f"; fi"
            f'; exit "$worker_rc"'
        )

    # --------------------------------------------------------------------------
    # 18. Launch via existing terminal background rail
    # --------------------------------------------------------------------------
    try:
        from tools.terminal_tool import terminal_tool
    except ImportError as e:
        raise WorkspaceError(
            f"background_launch_failed: cannot import terminal_tool: {e}"
        )

    result_json = terminal_tool(
        command=command,
        background=True,
        notify_on_complete=True,
        workdir=str(workspace_root),
    )

    try:
        result = json.loads(result_json)
    except json.JSONDecodeError:
        raise WorkspaceError(
            f"background_launch_failed: unparseable terminal_tool response"
        )

    launch_error = result.get("error")
    if launch_error:
        raise WorkspaceError(
            f"background_launch_failed: {launch_error}"
        )

    process_session_id = result.get("session_id", "")

    # ------------------------------------------------------------------
    # 19. Prepare running meta
    # ------------------------------------------------------------------
    now = utc_now_iso()

    new_meta = dict(existing_meta)
    new_meta["status"] = "running"
    new_meta["execution"] = {
        "start_id": task_id,
        "profile": derived_profile,
        "process_session_id": process_session_id,
        "started_at": now,
        "spec_revision": current_revision,
        "spec_sha256": actual_spec_sha256,
        "transport": "terminal_background",
        "notify_on_complete": True,
        "lifecycle_reconciliation": "pending",
        "completion_receipt_path": f"completion.{task_id}.json",
        "finalizer_version": 1,
        "worker_log": {
            "path": f"worker.{task_id}.log",
            "size_bytes": 0,
            "truncated": False,
        },
    }

    # P11-A: Add timeout metadata when configured
    if timeout_seconds is not None:
        new_meta["execution"]["timeout_seconds"] = timeout_seconds
        new_meta["execution"]["timeout_deadline_at"] = timeout_iso_deadline or ""
        new_meta["execution"]["timeout_triggered"] = False

    # ------------------------------------------------------------------
    # 20. Atomic meta commit
    # ------------------------------------------------------------------
    try:
        commit_meta: dict = dict(new_meta)
        # Temp write then os.replace for atomicity
        fd, tmp_path_str = tempfile.mkstemp(
            dir=str(task_dir),
            prefix=".meta.json.tmp_",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(commit_meta, f, indent=2, sort_keys=True, ensure_ascii=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path_str, str(meta_path))
        except Exception:
            # Rollback: kill the launched process
            try:
                _rollback_kill(process_session_id)
            except Exception:
                pass
            try:
                os.unlink(tmp_path_str)
            except OSError:
                pass
            raise WorkspaceError(
                "start_commit_failed_rolled_back: meta commit failed, "
                "worker process was terminated"
            )
    except WorkspaceError:
        raise
    except Exception as e:
        raise WorkspaceError(
            f"orphan_process_risk: meta commit failed, "
            f"process may still be running "
            f"(process_session_id={process_session_id}): {e}"
        )

    # ------------------------------------------------------------------
    # 21. Return compact success
    # ------------------------------------------------------------------
    human_checkpoint_policy = existing_meta.get("human_checkpoint_policy", "")

    return json.dumps(
        {
            "status": "running",
            "task_id": task_id,
            "workspace_id": workspace_id,
            "task_kind": task_kind,
            "profile": derived_profile,
            "revision": current_revision,
            "spec_sha256": actual_spec_sha256,
            "process_session_id": process_session_id,
            "started_at": now,
            "completion_transport": "terminal_background",
            "human_checkpoint_policy": human_checkpoint_policy,
        },
        sort_keys=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _verify_profile_available(profile: str) -> None:
    """Verify the Named Profile directory exists and is readable.

    Raises WorkspaceError if the profile is unavailable.
    """
    hermes_home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
    profile_dir = Path(hermes_home) / "profiles" / profile
    if not profile_dir.is_dir():
        raise WorkspaceError(
            f"profile_unavailable: profile '{profile}' not found at {profile_dir}"
        )
    # Check config.yaml exists
    config_path = profile_dir / "config.yaml"
    if not config_path.is_file():
        raise WorkspaceError(
            f"profile_unavailable: profile '{profile}' has no config.yaml"
        )


def _verify_subject_reviewable(workspace_id: str, subject_task_id: str) -> None:
    """Verify the subject task exists and is in a reviewable state.

    Reviewable states: done, failed, cancelled.
    draft and running subjects are not reviewable.
    Raises WorkspaceError if the subject is not found or not reviewable.
    """
    subject_dir = get_task_dir(workspace_id, subject_task_id)
    if not subject_dir.is_dir():
        raise WorkspaceError(
            f"subject_not_found: subject task '{subject_task_id}' "
            f"not found in workspace '{workspace_id}'"
        )
    subject_meta = load_meta(subject_dir)
    subject_status = subject_meta.get("status", "unknown")
    _REVIEWABLE_STATES = {"done", "failed", "cancelled"}
    if subject_status not in _REVIEWABLE_STATES:
        raise WorkspaceError(
            f"subject_not_reviewable: subject '{subject_task_id}' "
            f"has status '{subject_status}', "
            f"must be one of: {', '.join(sorted(_REVIEWABLE_STATES))}"
        )


def _rollback_kill(process_session_id: str) -> None:
    """Best-effort kill of a background process during rollback.

    Uses the existing ProcessRegistry termination path.
    """
    try:
        from tools.process_registry import process_registry

        process_registry.kill_process(process_session_id, source="rollback_kill")
    except Exception:
        pass
