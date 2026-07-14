"""Project-owned, fail-closed audit ledger foundation for future Plan mutations."""
from __future__ import annotations

import json
import os
import stat
import tempfile
import uuid
from pathlib import Path
from typing import Any, Final

from ._orchestrator_security_context import OrchestratorSecurityContext
from ._plan_common import PlanError, utc_now, validate_plan_id
from ._workspace import WorkspaceError, resolve_workspace

AUDIT_ROOT_PARTS: Final = (".aota", "forge", "audit", "plan-mutations")
MAX_AUDIT_BYTES: Final = 16 * 1024
MAX_AUDIT_FILES: Final = 200
_OPERATIONS: Final = frozenset({"create_plan", "set_plan_status", "update_current_state", "set_plan_next_action", "set_active_milestone", "set_active_work_item", "add_milestone", "set_milestone_status", "record_milestone_evidence", "add_work_item", "set_work_item_status", "set_work_item_next_action", "record_work_item_evidence", "link_task", "record_decision", "set_decision_status"})
_TARGET_TYPES: Final = frozenset({"plan", "milestone", "work_item", "decision", "evidence"})
_REASON_CODES: Final = frozenset({"PLAN_MUTATION_FAILED", "PLAN_AUDIT_COMMIT_FAILED", "PLAN_AUDIT_RECONCILIATION_REQUIRED"})
_STATES: Final = frozenset({"prepared", "committed", "denied", "aborted", "reconciliation_required"})


class PlanAuditError(RuntimeError):
    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


def _fail(code: str) -> None:
    raise PlanAuditError(code)


def _require_context(context: OrchestratorSecurityContext, workspace_id: str) -> None:
    if not context.available:
        _fail("ORCHESTRATOR_CONTEXT_UNAVAILABLE")
    if context.worker_context:
        _fail("PLAN_WRITE_FORBIDDEN_FOR_WORKER")
    if context.principal_type != "orchestrator_profile" or context.principal_id != "task-main":
        _fail("ORCHESTRATOR_PRINCIPAL_INVALID")
    if "plan_write" not in context.authorities:
        _fail("ORCHESTRATOR_AUTHORITY_DENIED")
    if context.workspace_id and context.workspace_id != workspace_id:
        _fail("ORCHESTRATOR_WORKSPACE_MISMATCH")


def _audit_root(workspace_id: str, *, create: bool) -> Path:
    try:
        root = resolve_workspace(workspace_id)
    except WorkspaceError as exc:
        raise PlanAuditError("PLAN_AUDIT_WORKSPACE_NOT_FOUND") from exc
    current = root
    for part in AUDIT_ROOT_PARTS:
        current = current / part
        if current.is_symlink():
            _fail("PLAN_AUDIT_ROOT_UNSAFE")
        if current.exists():
            if not current.is_dir():
                _fail("PLAN_AUDIT_ROOT_UNSAFE")
        elif create:
            try:
                current.mkdir()
            except OSError as exc:
                raise PlanAuditError("PLAN_AUDIT_GAP") from exc
        else:
            return current
        try:
            current.resolve().relative_to(root.resolve())
        except ValueError:
            _fail("PLAN_AUDIT_PATH_ESCAPE")
    return current


def _event_id() -> str:
    return f"pa_{uuid.uuid4().hex}"


def _event_file(root: Path, audit_event_id: str) -> Path:
    if not isinstance(audit_event_id, str) or not audit_event_id.startswith("pa_") or len(audit_event_id) != 35 or not all(ch in "0123456789abcdef" for ch in audit_event_id[3:]):
        _fail("PLAN_AUDIT_EVENT_INVALID")
    return root / f"{audit_event_id}.json"


def _validate_descriptor(*, plan_id: str, operation: str, target_type: str, target_id: str | None, previous_revision: int | None, expected_revision: int | None) -> None:
    try:
        validate_plan_id(plan_id)
    except PlanError as exc:
        raise PlanAuditError("PLAN_AUDIT_PLAN_ID_INVALID") from exc
    if operation not in _OPERATIONS or target_type not in _TARGET_TYPES:
        _fail("PLAN_AUDIT_DESCRIPTOR_INVALID")
    if target_id is not None and (not isinstance(target_id, str) or not target_id or len(target_id) > 100 or "/" in target_id or "\\" in target_id):
        _fail("PLAN_AUDIT_DESCRIPTOR_INVALID")
    if operation == "create_plan":
        if target_type != "plan" or target_id != plan_id or previous_revision is not None or expected_revision is not None:
            _fail("PLAN_AUDIT_DESCRIPTOR_INVALID")
        return
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in (previous_revision, expected_revision)):
        _fail("PLAN_AUDIT_REVISION_INVALID")


def _write_new(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_AUDIT_BYTES:
        _fail("PLAN_AUDIT_GAP")
    if path.is_symlink():
        _fail("PLAN_AUDIT_ARTIFACT_UNSAFE")
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".pa_", suffix=".tmp", delete=False) as handle:
            temp = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temp, path)
    except FileExistsError as exc:
        raise PlanAuditError("PLAN_AUDIT_COLLISION") from exc
    except OSError as exc:
        raise PlanAuditError("PLAN_AUDIT_GAP") from exc
    finally:
        try:
            temp.unlink(missing_ok=True)
        except (OSError, UnboundLocalError):
            pass


def _read_event(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.exists() or not path.is_file():
        _fail("PLAN_AUDIT_ARTIFACT_UNSAFE")
    try:
        if path.stat().st_size > MAX_AUDIT_BYTES:
            _fail("PLAN_AUDIT_TOO_LARGE")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                _fail("PLAN_AUDIT_ARTIFACT_UNSAFE")
            raw = handle.read(MAX_AUDIT_BYTES + 1)
    except PlanAuditError:
        raise
    except OSError as exc:
        raise PlanAuditError("PLAN_AUDIT_READ_FAILED") from exc
    if len(raw) > MAX_AUDIT_BYTES:
        _fail("PLAN_AUDIT_TOO_LARGE")
    try:
        event = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanAuditError("PLAN_AUDIT_INVALID") from exc
    required = {"schema_version", "audit_event_id", "workspace_id", "plan_id", "principal_type", "principal_id", "authority", "operation", "target_type", "target_id", "previous_revision", "expected_revision", "new_revision", "status", "denied_reason", "created_at", "updated_at"}
    if not isinstance(event, dict) or set(event) != required or event.get("schema_version") != 1 or event.get("status") not in _STATES:
        _fail("PLAN_AUDIT_INVALID")
    if (
        event.get("principal_type") != "orchestrator_profile" or event.get("principal_id") != "task-main"
        or event.get("authority") != "plan_write" or event.get("operation") not in _OPERATIONS
        or event.get("target_type") not in _TARGET_TYPES
        or (event.get("target_id") is not None and (not isinstance(event["target_id"], str) or not event["target_id"] or len(event["target_id"]) > 100 or "/" in event["target_id"] or "\\" in event["target_id"]))
        or (event["operation"] == "create_plan" and (event.get("previous_revision") is not None or event.get("expected_revision") is not None or event.get("target_type") != "plan" or event.get("target_id") != event.get("plan_id")))
        or (event["operation"] != "create_plan" and any(not isinstance(event.get(key), int) or isinstance(event[key], bool) or event[key] < 1 for key in ("previous_revision", "expected_revision")))
        or (event.get("new_revision") is not None and (not isinstance(event["new_revision"], int) or isinstance(event["new_revision"], bool) or event["new_revision"] < 1))
        or not all(isinstance(event.get(key), str) for key in ("workspace_id", "plan_id", "audit_event_id", "created_at", "updated_at"))
        or (event.get("denied_reason") is not None and event["denied_reason"] not in _REASON_CODES)
    ):
        _fail("PLAN_AUDIT_INVALID")
    try:
        validate_plan_id(event["plan_id"])
        _event_file(path.parent, event["audit_event_id"])
    except (PlanError, PlanAuditError) as exc:
        raise PlanAuditError("PLAN_AUDIT_INVALID") from exc
    return event


def _replace_event(path: Path, event: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_AUDIT_BYTES:
        _fail("PLAN_AUDIT_GAP")
    if path.is_symlink() or not path.is_file():
        _fail("PLAN_AUDIT_ARTIFACT_UNSAFE")
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".pa_", suffix=".tmp", delete=False) as handle:
            temp = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except OSError as exc:
        raise PlanAuditError("PLAN_AUDIT_GAP") from exc
    finally:
        try:
            temp.unlink(missing_ok=True)
        except (OSError, UnboundLocalError):
            pass
    return event


def prepare_plan_mutation_audit(*, context: OrchestratorSecurityContext, workspace_id: str, plan_id: str, operation: str, target_type: str, target_id: str | None, previous_revision: int | None, expected_revision: int | None) -> dict[str, Any]:
    """Create an immutable prepared intent before any future canonical write."""
    _require_context(context, workspace_id)
    _validate_descriptor(plan_id=plan_id, operation=operation, target_type=target_type, target_id=target_id, previous_revision=previous_revision, expected_revision=expected_revision)
    audit_event_id, now = _event_id(), utc_now()
    event = {"schema_version": 1, "audit_event_id": audit_event_id, "workspace_id": workspace_id, "plan_id": plan_id, "principal_type": "orchestrator_profile", "principal_id": "task-main", "authority": "plan_write", "operation": operation, "target_type": target_type, "target_id": target_id, "previous_revision": previous_revision, "expected_revision": expected_revision, "new_revision": None, "status": "prepared", "denied_reason": None, "created_at": now, "updated_at": now}
    root = _audit_root(workspace_id, create=True)
    _write_new(_event_file(root, audit_event_id), event)
    return event


def _transition(*, workspace_id: str, audit_event_id: str, target: str, new_revision: int | None = None, reason_code: str | None = None) -> dict[str, Any]:
    root = _audit_root(workspace_id, create=False)
    event = _read_event(_event_file(root, audit_event_id))
    if event["workspace_id"] != workspace_id or event["status"] != "prepared":
        _fail("PLAN_AUDIT_TRANSITION_INVALID")
    if target == "committed":
        if not isinstance(new_revision, int) or isinstance(new_revision, bool) or new_revision < 1:
            _fail("PLAN_AUDIT_REVISION_INVALID")
        event["new_revision"] = new_revision
    elif reason_code not in _REASON_CODES:
        _fail("PLAN_AUDIT_REASON_INVALID")
    else:
        event["denied_reason"] = reason_code
    event["status"], event["updated_at"] = target, utc_now()
    return _replace_event(_event_file(root, audit_event_id), event)


def commit_plan_mutation_audit(*, workspace_id: str, audit_event_id: str, new_revision: int) -> dict[str, Any]:
    return _transition(workspace_id=workspace_id, audit_event_id=audit_event_id, target="committed", new_revision=new_revision)


def abort_plan_mutation_audit(*, workspace_id: str, audit_event_id: str, reason_code: str = "PLAN_MUTATION_FAILED") -> dict[str, Any]:
    return _transition(workspace_id=workspace_id, audit_event_id=audit_event_id, target="aborted", reason_code=reason_code)


def mark_plan_audit_reconciliation_required(*, workspace_id: str, audit_event_id: str) -> dict[str, Any]:
    return _transition(workspace_id=workspace_id, audit_event_id=audit_event_id, target="reconciliation_required", reason_code="PLAN_AUDIT_RECONCILIATION_REQUIRED")


def find_unresolved_plan_audits(*, workspace_id: str, plan_id: str) -> list[str]:
    """Bounded, read-only same-workspace/same-Plan unresolved audit gate."""
    try:
        validate_plan_id(plan_id)
    except PlanError as exc:
        raise PlanAuditError("PLAN_AUDIT_PLAN_ID_INVALID") from exc
    root = _audit_root(workspace_id, create=False)
    if not root.exists():
        return []
    if root.is_symlink() or not root.is_dir():
        _fail("PLAN_AUDIT_ROOT_UNSAFE")
    entries = sorted(path for path in root.iterdir() if path.suffix == ".json")
    if len(entries) > MAX_AUDIT_FILES:
        _fail("PLAN_AUDIT_SCAN_LIMIT")
    unresolved: list[str] = []
    for path in entries:
        try:
            _event_file(root, path.stem)
        except PlanAuditError as exc:
            raise PlanAuditError("PLAN_AUDIT_INVALID") from exc
        event = _read_event(path)
        if event["plan_id"] == plan_id and event["status"] in {"prepared", "reconciliation_required"}:
            unresolved.append(event["audit_event_id"])
    return unresolved


def assert_no_unresolved_plan_audit(*, workspace_id: str, plan_id: str) -> None:
    if find_unresolved_plan_audits(workspace_id=workspace_id, plan_id=plan_id):
        _fail("PLAN_AUDIT_RECONCILIATION_REQUIRED")


def run_isolated_smoke() -> dict[str, str]:
    """Temporary registered workspace smoke; never writes a real audit root."""
    from . import _workspace
    context = OrchestratorSecurityContext(True, "orchestrator_profile", "task-main", frozenset({"plan_write"}), workspace_id="fixture", source="fixture")
    with tempfile.TemporaryDirectory() as raw:
        root, registry = Path(raw) / "workspace", Path(raw) / "workspaces.json"
        root.mkdir(); registry.write_text(json.dumps({"fixture": {"candidates": [str(root)]}}), encoding="utf-8")
        prior = _workspace._REGISTRY_PATH; _workspace._REGISTRY_PATH = registry
        try:
            prepared = prepare_plan_mutation_audit(context=context, workspace_id="fixture", plan_id="plan_fixture", operation="set_plan_status", target_type="plan", target_id=None, previous_revision=1, expected_revision=1)
            assert find_unresolved_plan_audits(workspace_id="fixture", plan_id="plan_fixture") == [prepared["audit_event_id"]]
            assert commit_plan_mutation_audit(workspace_id="fixture", audit_event_id=prepared["audit_event_id"], new_revision=2)["status"] == "committed"
            try:
                commit_plan_mutation_audit(workspace_id="fixture", audit_event_id=prepared["audit_event_id"], new_revision=3)
                raise AssertionError("illegal transition allowed")
            except PlanAuditError as exc:
                assert exc.error_code == "PLAN_AUDIT_TRANSITION_INVALID"
            second = prepare_plan_mutation_audit(context=context, workspace_id="fixture", plan_id="plan_second", operation="update_current_state", target_type="plan", target_id=None, previous_revision=2, expected_revision=2)
            assert abort_plan_mutation_audit(workspace_id="fixture", audit_event_id=second["audit_event_id"])["status"] == "aborted"
            third = prepare_plan_mutation_audit(context=context, workspace_id="fixture", plan_id="plan_fixture", operation="set_plan_next_action", target_type="plan", target_id=None, previous_revision=2, expected_revision=2)
            assert mark_plan_audit_reconciliation_required(workspace_id="fixture", audit_event_id=third["audit_event_id"])["status"] == "reconciliation_required"
            assert_no_unresolved_plan_audit(workspace_id="fixture", plan_id="plan_second")
            try:
                assert_no_unresolved_plan_audit(workspace_id="fixture", plan_id="plan_fixture")
                raise AssertionError("unresolved audit allowed")
            except PlanAuditError as exc:
                assert exc.error_code == "PLAN_AUDIT_RECONCILIATION_REQUIRED"
        finally:
            _workspace._REGISTRY_PATH = prior
    return {"status": "PASS", "root": ".aota/forge/audit/plan-mutations"}
