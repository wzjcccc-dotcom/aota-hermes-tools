"""aota_profile_task_start — start one existing validated AOTA draft task.

P5: Profile Task Start Foundation.
This tool verifies exact revision + SPEC SHA-256, derives the profile from
task_kind, uses the existing Hermes terminal background completion rail,
and transitions the task lifecycle from draft to running.
"""

from __future__ import annotations

import json
import hashlib
import os
import shlex
import subprocess
import sys
import tempfile
import re
import uuid
from pathlib import Path
from typing import Mapping

from ._profile_task_common import (
    validate_task_id,
    derive_profile,
    resolve_runner,
    generate_worker_prompt,
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
from ._spec_traceability import task_lineage
from ._spec_contract import ContractError, ROUTING, canonical_hash, validate_spec, validate_task_binding
from ._profile_task_paths import (
    build_profile_task_runtime_context,
    resolve_global_hermes_home,
    resolve_profile_home,
)
from ._profile_task_env import PROVIDER_METADATA
from ._task_spec_scope import CanonicalScopeError, compute_scope_digest, extract_canonical_scope

TOOL_NAME = "aota_profile_task_start"
TOOLSET_NAME = "aota_profile_task"

# Deployment-owned Plan authority must never cross the Profile Task boundary.
# Keep this allowlist exact: other AOTA worker/runtime variables are not removed.
_TRUSTED_ORCHESTRATOR_ENV_KEYS = (
    "AOTA_TRUSTED_PRINCIPAL",
    "AOTA_TRUSTED_AUTHORITIES",
    "AOTA_TRUSTED_WORKSPACE_ID",
)

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Start one existing validated frozen AOTA task using its fixed derived Named Profile. "
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
            "expected_spec_hash": {
                "type": "string",
                "description": "Canonical frozen SPEC hash (WI-09C).",
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
SCHEMA["parameters"]["required"] = ["workspace_id", "task_id", "expected_revision"]


def build_profile_task_worker_env(
    parent_env: Mapping[str, str], *, task_markers: Mapping[str, str]
) -> dict[str, str]:
    """Return a worker-only env copy with orchestrator authority removed."""
    child_env = dict(parent_env)
    for key in _TRUSTED_ORCHESTRATOR_ENV_KEYS:
        child_env.pop(key, None)
    child_env.update(task_markers)
    return child_env


def _trusted_env_unset_command() -> str:
    """Return the exact child-shell sanitization prefix; never accepts caller keys."""
    return f"unset {' '.join(_TRUSTED_ORCHESTRATOR_ENV_KEYS)};"


def run_worker_env_sanitization_smoke() -> dict[str, str]:
    """Isolated env-only smoke; it never mutates this process environment."""
    parent_env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "AOTA_RUNTIME_ROOT": "/fixture/runtime",
        "AOTA_TRUSTED_PRINCIPAL": "task-main",
        "AOTA_TRUSTED_AUTHORITIES": "plan_read,plan_write",
        "AOTA_TRUSTED_WORKSPACE_ID": "fixture",
    }
    markers = {
        "AOTA_PROFILE_TASK_ID": "pt_20260714T000000_deadbeef",
        "AOTA_PROFILE_TASK_START_ID": "pt_20260714T000000_deadbeef",
        "AOTA_PROFILE_TASK_PROFILE": "coder",
    }
    before_parent = dict(parent_env)
    before_process = dict(os.environ)
    child_env = build_profile_task_worker_env(parent_env, task_markers=markers)
    partial_env = build_profile_task_worker_env(
        {"AOTA_TRUSTED_PRINCIPAL": "", "PATH": parent_env["PATH"]},
        task_markers=markers,
    )
    assert parent_env == before_parent and dict(os.environ) == before_process
    assert all(key not in child_env for key in _TRUSTED_ORCHESTRATOR_ENV_KEYS)
    assert all(key not in partial_env for key in _TRUSTED_ORCHESTRATOR_ENV_KEYS)
    assert all(child_env[key] == value for key, value in markers.items())
    assert child_env["AOTA_RUNTIME_ROOT"] == "/fixture/runtime"
    nested_check = (
        "import os, subprocess, sys; keys="
        + repr(_TRUSTED_ORCHESTRATOR_ENV_KEYS)
        + "; assert all(key not in os.environ for key in keys); "
        + "subprocess.run([sys.executable, '-c', "
        + repr("import os; assert all(key not in os.environ for key in " + repr(_TRUSTED_ORCHESTRATOR_ENV_KEYS) + ")")
        + "], check=True)"
    )
    subprocess.run([sys.executable, "-c", nested_check], env=child_env, check=True)
    return {"status": "PASS", "trusted_keys": "absent", "nested_process": "absent"}


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
    expected_spec_sha256: str = args.get("expected_spec_hash") or args.get("expected_spec_sha256", "")
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
        except Exception as exc:
            _record_start_failure(workspace_id, task_id, task_dir, exc)
            raise
    finally:
        release_lock(lock_fd)


def _record_start_failure(
    workspace_id: str, task_id: str, task_dir: Path, error: Exception
) -> None:
    """Use the canonical fallback path for failures before background launch."""
    try:
        meta = read_json(task_dir / "meta.json")
        execution = meta.get("execution", {})
        if execution.get("start_id") != task_id or meta.get("status") != "running":
            return
        text = str(error)
        if "credential" in text:
            stage = "credential_bootstrap"
            if "credential_missing" in text:
                classification = "credential_missing"
            elif "credential_provider_unsupported" in text:
                classification = "credential_provider_unsupported"
            elif "authority_invalid" in text:
                classification = "credential_authority_invalid"
            else:
                classification = "credential_bootstrap"
        elif "invalid_frozen_scope" in text:
            stage = "scope_population"
            classification = "invalid_frozen_scope"
        elif "workspace_baseline" in text:
            stage = "scope_population"
            classification = "workspace_baseline_unavailable"
        elif "runner" in text:
            stage = "runner_resolution"
            classification = "runner_resolution"
        elif "background_launch" in text:
            stage = "worker_launch"
            classification = "worker_launch"
        else:
            stage = "launcher_initialization"
            classification = "launcher_initialization"
        from ._profile_task_finalize import write_failure_artifacts
        write_failure_artifacts(
            workspace_id=workspace_id,
            task_id=task_id,
            start_id=task_id,
            profile=execution.get("profile", meta.get("profile_hint", "")),
            spec_revision=execution.get("spec_revision", meta.get("revision", 0)),
            spec_sha256=execution.get("spec_sha256", meta.get("spec_sha256", "")),
            spec_hash=execution.get("spec_hash", meta.get("spec_hash", "")),
            exit_code=1,
            failure_stage=stage,
            error_classification=classification,
            diagnostics=text,
            command_summary=f"profile-task launcher task_id={task_id}",
            global_hermes_home=execution.get("global_hermes_home", ""),
            target_profile_home=execution.get("target_profile_home", ""),
            parent_profile=execution.get("parent_profile", ""),
            lock_already_held=True,
        )
    except Exception:
        # The original launch error remains the user-visible result.  The
        # already-created worker log is still the last-resort diagnostic.
        return


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
    canonical_approval_hash: str | None = None

    # WI-09C uses the canonical frozen JSON envelope and its canonical hash,
    # while the older launch rail still needs the on-disk SPEC.md checksum.
    # Convert only the in-memory lifecycle view after strict validation.
    if existing_meta.get("contract_version") == 1:
        spec = existing_meta.get("spec", {})
        if spec.get("workspace_context") is not None:
            from ._workspace_context import validate_workspace_context
            validate_workspace_context(spec["workspace_context"])
        try:
            validate_task_binding(existing_meta, expected_revision, expected_spec_sha256)
        except ContractError as exc:
            raise WorkspaceError(str(exc)) from exc
        if existing_meta.get("status") != "frozen" or existing_meta.get("revision") != expected_revision:
            raise WorkspaceError("task_not_startable: SPEC must be frozen at the requested revision")
        if expected_spec_sha256 != existing_meta.get("spec_hash") or canonical_hash(spec) != existing_meta.get("spec_hash"):
            raise WorkspaceError("spec_hash_conflict")
        canonical_approval_hash = existing_meta.get("spec_hash")
        kind = existing_meta.get("spec_kind")
        if kind not in ROUTING or existing_meta.get("resolved_profile") != ROUTING[kind]:
            raise WorkspaceError("profile_routing_mismatch")
        existing_meta = dict(existing_meta)
        existing_meta["status"] = STATUS_DRAFT
        existing_meta["frozen_revision"] = existing_meta.get("revision")
        # All remaining legacy launcher checks apply to the immutable file hash.
        expected_spec_sha256 = existing_meta.get("spec_sha256", "")

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
    # 8. Status gate: a frozen binding is required while lifecycle remains draft.
    # ------------------------------------------------------------------
    current_status = existing_meta.get("status", "")
    legacy_spec = existing_meta.get("schema_version", 0) < 3
    if current_status != STATUS_DRAFT or (not legacy_spec and existing_meta.get("frozen_revision") != existing_meta.get("revision")):
        raise WorkspaceError(
            f"task_not_startable: task '{task_id}' requires its current draft revision to be frozen"
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
        expected_approval_hash = canonical_approval_hash or actual_spec_sha256
        if approval_rev != current_revision or approval_hash != expected_approval_hash:
            raise WorkspaceError(
                f"approval_stale: APPROVAL.json is for rev={approval_rev} "
                f"but current rev={current_revision}. "
                f"Re-approve the updated SPEC with aota_profile_task_approve."
            )

    # ------------------------------------------------------------------
    # 12. Derive profile from task_kind
    # ------------------------------------------------------------------
    derived_profile = derive_profile(task_kind)

    # Establish the task-local durable rail before profile/config, credential,
    # runner, or terminal bootstrap can fail.  The worker log is intentionally
    # the single canonical launcher+worker capture artifact.
    parent_profile_hint = os.environ.get("AOTA_PROFILE_TASK_PARENT_PROFILE")
    launch_log_path = task_dir / f"worker.{task_id}.log"
    with launch_log_path.open("a", encoding="utf-8") as launch_log:
        launch_log.write(
            f"AOTA_LAUNCH_INIT task_id={task_id} profile={derived_profile} "
            f"parent_profile={parent_profile_hint or 'unknown'} "
            "failure_stage=launcher_initialization redaction_applied=true\n"
        )
    launch_started_at = utc_now_iso()
    launch_meta = dict(existing_meta)
    launch_meta["status"] = "running"
    launch_meta["execution"] = {
        "start_id": task_id,
        "profile": derived_profile,
        "process_session_id": "",
        "started_at": launch_started_at,
        "spec_revision": current_revision,
        "spec_hash": existing_meta.get("spec_hash") or actual_spec_sha256,
        "spec_sha256": actual_spec_sha256,
        "transport": "terminal_background",
        "notify_on_complete": True,
        "lifecycle_reconciliation": "pending",
        "completion_receipt_path": f"completion.{task_id}.json",
        "finalizer_expected_version": 1,
        "primary_finalizer_executed": False,
        "fallback_finalizer_executed": False,
        "global_hermes_home": "",
        "parent_profile": parent_profile_hint or "unknown",
        "parent_profile_home": "",
        "target_profile_home": "",
        "worker_log": {"path": launch_log_path.name, "size_bytes": launch_log_path.stat().st_size, "truncated": False},
    }
    write_json(meta_path, launch_meta)
    existing_meta = launch_meta

    # Project scope is projected only from the canonical frozen SPEC payload.
    # The running rail already exists so invalid scope receives the normal
    # failure receipt/handoff and never starts a worker.
    try:
        scope_info = extract_canonical_scope(existing_meta.get("spec", {}))
    except CanonicalScopeError as exc:
        raise WorkspaceError(f"invalid_frozen_scope: {exc}") from exc
    scope_digest = compute_scope_digest(scope_info)
    scope_process_session_id = "scope_" + uuid.uuid4().hex
    existing_meta["execution"].update({
        "scope_digest": scope_digest,
        "scope_source": scope_info["scope_source"],
        "scope_schema_version": scope_info["scope_schema_version"],
        "scope_process_session_id": scope_process_session_id,
    })
    write_json(meta_path, existing_meta)

    # Resolve the three distinct homes once. All later profile/config and
    # credential operations consume this explicit context.
    runtime_context = build_profile_task_runtime_context(
        parent_profile=parent_profile_hint,
        target_profile=derived_profile,
    )
    existing_meta["execution"].update({
        "global_hermes_home": runtime_context["global_hermes_home"],
        "parent_profile": runtime_context["parent_profile"],
        "parent_profile_home": runtime_context["parent_profile_home"],
        "target_profile_home": runtime_context["target_profile_home"],
    })
    with launch_log_path.open("a", encoding="utf-8") as launch_log:
        launch_log.write(
            f"AOTA_PROFILE_HOME_RESOLUTION global_hermes_home={runtime_context['global_hermes_home']} "
            f"parent_profile_home={runtime_context['parent_profile_home']} "
            f"target_profile_home={runtime_context['target_profile_home']}\n"
        )
    write_json(meta_path, existing_meta)

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
    _verify_profile_available(derived_profile, runtime_context)

    # ------------------------------------------------------------------
    # 14b. Review preflight: verify subject task
    # ------------------------------------------------------------------
    subject_task_id: str | None = existing_meta.get("subject_task_id")
    if task_kind == "review":
        if not subject_task_id:
            raise WorkspaceError(
                "review_missing_subject: review task requires subject_task_id"
            )
        _verify_subject_reviewable(
            workspace_id,
            subject_task_id,
            expected_project_id=existing_meta.get("project_id"),
        )
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
    read_scope_list: list[str] = scope_info["read_scope"]
    write_scope_list: list[str] = scope_info["write_scope"]
    forbidden_scope_list: list[str] = scope_info["forbidden_scope"]
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
        "schema_version": 1,
        "workspace_id": workspace_id,
        "project_id": existing_meta.get("project_id", ""),
        "task_id": task_id,
        "start_id": task_id,
        "spec_id": existing_meta.get("spec_id", task_id),
        "spec_revision": current_revision,
        "spec_hash": existing_meta.get("spec_hash") or actual_spec_sha256,
        "read_scope": read_scope_list,
        "write_scope": write_scope_list,
        "forbidden_scope": forbidden_scope_list,
        "scope_source": scope_info["scope_source"],
        "scope_schema_version": scope_info["scope_schema_version"],
        "scope_digest": scope_digest,
        "process_session_id": scope_process_session_id,
        "created_at": utc_now_iso(),
        "spec_sha256": actual_spec_sha256,
        "revision": current_revision,
    }
    write_json(task_dir / "scope.json", scope_manifest)

    # Capture pre-existing dirty state before any worker process can run.
    try:
        from ._profile_task_scope import capture_workspace_baseline
        capture_workspace_baseline(
            workspace_root=workspace_root,
            task_dir=task_dir,
            workspace_id=workspace_id,
            project_id=existing_meta.get("project_id", ""),
            task_id=task_id,
            start_id=task_id,
        )
    except Exception as exc:
        raise WorkspaceError(f"workspace_baseline_capture_failed: {type(exc).__name__}") from exc

    # --------------------------------------------------------------------------
    # 18. Freeze a shell-free launch manifest.
    # --------------------------------------------------------------------------
    # Profile config selects provider/model only. Credentials are resolved by
    # the structured launcher from the canonical global Hermes home.
    _provider_name, _provider_config = _load_profile_provider_config(
        derived_profile, runtime_context
    )
    _auth_type, _key_env, _base_url_env, _configured_base_url = _resolve_profile_credentials(
        _provider_name, _provider_config
    )
    timeout_iso_deadline: str | None = None
    if timeout_seconds is not None:
        import datetime as _dt
        deadline_dt = _dt.datetime.now(_dt.timezone.utc)
        deadline_ts = deadline_dt.timestamp() + timeout_seconds
        timeout_iso_deadline = _dt.datetime.fromtimestamp(
            deadline_ts, tz=_dt.timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    worker_log_path = task_dir / f"worker.{task_id}.log"
    profile_config_path = _resolve_named_profile_config_path(derived_profile, runtime_context)
    profile_config_bytes = profile_config_path.read_bytes()
    profile_config_digest = hashlib.sha256(profile_config_bytes).hexdigest()
    try:
        import yaml as _yaml
        profile_config = _yaml.safe_load(profile_config_bytes.decode("utf-8"))
        resolved_model = profile_config.get("model", {}).get("default", "")
    except Exception as exc:
        raise WorkspaceError(f"profile_launch_binding_mismatch: {type(exc).__name__}") from exc
    if not isinstance(resolved_model, str) or not resolved_model:
        raise WorkspaceError("profile_launch_binding_mismatch: model missing")

    prompt_path = task_dir / f"worker-prompt.{task_id}.txt"
    prompt_fd, prompt_tmp = tempfile.mkstemp(dir=str(task_dir), prefix=f".{prompt_path.name}.tmp_")
    try:
        with os.fdopen(prompt_fd, "w", encoding="utf-8") as prompt_file:
            prompt_file.write(worker_prompt)
            prompt_file.flush()
            os.fsync(prompt_file.fileno())
        os.chmod(prompt_tmp, 0o600)
        os.replace(prompt_tmp, prompt_path)
    except Exception:
        try:
            os.unlink(prompt_tmp)
        except OSError:
            pass
        raise

    launch_manifest_path = task_dir / f"launch.{task_id}.json"
    runtime_root = Path(os.environ.get("AOTA_RUNTIME_ROOT", "/aota-runtime"))
    workspace_context = existing_meta.get("spec", {}).get("workspace_context") or {}
    launch_manifest = {
        "schema_version": 1,
        "workspace_id": workspace_id,
        "project_id": existing_meta.get("project_id") or workspace_context.get("project_id", ""),
        "task_id": task_id,
        "start_id": task_id,
        "work_item_id": existing_meta.get("work_item_id", ""),
        "profile": derived_profile,
        "parent_profile": runtime_context["parent_profile"],
        "spec": {
            "spec_id": existing_meta.get("spec_id", task_id),
            "revision": current_revision,
            "spec_hash": existing_meta.get("spec_hash") or actual_spec_sha256,
            "spec_sha256": actual_spec_sha256,
        },
        "scope": {
            "scope_digest": scope_digest,
            "scope_source": scope_info["scope_source"],
            "scope_schema_version": scope_info["scope_schema_version"],
            "process_session_id": scope_process_session_id,
        },
        "paths": {
            "task_dir": str(task_dir), "workspace_root": str(workspace_root),
            "global_hermes_home": runtime_context["global_hermes_home"],
            "target_profile_home": runtime_context["target_profile_home"],
            "worker_log": str(worker_log_path),
            "completion_receipt": str(task_dir / f"completion.{task_id}.json"),
            "scope_manifest": str(task_dir / "scope.json"),
            "workspace_baseline": str(task_dir / "workspace-baseline.json"),
            "handoff": str(runtime_root / "handoffs" / workspace_id / "pending"),
        },
        "project_root": workspace_context.get("project_root", str(workspace_root)),
        "worker": {
            "runner": runner,
            "argv": [runner, "-p", derived_profile, "-z", "<prompt-file>"],
            "argv_template": [runner, "-p", derived_profile, "-z", "<prompt-file>"],
            "prompt_path": str(prompt_path), "cwd": str(workspace_root),
            "timeout_seconds": timeout_seconds or 0, "timeout_deadline_at": timeout_iso_deadline or "",
        },
        "provider": {
            "name": _provider_name, "model": resolved_model, "key_env": _key_env,
            "base_url_env": _base_url_env, "configured_base_url": _configured_base_url,
            "auth_type": _auth_type,
        },
        "binding": {
            "resolved_profile": derived_profile, "resolved_provider": _provider_name,
            "resolved_model": resolved_model, "profile_config_path": str(profile_config_path),
            "profile_config_digest": profile_config_digest, "credential_source_type": "global_hermes_home",
        },
        "origin": {"session_id": origin_session_id or "", "source": origin_source or ""},
        "created_at": utc_now_iso(),
    }
    write_json(launch_manifest_path, launch_manifest)
    launch_manifest_digest = hashlib.sha256(launch_manifest_path.read_bytes()).hexdigest()

    # --------------------------------------------------------------------------
    # 19. Launch exactly one minimal Python command through the background rail.
    # --------------------------------------------------------------------------
    try:
        from tools.terminal_tool import terminal_tool
    except ImportError as e:
        raise WorkspaceError(
            f"background_launch_failed: cannot import terminal_tool: {e}"
        )

    launcher_path = Path(__file__).with_name("_profile_task_launcher.py")
    command = (
        f"{shlex.quote(sys.executable)} {shlex.quote(str(launcher_path))}"
        f" --manifest {shlex.quote(str(launch_manifest_path))}"
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

    # The background rail can complete a fixture/early-failure launcher before
    # this caller finishes committing process metadata. Never overwrite a
    # terminal receipt with a stale running projection in that race.
    latest_meta = read_json(meta_path)
    latest_execution = latest_meta.get("execution", {})
    if latest_meta.get("status") in {"done", "failed", "timeout", "needs_input", "cancelled", "scope_violation"} and (
        task_dir / f"completion.{task_id}.json"
    ).is_file():
        return json.dumps(
            {
                "status": latest_meta.get("status"), "task_id": task_id,
                "workspace_id": workspace_id, "task_kind": task_kind,
                "profile": derived_profile, "revision": current_revision,
                "spec_sha256": actual_spec_sha256,
                "process_session_id": process_session_id or latest_execution.get("process_session_id", ""),
                "started_at": latest_execution.get("started_at", launch_started_at),
                "completion_transport": "terminal_background",
                "human_checkpoint_policy": existing_meta.get("human_checkpoint_policy", ""),
                "approval_status": "required" if task_kind == "implementation" else "not_required",
            }, sort_keys=True
        )

    # ------------------------------------------------------------------
    # 20. Prepare running meta
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
        "spec_hash": existing_meta.get("spec_hash") or actual_spec_sha256,
        "spec_sha256": actual_spec_sha256,
        "scope_digest": scope_digest,
        "scope_source": scope_info["scope_source"],
        "scope_schema_version": scope_info["scope_schema_version"],
        "scope_process_session_id": scope_process_session_id,
        "transport": "terminal_background",
        "notify_on_complete": True,
        "lifecycle_reconciliation": "pending",
        "completion_receipt_path": f"completion.{task_id}.json",
        "finalizer_version": 1,
        "finalizer_expected_version": 1,
        "primary_finalizer_executed": False,
        "fallback_finalizer_executed": False,
        "global_hermes_home": runtime_context["global_hermes_home"],
        "parent_profile": runtime_context["parent_profile"],
        "parent_profile_home": runtime_context["parent_profile_home"],
        "target_profile_home": runtime_context["target_profile_home"],
        "worker_log": {
            "path": f"worker.{task_id}.log",
            "size_bytes": 0,
            "truncated": False,
        },
        "launch_manifest": {
            "path": launch_manifest_path.name,
            "sha256": launch_manifest_digest,
            "schema_version": 1,
        },
        "resolved_profile": derived_profile,
        "resolved_provider": _provider_name,
        "resolved_model": resolved_model,
        "profile_config_digest": profile_config_digest,
        "credential_source_type": "global_hermes_home",
        "worker_started": False,
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
    lineage = task_lineage(existing_meta.get("spec", {}).get("source_traceability"))
    if lineage is not None:
        new_meta["execution"]["source_plan"] = lineage

    # P11-A: Add timeout metadata when configured
    if timeout_seconds is not None:
        new_meta["execution"]["timeout_seconds"] = timeout_seconds
        new_meta["execution"]["timeout_deadline_at"] = timeout_iso_deadline or ""
        new_meta["execution"]["timeout_triggered"] = False

    # ------------------------------------------------------------------
    # 21. Atomic meta commit
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
            "approval_status": "required" if task_kind == "implementation" else "not_required",
        },
        sort_keys=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_global_hermes_root(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Compatibility wrapper for the canonical path resolver."""
    try:
        return resolve_global_hermes_home(environ=environ, home=home)
    except ValueError as exc:
        raise WorkspaceError(f"HERMES_ROOT_INVALID: {exc}") from exc


def resolve_named_profile_dir(
    profile: object,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve one Named Profile inside the canonical global Hermes tree."""
    try:
        global_root = resolve_global_hermes_home(environ=environ, home=home)
        profile_dir = resolve_profile_home(global_root, profile)
    except ValueError as exc:
        raise WorkspaceError(f"profile_path_invalid: {exc}") from exc
    if not profile_dir.is_dir():
        raise WorkspaceError(f"profile_unavailable: profile={profile!r} root={global_root}")
    return profile_dir


def _resolve_named_profile_config_path(
    profile: object, runtime_context: Mapping[str, str] | None = None
) -> Path:
    """Resolve the target profile config without permitting symlink escape."""
    if runtime_context is None:
        profile_dir = resolve_named_profile_dir(profile)
    else:
        try:
            profile_dir = resolve_profile_home(
                runtime_context["global_hermes_home"], profile
            )
        except (KeyError, ValueError) as exc:
            raise WorkspaceError(f"profile_path_invalid: {exc}") from exc
        if not profile_dir.is_dir():
            raise WorkspaceError(f"profile_unavailable: profile={profile!r}")
    config_path = profile_dir / "config.yaml"
    if not config_path.is_file():
        raise WorkspaceError(f"profile_unavailable: profile={profile!r} has no config.yaml")
    resolved_config_path = config_path.resolve()
    try:
        resolved_config_path.relative_to(profile_dir)
    except ValueError:
        raise WorkspaceError(f"profile_config_path_escape: profile={profile!r}")
    return resolved_config_path


def _verify_profile_available(
    profile: str, runtime_context: Mapping[str, str] | None = None
) -> None:
    """Verify the target Named Profile and its config under the global root."""
    _resolve_named_profile_config_path(profile, runtime_context)


def _load_profile_provider_config(
    profile: str, runtime_context: Mapping[str, str] | None = None
) -> tuple[str | None, dict]:
    """Load derived Profile provider settings without reading credentials."""
    config_path = _resolve_named_profile_config_path(profile, runtime_context)
    try:
        import yaml as _yaml

        config = _yaml.safe_load(config_path.read_text("utf-8"))
        if not isinstance(config, dict):
            raise WorkspaceError("credential_provider_unsupported: invalid_profile_config")
        model = config.get("model")
        provider_name = model.get("provider") if isinstance(model, dict) else None
        providers = config.get("providers")
        provider = providers.get(provider_name) if isinstance(providers, dict) else None
        if not isinstance(provider_name, str) or not isinstance(provider, dict):
            raise WorkspaceError("credential_provider_unsupported: provider_config_missing")
        return provider_name, dict(provider)
    except WorkspaceError:
        raise
    except Exception as exc:
        raise WorkspaceError(
            f"credential_authority_invalid: provider_config_unreadable={type(exc).__name__}"
        ) from exc


def _resolve_profile_credentials(
    provider_name: str | None, provider_config: dict
) -> tuple[str, str, str, str]:
    """Return provider metadata and configured URL without reading secrets."""
    if not provider_name or provider_name not in PROVIDER_METADATA:
        raise WorkspaceError(
            f"credential_provider_unsupported: provider={provider_name or 'unknown'}"
        )
    metadata = PROVIDER_METADATA[provider_name]
    auth_type = provider_config.get("auth_type") or metadata["auth_type"]
    if auth_type not in {"api_key", "oauth", "oauth_external"}:
        raise WorkspaceError(f"credential_provider_unsupported: provider={provider_name}")
    key_env = provider_config.get("key_env", metadata.get("key_env", ""))
    base_url_env = provider_config.get("base_url_env", metadata.get("base_url_env", ""))
    if not isinstance(key_env, str) or not isinstance(base_url_env, str):
        raise WorkspaceError(f"credential_authority_invalid: provider={provider_name}")
    profile_api = provider_config.get("api")
    configured_url = profile_api.strip() if isinstance(profile_api, str) else ""
    return auth_type, key_env.strip(), base_url_env.strip(), configured_url


def _verify_subject_reviewable(
    workspace_id: str,
    subject_task_id: str,
    expected_project_id: str | None = None,
) -> None:
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
    if expected_project_id is not None and subject_meta.get("project_id") != expected_project_id:
        raise WorkspaceError(
            f"subject_project_mismatch: subject '{subject_task_id}' belongs to project "
            f"'{subject_meta.get('project_id')}', not '{expected_project_id}'"
        )
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
