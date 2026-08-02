"""Canonical session-state authority for AOTA current semantic references.

This module is the single control-plane authority for *current* session state.
It owns the ``session-state/`` runtime layout, the pointer schema, and the
atomic pointer operations.  It is deliberately separate from the durable
``profile-tasks/`` history store: durable artifacts record *what happened*,
while session-state pointers record *what the current session is working on*.

Layering (the highest principle):

    profile-tasks/   → durable historical truth (what happened)
    session-state/   → current-session semantic truth (what is current)
    indexes/         → derived/rebuildable (not an authority, not model-visible)

A current semantic reference (``current_draft_spec``, ``active_frozen_spec``,
``active_task``, ``current_completion``, ``current_handoff``,
``current_decision``, ``current_completed_task``) must resolve from a trusted
session-state pointer first and only.  Resolvers must not scan durable
history to guess the current subject.

Path components are canonicalized, bounded, path-safe, and never taken from
model arguments.  The filesystem path uses a session digest; the pointer body
retains the trusted session id for verification.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

# ---------------------------------------------------------------------------
# Roots
# ---------------------------------------------------------------------------

RUNTIME_ROOT = Path(os.environ.get("AOTA_RUNTIME_ROOT", "/aota-runtime"))
SESSION_STATE_ROOT = Path(
    os.environ.get("AOTA_SESSION_STATE_ROOT", str(RUNTIME_ROOT / "session-state"))
)
INDEXES_ROOT = Path(
    os.environ.get("AOTA_INDEXES_ROOT", str(RUNTIME_ROOT / "indexes"))
)
LEGACY_SESSION_ACTIVE_SPEC_ROOT = Path(
    os.environ.get("AOTA_SESSION_ACTIVE_SPEC_ROOT", str(RUNTIME_ROOT / "session-active-spec"))
)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

POINTER_SCHEMA_VERSION = 1

# The canonical pointer kinds.  Each maps to a durable artifact kind and a
# fixed pointer filename under the session partition.
POINTER_KIND_CURRENT_DRAFT_SPEC = "current_draft_spec"
POINTER_KIND_ACTIVE_FROZEN_SPEC = "active_frozen_spec"
POINTER_KIND_ACTIVE_TASK = "active_task"
POINTER_KIND_CURRENT_COMPLETION = "current_completion"
POINTER_KIND_CURRENT_HANDOFF = "current_handoff"
POINTER_KIND_CURRENT_DECISION = "current_decision"
POINTER_KIND_CURRENT_COMPLETED_TASK = "current_completed_task"
POINTER_KIND_CURRENT_WORK_CLASSIFICATION = "current_work_classification"

POINTER_KINDS: frozenset[str] = frozenset({
    POINTER_KIND_CURRENT_DRAFT_SPEC,
    POINTER_KIND_ACTIVE_FROZEN_SPEC,
    POINTER_KIND_ACTIVE_TASK,
    POINTER_KIND_CURRENT_COMPLETION,
    POINTER_KIND_CURRENT_HANDOFF,
    POINTER_KIND_CURRENT_DECISION,
    POINTER_KIND_CURRENT_COMPLETED_TASK,
    POINTER_KIND_CURRENT_WORK_CLASSIFICATION,
})

_POINTER_FILENAME: dict[str, str] = {
    POINTER_KIND_CURRENT_DRAFT_SPEC: "current-draft-spec.json",
    POINTER_KIND_ACTIVE_FROZEN_SPEC: "active-frozen-spec.json",
    POINTER_KIND_ACTIVE_TASK: "active-task.json",
    POINTER_KIND_CURRENT_COMPLETION: "current-completion.json",
    POINTER_KIND_CURRENT_HANDOFF: "current-handoff.json",
    POINTER_KIND_CURRENT_DECISION: "current-decision.json",
    POINTER_KIND_CURRENT_COMPLETED_TASK: "current-completed-task.json",
    POINTER_KIND_CURRENT_WORK_CLASSIFICATION: "current-work-classification.json",
}

_POINTER_STATES: frozenset[str] = frozenset({
    "active",        # the subject is the current session's active subject
    "terminal",      # active-task subject reached a terminal outcome
    "consumed",      # superseded by a later pointer in the lifecycle
    "closed",        # terminal closure acknowledged
})

# Trusted principals that may own a session pointer.  Workers never may.
TRUSTED_PRINCIPALS: frozenset[str] = frozenset({"task-main", "coordinator"})

# Canonical bounded sentinel for P0 standalone projects.
STANDALONE_PROJECT_SENTINEL = "standalone"

# Shared common fields present on every pointer (only applicable fields filled).
COMMON_POINTER_FIELDS: tuple[str, ...] = (
    "schema_version",
    "pointer_kind",
    "state",
    "workspace_id",
    "project_id",          # or STANDALONE_PROJECT_SENTINEL
    "session_id",          # trusted session identity (verification only)
    "principal",
    "profile",
    "subject_kind",
    "task_id",             # if applicable
    "spec_id",             # if applicable
    "start_id",            # if applicable
    "handoff_id",          # if applicable
    "decision_id",         # if applicable
    "revision",            # spec revision, if applicable
    "spec_hash",           # canonical hash, if applicable
    "spec_sha256",         # raw file SHA-256, if applicable
    "authority_source",    # the writer module that produced the pointer
    "created_at",
    "updated_at",
    "consumed_at",
    "superseded_by",       # pointer_kind:path of the superseding pointer
    "artifact_digest",     # binding digest of the durable subject artifact
)

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

# Path-component safe id.  Allows alnum, _, ., :, -, with a bounded length.
# Used for workspace_id, project_id, and the session digest path component.
_PATH_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
# Trusted session ids may carry more shapes (uuid, conversation keys, etc.).
# They are never used as a filesystem path component directly; only their
# bounded digest is.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,256}$")
_PRINCIPAL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_HEX_RE = re.compile(r"^[a-f0-9]{64}$")


class SessionStateError(ValueError):
    """Deterministic, bounded session-state authority failure."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code if not detail else f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _validate_path_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise SessionStateError("session_state_path_invalid", f"{field}_missing")
    if not _PATH_ID_RE.fullmatch(value) or ".." in value:
        raise SessionStateError("session_state_path_invalid", f"{field}_invalid")
    return value


def _validate_session_id(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise SessionStateError("session_state_binding_invalid", "session_id_missing")
    if not _SESSION_ID_RE.fullmatch(value):
        raise SessionStateError("session_state_binding_invalid", "session_id_invalid")
    return value


def _validate_principal(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise SessionStateError("session_state_binding_invalid", "principal_missing")
    if value not in TRUSTED_PRINCIPALS:
        raise SessionStateError("session_state_binding_invalid", "principal_untrusted")
    return value


def _validate_pointer_kind(value: object) -> str:
    if not isinstance(value, str) or value not in POINTER_KINDS:
        raise SessionStateError("session_state_pointer_invalid", "pointer_kind_unknown")
    return value


def _validate_state(value: object) -> str:
    if not isinstance(value, str) or value not in _POINTER_STATES:
        raise SessionStateError("session_state_pointer_invalid", "state_invalid")
    return value


def session_digest(session_id: str) -> str:
    """Return a bounded, path-safe digest of a trusted session id.

    The full session id is retained inside the pointer body for verification
    but is not used as a filesystem component.  This keeps long or structured
    session ids out of the filesystem without unnecessarily exposing them to
    the model: the digest is control-plane only.
    """
    if not isinstance(session_id, str) or not session_id:
        raise SessionStateError("session_state_binding_invalid", "session_id_missing")
    return "sd_" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]


def project_partition(project_id: object) -> str:
    """Return the canonical, path-safe project partition component.

    A P0 standalone project uses the bounded ``standalone`` sentinel so the
    layout is well-defined even when no administrative Plan/project exists.
    """
    if isinstance(project_id, str) and project_id.startswith("standalone:"):
        return STANDALONE_PROJECT_SENTINEL
    return _validate_path_id(project_id, "project_id")


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _session_dir(
    workspace_id: str,
    project_id: object,
    sid: str,
    *,
    root: Path | None = None,
) -> Path:
    """Return the canonical session partition directory.

    Layout: ``<root>/<workspace_key>/<project_key>/<session_digest>/``
    """
    base = root if root is not None else SESSION_STATE_ROOT
    if base.is_symlink():
        raise SessionStateError("session_state_path_invalid", "root_is_symlink")
    ws = _validate_path_id(workspace_id, "workspace_id")
    proj = project_partition(project_id)
    sd = session_digest(sid)
    path = base / ws / proj / sd
    # Reject any symlink escape on the partition components.
    for component in (base / ws, base / ws / proj, path):
        if component.exists() and component.is_symlink():
            raise SessionStateError("session_state_path_invalid", "partition_is_symlink")
    return path


def _pointer_path(
    pointer_kind: str,
    workspace_id: str,
    project_id: object,
    sid: str,
    *,
    root: Path | None = None,
) -> Path:
    kind = _validate_pointer_kind(pointer_kind)
    directory = _session_dir(workspace_id, project_id, sid, root=root)
    return directory / _POINTER_FILENAME[kind]


def _lock_path(pointer_path: Path) -> Path:
    return pointer_path.with_name(f".{pointer_path.name}.lock")


# ---------------------------------------------------------------------------
# Pointer schema validation
# ---------------------------------------------------------------------------


def validate_pointer(pointer: Mapping[str, Any], *, kind: str | None = None) -> dict[str, Any]:
    """Validate one pointer against the schema; fail closed on unknown shapes."""
    if not isinstance(pointer, Mapping):
        raise SessionStateError("session_state_pointer_invalid", "not_object")
    schema_version = pointer.get("schema_version")
    if schema_version != POINTER_SCHEMA_VERSION:
        raise SessionStateError("session_state_pointer_invalid", "schema_version_unknown")
    pk = _validate_pointer_kind(pointer.get("pointer_kind"))
    if kind is not None and pk != kind:
        raise SessionStateError("session_state_pointer_invalid", "pointer_kind_mismatch")
    _validate_state(pointer.get("state"))
    _validate_path_id(pointer.get("workspace_id"), "workspace_id")
    # project_id may be the standalone sentinel or a real id.
    project_partition(pointer.get("project_id"))
    _validate_session_id(pointer.get("session_id"))
    _validate_principal(pointer.get("principal"))
    principal = pointer.get("principal")
    if pointer.get("session_id") and principal not in TRUSTED_PRINCIPALS:
        raise SessionStateError("session_state_binding_invalid", "principal_untrusted")
    if not isinstance(pointer.get("authority_source"), str) or not pointer["authority_source"]:
        raise SessionStateError("session_state_pointer_invalid", "authority_source_missing")
    # Reject unknown top-level fields beyond the canonical set + a small
    # bounded extension set used by lifecycle writers.
    allowed_extra = {
        "completion_transport", "completion_delivery_expected", "deadline",
        "terminal_status", "terminal_outcome", "completed_at", "acknowledged",
        "outcome", "receipt_ref", "card_ref", "full_report_ref", "profile",
        "resolved_profile", "spec_kind", "work_item_id",
        "workspace_registry_digest", "project_manifest_digest",
        "project_registry_revision", "binding_source", "frozen_at",
        "approval_status", "origin_session_id", "subject_task_id",
        "binding_digest", "classification", "authority", "execution_depth",
        "classifier_result_digest", "source_operation", "consumed_by",
        "consumed_spec_id",
    }
    allowed = set(COMMON_POINTER_FIELDS) | allowed_extra
    unknown = set(pointer) - allowed
    if unknown:
        raise SessionStateError("session_state_pointer_invalid", f"unknown_fields:{sorted(unknown)}")
    if pk == POINTER_KIND_CURRENT_WORK_CLASSIFICATION:
        if pointer.get("subject_kind") != "work_classification":
            raise SessionStateError("session_state_pointer_invalid", "classification_subject_kind_invalid")
        if pointer.get("classification") not in {"P0", "P1", "P2"}:
            raise SessionStateError("session_state_pointer_invalid", "classification_invalid")
        if pointer.get("authority") not in {"A0", "A1", "A2"}:
            raise SessionStateError("session_state_pointer_invalid", "authority_invalid")
        if pointer.get("execution_depth") not in {"fast", "standard", "deep"}:
            raise SessionStateError("session_state_pointer_invalid", "execution_depth_invalid")
        digest = pointer.get("classifier_result_digest")
        if not isinstance(digest, str) or not _HEX_RE.fullmatch(digest):
            raise SessionStateError("session_state_pointer_invalid", "classifier_result_digest_invalid")
        if pointer.get("source_operation") != "aota_work_classify":
            raise SessionStateError("session_state_pointer_invalid", "source_operation_invalid")
        if pointer.get("state") == "consumed" and not pointer.get("consumed_by"):
            raise SessionStateError("session_state_pointer_invalid", "consumed_by_missing")
    return dict(pointer)


# ---------------------------------------------------------------------------
# Atomic pointer operations
# ---------------------------------------------------------------------------


def _atomic_replace_json(
    target: Path,
    payload: dict[str, Any],
) -> None:
    """Write *payload* to *target* atomically with fsync + parent fsync.

    Rejects symlink targets and symlink parent partitions.
    """
    if target.is_symlink():
        raise SessionStateError("session_state_path_invalid", "pointer_target_is_symlink")
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink():
        raise SessionStateError("session_state_path_invalid", "pointer_parent_is_symlink")
    lock_path = _lock_path(target)
    if lock_path.exists() and lock_path.is_symlink():
        raise SessionStateError("session_state_path_invalid", "pointer_lock_is_symlink")
    import fcntl
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        content = (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        temp_fd, temp_name = tempfile.mkstemp(
            dir=str(parent), prefix=f".{target.name}.tmp_"
        )
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, str(target))
            # Parent directory fsync for crash-safety per existing project
            # convention.
            try:
                dir_fd = os.open(str(parent), os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _read_pointer_file(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise SessionStateError("session_state_pointer_invalid", "pointer_not_regular")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SessionStateError("session_state_pointer_invalid", "pointer_unreadable") from exc
    if not isinstance(value, dict):
        raise SessionStateError("session_state_pointer_invalid", "pointer_not_object")
    return value


def write_pointer(pointer: Mapping[str, Any], *, root: Path | None = None) -> Path:
    """Atomically write one pointer for the current trusted session.

    The pointer is validated against the schema and the session identity in
    the body must match the session used for path partitioning.  Idempotent
    writes with the same payload are safe.
    """
    payload = validate_pointer(pointer)
    kind = payload["pointer_kind"]
    sid = payload["session_id"]
    path = _pointer_path(
        kind, payload["workspace_id"], payload["project_id"], sid, root=root
    )
    if path.parent.parent.parent.is_symlink() or path.parent.parent.is_symlink() or path.parent.is_symlink():
        raise SessionStateError("session_state_path_invalid", "partition_is_symlink")
    _atomic_replace_json(path, payload)
    return path


def read_pointer(
    pointer_kind: str,
    workspace_id: str,
    project_id: object,
    session_id: str,
    *,
    root: Path | None = None,
) -> dict[str, Any] | None:
    """Read one exact session pointer; missing is distinct from malformed."""
    path = _pointer_path(pointer_kind, workspace_id, project_id, session_id, root=root)
    if not path.exists():
        return None
    pointer = _read_pointer_file(path)
    payload = validate_pointer(pointer, kind=pointer_kind)
    # Verify the session_id in the body matches the partition session.
    if payload.get("session_id") != session_id:
        raise SessionStateError("session_state_binding_mismatch", "session_id_mismatch")
    return payload


def read_pointer_for_context(
    pointer_kind: str,
    context: Any,
    *,
    project_id: object = None,
    root: Path | None = None,
) -> dict[str, Any] | None:
    """Read the pointer for one trusted session without touching history.

    When the host has not projected a project id yet, only the bounded
    session-state project partitions are inspected.  This is an authority
    lookup, not a durable-artifact or workspace-wide uniqueness fallback.
    """
    partition = resolve_session_partition(context, project_id=project_id) if project_id is not None else resolve_session_partition(context)
    if not partition.available:
        raise SessionStateError("trusted_session_context_missing")
    if project_id is not None:
        return read_pointer(pointer_kind, partition.workspace_id, partition.project_id, partition.session_id, root=root)
    base = root if root is not None else SESSION_STATE_ROOT
    workspace_dir = base / partition.workspace_id
    if workspace_dir.is_symlink():
        raise SessionStateError("session_state_path_invalid", "workspace_partition_is_symlink")
    if not workspace_dir.is_dir():
        return None
    session_dir_name = session_digest(partition.session_id)
    matches: list[dict[str, Any]] = []
    for project_dir in sorted(workspace_dir.iterdir(), key=lambda item: item.name):
        if project_dir.is_symlink() or not project_dir.is_dir():
            continue
        candidate = project_dir / session_dir_name / _POINTER_FILENAME[_validate_pointer_kind(pointer_kind)]
        if candidate.exists():
            value = read_pointer(pointer_kind, partition.workspace_id, project_dir.name, partition.session_id, root=root)
            if value is not None:
                matches.append(value)
    if len(matches) > 1:
        raise SessionStateError("session_state_pointer_ambiguous", "multiple_project_partitions")
    return matches[0] if matches else None


def consume_pointer(
    pointer_kind: str,
    workspace_id: str,
    project_id: object,
    session_id: str,
    *,
    consumed_at: str,
    superseded_by: str = "",
    updates: Mapping[str, Any] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Mark one pointer consumed (superseded by a later lifecycle pointer)."""
    path = _pointer_path(pointer_kind, workspace_id, project_id, session_id, root=root)
    if not path.exists():
        raise SessionStateError("session_state_pointer_missing", "consume_target_missing")
    pointer = _read_pointer_file(path)
    payload = validate_pointer(pointer, kind=pointer_kind)
    if payload.get("session_id") != session_id:
        raise SessionStateError("session_state_binding_mismatch", "session_id_mismatch")
    if payload.get("state") == "consumed":
        return payload  # idempotent
    if payload.get("state") not in {"active", "terminal"}:
        raise SessionStateError("session_state_pointer_invalid", "consume_state_not_active")
    payload["state"] = "consumed"
    payload["consumed_at"] = consumed_at
    payload["updated_at"] = consumed_at
    if superseded_by:
        payload["superseded_by"] = superseded_by
    if updates:
        payload.update({key: value for key, value in updates.items() if value is not None})
    validate_pointer(payload, kind=pointer_kind)
    _atomic_replace_json(path, payload)
    return payload


def supersede_pointer(
    pointer_kind: str,
    workspace_id: str,
    project_id: object,
    session_id: str,
    *,
    new_pointer: Mapping[str, Any],
    consumed_at: str,
    root: Path | None = None,
) -> tuple[dict[str, Any], Path]:
    """Consume the old pointer and write the new one atomically per-pointer.

    Both operations use the same per-pointer lock for the *old* pointer; the
    new pointer has its own per-pointer lock.  This is not a cross-pointer
    transaction but it preserves the single-pointer invariants required by the
    contract: the old pointer is only consumed by the same session, the new
    pointer is only written by the same session, and neither can be silently
    overwritten by a different subject.
    """
    old = consume_pointer(
        pointer_kind,
        workspace_id,
        project_id,
        session_id,
        consumed_at=consumed_at,
        superseded_by=f"{new_pointer.get('pointer_kind', '')}",
        updates=(
            {"consumed_by": "session_state_supersede", "consumed_spec_id": ""}
            if pointer_kind == POINTER_KIND_CURRENT_WORK_CLASSIFICATION else None
        ),
        root=root,
    )
    new_path = write_pointer(new_pointer, root=root)
    return old, new_path


def clear_pointer_if_exact(
    pointer_kind: str,
    workspace_id: str,
    project_id: object,
    session_id: str,
    *,
    expected_digest: str,
    root: Path | None = None,
) -> bool:
    """Remove a pointer only if its binding digest matches exactly.

    Used for terminal closure.  Mismatched digest fail-closed (returns False
    and raises no error so the caller can decide to stop).
    """
    path = _pointer_path(pointer_kind, workspace_id, project_id, session_id, root=root)
    if not path.exists():
        return False
    pointer = _read_pointer_file(path)
    payload = validate_pointer(pointer, kind=pointer_kind)
    if payload.get("session_id") != session_id:
        raise SessionStateError("session_state_binding_mismatch", "session_id_mismatch")
    actual = payload.get("artifact_digest") or _binding_digest(payload)
    if actual != expected_digest:
        return False
    # Atomic unlink via temp rename to avoid partial state.
    lock_path = _lock_path(path)
    import fcntl
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            os.replace(str(path), str(path.with_suffix(".json.closed")))
        except OSError as exc:
            raise SessionStateError("session_state_pointer_invalid", "clear_failed") from exc
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
    return True


def _binding_digest(pointer: Mapping[str, Any]) -> str:
    """Deterministic binding digest of a pointer's subject fields."""
    subject_keys = (
        "workspace_id", "project_id", "task_id", "spec_id", "start_id",
        "handoff_id", "decision_id", "revision", "spec_hash", "spec_sha256",
    )
    parts = [f"{k}={pointer.get(k, '')}" for k in subject_keys]
    if pointer.get("pointer_kind") == POINTER_KIND_CURRENT_WORK_CLASSIFICATION:
        parts.extend(
            f"{k}={pointer.get(k, '')}"
            for k in (
                "pointer_kind", "classification", "authority", "execution_depth",
                "classifier_result_digest",
            )
        )
    return "bd_" + hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Trusted-context projection for session-state
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionPartition:
    """Resolved, validated session partition for pointer operations."""

    workspace_id: str
    project_id: str
    session_id: str
    principal: str
    profile: str
    origin_session_id: str
    available: bool

    @property
    def is_standalone(self) -> bool:
        return self.project_id == STANDALONE_PROJECT_SENTINEL


def resolve_session_partition(
    context: Any,
    *,
    project_id: object = None,
) -> SessionPartition:
    """Derive a canonical session partition from a trusted runtime context.

    The trusted context must expose ``session_id``, ``principal``/
    ``profile``, ``workspace_id``.  ``project_id`` defaults to the standalone
    sentinel when the trusted context does not carry a non-standalone project
    binding; callers that already know the canonical project id may pass it.
    """
    sid = getattr(context, "session_id", "") or ""
    principal = getattr(context, "principal", "") or getattr(context, "profile", "") or ""
    workspace_id = getattr(context, "workspace_id", "") or ""
    if not sid or principal not in TRUSTED_PRINCIPALS or not workspace_id:
        return SessionPartition(
            workspace_id="",
            project_id="",
            session_id="",
            principal="",
            profile="",
            origin_session_id="",
            available=False,
        )
    # Worker contexts may never own session-state pointers.
    if getattr(context, "worker_context", False):
        return SessionPartition(
            workspace_id="",
            project_id="",
            session_id="",
            principal="",
            profile="",
            origin_session_id="",
            available=False,
        )
    if project_id is None:
        project_id = STANDALONE_PROJECT_SENTINEL
    proj = project_partition(project_id)
    profile = getattr(context, "profile", "") or principal
    origin = getattr(context, "origin_session_id", "") or sid
    return SessionPartition(
        workspace_id=workspace_id,
        project_id=proj,
        session_id=sid,
        principal=principal,
        profile=profile,
        origin_session_id=origin,
        available=True,
    )


def missing_context_result(*, operation: str) -> dict[str, Any]:
    """Stable result for an operation lacking a trusted session context."""
    return {
        "status": "rejected",
        "operation_result": operation,
        "error": "trusted_session_context_missing",
        "retryable": False,
        "human_action_required": False,
        "next_action": "stop_and_report_runtime_context_missing",
    }


# ---------------------------------------------------------------------------
# Failure-contract helpers (Scope K)
# ---------------------------------------------------------------------------


def missing_result(pointer_kind: str, *, next_action: str) -> dict[str, Any]:
    return {
        "status": "rejected",
        "error": f"{pointer_kind}_missing",
        "retryable": False,
        "human_action_required": False,
        "next_action": next_action,
    }


def stale_result(pointer_kind: str, *, next_action: str, detail: str = "") -> dict[str, Any]:
    return {
        "status": "rejected",
        "error": f"{pointer_kind}_binding_stale",
        "detail": detail,
        "retryable": False,
        "human_action_required": False,
        "next_action": next_action,
    }


def mismatch_result(pointer_kind: str, *, next_action: str, detail: str = "") -> dict[str, Any]:
    return {
        "status": "rejected",
        "error": f"{pointer_kind}_binding_mismatch",
        "detail": detail,
        "retryable": False,
        "human_action_required": False,
        "next_action": next_action,
    }


def consumed_result(pointer_kind: str, *, next_action: str) -> dict[str, Any]:
    return {
        "status": "rejected",
        "error": f"{pointer_kind}_consumed",
        "retryable": False,
        "human_action_required": False,
        "next_action": next_action,
    }


# ---------------------------------------------------------------------------
# Subject pointer builders
# ---------------------------------------------------------------------------


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _value(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _canonical_project(value: object) -> str:
    if isinstance(value, str) and value.startswith("standalone:"):
        return STANDALONE_PROJECT_SENTINEL
    return project_partition(value)


def build_pointer(
    pointer_kind: str,
    context: Any,
    *,
    subject_kind: str,
    task_id: object = None,
    spec_id: object = None,
    start_id: object = None,
    handoff_id: object = None,
    decision_id: object = None,
    project_id: object = None,
    profile: object = None,
    revision: object = None,
    spec_hash: object = None,
    spec_sha256: object = None,
    artifact_digest: object = None,
    authority_source: str,
    state: str = "active",
    now: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a validated pointer from trusted context and durable bindings.

    This helper intentionally accepts durable artifact metadata, never model
    selectors.  Missing trusted identity is rejected before a path can be
    derived.
    """
    partition = resolve_session_partition(context, project_id=project_id)
    if not partition.available:
        raise SessionStateError("trusted_session_context_missing")
    timestamp = now or _now()
    payload: dict[str, Any] = {
        "schema_version": POINTER_SCHEMA_VERSION,
        "pointer_kind": pointer_kind,
        "state": state,
        "workspace_id": partition.workspace_id,
        "project_id": partition.project_id,
        "session_id": partition.session_id,
        "principal": partition.principal,
        "profile": profile or partition.profile,
        "subject_kind": subject_kind,
        "task_id": task_id,
        "spec_id": spec_id,
        "start_id": start_id,
        "handoff_id": handoff_id,
        "decision_id": decision_id,
        "revision": revision,
        "spec_hash": spec_hash,
        "spec_sha256": spec_sha256,
        "authority_source": authority_source,
        "created_at": timestamp,
        "updated_at": timestamp,
        "consumed_at": None,
        "superseded_by": None,
        "artifact_digest": artifact_digest,
    }
    payload.update({key: value for key, value in extra.items() if value is not None})
    payload["artifact_digest"] = payload.get("artifact_digest") or _binding_digest(payload)
    return validate_pointer(payload, kind=pointer_kind)


def write_subject_pointer(
    pointer_kind: str,
    context: Any,
    *,
    subject_kind: str,
    root: Path | None = None,
    **bindings: Any,
) -> tuple[dict[str, Any], Path]:
    """Build and atomically publish one lifecycle pointer."""
    pointer = build_pointer(
        pointer_kind,
        context,
        subject_kind=subject_kind,
        **bindings,
    )
    return pointer, write_pointer(pointer, root=root)


def write_current_work_classification_pointer(
    context: Any,
    *,
    classification: str,
    authority: str,
    execution_depth: str,
    classifier_result_digest: str,
    project_id: object = None,
    source_operation: str = "aota_work_classify",
    root: Path | None = None,
) -> tuple[dict[str, Any], Path, bool]:
    """Publish the one current classifier result for a trusted session.

    The existing pointer writer supplies locking, temp-file fsync, atomic
    replace, and path/symlink validation.  ``True`` in the return tuple means
    the active binding was already identical and was reused idempotently.
    """
    pointer = build_pointer(
        POINTER_KIND_CURRENT_WORK_CLASSIFICATION,
        context,
        subject_kind="work_classification",
        project_id=project_id,
        authority_source="work_classifier",
        classification=classification,
        authority=authority,
        execution_depth=execution_depth,
        classifier_result_digest=classifier_result_digest,
        source_operation=source_operation,
    )
    # Resolve the current authority across only this trusted session's
    # existing project partitions.  This permits a safe P0 standalone → P1
    # project-bound replacement without introducing a durable history scan.
    existing = read_current_work_classification_pointer(context, root=root)
    if existing is not None:
        same = (
            existing.get("state") == "active"
            and existing.get("project_id") == pointer.get("project_id")
            and existing.get("classification") == pointer["classification"]
            and existing.get("authority") == pointer["authority"]
            and existing.get("execution_depth") == pointer["execution_depth"]
            and existing.get("classifier_result_digest") == pointer["classifier_result_digest"]
        )
        if same:
            path = _pointer_path(
                POINTER_KIND_CURRENT_WORK_CLASSIFICATION,
                pointer["workspace_id"], pointer["project_id"], pointer["session_id"], root=root,
            )
            return existing, path, True
        supersede_pointer(
            POINTER_KIND_CURRENT_WORK_CLASSIFICATION,
            existing["workspace_id"],
            existing["project_id"],
            existing["session_id"],
            new_pointer=pointer,
            consumed_at=pointer["created_at"],
            root=root,
        )
        return pointer, _pointer_path(
            POINTER_KIND_CURRENT_WORK_CLASSIFICATION,
            pointer["workspace_id"], pointer["project_id"], pointer["session_id"], root=root,
        ), False
    return pointer, write_pointer(pointer, root=root), False


def read_current_work_classification_pointer(
    context: Any,
    *,
    project_id: object = None,
    root: Path | None = None,
) -> dict[str, Any] | None:
    """Resolve the current classification through session-state only."""
    if project_id is not None:
        return read_pointer_for_context(
            POINTER_KIND_CURRENT_WORK_CLASSIFICATION,
            context,
            project_id=project_id,
            root=root,
        )
    partition = resolve_session_partition(context)
    if not partition.available:
        raise SessionStateError("trusted_session_context_missing")
    base = root if root is not None else SESSION_STATE_ROOT
    workspace_dir = base / partition.workspace_id
    if workspace_dir.is_symlink():
        raise SessionStateError("session_state_path_invalid", "workspace_partition_is_symlink")
    if not workspace_dir.is_dir():
        return None
    session_dir_name = session_digest(partition.session_id)
    matches: list[dict[str, Any]] = []
    for project_dir in sorted(workspace_dir.iterdir(), key=lambda item: item.name):
        if project_dir.is_symlink() or not project_dir.is_dir():
            continue
        candidate = project_dir / session_dir_name / _POINTER_FILENAME[POINTER_KIND_CURRENT_WORK_CLASSIFICATION]
        if candidate.exists():
            value = read_pointer(
                POINTER_KIND_CURRENT_WORK_CLASSIFICATION,
                partition.workspace_id,
                project_dir.name,
                partition.session_id,
                root=root,
            )
            if value is not None:
                matches.append(value)
    active = [value for value in matches if value.get("state") == "active"]
    if len(active) > 1:
        raise SessionStateError("session_state_pointer_ambiguous", "multiple_active_project_partitions")
    if active:
        return active[0]
    if len(matches) > 1:
        # No active binding remains.  A consumed result is still useful for
        # the deterministic "must re-run classify" failure contract; choose
        # the latest bounded lifecycle timestamp rather than treating history
        # as a competing current authority.
        return max(
            matches,
            key=lambda value: (
                str(value.get("consumed_at") or ""),
                str(value.get("updated_at") or ""),
                str(value.get("project_id") or ""),
            ),
        )
    return matches[0] if matches else None


def consume_current_work_classification_pointer(
    context: Any,
    binding: Mapping[str, Any],
    *,
    consumed_by: str,
    consumed_spec_id: str,
    consumed_at: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Consume an exact active classification after SPEC draft publication."""
    if not isinstance(binding, Mapping) or binding.get("pointer_kind") != POINTER_KIND_CURRENT_WORK_CLASSIFICATION:
        raise SessionStateError("classification_context_ambiguous", "binding_kind_invalid")
    if binding.get("state") != "active":
        raise SessionStateError("classification_context_consumed", "binding_not_active")
    partition = resolve_session_partition(context, project_id=binding.get("project_id"))
    if not partition.available:
        raise SessionStateError("classification_context_session_mismatch")
    current = read_pointer(
        POINTER_KIND_CURRENT_WORK_CLASSIFICATION,
        partition.workspace_id,
        partition.project_id,
        partition.session_id,
        root=root,
    )
    if current is None:
        raise SessionStateError("classification_context_missing", "consume_target_missing")
    if current.get("artifact_digest") != binding.get("artifact_digest"):
        raise SessionStateError("classification_context_ambiguous", "binding_changed_before_consume")
    if current.get("session_id") != partition.session_id:
        raise SessionStateError("classification_context_session_mismatch")
    if current.get("state") != "active":
        raise SessionStateError("classification_context_consumed", "binding_not_active")
    timestamp = consumed_at or _now()
    return consume_pointer(
        POINTER_KIND_CURRENT_WORK_CLASSIFICATION,
        partition.workspace_id,
        partition.project_id,
        partition.session_id,
        consumed_at=timestamp,
        updates={
            "consumed_by": consumed_by,
            "consumed_spec_id": consumed_spec_id,
        },
        root=root,
    )


def write_current_draft_spec_pointer(context: Any, meta: Mapping[str, Any], *, root: Path | None = None) -> tuple[dict[str, Any], Path]:
    return write_subject_pointer(
        POINTER_KIND_CURRENT_DRAFT_SPEC, context, subject_kind="spec", root=root,
        project_id=_value(meta, "project_id") or _value(meta.get("spec", {}) if isinstance(meta.get("spec"), Mapping) else {}, "project_id"),
        task_id=_value(meta, "task_id", "spec_id"), spec_id=_value(meta, "spec_id", "task_id"),
        revision=_value(meta, "revision"), spec_hash=_value(meta, "spec_hash"),
        spec_sha256=_value(meta, "spec_sha256"), profile=_value(meta, "resolved_profile", "profile_hint"),
        authority_source="task_spec_create",
    )


def write_active_frozen_spec_pointer(context: Any, meta: Mapping[str, Any], *, root: Path | None = None) -> tuple[dict[str, Any], Path]:
    return write_subject_pointer(
        POINTER_KIND_ACTIVE_FROZEN_SPEC, context, subject_kind="spec", root=root,
        project_id=_value(meta, "project_id") or _value(meta.get("spec", {}) if isinstance(meta.get("spec"), Mapping) else {}, "project_id"),
        task_id=_value(meta, "task_id", "spec_id"), spec_id=_value(meta, "spec_id", "task_id"),
        revision=_value(meta, "revision", "frozen_revision"), spec_hash=_value(meta, "spec_hash"),
        spec_sha256=_value(meta, "spec_sha256"), profile=_value(meta, "resolved_profile", "profile_hint"),
        authority_source="task_spec_freeze", frozen_at=_value(meta, "frozen_at"),
        approval_status="required" if _value(meta, "spec_kind", "task_kind") == "implementation" else "not_required",
    )


def write_active_task_pointer(context: Any, meta: Mapping[str, Any], *, root: Path | None = None, start_id: object = None, state: str = "active") -> tuple[dict[str, Any], Path]:
    execution = meta.get("execution") if isinstance(meta.get("execution"), Mapping) else {}
    return write_subject_pointer(
        POINTER_KIND_ACTIVE_TASK, context, subject_kind="profile_task", root=root,
        project_id=_value(meta, "project_id"), task_id=_value(meta, "task_id"),
        start_id=start_id or _value(execution, "start_id"), revision=_value(meta, "revision", "spec_revision"),
        spec_hash=_value(meta, "spec_hash"), spec_sha256=_value(meta, "spec_sha256"),
        profile=_value(meta, "resolved_profile", "profile") or _value(execution, "profile"),
        authority_source="profile_task_start", state=state,
        completion_transport=_value(execution, "completion_transport"),
        completion_delivery_expected=_value(execution, "completion_delivery_expected"),
        deadline=_value(execution, "deadline"),
    )


def write_current_completion_pointer(context: Any, subject: Mapping[str, Any], *, root: Path | None = None) -> tuple[dict[str, Any], Path]:
    return write_subject_pointer(
        POINTER_KIND_CURRENT_COMPLETION, context, subject_kind="completion", root=root,
        project_id=_value(subject, "project_id"), task_id=_value(subject, "task_id"),
        start_id=_value(subject, "start_id"), handoff_id=_value(subject, "handoff_id"),
        decision_id=_value(subject, "decision_id"), revision=_value(subject, "spec_revision", "revision"),
        spec_hash=_value(subject, "spec_hash"), spec_sha256=_value(subject, "spec_sha256"),
        profile=_value(subject, "profile", "resolved_profile"), authority_source="completion_finalizer",
        outcome=_value(subject, "outcome", "terminal_outcome"), receipt_ref=_value(subject, "receipt_ref"),
        card_ref=_value(subject, "card_ref"), full_report_ref=_value(subject, "full_report_ref"),
        origin_session_id=_value(subject, "origin_session_id"), terminal_status=_value(subject, "terminal_status", "status"),
        completed_at=_value(subject, "completed_at"),
    )


def write_current_handoff_pointer(context: Any, subject: Mapping[str, Any], *, root: Path | None = None) -> tuple[dict[str, Any], Path]:
    return write_subject_pointer(
        POINTER_KIND_CURRENT_HANDOFF, context, subject_kind="handoff", root=root,
        project_id=_value(subject, "project_id"), task_id=_value(subject, "task_id"),
        start_id=_value(subject, "start_id"), handoff_id=_value(subject, "handoff_id"),
        revision=_value(subject, "spec_revision", "revision"), spec_hash=_value(subject, "spec_hash"),
        spec_sha256=_value(subject, "spec_sha256"), profile=_value(subject, "profile", "resolved_profile"),
        authority_source="handoff_create", completion_transport=_value(subject, "completion_transport"),
        origin_session_id=_value(subject, "origin_session_id"), acknowledged=_value(subject, "acknowledged"),
    )


def write_current_decision_pointer(context: Any, subject: Mapping[str, Any], *, root: Path | None = None) -> tuple[dict[str, Any], Path]:
    return write_subject_pointer(
        POINTER_KIND_CURRENT_DECISION, context, subject_kind="decision", root=root,
        project_id=_value(subject, "project_id"), task_id=_value(subject, "task_id"),
        start_id=_value(subject, "start_id"), handoff_id=_value(subject, "handoff_id"),
        decision_id=_value(subject, "decision_id"), revision=_value(subject, "spec_revision", "revision"),
        spec_hash=_value(subject, "spec_hash"), spec_sha256=_value(subject, "spec_sha256"),
        profile=_value(subject, "profile", "resolved_profile"), authority_source="decision_record",
        outcome=_value(subject, "outcome"), origin_session_id=_value(subject, "origin_session_id"),
    )


def write_current_completed_task_pointer(context: Any, subject: Mapping[str, Any], *, root: Path | None = None) -> tuple[dict[str, Any], Path]:
    return write_subject_pointer(
        POINTER_KIND_CURRENT_COMPLETED_TASK, context, subject_kind="profile_task", root=root,
        project_id=_value(subject, "project_id"), task_id=_value(subject, "task_id"),
        start_id=_value(subject, "start_id"), handoff_id=_value(subject, "handoff_id"),
        revision=_value(subject, "spec_revision", "revision"),
        spec_hash=_value(subject, "spec_hash"), spec_sha256=_value(subject, "spec_sha256"),
        profile=_value(subject, "profile", "resolved_profile"), authority_source="completion_finalizer",
        state="active", terminal_status=_value(subject, "terminal_status", "status"),
        terminal_outcome=_value(subject, "outcome", "terminal_outcome"), completed_at=_value(subject, "completed_at"),
        origin_session_id=_value(subject, "origin_session_id"),
    )


# ---------------------------------------------------------------------------
# Smoke
# ---------------------------------------------------------------------------


def run_isolated_smoke() -> dict[str, str]:
    """Bounded stdlib-only smoke for the session-state authority contract."""
    # Path-component safety rejects traversal.
    try:
        _validate_path_id("..", "workspace_id")
        raise AssertionError("traversal accepted")
    except SessionStateError as exc:
        assert exc.code == "session_state_path_invalid"
    # Unknown pointer kind rejected.
    try:
        _validate_pointer_kind("latest")
        raise AssertionError("unknown pointer kind accepted")
    except SessionStateError as exc:
        assert exc.code == "session_state_pointer_invalid"
    # Untrusted principal rejected.
    try:
        _validate_principal("coder")
        raise AssertionError("untrusted principal accepted")
    except SessionStateError as exc:
        assert exc.code == "session_state_binding_invalid"
    # Session digest is path-safe and stable.
    sd = session_digest("sess-123")
    assert sd.startswith("sd_") and len(sd) == 3 + 32
    # Standalone project partition is the sentinel.
    assert project_partition("standalone:ws") == STANDALONE_PROJECT_SENTINEL
    # Missing trusted context returns a deterministic stop.
    from types import SimpleNamespace
    part = resolve_session_partition(SimpleNamespace())
    assert not part.available
    res = missing_context_result(operation="smoke")
    assert res["error"] == "trusted_session_context_missing"
    assert res["retryable"] is False
    # Round-trip a pointer through a temporary root.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pointer = {
            "schema_version": POINTER_SCHEMA_VERSION,
            "pointer_kind": POINTER_KIND_CURRENT_DRAFT_SPEC,
            "state": "active",
            "workspace_id": "ws-fixture",
            "project_id": STANDALONE_PROJECT_SENTINEL,
            "session_id": "sess-fixture-1",
            "principal": "task-main",
            "profile": "task-main",
            "subject_kind": "spec",
            "task_id": "pt_20260728T000000_00000000",
            "spec_id": "pt_20260728T000000_00000000",
            "revision": 1,
            "spec_sha256": "a" * 64,
            "authority_source": "smoke",
            "created_at": "2026-07-28T00:00:00Z",
            "updated_at": "2026-07-28T00:00:00Z",
            "consumed_at": None,
            "superseded_by": None,
            "artifact_digest": None,
        }
        path = write_pointer(pointer, root=root)
        assert path.is_file() and not path.is_symlink()
        read_back = read_pointer(
            POINTER_KIND_CURRENT_DRAFT_SPEC,
            "ws-fixture",
            STANDALONE_PROJECT_SENTINEL,
            "sess-fixture-1",
            root=root,
        )
        assert read_back is not None
        assert read_back["task_id"] == pointer["task_id"]
        # Cross-session isolation: a different session must not see this pointer.
        other = read_pointer(
            POINTER_KIND_CURRENT_DRAFT_SPEC,
            "ws-fixture",
            STANDALONE_PROJECT_SENTINEL,
            "sess-fixture-2",
            root=root,
        )
        assert other is None
        # Cross-workspace isolation.
        other_ws = read_pointer(
            POINTER_KIND_CURRENT_DRAFT_SPEC,
            "ws-other",
            STANDALONE_PROJECT_SENTINEL,
            "sess-fixture-1",
            root=root,
        )
        assert other_ws is None
        # Consume marks consumed and is idempotent.
        consumed = consume_pointer(
            POINTER_KIND_CURRENT_DRAFT_SPEC,
            "ws-fixture",
            STANDALONE_PROJECT_SENTINEL,
            "sess-fixture-1",
            consumed_at="2026-07-28T00:00:01Z",
            root=root,
        )
        assert consumed["state"] == "consumed"
        consumed_again = consume_pointer(
            POINTER_KIND_CURRENT_DRAFT_SPEC,
            "ws-fixture",
            STANDALONE_PROJECT_SENTINEL,
            "sess-fixture-1",
            consumed_at="2026-07-28T00:00:02Z",
            root=root,
        )
        assert consumed_again["state"] == "consumed"
    return {"status": "PASS", "scope": "session_state_authority_contract"}


__all__ = [
    "RUNTIME_ROOT",
    "SESSION_STATE_ROOT",
    "INDEXES_ROOT",
    "LEGACY_SESSION_ACTIVE_SPEC_ROOT",
    "POINTER_SCHEMA_VERSION",
    "POINTER_KINDS",
    "POINTER_KIND_CURRENT_DRAFT_SPEC",
    "POINTER_KIND_ACTIVE_FROZEN_SPEC",
    "POINTER_KIND_ACTIVE_TASK",
    "POINTER_KIND_CURRENT_COMPLETION",
    "POINTER_KIND_CURRENT_HANDOFF",
    "POINTER_KIND_CURRENT_DECISION",
    "POINTER_KIND_CURRENT_COMPLETED_TASK",
    "POINTER_KIND_CURRENT_WORK_CLASSIFICATION",
    "COMMON_POINTER_FIELDS",
    "TRUSTED_PRINCIPALS",
    "STANDALONE_PROJECT_SENTINEL",
    "SessionStateError",
    "SessionPartition",
    "session_digest",
    "project_partition",
    "resolve_session_partition",
    "validate_pointer",
    "write_pointer",
    "read_pointer",
    "read_pointer_for_context",
    "consume_pointer",
    "supersede_pointer",
    "clear_pointer_if_exact",
    "missing_context_result",
    "missing_result",
    "stale_result",
    "mismatch_result",
    "consumed_result",
    "build_pointer",
    "write_subject_pointer",
    "write_current_work_classification_pointer",
    "read_current_work_classification_pointer",
    "consume_current_work_classification_pointer",
    "write_current_draft_spec_pointer",
    "write_active_frozen_spec_pointer",
    "write_active_task_pointer",
    "write_current_completion_pointer",
    "write_current_handoff_pointer",
    "write_current_decision_pointer",
    "write_current_completed_task_pointer",
    "run_isolated_smoke",
]
