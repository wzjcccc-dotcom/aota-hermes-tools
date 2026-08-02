"""Canonical projection of host-owned runtime identity for AOTA handlers.

Hermes has more than one identity carrier depending on the transport.  The
registry currently passes ``session_id``/``task_id`` kwargs, while gateway and
WebUI paths also bind ``HERMES_SESSION_*`` ContextVars.  This module is the
single, read-only normalization point.  Tool arguments are deliberately not
consulted here.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")
_PRINCIPALS = frozenset({"task-main", "coordinator"})
_WORKER_ENV = (
    "AOTA_PROFILE_TASK_ID",
    "AOTA_PROFILE_TASK_START_ID",
    "AOTA_PROFILE_TASK_PROFILE",
)


@dataclass(frozen=True, slots=True)
class TrustedRuntimeContext:
    principal: str = ""
    profile: str = ""
    session_id: str = ""
    origin_session_id: str = ""
    parent_session_id: str = ""
    workspace_id: str = ""
    # Host-projected project identity is control-plane metadata only.  It is
    # intentionally absent from the model-facing tool schema.
    project_id: str = ""
    execution_context: Mapping[str, Any] = field(default_factory=dict)
    context_source: str = ""
    worker_context: bool = False

    @property
    def usable_orchestrator_session(self) -> bool:
        return bool(
            self.session_id
            and self.principal in _PRINCIPALS
            and not self.worker_context
        )

    def bounded_projection(self) -> dict[str, Any]:
        """Return non-secret identity metadata suitable for diagnostics."""
        return {
            "principal": self.principal or None,
            "profile": self.profile or None,
            "session_id": self.session_id or None,
            "origin_session_id": self.origin_session_id or None,
            "parent_session_id": self.parent_session_id or None,
            "workspace_id": self.workspace_id or None,
            "project_id": self.project_id or None,
            "context_source": self.context_source or None,
            "worker_context": self.worker_context,
        }


def _value(mapping: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = mapping.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _contextvar(name: str) -> str:
    try:
        from gateway.session_context import get_session_env

        return str(get_session_env(name, "") or "").strip()
    except Exception:
        return str(os.environ.get(name, "") or "").strip()


def _valid(value: str, field_name: str) -> str:
    if not value:
        return ""
    if not _ID_RE.fullmatch(value):
        raise ValueError(f"{field_name}_invalid")
    return value


def get_trusted_runtime_context(
    handler_kwargs: Mapping[str, Any] | None = None,
) -> TrustedRuntimeContext:
    """Normalize trusted host/runtime metadata; never read model arguments."""
    kwargs = handler_kwargs or {}
    worker_context = any(os.environ.get(name, "") for name in _WORKER_ENV)

    # The Hermes registry dispatch is the strongest current source because it
    # receives the agent's host-owned session identity for every tool call.
    session_id = _value(kwargs, "session_id", "conversation_id", "session_key")
    source_parts: list[str] = []
    if session_id:
        source_parts.append("handler_kwargs")
    if not session_id:
        session_id = _contextvar("HERMES_SESSION_ID")
        if session_id:
            source_parts.append("HERMES_SESSION_ID")
    if not session_id:
        session_id = _contextvar("HERMES_SESSION_KEY")
        if session_id:
            source_parts.append("HERMES_SESSION_KEY")

    origin = _value(
        kwargs,
        "origin_session_id",
        "origin_session_key",
        "parent_session_id",
    )
    if not origin:
        origin = str(os.environ.get("AOTA_ORIGIN_SESSION_ID", "") or "").strip()
    if not origin:
        origin = _contextvar("HERMES_SESSION_KEY") or session_id

    parent = _value(kwargs, "parent_session_id", "parent_session", "origin_session_id")
    if not parent:
        parent = str(os.environ.get("AOTA_PARENT_SESSION_ID", "") or "").strip()

    profile = _value(kwargs, "profile", "principal")
    if not profile:
        profile = _contextvar("HERMES_SESSION_PROFILE")
    if not profile:
        profile = str(os.environ.get("AOTA_TRUSTED_PRINCIPAL", "") or "").strip()
    if not profile:
        try:
            from hermes_cli.profiles import get_active_profile_name

            profile = str(get_active_profile_name() or "").strip()
        except Exception:
            profile = ""

    workspace_id = _value(kwargs, "workspace_id", "workspace")
    if not workspace_id:
        workspace_id = str(os.environ.get("AOTA_TRUSTED_WORKSPACE_ID", "") or "").strip()

    project_id = _value(kwargs, "trusted_project_id", "current_project_id", "project_id")
    if not project_id:
        project_id = str(
            os.environ.get("AOTA_TRUSTED_PROJECT_ID", os.environ.get("AOTA_CURRENT_PROJECT_ID", ""))
            or ""
        ).strip()

    for name, value in (
        ("session_id", session_id),
        ("origin_session_id", origin),
        ("parent_session_id", parent),
        ("workspace_id", workspace_id),
        ("project_id", project_id),
    ):
        if value:
            _valid(value, name)

    execution_context = {
        key: value
        for key, value in (
            ("task_id", _value(kwargs, "task_id")),
            ("turn_id", _value(kwargs, "turn_id")),
            ("tool_call_id", _value(kwargs, "tool_call_id")),
            ("api_request_id", _value(kwargs, "api_request_id")),
        )
        if value
    }
    source = "+".join(source_parts) or (
        "deployment_injected_context"
        if session_id or profile or workspace_id
        else "missing"
    )
    return TrustedRuntimeContext(
        principal=profile,
        profile=profile,
        session_id=session_id,
        origin_session_id=origin,
        parent_session_id=parent,
        workspace_id=workspace_id,
        project_id=project_id,
        execution_context=execution_context,
        context_source=source,
        worker_context=worker_context,
    )


def missing_context_result(*, operation: str) -> dict[str, Any]:
    """Stable result for a model-visible operation with no trusted context."""
    return {
        "status": "rejected",
        "operation_result": operation,
        "error": "trusted_session_context_missing",
        "retryable": False,
        "human_action_required": False,
        "next_action": "stop_and_report_runtime_context_missing",
    }


__all__ = [
    "TrustedRuntimeContext",
    "get_trusted_runtime_context",
    "missing_context_result",
]
