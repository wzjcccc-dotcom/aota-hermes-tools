"""Security error codes for AOTA Tool Layer enforcement.

Provides 14 specific SecurityError subclasses — no RuntimeError.
Each corresponds to a specific validator failure or audit gap.
"""

from __future__ import annotations


class SecurityError(Exception):
    """Base class for all Tool Layer security errors."""

    error_code: str = "SECURITY_ERROR"

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(f"[{self.error_code}] {detail}")


class WorkspaceEscapeError(SecurityError):
    """Path escapes the registered workspace root."""

    error_code = "WORKSPACE_ESCAPE"


class WriteScopeViolationError(SecurityError):
    """Target path is not in the approved write_scope."""

    error_code = "WRITE_SCOPE_VIOLATION"


class ForbiddenScopeError(SecurityError):
    """Target path matches a forbidden_scope pattern."""

    error_code = "FORBIDDEN_SCOPE"


class ProfileForbiddenError(SecurityError):
    """Current profile is not in the allowed profiles for this tool."""

    error_code = "PROFILE_FORBIDDEN"


class TaskKindForbiddenError(SecurityError):
    """Current task_kind is not in the allowed task kinds for this tool."""

    error_code = "TASK_KIND_FORBIDDEN"


class SpecNotApprovedError(SecurityError):
    """SPEC has not been approved (no APPROVAL.json)."""

    error_code = "SPEC_NOT_APPROVED"


class StaleSpecError(SecurityError):
    """SPEC revision or hash does not match the bound execution context."""

    error_code = "STALE_SPEC"


class ChangeBudgetExceededError(SecurityError):
    """Change count exceeds max_changed_files budget."""

    error_code = "CHANGE_BUDGET_EXCEEDED"


class DirtyConflictError(SecurityError):
    """Workspace contains uncommitted changes not part of the task."""

    error_code = "DIRTY_CONFLICT"


class RequiresHumanCheckpointError(SecurityError):
    """Operation requires human checkpoint before proceeding."""

    error_code = "REQUIRES_HUMAN_CHECKPOINT"


class StaleWorkerError(SecurityError):
    """start_id in env vars does not match meta.json execution.start_id."""

    error_code = "STALE_WORKER"


class BudgetMismatchError(SecurityError):
    """Runtime budget tracking disagrees with worker-reported changed file count."""

    error_code = "BUDGET_MISMATCH"


class AuditGapError(SecurityError):
    """Audit write failed (logged but non-blocking)."""

    error_code = "AUDIT_GAP"


class SecurityContextInvalidError(SecurityError):
    """Security context is invalid — bound identity missing or SPEC.md integrity issue."""

    error_code = "SECURITY_CONTEXT_INVALID"


# Map error codes to classes for programmatic lookup
ERROR_CODE_MAP: dict[str, type[SecurityError]] = {
    "WORKSPACE_ESCAPE": WorkspaceEscapeError,
    "WRITE_SCOPE_VIOLATION": WriteScopeViolationError,
    "FORBIDDEN_SCOPE": ForbiddenScopeError,
    "PROFILE_FORBIDDEN": ProfileForbiddenError,
    "TASK_KIND_FORBIDDEN": TaskKindForbiddenError,
    "SPEC_NOT_APPROVED": SpecNotApprovedError,
    "STALE_SPEC": StaleSpecError,
    "CHANGE_BUDGET_EXCEEDED": ChangeBudgetExceededError,
    "DIRTY_CONFLICT": DirtyConflictError,
    "REQUIRES_HUMAN_CHECKPOINT": RequiresHumanCheckpointError,
    "STALE_WORKER": StaleWorkerError,
    "BUDGET_MISMATCH": BudgetMismatchError,
    "AUDIT_GAP": AuditGapError,
    "SECURITY_CONTEXT_INVALID": SecurityContextInvalidError,
}
