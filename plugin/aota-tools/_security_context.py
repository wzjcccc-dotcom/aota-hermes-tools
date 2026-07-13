"""Security context dataclass and get_security_context() for AOTA Tool Layer.

Provides centralized trusted execution context for all worker mutation tools.
Reads AOTA_PROFILE_TASK_* env vars + meta.json + scope.json on every call.
When env vars are absent, returns SecurityContext with available=False.

P11-N.1: Separates bound SPEC identity (from worker start env vars) from
current SPEC identity (re-read from meta.json + SPEC.md on every call).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from ._task_spec_common import compute_sha256


@dataclass(slots=True)
class SecurityContext:
    """Trusted execution context for a Profile Task worker.

    When available=False (task-main or non-worker context), mutation tools
    that also serve task-main (like file_copy) fall back to workspace
    containment only — no SPEC scope/budget enforcement.
    """

    available: bool
    workspace_id: str = ""
    task_id: str = ""
    start_id: str = ""
    profile: str = ""
    task_kind: str = ""
    profile_task_root: str = ""
    task_dir: str = ""

    # Bound identity — from env vars injected at worker start
    # These represent the SPEC identity the worker was started against
    bound_spec_revision: int = 0
    bound_spec_sha256: str = ""

    # P11-L.1C: True when worker identity env vars are present but SPEC binding
    # env vars (AOTA_PROFILE_TASK_SPEC_REVISION/SHA256) are empty — launch
    # contract violation signal. Mutation callers can use this for clear error
    # messages instead of relying solely on stale-SPEC detection (revision==0).
    binding_contract_missing: bool = False

    # Current identity — re-read from meta.json + SPEC.md on every call
    # These represent the current state of the task directory
    current_spec_revision: int = 0
    current_meta_spec_sha256: str = ""
    current_file_spec_sha256: str = ""

    # Deprecated — kept for backward compatibility, mapped to current_* fields
    @property
    def spec_revision(self) -> int:
        """Deprecated: use current_spec_revision instead."""
        return self.current_spec_revision

    @spec_revision.setter
    def spec_revision(self, value: int) -> None:
        self.current_spec_revision = value

    @property
    def spec_sha256(self) -> str:
        """Deprecated: use current_meta_spec_sha256 instead."""
        return self.current_meta_spec_sha256

    @spec_sha256.setter
    def spec_sha256(self, value: str) -> None:
        self.current_meta_spec_sha256 = value

    # From meta.json
    meta: dict = field(default_factory=dict)
    status: str = ""

    # From scope.json
    scope: dict = field(default_factory=dict)
    write_scope: list[str] = field(default_factory=list)
    read_scope: list[str] = field(default_factory=list)
    forbidden_scope: list[str] = field(default_factory=list)
    scope_revision: int = 0
    scope_sha256: str = ""

    # From meta.json role_contract
    change_budget: dict = field(default_factory=dict)
    human_checkpoints: list[str] = field(default_factory=list)
    forbidden_operations: list[str] = field(default_factory=list)

    # Approval check
    approval_path: str = ""
    is_approved: bool = False


def get_security_context() -> SecurityContext:
    """Build SecurityContext from trusted env vars + meta.json + scope.json.

    Called on every tool invocation. When AOTA_PROFILE_TASK_* env vars are
    absent, returns SecurityContext(available=False) — callers must handle
    the non-worker path.

    P11-N.1: Reads bound identity from AOTA_PROFILE_TASK_SPEC_REVISION and
    AOTA_PROFILE_TASK_SPEC_SHA256 env vars. Reads current identity by
    re-reading meta.json and SPEC.md on every call.
    """
    workspace_id = os.environ.get("AOTA_PROFILE_TASK_WORKSPACE_ID", "")
    task_id = os.environ.get("AOTA_PROFILE_TASK_ID", "")
    start_id = os.environ.get("AOTA_PROFILE_TASK_START_ID", "")
    profile = os.environ.get("AOTA_PROFILE_TASK_PROFILE", "")
    profile_task_root = os.environ.get(
        "AOTA_PROFILE_TASK_ROOT", "/aota-runtime/profile-tasks"
    )

    # P11-N.1: Bound identity from env vars
    bound_revision_raw = os.environ.get("AOTA_PROFILE_TASK_SPEC_REVISION", "")
    bound_sha256_raw = os.environ.get("AOTA_PROFILE_TASK_SPEC_SHA256", "")

    bound_spec_revision: int = 0
    bound_spec_sha256: str = ""
    binding_contract_missing: bool = False
    if bound_revision_raw:
        try:
            bound_spec_revision = int(bound_revision_raw)
        except ValueError:
            bound_spec_revision = 0
    if bound_sha256_raw:
        bound_spec_sha256 = bound_sha256_raw

    if not all([workspace_id, task_id, start_id, profile]):
        return SecurityContext(available=False)

    # P11-L.1C: All four required worker identity env vars are present but SPEC
    # binding env vars are empty/missing — launch contract violation.
    # Signal via binding_contract_missing so mutation callers can fail fast.
    # This is a structured diagnostic, NOT a relaxed gate: when
    # bound_spec_revision==0 the existing stale-SPEC check already blocks
    # mutations. We surface a clear reason instead of a confusing "STALE_SPEC".
    if not bound_revision_raw or not bound_sha256_raw:
        binding_contract_missing = True

    ctx = SecurityContext(
        available=True,
        workspace_id=workspace_id,
        task_id=task_id,
        start_id=start_id,
        profile=profile,
        profile_task_root=profile_task_root,
        bound_spec_revision=bound_spec_revision,
        bound_spec_sha256=bound_spec_sha256,
        binding_contract_missing=binding_contract_missing,
    )

    task_dir = Path(profile_task_root) / workspace_id / task_id
    ctx.task_dir = str(task_dir)

    # Load meta.json
    meta_path = task_dir / "meta.json"
    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                ctx.meta = json.load(f)
        except (OSError, json.JSONDecodeError):
            ctx.meta = {}

    ctx.status = ctx.meta.get("status", "")
    ctx.current_spec_revision = ctx.meta.get("revision", 0)
    ctx.current_meta_spec_sha256 = ctx.meta.get("spec_sha256", "")
    ctx.task_kind = ctx.meta.get("task_kind", "")

    # P11-N.1: Read SPEC.md and compute current_file_spec_sha256
    spec_path = task_dir / "SPEC.md"
    if spec_path.exists():
        try:
            spec_content = spec_path.read_text("utf-8")
            ctx.current_file_spec_sha256 = compute_sha256(spec_content)
        except (OSError, UnicodeDecodeError):
            ctx.current_file_spec_sha256 = ""
    else:
        # SPEC.md missing — integrity issue
        ctx.current_file_spec_sha256 = ""

    # Load scope.json
    scope_path = task_dir / "scope.json"
    if scope_path.exists():
        try:
            with open(scope_path, "r", encoding="utf-8") as f:
                ctx.scope = json.load(f)
        except (OSError, json.JSONDecodeError):
            ctx.scope = {}

    ctx.write_scope = ctx.scope.get("write_scope", [])
    ctx.read_scope = ctx.scope.get("read_scope", [])
    ctx.forbidden_scope = ctx.scope.get("forbidden_scope", [])
    ctx.scope_revision = ctx.scope.get("revision", 0)
    ctx.scope_sha256 = ctx.scope.get("spec_sha256", "")

    # Extract role_contract from meta
    role_contract = ctx.meta.get("spec", {}).get("role_contract", {})
    if isinstance(role_contract, dict):
        ctx.change_budget = role_contract.get("change_budget", {})
        ctx.human_checkpoints = role_contract.get("checkpoint_conditions", [])
        ctx.forbidden_operations = role_contract.get("forbidden_operations", [])
    else:
        ctx.change_budget = {}
        ctx.human_checkpoints = []
        ctx.forbidden_operations = []

    # Check for APPROVAL.json
    approval_path = task_dir / "APPROVAL.json"
    ctx.approval_path = str(approval_path)
    if approval_path.exists():
        ctx.is_approved = True

    return ctx
