"""Trusted orchestration identity contract for future Plan mutations.

This source-only foundation has no production default.  Runtime activation
requires a controlled task-main profile deployment to inject the documented
environment contract; model-provided tool input is never consulted.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Final

_ALLOWED_PRINCIPAL: Final = "task-main"
_ALLOWED_AUTHORITIES: Final = frozenset({"plan_read", "plan_write"})
_SESSION_RE: Final = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_WORKSPACE_RE: Final = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_WORKER_ENV: Final = (
    "AOTA_PROFILE_TASK_ID",
    "AOTA_PROFILE_TASK_START_ID",
    "AOTA_PROFILE_TASK_PROFILE",
)


class OrchestratorSecurityError(PermissionError):
    """Fail-closed authorization error with a canonical, bounded code."""

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


@dataclass(frozen=True, slots=True)
class OrchestratorSecurityContext:
    available: bool
    principal_type: str = ""
    principal_id: str = ""
    authorities: frozenset[str] = frozenset()
    origin_session_id: str = ""
    workspace_id: str = ""
    source: str = ""
    worker_context: bool = False

    def summary(self) -> dict[str, object]:
        """Return the bounded projection permitted for audit/result output."""
        return {
            "available": self.available,
            "principal_type": self.principal_type,
            "principal_id": self.principal_id,
            "authorities": sorted(self.authorities),
            "origin_session_id": self.origin_session_id or None,
            "workspace_id": self.workspace_id or None,
            "source": self.source,
        }


def _worker_context_present() -> bool:
    return any(os.environ.get(name, "") for name in _WORKER_ENV)


def _parse_authorities(raw: str) -> frozenset[str] | None:
    values = raw.split(",")
    if not values or any(not item or item != item.strip() for item in values):
        return None
    parsed = frozenset(values)
    if len(parsed) != len(values) or not parsed.issubset(_ALLOWED_AUTHORITIES):
        return None
    return parsed


def get_orchestrator_security_context() -> OrchestratorSecurityContext:
    """Read the deployment-injected fallback contract without a default.

    Hermes' plugin registration context exposes profile metadata only while a
    plugin is loaded; it does not pass invocation principal/session/toolset
    metadata to registered handlers.  Therefore this provider remains
    unavailable unless controlled deployment injects all required values.
    """
    worker_context = _worker_context_present()
    principal = os.environ.get("AOTA_TRUSTED_PRINCIPAL", "")
    authorities = _parse_authorities(os.environ.get("AOTA_TRUSTED_AUTHORITIES", ""))
    workspace_id = os.environ.get("AOTA_TRUSTED_WORKSPACE_ID", "")
    origin_session_id = os.environ.get("AOTA_ORIGIN_SESSION_ID", "")
    if (
        worker_context
        or not principal
        or authorities is None
        or (workspace_id and not _WORKSPACE_RE.fullmatch(workspace_id))
        or (origin_session_id and not _SESSION_RE.fullmatch(origin_session_id))
    ):
        return OrchestratorSecurityContext(available=False, worker_context=worker_context)
    return OrchestratorSecurityContext(
        available=True,
        principal_type="orchestrator_profile",
        principal_id=principal,
        authorities=authorities,
        origin_session_id=origin_session_id,
        workspace_id=workspace_id,
        source="deployment_injected_principal",
    )


def require_plan_write_authority(*, workspace_id: str) -> OrchestratorSecurityContext:
    """Authorize only the fixed Plan-write capability; no caller actor exists."""
    context = get_orchestrator_security_context()
    if context.worker_context:
        raise OrchestratorSecurityError("PLAN_WRITE_FORBIDDEN_FOR_WORKER")
    if not context.available:
        raise OrchestratorSecurityError("ORCHESTRATOR_CONTEXT_UNAVAILABLE")
    if context.principal_type != "orchestrator_profile" or context.principal_id != _ALLOWED_PRINCIPAL:
        raise OrchestratorSecurityError("ORCHESTRATOR_PRINCIPAL_INVALID")
    if "plan_write" not in context.authorities:
        raise OrchestratorSecurityError("ORCHESTRATOR_AUTHORITY_DENIED")
    if context.workspace_id and context.workspace_id != workspace_id:
        raise OrchestratorSecurityError("ORCHESTRATOR_WORKSPACE_MISMATCH")
    return context


def run_isolated_smoke() -> dict[str, str]:
    """Exercise the env contract only; restores environment before return."""
    names = (*_WORKER_ENV, "AOTA_TRUSTED_PRINCIPAL", "AOTA_TRUSTED_AUTHORITIES", "AOTA_TRUSTED_WORKSPACE_ID", "AOTA_ORIGIN_SESSION_ID")
    before = {name: os.environ.get(name) for name in names}
    try:
        for name in names:
            os.environ.pop(name, None)
        try:
            require_plan_write_authority(workspace_id="fixture")
            raise AssertionError("missing context allowed")
        except OrchestratorSecurityError as exc:
            assert exc.error_code == "ORCHESTRATOR_CONTEXT_UNAVAILABLE"
        os.environ.update({"AOTA_TRUSTED_PRINCIPAL": "task-main", "AOTA_TRUSTED_AUTHORITIES": "plan_read,plan_write", "AOTA_TRUSTED_WORKSPACE_ID": "fixture"})
        assert require_plan_write_authority(workspace_id="fixture").summary()["principal_id"] == "task-main"
        os.environ["AOTA_TRUSTED_AUTHORITIES"] = "plan_read,unknown"
        assert not get_orchestrator_security_context().available
        os.environ["AOTA_TRUSTED_AUTHORITIES"] = "plan_read"
        try:
            require_plan_write_authority(workspace_id="fixture")
            raise AssertionError("read-only authority allowed")
        except OrchestratorSecurityError as exc:
            assert exc.error_code == "ORCHESTRATOR_AUTHORITY_DENIED"
        os.environ["AOTA_TRUSTED_AUTHORITIES"] = "plan_write"
        os.environ["AOTA_PROFILE_TASK_ID"] = "pt_fixture"
        try:
            require_plan_write_authority(workspace_id="fixture")
            raise AssertionError("worker allowed")
        except OrchestratorSecurityError as exc:
            assert exc.error_code == "PLAN_WRITE_FORBIDDEN_FOR_WORKER"
    finally:
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return {"status": "PASS", "runtime_activation": "NOT_CONFIGURED"}
