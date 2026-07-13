"""aota_file_copy — direct byte-preserving file/directory copy inside an allowlisted workspace.

Uses shutil for actual copy operations; no shell cp/rsync.

P11-N Tool-Layer Security Foundation integration:
  - When SecurityContext.available=True (worker): full enforcement chain
    (workspace containment, write_scope, forbidden_scope, profile capability,
    task_kind, approval, revision, hash, change_budget, dirty, human checkpoint)
    plus audit entry.
  - When SecurityContext.available=False (task-main): workspace containment
    only (existing behavior preserved). No SPEC scope/budget enforcement.

P11-N.1: Stale Worker Enforcement Closure
  - Calls validate_worker_spec_freshness before any file operation
  - Zero side effects on stale (returns rejected with STALE_SPEC or SECURITY_CONTEXT_INVALID)
  - Denied audit entries for STALE_SPEC and WORKSPACE_ESCAPE

P11-N.2: Denied Audit Boundary Closure
  - All security rejections via mutation_audit_boundary produce automatic denied audit
  - Shared boundary catches SecurityError subclasses → denied audit → re-raise
  - Fail-closed: audit writer failure raises AuditGapError, mutation blocked
  - audit_event_id per tool call for exactly-once tracking
  - Worker cannot skip or manually write denied audit
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from ._workspace import resolve_relative_path, WorkspaceError
from ._security_context import get_security_context
from ._validators import (
    validate_write_scope,
    validate_forbidden_scope,
    validate_profile_capability,
    validate_task_kind,
    validate_approval,
    validate_revision,
    validate_hash,
    validate_change_budget,
    validate_dirty,
    check_human_checkpoint,
    validate_worker_spec_freshness,
    run_validators,
)
from ._audit import write_audit_entry
from ._errors import SecurityError
from ._mutation_audit import mutation_audit_boundary

TOOL_NAME = "aota_file_copy"
TOOLSET_NAME = "aota_fs_copy"

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Use for direct byte-preserving file or directory copying inside an "
        "allowlisted workspace. Prefer over delegate_task + terminal cp for "
        "simple copy operations. Do not use for editing or transforming file content."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {
                "type": "string",
                "description": "Registered workspace identifier (e.g. 'aota-runtime')",
            },
            "source": {
                "type": "string",
                "description": "Workspace-relative source path (file or directory)",
            },
            "destination": {
                "type": "string",
                "description": "Workspace-relative destination path",
            },
            "overwrite": {
                "type": "boolean",
                "description": "Allow overwriting existing destination (default: false)",
                "default": False,
            },
        },
        "required": ["workspace_id", "source", "destination"],
        "additionalProperties": False,
    },
}

# Hard limits
MAX_FILE_COUNT = 10000
MAX_TOTAL_BYTES = 10737418240  # 10 GiB


# ---------------------------------------------------------------------------
# Preflight helpers
# ---------------------------------------------------------------------------

def _classify_source(path: Path) -> tuple[str, int, int, int]:
    """Classify source path and count bytes/files.

    Returns (source_type, total_bytes, file_count, directory_count).
    Raises WorkspaceError for unsupported types.
    """
    if not path.exists():
        raise WorkspaceError("source path does not exist")

    # Resolve symlinks for classification
    try:
        real = path.resolve()
    except (OSError, RuntimeError):
        real = path

    # Check for symlink (we resolve first, then check if original was symlink)
    if path.is_symlink():
        raise WorkspaceError("source is a symlink; use aota_path_info to resolve first")

    if path.is_file():
        st = path.stat()
        return ("file", st.st_size, 1, 0)

    if path.is_dir():
        total_bytes = 0
        file_count = 0
        directory_count = 0
        for root_str, dirs, files in os.walk(str(path)):
            root = Path(root_str)
            directory_count += 1 if root != path else 0  # don't count root itself
            for fname in files:
                fp = root / fname
                try:
                    if fp.is_symlink():
                        # Count symlinks as 0 bytes for preflight
                        continue
                    st = fp.stat()
                    total_bytes += st.st_size
                    file_count += 1
                except OSError:
                    continue
            for dname in dirs:
                dp = root / dname
                if not dp.is_symlink():
                    directory_count += 1

        return ("directory", total_bytes, file_count, directory_count)

    # Check for special files
    mode = path.stat().st_mode
    import stat
    if stat.S_ISSOCK(mode):
        raise WorkspaceError("source is a socket; not copyable")
    if stat.S_ISFIFO(mode):
        raise WorkspaceError("source is a FIFO; not copyable")
    if stat.S_ISBLK(mode) or stat.S_ISCHR(mode):
        raise WorkspaceError("source is a device file; not copyable")

    raise WorkspaceError("source is not a regular file or directory")


def _check_free_space(dest_root: Path, needed_bytes: int) -> None:
    """Check that destination filesystem has enough free space."""
    try:
        usage = shutil.disk_usage(str(dest_root))
        free = usage.free
        if needed_bytes > free * 0.8:
            raise WorkspaceError(
                f"insufficient free space on destination filesystem: "
                f"need {needed_bytes} bytes, only {free} bytes free"
            )
    except OSError as e:
        raise WorkspaceError(f"failed to check disk space: {e}") from e


# ---------------------------------------------------------------------------
# Helpers: build rejected response with error_code
# ---------------------------------------------------------------------------

def _rejected_response(
    workspace_id: str,
    source: str,
    destination: str,
    error: str,
    error_code: str = "",
    partial_copy: bool = False,
) -> str:
    """Build a standardized rejected JSON response."""
    resp: dict = {
        "workspace_id": workspace_id,
        "source": source,
        "destination": destination,
        "source_type": None,
        "bytes_copied": 0,
        "file_count": 0,
        "overwritten": False,
        "status": "rejected",
        "error": error,
        "partial_copy": partial_copy,
    }
    if error_code:
        resp["error_code"] = error_code
    return json.dumps(resp, sort_keys=True)


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def handle(args: dict, **_kwargs) -> str:
    workspace_id: str = args.get("workspace_id", "")
    source: str = args.get("source", "")
    destination: str = args.get("destination", "")
    overwrite: bool = args.get("overwrite", False)

    try:
        return _do_copy(workspace_id, source, destination, overwrite)
    except WorkspaceError as e:
        error_code = ""
        error_msg = str(e)
        # P11-N.1: Normalize resolve_relative_path errors to WORKSPACE_ESCAPE
        if error_msg.startswith("source: ") or error_msg.startswith("destination: "):
            error_code = "WORKSPACE_ESCAPE"
        elif "path escapes workspace root" in error_msg:
            error_code = "WORKSPACE_ESCAPE"
        return _rejected_response(
            workspace_id, source, destination, error_msg, error_code
        )
    except SecurityError as e:
        return _rejected_response(
            workspace_id, source, destination, str(e), e.error_code
        )
    except Exception as e:
        return json.dumps(
            {
                "workspace_id": workspace_id,
                "source": source,
                "destination": destination,
                "source_type": None,
                "bytes_copied": 0,
                "file_count": 0,
                "overwritten": False,
                "status": "failed",
                "error": str(e),
                "partial_copy": False,
            },
            sort_keys=True,
        )


def _do_copy(
    workspace_id: str,
    source: str,
    destination: str,
    overwrite: bool,
) -> str:
    # ------------------------------------------------------------------
    # P11-N: Get security context
    # ------------------------------------------------------------------
    ctx = get_security_context()

    # ------------------------------------------------------------------
    # P11-N.2: Mutation audit boundary wraps entire operation
    # All SecurityError rejections automatically produce denied audit
    # ------------------------------------------------------------------
    with mutation_audit_boundary(
        tool_name=TOOL_NAME,
        operation="file_copy",
        raw_target=destination,
        ctx=ctx,
    ) as audit:
        # ----------------------------------------------------------
        # P11-N.1: Freshness validation — MUST come before any file operation
        # ----------------------------------------------------------
        if ctx.available:
            freshness_result = validate_worker_spec_freshness(ctx)
            if not freshness_result.allowed:
                from ._errors import StaleSpecError, SecurityContextInvalidError
                if freshness_result.error_code == "STALE_SPEC":
                    raise StaleSpecError(freshness_result.detail)
                else:
                    raise SecurityContextInvalidError(freshness_result.detail)

        # ----------------------------------------------------------
        # Resolve source (must exist)
        # ----------------------------------------------------------
        try:
            source_root, source_path = resolve_relative_path(workspace_id, source, must_exist=True)
        except WorkspaceError as e:
            from ._errors import WorkspaceEscapeError
            raise WorkspaceEscapeError(f"source: {e}") from e

        # ----------------------------------------------------------
        # Resolve destination (may not exist)
        # ----------------------------------------------------------
        try:
            dest_root, dest_path = resolve_relative_path(workspace_id, destination, must_exist=False)
        except WorkspaceError as e:
            from ._errors import WorkspaceEscapeError
            raise WorkspaceEscapeError(f"destination: {e}") from e

        # Source and destination must be in same workspace
        if source_root != dest_root:
            from ._errors import WorkspaceEscapeError
            raise WorkspaceEscapeError("source and destination must be in the same workspace")

        # ----------------------------------------------------------
        # P11-N: Worker context — full enforcement chain
        # All rejections raise SecurityError subclasses → boundary catches → denied audit
        # ----------------------------------------------------------
        if ctx.available:
            from ._errors import (
                WriteScopeViolationError, ForbiddenScopeError,
                ProfileForbiddenError, TaskKindForbiddenError,
                SpecNotApprovedError, StaleSpecError,
                ChangeBudgetExceededError, DirtyConflictError,
                RequiresHumanCheckpointError,
            )

            # Validate source against write_scope and forbidden_scope
            source_result = run_validators([
                lambda: validate_write_scope(ctx.write_scope, source),
                lambda: validate_forbidden_scope(ctx.forbidden_scope, source),
            ])
            if not source_result.allowed:
                if source_result.error_code == "WRITE_SCOPE_VIOLATION":
                    raise WriteScopeViolationError(source_result.detail)
                else:
                    raise ForbiddenScopeError(source_result.detail)

            # Validate destination against write_scope and forbidden_scope
            dest_result = run_validators([
                lambda: validate_write_scope(ctx.write_scope, destination),
                lambda: validate_forbidden_scope(ctx.forbidden_scope, destination),
            ])
            if not dest_result.allowed:
                if dest_result.error_code == "WRITE_SCOPE_VIOLATION":
                    raise WriteScopeViolationError(dest_result.detail)
                else:
                    raise ForbiddenScopeError(dest_result.detail)

            # Validate profile capability
            profile_result = validate_profile_capability("coder", ctx.profile)
            if not profile_result.allowed:
                raise ProfileForbiddenError(profile_result.detail)

            # Validate task kind
            task_result = validate_task_kind("implementation", ctx.task_kind)
            if not task_result.allowed:
                raise TaskKindForbiddenError(task_result.detail)

            # Validate approval
            approval_result = validate_approval(ctx.is_approved)
            if not approval_result.allowed:
                raise SpecNotApprovedError(approval_result.detail)

            # P11-N.1: Use bound identity for revision/hash checks
            revision_result = validate_revision(ctx.bound_spec_revision, ctx.current_spec_revision)
            if not revision_result.allowed:
                raise StaleSpecError(revision_result.detail)

            hash_result = validate_hash(ctx.bound_spec_sha256, ctx.current_meta_spec_sha256)
            if not hash_result.allowed:
                raise StaleSpecError(hash_result.detail)

            # Validate change budget — count this as one changed file
            budget_result = validate_change_budget(ctx.change_budget, 1)
            if not budget_result.allowed:
                raise ChangeBudgetExceededError(budget_result.detail)

            # Validate dirty (check workspace for uncommitted changes)
            dirty_result = validate_dirty(str(source_root))
            if not dirty_result.allowed:
                raise DirtyConflictError(dirty_result.detail)

            # Check human checkpoints
            checkpoint_result = check_human_checkpoint(
                ctx.human_checkpoints, "file_copy"
            )
            if not checkpoint_result.allowed:
                raise RequiresHumanCheckpointError(checkpoint_result.detail)

        # ----------------------------------------------------------
        # Source classification
        # ----------------------------------------------------------
        source_type, total_bytes, file_count, directory_count = _classify_source(source_path)

        # ----------------------------------------------------------
        # Self-copy check
        # ----------------------------------------------------------
        if source_path.resolve() == dest_path.resolve():
            raise WorkspaceError("source and destination are the same path; self-copy rejected")

        # ----------------------------------------------------------
        # Subtree check (directory copy into own subtree)
        # ----------------------------------------------------------
        if source_type == "directory":
            try:
                dest_path.resolve().relative_to(source_path.resolve())
                raise WorkspaceError(
                    "destination is inside the source directory subtree; "
                    "recursive self-copy rejected"
                )
            except ValueError:
                # Not inside — good
                pass

        # ----------------------------------------------------------
        # Destination pre-checks
        # ----------------------------------------------------------
        dest_exists = dest_path.exists()
        dest_is_dir = dest_path.is_dir() if dest_exists else False

        if dest_exists:
            if not overwrite:
                raise WorkspaceError(
                    f"destination already exists: {destination} "
                    "(set overwrite=true to overwrite)"
                )
            if dest_is_dir:
                raise WorkspaceError(
                    f"destination is an existing directory: {destination}; "
                    "overwrite not supported for directories"
                )
        else:
            dest_path.parent.mkdir(parents=True, exist_ok=True)

        # ----------------------------------------------------------
        # Resource preflight
        # ----------------------------------------------------------
        if file_count > MAX_FILE_COUNT:
            raise WorkspaceError(
                f"source has {file_count} files; maximum allowed is {MAX_FILE_COUNT}"
            )
        if total_bytes > MAX_TOTAL_BYTES:
            raise WorkspaceError(
                f"source total size {total_bytes} bytes exceeds maximum {MAX_TOTAL_BYTES}"
            )

        _check_free_space(dest_root, total_bytes)

        # ----------------------------------------------------------
        # Perform copy
        # ----------------------------------------------------------
        overwritten = dest_exists

        if source_type == "file":
            dest_dir = dest_path.parent
            fd, tmp_path_str = tempfile.mkstemp(dir=str(dest_dir), prefix=".aota_copy_")
            os.close(fd)
            tmp_path = Path(tmp_path_str)
            try:
                shutil.copy2(str(source_path), str(tmp_path))
                os.replace(tmp_path_str, str(dest_path))
            except (OSError, shutil.Error) as e:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise WorkspaceError(f"file copy failed: {e}") from e

            bytes_copied = total_bytes
            copied_file_count = 1

        else:
            try:
                shutil.copytree(
                    str(source_path), str(dest_path),
                    symlinks=True,
                    dirs_exist_ok=False,
                )
            except FileExistsError:
                raise WorkspaceError(
                    f"destination directory already exists: {destination}"
                ) from None
            except shutil.Error as e:
                raise WorkspaceError(f"directory copy failed: {e}") from e

            bytes_copied = total_bytes
            copied_file_count = file_count

        # ----------------------------------------------------------
        # P11-N: Success audit (worker context only)
        # ----------------------------------------------------------
        if ctx.available:
            write_audit_entry(
                ctx=ctx,
                tool=TOOL_NAME,
                operation="file_copy",
                target=destination,
                approved=ctx.is_approved,
                result="copied",
                audit_event_id=audit.event_id,
            )
        audit.mark_success()

        return json.dumps(
            {
                "workspace_id": workspace_id,
                "source": source,
                "destination": destination,
                "source_type": source_type,
                "bytes_copied": bytes_copied,
                "file_count": copied_file_count,
                "overwritten": overwritten,
                "status": "copied",
                "error": None,
                "partial_copy": False,
            },
            sort_keys=True,
        )
