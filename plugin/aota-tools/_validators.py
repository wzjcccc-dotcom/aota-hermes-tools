"""Centralized validator functions for AOTA Tool Layer enforcement.

Provides 14 validator functions, each returning ValidationResult.
All validators are reusable and independent — callers chain them as needed.

Validator -> error_code mapping:
  validate_workspace_containment   -> WORKSPACE_ESCAPE
  validate_write_scope             -> WRITE_SCOPE_VIOLATION
  validate_forbidden_scope         -> FORBIDDEN_SCOPE
  validate_profile_capability      -> PROFILE_FORBIDDEN
  validate_task_kind               -> TASK_KIND_FORBIDDEN
  validate_approval                -> SPEC_NOT_APPROVED
  validate_revision                -> STALE_SPEC
  validate_hash                    -> STALE_SPEC
  validate_change_budget           -> CHANGE_BUDGET_EXCEEDED
  validate_dirty                   -> DIRTY_CONFLICT
  check_human_checkpoint           -> REQUIRES_HUMAN_CHECKPOINT
  validate_worker_spec_freshness   -> STALE_SPEC / SECURITY_CONTEXT_INVALID
  validate_stale_worker            -> STALE_WORKER
  validate_budget_mismatch         -> BUDGET_MISMATCH
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TYPE_CHECKING

from ._workspace import resolve_relative_path, WorkspaceError

if TYPE_CHECKING:
    from ._security_context import SecurityContext


@dataclass(slots=True)
class ValidationResult:
    """Result of a single validator call.

    allowed=True means the check passed.
    allowed=False means the check failed with error_code and detail.
    """

    allowed: bool
    error_code: str = ""
    detail: str = ""


def _pass() -> ValidationResult:
    return ValidationResult(allowed=True)


def _fail(error_code: str, detail: str) -> ValidationResult:
    return ValidationResult(allowed=False, error_code=error_code, detail=detail)


# ---------------------------------------------------------------------------
# Validator 1: workspace_containment
# ---------------------------------------------------------------------------


def validate_workspace_containment(
    workspace_id: str,
    target_path: str,
) -> ValidationResult:
    """Verify target_path is contained within the registered workspace.

    Delegates to _workspace.py resolve_relative_path() for path safety.
    """
    if not workspace_id:
        return _fail("WORKSPACE_ESCAPE", "workspace_id is empty")
    if not target_path:
        return _fail("WORKSPACE_ESCAPE", "target_path is empty")

    try:
        resolve_relative_path(workspace_id, target_path, must_exist=False)
    except WorkspaceError as e:
        return _fail("WORKSPACE_ESCAPE", str(e))

    return _pass()


# ---------------------------------------------------------------------------
# Validator 2: write_scope
# ---------------------------------------------------------------------------


def validate_write_scope(
    write_scope: list[str],
    target_path: str,
) -> ValidationResult:
    """Verify target_path is within the SPEC-authorized write_scope.

    Uses fnmatch for glob-style pattern matching.
    """
    if not write_scope:
        return _fail("WRITE_SCOPE_VIOLATION", "write_scope is empty")

    # Normalize path separators
    normalized = target_path.replace("\\", "/")

    for pattern in write_scope:
        pattern_normalized = pattern.replace("\\", "/")
        if fnmatch.fnmatch(normalized, pattern_normalized):
            return _pass()

    return _fail(
        "WRITE_SCOPE_VIOLATION",
        f"'{target_path}' is not in write_scope",
    )


# ---------------------------------------------------------------------------
# Validator 3: forbidden_scope
# ---------------------------------------------------------------------------


def validate_forbidden_scope(
    forbidden_scope: list[str],
    target_path: str,
) -> ValidationResult:
    """Verify target_path does NOT match any forbidden_scope pattern."""
    if not forbidden_scope:
        return _pass()

    normalized = target_path.replace("\\", "/")

    for pattern in forbidden_scope:
        pattern_normalized = pattern.replace("\\", "/")
        if fnmatch.fnmatch(normalized, pattern_normalized):
            return _fail(
                "FORBIDDEN_SCOPE",
                f"'{target_path}' matches forbidden scope pattern '{pattern}'",
            )

    return _pass()


# ---------------------------------------------------------------------------
# Validator 4: profile_capability
# ---------------------------------------------------------------------------


def validate_profile_capability(
    allowed_profile: str,
    current_profile: str,
) -> ValidationResult:
    """Verify the current profile matches the allowed profile for this tool."""
    if not current_profile:
        return _fail("PROFILE_FORBIDDEN", "current profile is empty")
    if not allowed_profile:
        return _fail("PROFILE_FORBIDDEN", "allowed_profile is empty")
    if current_profile != allowed_profile:
        return _fail(
            "PROFILE_FORBIDDEN",
            f"profile '{current_profile}' is not '{allowed_profile}'",
        )
    return _pass()


# ---------------------------------------------------------------------------
# Validator 5: task_kind
# ---------------------------------------------------------------------------


def validate_task_kind(
    allowed_task_kind: str | None,
    current_task_kind: str,
) -> ValidationResult:
    """Verify the current task_kind matches the allowed task kind.

    If allowed_task_kind is None, all task kinds are permitted.
    """
    if allowed_task_kind is None:
        return _pass()
    if not current_task_kind:
        return _fail("TASK_KIND_FORBIDDEN", "current task_kind is empty")
    if current_task_kind != allowed_task_kind:
        return _fail(
            "TASK_KIND_FORBIDDEN",
            f"task_kind '{current_task_kind}' is not '{allowed_task_kind}'",
        )
    return _pass()


# ---------------------------------------------------------------------------
# Validator 6: approval
# ---------------------------------------------------------------------------


def validate_approval(
    is_approved: bool,
) -> ValidationResult:
    """Verify the task SPEC has been approved."""
    if not is_approved:
        return _fail(
            "SPEC_NOT_APPROVED",
            "task SPEC has not been approved (no APPROVAL.json)",
        )
    return _pass()


# ---------------------------------------------------------------------------
# Validator 7: revision
# ---------------------------------------------------------------------------


def validate_revision(
    bound_revision: int | None,
    current_revision: int,
) -> ValidationResult:
    """Verify the SPEC revision matches the bound execution revision.

    If bound_revision is None, revision check is skipped.
    """
    if bound_revision is None:
        return _pass()
    if bound_revision != current_revision:
        return _fail(
            "STALE_SPEC",
            f"SPEC revision mismatch: bound={bound_revision}, current={current_revision}",
        )
    return _pass()


# ---------------------------------------------------------------------------
# Validator 8: hash
# ---------------------------------------------------------------------------


def validate_hash(
    bound_sha256: str | None,
    current_sha256: str,
) -> ValidationResult:
    """Verify the SPEC SHA-256 hash matches the bound execution hash.

    If bound_sha256 is None or empty, hash check is skipped.
    """
    if not bound_sha256:
        return _pass()
    if bound_sha256 != current_sha256:
        return _fail(
            "STALE_SPEC",
            f"SPEC hash mismatch: bound={bound_sha256[:16]}..., current={current_sha256[:16]}...",
        )
    return _pass()


# ---------------------------------------------------------------------------
# Validator 9: change_budget
# ---------------------------------------------------------------------------


def validate_change_budget(
    change_budget: dict,
    changed_count: int,
) -> ValidationResult:
    """Verify the number of changed files does not exceed the budget.

    change_budget may contain:
      - max_changed_files: int (required for enforcement)
      - allow_create: bool
      - allow_delete: bool
      - allow_move: bool
      - allow_dependency_change: bool
    """
    if not change_budget:
        return _pass()  # No budget = no limit

    max_changed = change_budget.get("max_changed_files")
    if max_changed is None:
        return _pass()  # No max = no limit

    if changed_count > max_changed:
        return _fail(
            "CHANGE_BUDGET_EXCEEDED",
            f"changed {changed_count} files, budget allows {max_changed}",
        )
    return _pass()


# ---------------------------------------------------------------------------
# Validator 10: dirty conflict
# ---------------------------------------------------------------------------


def validate_dirty(
    workspace_root: str | None,
) -> ValidationResult:
    """Check for uncommitted changes in the workspace Git repo.

    This is a lightweight check — if the workspace is not a Git repo,
    the check is skipped (no false positives).

    The caller must have already resolved the workspace root to a
    real filesystem path.
    """
    if not workspace_root:
        return _pass()  # Can't check without a root

    git_dir = Path(workspace_root) / ".git"
    if not git_dir.exists():
        return _pass()  # Not a Git repo — skip

    # Use git status --porcelain for a lightweight dirty check
    import subprocess

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=workspace_root,
            timeout=10,
        )
        if result.returncode != 0:
            # Git command failed — don't block, just skip
            return _pass()
        if result.stdout.strip():
            return _fail(
                "DIRTY_CONFLICT",
                "workspace has uncommitted changes",
            )
    except (subprocess.SubprocessError, OSError):
        return _pass()  # Git not available — skip

    return _pass()


# ---------------------------------------------------------------------------
# Validator 11: human_checkpoint
# ---------------------------------------------------------------------------


def check_human_checkpoint(
    human_checkpoints: list[str],
    operation: str,
) -> ValidationResult:
    """Check if the given operation requires a human checkpoint.

    human_checkpoints is a list of checkpoint triggers from the
    role_contract (e.g. ['deploy', 'reload', 'live_worker']).
    operation is the current operation being attempted (e.g. 'deploy').
    """
    if not operation:
        return _pass()

    # Check if the operation matches any checkpoint trigger
    for checkpoint in human_checkpoints:
        if checkpoint.lower() in operation.lower():
            return _fail(
                "REQUIRES_HUMAN_CHECKPOINT",
                f"operation '{operation}' requires human checkpoint '{checkpoint}'",
            )

    return _pass()


# ---------------------------------------------------------------------------
# Validator 12: worker_spec_freshness (P11-N.1)
# ---------------------------------------------------------------------------


def validate_worker_spec_freshness(
    ctx: "SecurityContext",
) -> ValidationResult:
    """Unified freshness validator for all mutation tools.

    Validation order:
      1. Bound completeness — bound_spec_revision and bound_spec_sha256 must be non-zero/non-empty
      2. Current completeness — current_spec_revision and current_meta_spec_sha256 must be non-zero/non-empty
      3. Meta/file integrity — current_meta_spec_sha256 must match current_file_spec_sha256
         (if SPEC.md is missing, current_file_spec_sha256 is empty → SECURITY_CONTEXT_INVALID)
      4. Revision mismatch — bound_spec_revision != current_spec_revision → STALE_SPEC
      5. Hash mismatch — bound_spec_sha256 != current_meta_spec_sha256 → STALE_SPEC

    Returns _pass() if fresh, _fail() with appropriate error code otherwise.
    Must NOT be called for report submit or outcome submit tools.
    """
    # 1. Bound completeness
    if ctx.bound_spec_revision == 0:
        return _fail(
            "SECURITY_CONTEXT_INVALID",
            "bound_spec_revision is missing (old task without bound env vars)",
        )
    if not ctx.bound_spec_sha256:
        return _fail(
            "SECURITY_CONTEXT_INVALID",
            "bound_spec_sha256 is missing (old task without bound env vars)",
        )

    # 2. Current completeness
    if ctx.current_spec_revision == 0:
        return _fail(
            "SECURITY_CONTEXT_INVALID",
            "current_spec_revision is missing (meta.json may be corrupt)",
        )
    if not ctx.current_meta_spec_sha256:
        return _fail(
            "SECURITY_CONTEXT_INVALID",
            "current_meta_spec_sha256 is missing (meta.json may be corrupt)",
        )

    # 3. Meta/file integrity — check SPEC.md existence and hash match
    if not ctx.current_file_spec_sha256:
        # SPEC.md is missing from task directory
        return _fail(
            "SECURITY_CONTEXT_INVALID",
            "SPEC.md is missing from task directory — integrity issue",
        )
    if ctx.current_meta_spec_sha256 != ctx.current_file_spec_sha256:
        return _fail(
            "SECURITY_CONTEXT_INVALID",
            f"meta.json spec_sha256 ({ctx.current_meta_spec_sha256[:16]}...) "
            f"does not match SPEC.md file hash ({ctx.current_file_spec_sha256[:16]}...)",
        )

    # 4. Revision mismatch
    if ctx.bound_spec_revision != ctx.current_spec_revision:
        return _fail(
            "STALE_SPEC",
            f"SPEC revision mismatch: bound={ctx.bound_spec_revision}, "
            f"current={ctx.current_spec_revision}",
        )

    # 5. Hash mismatch
    if ctx.bound_spec_sha256 != ctx.current_meta_spec_sha256:
        return _fail(
            "STALE_SPEC",
            f"SPEC hash mismatch: bound={ctx.bound_spec_sha256[:16]}..., "
            f"current={ctx.current_meta_spec_sha256[:16]}...",
        )

    return _pass()


# ---------------------------------------------------------------------------
# Validator 13: stale_worker (P11-N.1)
# ---------------------------------------------------------------------------


def validate_stale_worker(
    ctx: "SecurityContext",
) -> ValidationResult:
    """Consistency check: bound start_id vs meta.json execution.start_id.

    If the env var start_id does not match meta.json execution.start_id,
    the worker is stale — the meta.json may have been updated by a
    different process.

    Used in consistency checks, not in mutation preflight.
    """
    if not ctx.available:
        return _pass()

    execution = ctx.meta.get("execution", {})
    meta_start_id = execution.get("start_id", "")

    if ctx.start_id and meta_start_id and ctx.start_id != meta_start_id:
        return _fail(
            "STALE_WORKER",
            f"bound start_id ({ctx.start_id}) does not match "
            f"meta.json execution.start_id ({meta_start_id})",
        )
    return _pass()


# ---------------------------------------------------------------------------
# Validator 14: budget_mismatch (P11-N.1)
# ---------------------------------------------------------------------------


def validate_budget_mismatch(
    ctx: "SecurityContext",
    runtime_changed_count: int,
) -> ValidationResult:
    """Consistency check: runtime budget tracking vs worker-reported count.

    Compares the runtime ledger's changed file count with the count
    reported by the worker. A mismatch indicates potential tampering
    or accounting error.

    Used in consistency checks, not in mutation preflight.
    """
    if not ctx.change_budget:
        return _pass()

    # Read from audit ledger as the authoritative runtime count
    task_dir = ctx.task_dir
    if not task_dir:
        return _pass()

    ledger_path = Path(task_dir) / "audit-ledger.jsonl"
    if not ledger_path.exists():
        return _pass()  # No ledger yet — skip

    try:
        with open(ledger_path, "r", encoding="utf-8") as f:
            ledger_count = sum(1 for _ in f)
    except OSError:
        return _pass()  # Can't read ledger — skip

    if runtime_changed_count != ledger_count:
        return _fail(
            "BUDGET_MISMATCH",
            f"runtime changed count ({runtime_changed_count}) does not match "
            f"audit ledger count ({ledger_count})",
        )
    return _pass()


# ---------------------------------------------------------------------------
# Convenience: run a chain of validators, stop on first failure
# ---------------------------------------------------------------------------


def run_validators(
    validators: list[Callable[[], ValidationResult]],
) -> ValidationResult:
    """Run a chain of zero-arg validators, returning the first failure.

    Returns _pass() if all validators pass.
    """
    for validator in validators:
        result = validator()
        if not result.allowed:
            return result
    return _pass()
