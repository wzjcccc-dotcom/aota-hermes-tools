"""Trusted task-main session binding for ``active_frozen_spec``.

The binding is a small runtime context artifact, not a second task registry.
It is written only from trusted handler metadata and never from model input or
worker context.  The resolver re-reads the task/SPEC before using it.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ._orchestrator_security_context import get_orchestrator_security_context
from ._trusted_runtime_context import get_trusted_runtime_context


SESSION_ACTIVE_SPEC_ROOT = Path(
    os.environ.get("AOTA_RUNTIME_ROOT", "/aota-runtime")
) / "session-active-spec"
_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
TRUSTED_PRINCIPALS = frozenset({"task-main", "coordinator"})


class SessionBindingError(ValueError):
    """Deterministic session binding read/write failure."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code if not detail else f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class TrustedSessionContext:
    session_id: str = ""
    principal: str = ""
    source: str = ""
    worker_context: bool = False
    profile: str = ""
    origin_session_id: str = ""
    parent_session_id: str = ""
    workspace_id: str = ""
    project_id: str = ""
    execution_context: Mapping[str, Any] = field(default_factory=dict)

    @property
    def usable_for_active_spec(self) -> bool:
        return bool(
            self.session_id
            and self.principal in TRUSTED_PRINCIPALS
            and not self.worker_context
        )


def _validate_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise SessionBindingError("reference_invalid", f"{field}_invalid")
    return value


def trusted_session_context(handler_kwargs: Mapping[str, Any] | None = None) -> TrustedSessionContext:
    """Project the shared trusted runtime helper; never inspect tool args."""
    context = get_trusted_runtime_context(handler_kwargs)
    if context.usable_orchestrator_session or context.session_id or context.worker_context:
        return TrustedSessionContext(
            session_id=context.session_id,
            principal=context.principal,
            source=context.context_source,
            worker_context=context.worker_context,
            profile=context.profile,
            origin_session_id=context.origin_session_id,
            parent_session_id=context.parent_session_id,
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            execution_context=dict(context.execution_context),
        )
    security = get_orchestrator_security_context()
    if security.available and security.origin_session_id:
        return TrustedSessionContext(
            session_id=security.origin_session_id,
            principal=security.principal_id,
            source=security.source or "deployment_injected_context",
            workspace_id=security.workspace_id,
        )
    return TrustedSessionContext()


def _binding_path(workspace_id: str, session_id: str) -> Path:
    workspace = _validate_id(workspace_id, "workspace_id")
    session = _validate_id(session_id, "session_id")
    return SESSION_ACTIVE_SPEC_ROOT / workspace / session / "active-frozen-spec.json"


def _lock_path(workspace_id: str, session_id: str) -> Path:
    return _binding_path(workspace_id, session_id).with_name(".active-frozen-spec.lock")


def read_session_active_spec(workspace_id: str, session_id: str) -> dict[str, Any] | None:
    """Read one exact session artifact; missing is distinct from malformed."""
    path = _binding_path(workspace_id, session_id)
    if SESSION_ACTIVE_SPEC_ROOT.is_symlink() or path.parent.parent.is_symlink() or path.parent.is_symlink():
        raise SessionBindingError("reference_stale", "session_binding_path_is_symlink")
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise SessionBindingError("reference_stale", "session_binding_artifact_invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SessionBindingError("reference_stale", "session_binding_unreadable") from exc
    if not isinstance(value, dict):
        raise SessionBindingError("reference_stale", "session_binding_not_object")
    return value


def write_session_active_spec(binding: Mapping[str, Any]) -> Path:
    """Atomically supersede the current binding for one trusted session."""
    workspace_id = _validate_id(binding.get("workspace_id"), "workspace_id")
    session_id = _validate_id(binding.get("session_id"), "session_id")
    if binding.get("binding_source") != "task_spec_freeze":
        raise SessionBindingError("reference_invalid", "binding_source_invalid")
    required = (
        "project_id", "task_id", "spec_id", "revision", "spec_hash",
        "spec_sha256", "frozen_at", "resolved_profile", "approval_status",
        "workspace_registry_digest", "project_manifest_digest",
        "project_registry_revision",
    )
    if any(key not in binding for key in required):
        raise SessionBindingError("reference_invalid", "session_binding_fields_missing")
    if any(binding.get(key) in (None, "") for key in ("project_id", "task_id", "spec_id", "spec_hash", "spec_sha256", "frozen_at", "resolved_profile", "approval_status", "workspace_registry_digest", "project_manifest_digest")):
        raise SessionBindingError("reference_invalid", "session_binding_fields_empty")
    if isinstance(binding.get("revision"), bool) or not isinstance(binding.get("revision"), int) or binding["revision"] < 1:
        raise SessionBindingError("reference_invalid", "session_binding_revision_invalid")
    path = _binding_path(workspace_id, session_id)
    lock_path = _lock_path(workspace_id, session_id)
    if SESSION_ACTIVE_SPEC_ROOT.is_symlink() or path.parent.parent.is_symlink() or path.parent.is_symlink():
        raise SessionBindingError("reference_stale", "session_binding_path_is_symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or lock_path.is_symlink():
        raise SessionBindingError("reference_stale", "session_binding_path_is_symlink")
    # A small lock protects concurrent freezes in the same trusted session.
    import fcntl
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        content = json.dumps(dict(binding), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        temp_fd, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".active-frozen-spec.tmp_")
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
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
    return path
