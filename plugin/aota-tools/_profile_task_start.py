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
import re
from pathlib import Path
from typing import Mapping

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
            "origin_session_id": {
                "type": "string",
                "maxLength": 200,
                "description": "Optional originating Hermes session ID when trusted invocation context is unavailable.",
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

def handle(args: dict, **kwargs) -> str:
    try:
        return _do_start(args, trusted_session_id=kwargs.get("session_id"))
    except WorkspaceError as e:
        return json.dumps(
            {"status": "rejected", "error": str(e)}, sort_keys=True
        )
    except Exception as e:
        return json.dumps(
            {"status": "failed", "error": str(e)}, sort_keys=True
        )


def _validate_origin_session_id(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 200:
        raise WorkspaceError("invalid_origin_session_id")
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", value):
        raise WorkspaceError("invalid_origin_session_id")
    return value


def _do_start(args: dict, trusted_session_id: object = None) -> str:
    # ------------------------------------------------------------------
    # Extract parameters
    # ------------------------------------------------------------------
    workspace_id: str = args.get("workspace_id", "")
    task_id: str = args.get("task_id", "")
    expected_revision: int = args.get("expected_revision", 0)
    expected_spec_sha256: str = args.get("expected_spec_sha256", "")
    timeout_seconds_raw = args.get("timeout_seconds")
    origin_session_id = _validate_origin_session_id(trusted_session_id)
    origin_source: str | None = "hermes_session" if origin_session_id else None
    if origin_session_id is None:
        origin_session_id = _validate_origin_session_id(args.get("origin_session_id"))
        origin_source = "explicit_argument" if origin_session_id else None

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
            origin_session_id=origin_session_id,
            origin_source=origin_source,
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
    origin_session_id: str | None,
    origin_source: str | None,
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
        # P11-K: Review subject SPEC binding verification
        bound_revision = existing_meta.get("subject_spec_revision")
        bound_sha256 = existing_meta.get("subject_spec_sha256")
        # Only verify if binding exists (legacy review tasks may not have binding)
        if bound_revision is not None and bound_sha256:
            subject_dir = get_task_dir(workspace_id, subject_task_id)
            subject_meta = load_meta(subject_dir)
            current_subject_revision = subject_meta.get("revision")
            current_subject_sha256 = subject_meta.get("spec_sha256")
            if current_subject_revision != bound_revision or current_subject_sha256 != bound_sha256:
                raise WorkspaceError(
                    f"review_subject_stale: subject SPEC has changed since review binding. "
                    f"bound revision={bound_revision}, current={current_subject_revision}; "
                    f"bound hash={bound_sha256[:12]}..., current={current_subject_sha256[:12] if current_subject_sha256 else 'None'}..."
                )

    if task_kind == "architecture":
        if not subject_task_id:
            raise WorkspaceError(
                "architecture_missing_subject: architecture task requires subject_task_id"
            )
        _verify_subject_exists(workspace_id, subject_task_id)
        # P11-J.1-A: For spec_preflight, verify subject SPEC binding
        architecture_mode = existing_meta.get("architecture_mode")
        if architecture_mode == "spec_preflight":
            bound_revision = existing_meta.get("subject_spec_revision")
            bound_sha256 = existing_meta.get("subject_spec_sha256")
            if bound_revision is None or not bound_sha256:
                raise WorkspaceError(
                    "preflight_binding_missing: spec_preflight task missing subject_spec_revision/subject_spec_sha256"
                )
            # Read current subject meta and verify match
            subject_dir = get_task_dir(workspace_id, subject_task_id)
            subject_meta = load_meta(subject_dir)
            current_revision = subject_meta.get("revision")
            current_sha256 = subject_meta.get("spec_sha256")
            if current_revision != bound_revision or current_sha256 != bound_sha256:
                raise WorkspaceError(
                    f"preflight_stale: subject SPEC has changed since preflight binding. "
                    f"bound revision={bound_revision}, current={current_revision}; "
                    f"bound hash={bound_sha256[:12]}..., current={current_sha256[:12] if current_sha256 else 'None'}..."
                )

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
    architecture_mode: str | None = existing_meta.get("architecture_mode")
    role_contract: dict = spec.get("role_contract", {})
    process_path: str | None = spec.get("process_path")
    validation_tier: int | None = spec.get("validation_tier")
    human_checkpoints: list[str] = spec.get("human_checkpoints", [])

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
        architecture_mode=architecture_mode,
        role_contract=role_contract,
        process_path=process_path,
        validation_tier=validation_tier,
        human_checkpoints=human_checkpoints,
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

    # P8-C.5: Inject trusted execution context into worker env.
    # Credential routing is Profile-driven: read the derived Profile's model
    # provider, use its key_env only as the parent environment lookup name,
    # and use its api as the child base URL.  Never read delegation credentials.
    _provider_name, _provider_config = _load_profile_provider_config(derived_profile)
    _opencode_key_env, _opencode_base_url = _resolve_profile_credentials(_provider_config)
    # Pass the credential by environment-name mapping, never by interpolating
    # its value into the visible launcher command.
    _opencode_export = (
        f'export OPENCODE_GO_API_KEY="${{{_opencode_key_env}}}"; '
        f"export OPENCODE_GO_BASE_URL={shlex.quote(_opencode_base_url)}; "
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

    # P11-L.1C: Only trusted launch-time verified revision/SHA are injected.
    # These override (not inherit) any parent-propagated values — the env_exports
    # are prepended to the shell command, creating a sanitized child environment
    # that does NOT trust parent-process AOTA_PROFILE_TASK_SPEC_* values.
    env_exports = (
        f"export AOTA_PROFILE_TASK_WORKSPACE_ID={shlex.quote(workspace_id)}; "
        f"export AOTA_PROFILE_TASK_ID={shlex.quote(task_id)}; "
        f"export AOTA_PROFILE_TASK_START_ID={shlex.quote(task_id)}; "
        f"export AOTA_PROFILE_TASK_PROFILE={shlex.quote(derived_profile)};"
        f"{_opencode_export}"
        f"export AOTA_PROFILE_TASK_DIR={shlex.quote(str(task_dir))};"
        # Bound SPEC identity — frozen at spawn, overrides any parent value
        f"export AOTA_PROFILE_TASK_SPEC_REVISION={shlex.quote(str(current_revision))}; "
        f"export AOTA_PROFILE_TASK_SPEC_SHA256={q_sha};"
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
            f"WORKER_PGID=$(cat /proc/$WORKER_PID/stat 2>/dev/null | awk '{{print $5}}'); "
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
    new_meta["origin_session_id"] = origin_session_id
    new_meta["origin_profile"] = None
    new_meta["origin_source"] = origin_source
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

    # P11-L.1C: Launch binding receipt — frozen at spawn boundary, not a separate
    # drift-able authority. Records that the binding was injected into child env.
    new_meta["execution"]["launch_binding"] = {
        "injected": True,
        "spec_revision": current_revision,
        "spec_sha256": actual_spec_sha256,
        "injector_version": 1,
        "injector_source": "aota_profile_task_start",
        "injected_at": now,
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

_NAMED_PROFILE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def _resolve_global_hermes_root_with_source(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> tuple[Path, str]:
    """Resolve the one trusted global Hermes root from the runtime contract.

    Hermes sets ``HERMES_HOME`` to either the global root or the current
    profile home.  A profile-local value is recognised only for the exact
    ``<global-root>/profiles/<profile>`` layout; deeper nesting is invalid
    rather than guessed.  No filesystem search or cwd fallback is used.
    """
    env = os.environ if environ is None else environ
    raw_home = env.get("HERMES_HOME")
    source = "HERMES_HOME" if raw_home else "default_home"
    candidate = Path(raw_home).expanduser() if raw_home else (
        (Path.home() if home is None else Path(home)) / ".hermes"
    )
    candidate = candidate.resolve()

    if candidate.name == "profiles":
        raise WorkspaceError(f"HERMES_ROOT_INVALID: root_source={source}")

    if candidate.parent.name == "profiles":
        global_root = candidate.parent.parent.resolve()
        # ``.../profiles/current/profiles/target`` is not a supported profile
        # home.  Treat it as an invalid launch context instead of climbing again.
        if global_root.parent.name == "profiles":
            raise WorkspaceError(f"HERMES_ROOT_INVALID: root_source={source}")
        return global_root, source

    return candidate, source


def resolve_global_hermes_root(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the global Hermes root for global or profile-local HERMES_HOME."""
    root, _source = _resolve_global_hermes_root_with_source(
        environ=environ,
        home=home,
    )
    return root


def _validate_named_profile(profile: object) -> str:
    if not isinstance(profile, str) or not _NAMED_PROFILE_RE.fullmatch(profile):
        raise WorkspaceError("invalid_profile_name")
    return profile


def _profile_resolution_details(
    profile: str,
    global_root: Path,
    root_source: str,
) -> str:
    profile_dir = global_root / "profiles" / profile
    return (
        f"profile={profile!r} resolved_global_root={global_root} "
        f"resolved_profile_dir={profile_dir} root_source={root_source}"
    )


def resolve_named_profile_dir(
    profile: object,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve one Named Profile inside the trusted global Hermes tree."""
    profile_name = _validate_named_profile(profile)
    global_root, root_source = _resolve_global_hermes_root_with_source(
        environ=environ,
        home=home,
    )
    profiles_root = global_root / "profiles"
    profile_dir = profiles_root / profile_name
    details = _profile_resolution_details(profile_name, global_root, root_source)
    if not profile_dir.is_dir():
        raise WorkspaceError(f"profile_unavailable: {details}")

    resolved_profiles_root = profiles_root.resolve()
    resolved_profile_dir = profile_dir.resolve()
    try:
        resolved_profile_dir.relative_to(resolved_profiles_root)
    except ValueError:
        raise WorkspaceError(f"profile_path_escape: {details}")
    return resolved_profile_dir


def _resolve_named_profile_config_path(profile: object) -> Path:
    """Resolve the target profile config without permitting symlink escape."""
    profile_dir = resolve_named_profile_dir(profile)
    config_path = profile_dir / "config.yaml"
    if not config_path.is_file():
        raise WorkspaceError(f"profile_unavailable: profile={profile!r} has no config.yaml")
    resolved_config_path = config_path.resolve()
    try:
        resolved_config_path.relative_to(profile_dir)
    except ValueError:
        raise WorkspaceError(f"profile_config_path_escape: profile={profile!r}")
    return resolved_config_path


def _verify_profile_available(profile: str) -> None:
    """Verify the target Named Profile and its config under the global root."""
    _resolve_named_profile_config_path(profile)


def _load_profile_provider_config(profile: str) -> tuple[str | None, dict]:
    """Load derived Profile provider settings without reading credentials."""
    fallback_env = "OPENCODE_GO_API_KEY"
    config_path = _resolve_named_profile_config_path(profile)
    try:
        import yaml as _yaml

        config = _yaml.safe_load(config_path.read_text("utf-8"))
        if not isinstance(config, dict):
            return None, {"key_env": fallback_env}
        model = config.get("model")
        provider_name = model.get("provider") if isinstance(model, dict) else None
        providers = config.get("providers")
        provider = providers.get(provider_name) if isinstance(providers, dict) else None
        if not isinstance(provider_name, str) or not isinstance(provider, dict):
            return None, {"key_env": fallback_env}
        result = dict(provider)
        key_env = result.get("key_env")
        result["key_env"] = key_env.strip() if isinstance(key_env, str) and key_env.strip() else fallback_env
        return provider_name, result
    except Exception:
        return None, {"key_env": fallback_env}


def _resolve_profile_credentials(provider_config: dict) -> tuple[str, str]:
    """Return credential env-name mapping and URL without reading secrets."""
    key_env = provider_config.get("key_env") or "OPENCODE_GO_API_KEY"
    if not isinstance(key_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env):
        key_env = "OPENCODE_GO_API_KEY"
    profile_api = provider_config.get("api")
    base_url = (
        profile_api.strip() if isinstance(profile_api, str) and profile_api.strip()
        else os.environ.get("OPENCODE_GO_BASE_URL", "")
    )
    return key_env, base_url


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


def _verify_subject_exists(workspace_id: str, subject_task_id: str) -> None:
    """Verify the subject task exists (for architecture review)."""
    subject_dir = get_task_dir(workspace_id, subject_task_id)
    if not subject_dir.is_dir():
        raise WorkspaceError(
            f"subject_not_found: subject task '{subject_task_id}' "
            f"not found in workspace '{workspace_id}'"
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
