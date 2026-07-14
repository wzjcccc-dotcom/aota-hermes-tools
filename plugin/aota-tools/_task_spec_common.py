"""Shared utilities for AOTA task spec artifact creation and update.

Provides: task ID generation, scope validation, SPEC.md rendering,
meta.json schema, SHA-256 hashing, atomic writes, flock-based locking,
field limits, and semantic validation rules.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import secrets
import tempfile
import time
import fcntl
from pathlib import Path
from typing import Any, Optional

from ._workspace import WorkspaceError

from ._role_contracts import (
    SPEC_SCHEMA_VERSION as ROLE_SCHEMA_VERSION,
    PROCESS_PATHS,
    VALIDATION_TIERS,
    HUMAN_CHECKPOINT_VALUES,
    get_fields_for_kind,
    get_render_order,
    get_prompt_projection,
    validate_role_contract,
    apply_defaults,
    validate_implementation_semantics,
    validate_diagnosis_semantics,
    validate_review_semantics,
    validate_architecture_semantics,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TASK_KIND_PROFILE_HINT: dict[str, str] = {
    "implementation": "coder",
    "diagnosis": "debugger",
    "review": "reviewer",
    "architecture": "architect",
}

HUMAN_CHECKPOINT_POLICY_MAP: dict[str, str] = {
    "implementation": "required_before_start",
    "diagnosis": "not_required_for_readonly_diagnosis",
    "review": "not_required_for_readonly_review",
    "architecture": "not_required_for_readonly_architecture",
}

SOURCE_MUTATION_POLICY_MAP: dict[str, str] = {
    "implementation": "allowed_after_approval",
    "diagnosis": "forbidden",
    "review": "forbidden",
    "architecture": "forbidden",
}

EXECUTION_POLICY_LINES: dict[str, list[str]] = {
    "implementation": [
        "- This SPEC is a draft artifact.",
        "- Creating or updating this SPEC does not start execution.",
        "- Profile execution is a separate action.",
        "- Human approval is required before implementation tasks start.",
    ],
    "diagnosis": [
        "- This SPEC is a draft artifact.",
        "- Creating or updating this SPEC does not start execution.",
        "- Profile execution is a separate action.",
        "- Human approval is not required for read-only diagnosis tasks.",
    ],
    "review": [
        "- This SPEC is a draft artifact.",
        "- Creating or updating this SPEC does not start execution.",
        "- Profile execution is a separate action.",
        "- Human approval is not required for read-only review tasks.",
    ],
    "architecture": [
        "- This SPEC is a draft artifact.",
        "- Creating or updating this SPEC does not start execution.",
        "- Profile execution is a separate action.",
        "- Human approval is not required for read-only architecture review tasks.",
    ],
}

RISK_LEVELS = ("low", "medium", "high")
TASK_KINDS = ("implementation", "diagnosis", "review", "architecture")
ARCHITECTURE_MODES = ("design_review", "spec_preflight")
STATUS_DRAFT = "draft"
STATUS_NEEDS_INPUT = "needs_input"

FIELD_LIMITS: dict[str, int] = {
    "title": 200,
    "goal": 8000,
    "list_item": 2000,
    "list_count": 100,
    "spec_md_bytes": 65536,
}

AOTA_RUNTIME_ROOT = os.environ.get("AOTA_RUNTIME_ROOT", "/aota-runtime")
AOTA_PROFILE_TASK_ROOT = os.environ.get(
    "AOTA_PROFILE_TASK_ROOT", "/aota-runtime/profile-tasks"
)

LOCK_ROOT = Path(AOTA_RUNTIME_ROOT) / "locks" / "task-spec"
PROFILE_TASK_ROOT = Path(AOTA_PROFILE_TASK_ROOT)

# Fields that can be modified on update
MUTABLE_SPEC_FIELDS = (
    "title",
    "goal",
    "risk_level",
    "known_inputs",
    "read_scope",
    "write_scope",
    "forbidden_scope",
    "acceptance_criteria",
    "validation_policy",
    "stop_conditions",
    "evidence_required",
    "process_path",
    "validation_tier",
    "human_checkpoints",
    "role_contract",
)

SPEC_SCHEMA_VERSION_LEGACY = 2   # P11-K: role-specific contract before traceability
SPEC_SCHEMA_VERSION_CURRENT = 3  # PF-WI-05: verified Plan traceability

# New shared fields added in P11-K
NEW_SHARED_FIELDS = ("process_path", "validation_tier", "human_checkpoints")


# ---------------------------------------------------------------------------
# Task ID generation
# ---------------------------------------------------------------------------

def generate_task_id() -> str:
    """Generate a unique task ID: pt_<UTC_TIMESTAMP>_<RANDOM>."""
    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    rand = secrets.token_hex(4)  # 8 hex chars
    return f"pt_{ts}_{rand}"


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def utc_now_iso() -> str:
    """Return current UTC time in ISO 8601 format (Z suffix)."""
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# SHA-256
# ---------------------------------------------------------------------------

def compute_sha256(content: str) -> str:
    """Compute SHA-256 hex digest of *content* (UTF-8 encoded)."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Scope expression validation
# ---------------------------------------------------------------------------

def validate_scope_expressions(expressions: list[str]) -> Optional[str]:
    """Validate a list of scope glob expressions.

    Rejects: absolute path, NUL byte, ``..`` segment, backslash escape,
    blank/whitespace-only item.
    Returns *None* when valid, an error string otherwise.
    """
    if not isinstance(expressions, list):
        return "scope expressions must be a list"

    for i, expr in enumerate(expressions):
        if not isinstance(expr, str) or not expr.strip():
            return f"scope item at index {i} is empty or not a string"
        if expr.startswith("/"):
            return f"scope item at index {i} is an absolute path: {expr!r}"
        if "\x00" in expr:
            return f"scope item at index {i} contains NUL byte"
        if ".." in expr.split("/"):
            return f"scope item at index {i} contains a '..' segment: {expr!r}"
        if "\\" in expr:
            return f"scope item at index {i} contains a backslash escape: {expr!r}"
    return None


# ---------------------------------------------------------------------------
# Semantic validation per task_kind
# ---------------------------------------------------------------------------

def validate_semantic_rules(
    task_kind: str,
    write_scope: list[str],
    subject_task_id: Optional[str] = None,
    architecture_mode: Optional[str] = None,
) -> Optional[str]:
    """Validate task-kind-specific rules.

    * **implementation** — *write_scope* must be non-empty
    * **diagnosis** — *write_scope* must be empty []
    * **review** — *write_scope* must be empty [] and *subject_task_id* required
    * **architecture** — *write_scope* must be empty [], *subject_task_id* required,
      and *architecture_mode* must be a valid mode
    """
    if task_kind == "implementation":
        if not write_scope:
            return (
                "implementation tasks must have a non-empty write_scope"
            )
    elif task_kind == "diagnosis":
        if write_scope:
            return "diagnosis tasks must have an empty write_scope"
    elif task_kind == "review":
        if write_scope:
            return "review tasks must have an empty write_scope"
        if not subject_task_id:
            return "review tasks require a subject_task_id"
    elif task_kind == "architecture":
        if write_scope:
            return "architecture tasks must have an empty write_scope"
        if not subject_task_id:
            return "architecture tasks require a subject_task_id"
        if architecture_mode not in ARCHITECTURE_MODES:
            return f"architecture tasks require a valid architecture_mode (design_review or spec_preflight), got {architecture_mode!r}"
    return None


def check_scope_overlap(
    write_scope: list[str], forbidden_scope: list[str]
) -> Optional[str]:
    """Check for exact literal overlap between write_scope and forbidden_scope."""
    write_set = set(write_scope)
    forbidden_set = set(forbidden_scope)
    overlap = write_set & forbidden_set
    if overlap:
        items = ", ".join(sorted(overlap))
        return f"write_scope and forbidden_scope overlap on: {items}"
    return None


# ---------------------------------------------------------------------------
# P11-K: Shared field validators
# ---------------------------------------------------------------------------

def validate_process_path(process_path: Optional[str]) -> Optional[str]:
    """Validate process_path value."""
    if process_path is None:
        return None  # Optional field
    if process_path not in PROCESS_PATHS:
        return f"process_path must be one of {PROCESS_PATHS}, got {process_path!r}"
    return None


def validate_validation_tier(tier: Any) -> Optional[str]:
    """Validate validation_tier value."""
    if tier is None:
        return None  # Optional field
    if not isinstance(tier, int) or isinstance(tier, bool):
        return f"validation_tier must be an integer, got {type(tier).__name__}"
    if tier not in VALIDATION_TIERS:
        return f"validation_tier must be one of {VALIDATION_TIERS}, got {tier}"
    return None


def validate_human_checkpoints(checkpoints: list[str]) -> Optional[str]:
    """Validate human_checkpoints list."""
    if not isinstance(checkpoints, list):
        return "human_checkpoints must be a list"
    for i, cp in enumerate(checkpoints):
        if not isinstance(cp, str):
            return f"human_checkpoints[{i}] is not a string"
        if cp not in HUMAN_CHECKPOINT_VALUES:
            return f"human_checkpoints[{i}] has invalid value {cp!r}, must be one of {HUMAN_CHECKPOINT_VALUES}"
    return None


def validate_role_contract_for_task(
    task_kind: str,
    role_contract: Optional[dict[str, Any]],
    architecture_mode: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Full role contract validation including semantic rules.
    
    Returns list of error dicts (empty = valid).
    """
    if role_contract is None:
        role_contract = {}
    if not isinstance(role_contract, dict):
        return [{
            "code": "ROLE_CONTRACT_INVALID_TYPE",
            "task_kind": task_kind,
            "field": "role_contract",
            "reason": f"role_contract must be a dict, got {type(role_contract).__name__}",
        }]
    
    errors = list(validate_role_contract(task_kind, role_contract, architecture_mode))
    
    # Run semantic validators
    if task_kind == "implementation":
        errors.extend(validate_implementation_semantics(role_contract))
    elif task_kind == "diagnosis":
        errors.extend(validate_diagnosis_semantics(role_contract))
    elif task_kind == "review":
        errors.extend(validate_review_semantics(role_contract))
    elif task_kind == "architecture":
        errors.extend(validate_architecture_semantics(role_contract, architecture_mode))
    
    return errors


def role_contract_errors_to_message(errors: list[dict[str, Any]]) -> str:
    """Convert role contract validation errors to a human-readable message."""
    if not errors:
        return ""
    parts = []
    for e in errors:
        code = e.get("code", "UNKNOWN")
        field = e.get("field", "")
        reason = e.get("reason", "")
        parts.append(f"[{code}] {field}: {reason}")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Field-length validation
# ---------------------------------------------------------------------------

def validate_field_limits(
    title: str,
    goal: str,
    known_inputs: list[str],
    read_scope: list[str],
    write_scope: list[str],
    forbidden_scope: list[str],
    acceptance_criteria: list[str],
    validation_policy: list[str],
    stop_conditions: list[str],
    evidence_required: list[str],
) -> Optional[str]:
    """Enforce max-length and max-count constraints.

    Returns *None* on success, error string on first violation.
    """
    if len(title) > FIELD_LIMITS["title"]:
        return (
            f"title exceeds {FIELD_LIMITS['title']} characters "
            f"(got {len(title)})"
        )
    if len(goal) > FIELD_LIMITS["goal"]:
        return (
            f"goal exceeds {FIELD_LIMITS['goal']} characters "
            f"(got {len(goal)})"
        )

    all_lists: dict[str, list[str]] = {
        "known_inputs": known_inputs,
        "read_scope": read_scope,
        "write_scope": write_scope,
        "forbidden_scope": forbidden_scope,
        "acceptance_criteria": acceptance_criteria,
        "validation_policy": validation_policy,
        "stop_conditions": stop_conditions,
        "evidence_required": evidence_required,
    }

    for list_name, items in all_lists.items():
        if len(items) > FIELD_LIMITS["list_count"]:
            return (
                f"{list_name} exceeds {FIELD_LIMITS['list_count']} items "
                f"(got {len(items)})"
            )
        for idx, item in enumerate(items):
            if len(item) > FIELD_LIMITS["list_item"]:
                return (
                    f"{list_name}[{idx}] exceeds "
                    f"{FIELD_LIMITS['list_item']} characters"
                )

    return None


# ---------------------------------------------------------------------------
# SPEC.md rendering
# ---------------------------------------------------------------------------

def render_spec_md(
    task_id: str,
    workspace_id: str,
    task_kind: str,
    profile_hint: str,
    risk_level: str,
    revision: int,
    status: str,
    goal: str,
    known_inputs: list[str],
    read_scope: list[str],
    write_scope: list[str],
    forbidden_scope: list[str],
    acceptance_criteria: list[str],
    validation_policy: list[str],
    stop_conditions: list[str],
    evidence_required: list[str],
    subject_task_id: Optional[str],
    parent_task_id: Optional[str],
    architecture_mode: Optional[str] = None,
    subject_spec_revision: Optional[int] = None,
    subject_spec_sha256: Optional[str] = None,
    # P11-K new fields
    process_path: Optional[str] = None,
    validation_tier: Optional[int] = None,
    human_checkpoints: Optional[list[str]] = None,
    role_contract: Optional[dict[str, Any]] = None,
    source_traceability: Optional[dict[str, Any]] = None,
    spec_schema_version: int = SPEC_SCHEMA_VERSION_CURRENT,
) -> str:
    """Render a deterministic SPEC.md document.

    The structure is fixed per the AOTA task specification artifact schema.
    """
    lines: list[str] = []

    lines.append("# Task")
    lines.append("")
    lines.append("## Identity")
    lines.append(f"- Task ID: {task_id}")
    lines.append(f"- Workspace: {workspace_id}")
    lines.append(f"- Task Kind: {task_kind}")
    lines.append(f"- Profile Hint: {profile_hint}")
    lines.append(f"- Risk Level: {risk_level}")
    lines.append(f"- Revision: {revision}")
    lines.append(f"- Status: {status}")
    lines.append(f"- Spec Schema Version: {spec_schema_version}")
    lines.append("")
    lines.append("## Goal")
    lines.append(goal)
    lines.append("")
    lines.append("## Known Inputs")
    if known_inputs:
        for item in known_inputs:
            lines.append(f"- {item}")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Read Scope")
    for item in read_scope:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Write Scope")
    if write_scope:
        for item in write_scope:
            lines.append(f"- {item}")
    else:
        lines.append("- None (enforced)")
    lines.append("")
    lines.append("## Forbidden Scope")
    if forbidden_scope:
        for item in forbidden_scope:
            lines.append(f"- {item}")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Acceptance Criteria")
    for item in acceptance_criteria:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Validation Policy")
    if validation_policy:
        for item in validation_policy:
            lines.append(f"- {item}")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Stop Conditions")
    for item in stop_conditions:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Evidence Required")
    for item in evidence_required:
        lines.append(f"- {item}")
    lines.append("")

    # P11-K: New shared fields
    lines.append("## Process Path")
    lines.append(f"- {process_path or 'standard'}")
    lines.append("")
    lines.append("## Validation Tier")
    lines.append(f"- {validation_tier or '0'}")
    lines.append("")
    lines.append("## Human Checkpoints")
    hc = human_checkpoints or []
    if hc:
        for item in hc:
            lines.append(f"- {item}")
    else:
        lines.append("- None")
    lines.append("")

    # P11-K: Role-specific contract fields
    if role_contract:
        render_order = get_render_order(task_kind, architecture_mode)
        for field_name in render_order:
            value = role_contract.get(field_name)
            # Convert snake_case to Title Case for section heading
            heading = field_name.replace("_", " ").title()
            lines.append(f"## {heading}")
            if value is not None:
                if isinstance(value, bool):
                    lines.append(f"- {'True' if value else 'False'}")
                elif isinstance(value, dict):
                    for sub_key, sub_val in value.items():
                        sub_heading = sub_key.replace("_", " ").title()
                        lines.append(f"- {sub_heading}: {sub_val}")
                elif isinstance(value, list):
                    if len(value) > 0:
                        for item in value:
                            lines.append(f"- {item}")
                    else:
                        lines.append("- None")
                elif isinstance(value, str):
                    lines.append(f"- {value}")
                else:
                    lines.append(f"- {value}")
            else:
                lines.append("- None")
            lines.append("")

    lines.append("## Related Tasks")
    lines.append(f"- Subject Task: {subject_task_id or 'None'}")
    lines.append(f"- Parent Task: {parent_task_id or 'None'}")
    lines.append("")

    if spec_schema_version >= 3:
        lines.append("## Source Traceability")
        if source_traceability is None:
            lines.append("- Mode: standalone")
        else:
            lines.append("- Mode: plan_linked")
            lines.append(f"- Plan ID: {source_traceability['plan_id']}")
            lines.append(f"- Plan Revision: {source_traceability['plan_revision']}")
            lines.append(f"- Plan SHA-256: {source_traceability['plan_sha256']}")
            lines.append(f"- Milestone ID: {source_traceability['milestone_id']}")
            lines.append(f"- Work Item ID: {source_traceability['work_item_id']}")
            lines.append(f"- Architect Review ID: {source_traceability['architect_review_id'] or 'None'}")
            lines.append(f"- Verification Status: {source_traceability['verification_status']}")
        lines.append("")

    if architecture_mode:
        lines.append("## Architecture Mode")
        lines.append(f"- Mode: {architecture_mode}")
        if subject_spec_revision is not None:
            lines.append(f"- Subject SPEC Revision: {subject_spec_revision}")
        if subject_spec_sha256:
            lines.append(f"- Subject SPEC SHA-256: {subject_spec_sha256}")
        lines.append("")

    lines.append("## Execution Policy")
    for line in EXECUTION_POLICY_LINES[task_kind]:
        lines.append(line)
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Spec dict / meta builders
# ---------------------------------------------------------------------------

def build_spec_dict(
    title: str,
    goal: str,
    known_inputs: list[str],
    read_scope: list[str],
    write_scope: list[str],
    forbidden_scope: list[str],
    acceptance_criteria: list[str],
    validation_policy: list[str],
    stop_conditions: list[str],
    evidence_required: list[str],
    process_path: Optional[str] = None,
    validation_tier: Optional[int] = None,
    human_checkpoints: Optional[list[str]] = None,
    role_contract: Optional[dict[str, Any]] = None,
    source_traceability: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build the ``spec`` sub-dict for meta.json."""
    return {
        "title": title,
        "goal": goal,
        "known_inputs": known_inputs,
        "read_scope": read_scope,
        "write_scope": write_scope,
        "forbidden_scope": forbidden_scope,
        "acceptance_criteria": acceptance_criteria,
        "validation_policy": validation_policy,
        "stop_conditions": stop_conditions,
        "evidence_required": evidence_required,
        "process_path": process_path,
        "validation_tier": validation_tier,
        "human_checkpoints": human_checkpoints or [],
        "role_contract": role_contract or {},
        "source_traceability": source_traceability,
    }


def build_meta(
    task_id: str,
    workspace_id: str,
    workspace_root_at_creation: str,
    task_kind: str,
    profile_hint: str,
    risk_level: str,
    revision: int,
    created_at: str,
    updated_at: str,
    subject_task_id: Optional[str],
    parent_task_id: Optional[str],
    human_checkpoint_policy: str,
    source_mutation_policy: str,
    spec_sha256: str,
    spec: dict[str, Any],
    architecture_mode: Optional[str] = None,
    subject_spec_revision: Optional[int] = None,
    subject_spec_sha256: Optional[str] = None,
    spec_schema_version: int = SPEC_SCHEMA_VERSION_CURRENT,
) -> dict[str, Any]:
    """Build the full ``meta.json`` dict."""
    return {
        "schema_version": spec_schema_version,
        "task_id": task_id,
        "workspace_id": workspace_id,
        "workspace_root_at_creation": workspace_root_at_creation,
        "task_kind": task_kind,
        "profile_hint": profile_hint,
        "risk_level": risk_level,
        "status": STATUS_DRAFT,
        "revision": revision,
        "created_at": created_at,
        "updated_at": updated_at,
        "subject_task_id": subject_task_id,
        "parent_task_id": parent_task_id,
        "architecture_mode": architecture_mode,
        "subject_spec_revision": subject_spec_revision,
        "subject_spec_sha256": subject_spec_sha256,
        "human_checkpoint_policy": human_checkpoint_policy,
        "source_mutation_policy": source_mutation_policy,
        "spec_sha256": spec_sha256,
        "spec_path": "SPEC.md",
        "spec": spec,
    }


# ---------------------------------------------------------------------------
# Atomic file I/O
# ---------------------------------------------------------------------------

def atomic_write(path: Path, content: str) -> None:
    """Write *content* to *path* atomically via temp → os.replace.

    Creates parent directories if needed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.tmp_"
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


def read_json(path: Path) -> dict[str, Any]:
    """Read and parse a JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Atomically write a JSON dict to *path*."""
    content = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    atomic_write(path, content)


# ---------------------------------------------------------------------------
# Lock helpers  (fcntl.flock)
# ---------------------------------------------------------------------------

def get_lock_path(workspace_id: str, task_id: str) -> Path:
    """Return the filesystem path for the flock file of a task."""
    return LOCK_ROOT / workspace_id / f"{task_id}.lock"


def acquire_lock(workspace_id: str, task_id: str, timeout: float = 5.0) -> int:
    """Acquire an exclusive flock on *workspace_id*/*task_id*.

    Retries with ``LOCK_NB`` every 100 ms up to *timeout* seconds.
    Returns an open file descriptor the caller **must** release via
    :func:`release_lock`.

    Raises
        TimeoutError — if the lock could not be acquired within *timeout*.
        WorkspaceError — on other OS errors.
    """
    lock_path = get_lock_path(workspace_id, task_id)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as e:
        raise WorkspaceError(f"failed to open lock file: {e}") from e

    start = time.monotonic()
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() - start >= timeout:
                os.close(fd)
                raise TimeoutError(
                    f"could not acquire lock for {workspace_id}/{task_id} "
                    f"within {timeout}s"
                )
            time.sleep(0.1)
        except OSError as e:
            os.close(fd)
            raise WorkspaceError(f"flock error: {e}") from e


def release_lock(fd: int) -> None:
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
# Task directory helpers
# ---------------------------------------------------------------------------

def get_task_dir(workspace_id: str, task_id: str) -> Path:
    """Return the task directory path for *workspace_id*/*task_id*."""
    return PROFILE_TASK_ROOT / workspace_id / task_id


def load_meta(task_dir: Path) -> dict[str, Any]:
    """Load and return the contents of *task_dir*/meta.json."""
    meta_path = task_dir / "meta.json"
    if not meta_path.exists():
        raise WorkspaceError("meta.json not found")
    return read_json(meta_path)


def load_spec_md(task_dir: Path) -> str:
    """Load and return the contents of *task_dir*/SPEC.md."""
    spec_path = task_dir / "SPEC.md"
    if not spec_path.exists():
        raise WorkspaceError("SPEC.md not found")
    with open(spec_path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Reference task validation
# ---------------------------------------------------------------------------

def validate_task_reference(
    workspace_id: str, ref_task_id: str, label: str, self_task_id: Optional[str] = None
) -> None:
    """Check that a referenced task exists in the same workspace.

    *label* is used in error messages (e.g. ``"subject_task_id"``).
    If *self_task_id* is given, rejects a self-reference.
    """
    if ref_task_id == self_task_id:
        raise WorkspaceError(f"{label} cannot reference the task itself")

    ref_dir = get_task_dir(workspace_id, ref_task_id)
    ref_meta_path = ref_dir / "meta.json"
    if not ref_meta_path.exists():
        raise WorkspaceError(
            f"{label} '{ref_task_id}' not found in workspace '{workspace_id}'"
        )

    # Verify it belongs to the same workspace
    ref_meta = read_json(ref_meta_path)
    if ref_meta.get("workspace_id") != workspace_id:
        raise WorkspaceError(
            f"{label} '{ref_task_id}' belongs to workspace "
            f"'{ref_meta.get('workspace_id')}', not '{workspace_id}'"
        )


def create_exclusive_task_dir(workspace_id: str, max_attempts: int = 10) -> tuple[str, Path]:
    """Generate a unique task_id and create its directory exclusively.

    Retries up to *max_attempts* times on directory collision.
    Returns (task_id, task_dir).
    """
    for _attempt in range(max_attempts):
        task_id = generate_task_id()
        task_dir = get_task_dir(workspace_id, task_id)
        try:
            task_dir.mkdir(parents=True, exist_ok=False)
            return task_id, task_dir
        except FileExistsError:
            continue
    raise WorkspaceError(
        f"could not generate unique task ID after {max_attempts} attempts"
    )
