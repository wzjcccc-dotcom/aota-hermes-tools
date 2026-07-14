"""Bounded, audited Plan v1 create/update tools; runtime activation remains disabled."""
from __future__ import annotations

import copy
import json
import os
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

from ._handoff_common import validate_workspace_id
from ._orchestrator_security_context import OrchestratorSecurityError, require_plan_write_authority
from ._plan_audit import (
    PlanAuditError,
    abort_plan_mutation_audit,
    assert_no_unresolved_plan_audit,
    commit_plan_mutation_audit,
    mark_plan_audit_reconciliation_required,
    prepare_plan_mutation_audit,
)
from ._plan_common import (
    ARCHITECT_GATES, DECISION_SOURCES, DECISION_STATUSES, DELIVERY_PATHS,
    EVIDENCE_TYPES, MILESTONE_STATUSES, PLANNING_DEPTHS, RISK_LEVELS,
    SPEC_PREFLIGHTS, WORK_ITEM_STATUSES, PLAN_FILE, PROJECTION_FILE,
    PlanError, compute_plan_sha256, create_plan, generate_plan_id,
    render_plan_markdown, utc_now, validate_decision_transition,
    validate_milestone_transition, validate_plan, validate_plan_id,
    validate_plan_transition, validate_work_item_transition,
)
from ._plan_open import _PlanOpenError, _read_plan_json, _resolve_plan_file, _verify_and_validate
from ._workspace import WorkspaceError, resolve_workspace

TOOLSET_NAME = "aota_plan_write"
CREATE_TOOL_NAME = "aota_plan_create"
UPDATE_TOOL_NAME = "aota_plan_update"
_PLAN_ROOT_PARTS = (".aota", "forge", "plans")
_MAX = {"title": 200, "goal": 8000, "state": 8000, "next": 1000, "summary": 4000, "rationale": 4000, "reference": 300, "acceptance": 1000}
_ID_PREFIX = {"milestone": "ms", "work_item": "wi", "decision": "pd"}
_TERMINAL = {"closed", "cancelled", "superseded"}

CREATE_SCHEMA = {
    "name": CREATE_TOOL_NAME,
    "description": "Create one minimal, audited canonical AOTA Plan in a registered workspace. Requires trusted task-main plan_write authority; no path, plan ID, actor, or raw Plan input is accepted.",
    "parameters": {"type": "object", "properties": {
        "workspace_id": {"type": "string"}, "title": {"type": "string"},
        "planning_depth": {"type": "string", "enum": ["P1", "P2"]},
        "architect_gate": {"type": "string", "enum": ["A0", "A1", "A2"]},
        "delivery_path": {"type": "string", "enum": ["fast", "standard", "deep"]},
        "goal": {"type": "string"}, "non_goals": {"type": "array", "items": {"type": "string"}},
        "current_state": {"type": "string"}, "next_action": {"type": ["string", "null"]},
    }, "required": ["workspace_id", "title", "planning_depth", "architect_gate", "delivery_path", "goal"], "additionalProperties": False},
}
UPDATE_SCHEMA = {
    "name": UPDATE_TOOL_NAME,
    "description": "Apply exactly one bounded, audited AOTA Plan operation with mandatory optimistic revision matching. Arbitrary JSON, Markdown, paths, actor, and authority are rejected.",
    "parameters": {"type": "object", "properties": {
        "workspace_id": {"type": "string"}, "plan_id": {"type": "string"},
        "expected_revision": {"type": "integer", "minimum": 1},
        "operation": {"type": "string", "enum": ["set_plan_status", "update_current_state", "set_plan_next_action", "add_milestone", "set_milestone_status", "set_active_milestone", "record_milestone_evidence", "add_work_item", "set_work_item_status", "set_active_work_item", "set_work_item_next_action", "record_work_item_evidence", "link_task", "record_decision", "set_decision_status"]},
        "payload": {"type": "object"},
    }, "required": ["workspace_id", "plan_id", "expected_revision", "operation", "payload"], "additionalProperties": False},
}


class _MutationError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _error(code: str, workspace_id: str | None = None, plan_id: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "error", "error_code": code}
    if workspace_id:
        result["workspace_id"] = workspace_id
    if plan_id:
        result["plan_id"] = plan_id
    return result


def _text(value: Any, maximum: int, *, nullable: bool = False, allow_empty: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or (not allow_empty and not value) or len(value) > maximum or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise _MutationError("PLAN_OPERATION_PAYLOAD_INVALID")
    return value


def _enum(value: Any, allowed: frozenset[str]) -> str:
    value = _text(value, 40)
    if value not in allowed:
        raise _MutationError("PLAN_OPERATION_PAYLOAD_INVALID")
    return value


def _object(value: Any, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise _MutationError("PLAN_OPERATION_PAYLOAD_INVALID")
    return dict(value)


def _list(value: Any, maximum: int = 200) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum:
        raise _MutationError("PLAN_OPERATION_PAYLOAD_INVALID")
    return value


def _id(value: Any, prefix: str | None = None) -> str:
    if not isinstance(value, str) or not value or len(value) > 100 or "/" in value or "\\" in value or any(not (ch.isalnum() or ch in "_-") for ch in value):
        raise _MutationError("PLAN_OPERATION_PAYLOAD_INVALID")
    if prefix and not value.startswith(prefix + "_"):
        raise _MutationError("PLAN_OPERATION_PAYLOAD_INVALID")
    return value


def _id_list(value: Any, prefix: str | None = None) -> list[str]:
    result = [_id(item, prefix) for item in _list(value)]
    if len(set(result)) != len(result):
        raise _MutationError("PLAN_OPERATION_PAYLOAD_INVALID")
    return result


def _string_list(value: Any, maximum: int) -> list[str]:
    return [_text(item, maximum) for item in _list(value)]  # type: ignore[list-item]


def _new_id(kind: str) -> str:
    return f"{_ID_PREFIX[kind]}_{secrets.token_hex(8)}"


def _plan_root(root: Path) -> Path:
    current = root
    for part in _PLAN_ROOT_PARTS:
        current = current / part
        if current.is_symlink():
            raise _MutationError("PLAN_ROOT_UNSAFE")
        if current.exists():
            if not current.is_dir():
                raise _MutationError("PLAN_ROOT_UNSAFE")
        else:
            try:
                current.mkdir()
            except OSError as exc:
                raise _MutationError("PLAN_ROOT_UNSAFE") from exc
        try:
            current.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise _MutationError("PLAN_PATH_ESCAPE") from exc
    return current


def _write_file(path: Path, content: str) -> None:
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _canonical_json(plan: Mapping[str, Any]) -> str:
    return json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _abort_or_gap(workspace_id: str, event_id: str) -> str:
    try:
        abort_plan_mutation_audit(workspace_id=workspace_id, audit_event_id=event_id)
        return "PLAN_WRITE_FAILED"
    except PlanAuditError:
        return "PLAN_AUDIT_GAP"


def _reconcile(workspace_id: str, event_id: str) -> None:
    try:
        mark_plan_audit_reconciliation_required(workspace_id=workspace_id, audit_event_id=event_id)
    except PlanAuditError:
        pass  # prepared artifact remains the fail-closed gate


def _map_plan_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "sha" in text:
        return "PLAN_SHA_MISMATCH"
    if "revision" in text:
        return "PLAN_REVISION_CONFLICT"
    if "cycle" in text:
        return "PLAN_DEPENDENCY_CYCLE"
    if "active" in text or "terminal" in text:
        return "PLAN_ACTIVE_STATE_INVALID"
    if "transition" in text or "status" in text:
        return "PLAN_STATUS_TRANSITION_INVALID"
    if "missing" in text or "references" in text or "dependency" in text:
        return "PLAN_REFERENCE_INVALID"
    return "PLAN_SCHEMA_INVALID"


def _authorize(workspace_id: Any) -> tuple[str, Any, Path]:
    if not isinstance(workspace_id, str) or validate_workspace_id(workspace_id):
        raise _MutationError("WORKSPACE_NOT_FOUND")
    try:
        context = require_plan_write_authority(workspace_id=workspace_id)
    except OrchestratorSecurityError as exc:
        raise _MutationError(exc.error_code) from exc
    try:
        root = resolve_workspace(workspace_id)
    except WorkspaceError as exc:
        raise _MutationError("WORKSPACE_NOT_FOUND") from exc
    return workspace_id, context, root


def _validate_create(args: Mapping[str, Any]) -> dict[str, Any]:
    depth = args.get("planning_depth")
    if depth == "P0":
        raise _MutationError("PLAN_NOT_APPLICABLE_FOR_P0")
    result = {
        "title": _text(args.get("title"), _MAX["title"]),
        "goal": _text(args.get("goal"), _MAX["goal"]),
        "planning_depth": _enum(depth, PLANNING_DEPTHS),
        "architect_gate": _enum(args.get("architect_gate"), ARCHITECT_GATES),
        "delivery_path": _enum(args.get("delivery_path"), DELIVERY_PATHS),
        "non_goals": _string_list(args.get("non_goals", []), _MAX["goal"]),
        "current_state": _text(args.get("current_state", ""), _MAX["state"], allow_empty=True),
        "next_action": _text(args.get("next_action", None), _MAX["next"], nullable=True),
    }
    return result


def _evidence(value: Any) -> dict[str, Any]:
    item = _object(value, {"evidence_type", "reference_id", "summary"})
    return {"evidence_type": _enum(item["evidence_type"], EVIDENCE_TYPES), "reference_id": _id(item["reference_id"]), "summary": _text(item["summary"], _MAX["summary"]), "recorded_at": utc_now()}


def _operation(plan: dict[str, Any], operation: str, payload: Any) -> tuple[dict[str, Any], str, str]:
    updated = copy.deepcopy(plan)
    now = utc_now()
    if operation == "set_plan_status":
        value = _object(payload, {"status"}); status = _enum(value["status"], frozenset(plan_status for plan_status in {"draft", "approved", "in_progress", "human_checkpoint", "blocked", "completed", "cancelled", "superseded"}))
        validate_plan_transition(updated["status"], status); updated["status"] = status
        return updated, "plan", updated["plan_id"]
    if operation == "update_current_state":
        value = _object(payload, {"current_state"}); updated["current_state"] = _text(value["current_state"], _MAX["state"], allow_empty=True)
        return updated, "plan", updated["plan_id"]
    if operation == "set_plan_next_action":
        value = _object(payload, {"next_action"}); updated["next_action"] = _text(value["next_action"], _MAX["next"], nullable=True)
        return updated, "plan", updated["plan_id"]
    if operation == "add_milestone":
        value = _object(payload, {"title", "objective", "dependencies", "acceptance_criteria", "architect_review_id"})
        milestone_id = _new_id("milestone")
        updated["milestones"].append({"milestone_id": milestone_id, "title": _text(value["title"], _MAX["title"]), "status": "planned", "objective": _text(value["objective"], _MAX["goal"]), "dependencies": _id_list(value["dependencies"], "ms"), "acceptance_criteria": _string_list(value["acceptance_criteria"], _MAX["acceptance"]), "evidence": [], "architect_review_id": _id(value["architect_review_id"]) if value["architect_review_id"] is not None else None, "created_at": now, "updated_at": now})
        return updated, "milestone", milestone_id
    if operation == "set_milestone_status":
        value = _object(payload, {"milestone_id", "status"}); target_id = _id(value["milestone_id"], "ms"); entry = next((x for x in updated["milestones"] if x["milestone_id"] == target_id), None)
        if entry is None: raise _MutationError("PLAN_REFERENCE_INVALID")
        status = _enum(value["status"], MILESTONE_STATUSES); validate_milestone_transition(entry["status"], status); entry["status"], entry["updated_at"] = status, now
        return updated, "milestone", target_id
    if operation == "set_active_milestone":
        value = _object(payload, {"milestone_id"}); target_id = value["milestone_id"]
        if target_id is None:
            if updated["active_work_item_id"] is not None: raise _MutationError("PLAN_ACTIVE_STATE_INVALID")
        else:
            target_id = _id(target_id, "ms"); entry = next((x for x in updated["milestones"] if x["milestone_id"] == target_id), None)
            if entry is None or entry["status"] in _TERMINAL: raise _MutationError("PLAN_ACTIVE_STATE_INVALID")
        updated["active_milestone_id"] = target_id
        return updated, "milestone", target_id or updated["plan_id"]
    if operation == "record_milestone_evidence":
        value = _object(payload, {"milestone_id", "evidence"}); target_id = _id(value["milestone_id"], "ms"); entry = next((x for x in updated["milestones"] if x["milestone_id"] == target_id), None)
        if entry is None: raise _MutationError("PLAN_REFERENCE_INVALID")
        entry["evidence"].append(_evidence(value["evidence"])); entry["updated_at"] = now
        return updated, "evidence", target_id
    if operation == "add_work_item":
        value = _object(payload, {"milestone_id", "title", "goal", "risk_level", "architect_gate", "spec_preflight", "dependencies", "next_action"})
        milestone_id = _id(value["milestone_id"], "ms")
        if not any(x["milestone_id"] == milestone_id for x in updated["milestones"]): raise _MutationError("PLAN_REFERENCE_INVALID")
        item_id = _new_id("work_item")
        updated["work_items"].append({"work_item_id": item_id, "milestone_id": milestone_id, "title": _text(value["title"], _MAX["title"]), "goal": _text(value["goal"], _MAX["goal"]), "status": "planned", "risk_level": _enum(value["risk_level"], RISK_LEVELS), "architect_gate": _enum(value["architect_gate"], ARCHITECT_GATES), "architect_review_id": None, "spec_preflight": _enum(value["spec_preflight"], SPEC_PREFLIGHTS), "dependencies": _id_list(value["dependencies"], "wi"), "linked_task_ids": [], "evidence": [], "next_action": _text(value["next_action"], _MAX["next"], nullable=True), "created_at": now, "updated_at": now})
        return updated, "work_item", item_id
    if operation == "set_work_item_status":
        value = _object(payload, {"work_item_id", "status"}); target_id = _id(value["work_item_id"], "wi"); entry = next((x for x in updated["work_items"] if x["work_item_id"] == target_id), None)
        if entry is None: raise _MutationError("PLAN_REFERENCE_INVALID")
        status = _enum(value["status"], WORK_ITEM_STATUSES); validate_work_item_transition(entry["status"], status); entry["status"], entry["updated_at"] = status, now
        return updated, "work_item", target_id
    if operation == "set_active_work_item":
        value = _object(payload, {"work_item_id"}); target_id = value["work_item_id"]
        if target_id is not None:
            target_id = _id(target_id, "wi"); entry = next((x for x in updated["work_items"] if x["work_item_id"] == target_id), None)
            if entry is None or entry["status"] in _TERMINAL or not updated["active_milestone_id"] or entry["milestone_id"] != updated["active_milestone_id"]: raise _MutationError("PLAN_ACTIVE_STATE_INVALID")
        updated["active_work_item_id"] = target_id
        return updated, "work_item", target_id or updated["plan_id"]
    if operation == "set_work_item_next_action":
        value = _object(payload, {"work_item_id", "next_action"}); target_id = _id(value["work_item_id"], "wi"); entry = next((x for x in updated["work_items"] if x["work_item_id"] == target_id), None)
        if entry is None: raise _MutationError("PLAN_REFERENCE_INVALID")
        entry["next_action"], entry["updated_at"] = _text(value["next_action"], _MAX["next"], nullable=True), now
        return updated, "work_item", target_id
    if operation == "record_work_item_evidence":
        value = _object(payload, {"work_item_id", "evidence"}); target_id = _id(value["work_item_id"], "wi"); entry = next((x for x in updated["work_items"] if x["work_item_id"] == target_id), None)
        if entry is None: raise _MutationError("PLAN_REFERENCE_INVALID")
        entry["evidence"].append(_evidence(value["evidence"])); entry["updated_at"] = now
        return updated, "evidence", target_id
    if operation == "link_task":
        value = _object(payload, {"work_item_id", "task_id"}); target_id = _id(value["work_item_id"], "wi"); task_id = _id(value["task_id"])
        if not task_id.startswith("pt_"): raise _MutationError("PLAN_OPERATION_PAYLOAD_INVALID")
        entry = next((x for x in updated["work_items"] if x["work_item_id"] == target_id), None)
        if entry is None: raise _MutationError("PLAN_REFERENCE_INVALID")
        if task_id in entry["linked_task_ids"]: return plan, "work_item", target_id
        entry["linked_task_ids"].append(task_id); entry["updated_at"] = now
        return updated, "work_item", target_id
    if operation == "record_decision":
        value = _object(payload, {"summary", "rationale", "status", "source", "related_milestone_ids", "related_work_item_ids", "evidence"})
        decision_id = _new_id("decision")
        updated["decisions"].append({"decision_id": decision_id, "summary": _text(value["summary"], _MAX["summary"]), "rationale": _text(value["rationale"], _MAX["rationale"]), "status": _enum(value["status"], DECISION_STATUSES), "source": _enum(value["source"], DECISION_SOURCES), "related_milestone_ids": _id_list(value["related_milestone_ids"], "ms"), "related_work_item_ids": _id_list(value["related_work_item_ids"], "wi"), "evidence": [_evidence(item) for item in _list(value["evidence"], 50)], "created_at": now})
        return updated, "decision", decision_id
    if operation == "set_decision_status":
        value = _object(payload, {"decision_id", "status"}); target_id = _id(value["decision_id"], "pd"); entry = next((x for x in updated["decisions"] if x["decision_id"] == target_id), None)
        if entry is None: raise _MutationError("PLAN_REFERENCE_INVALID")
        status = _enum(value["status"], DECISION_STATUSES); validate_decision_transition(entry["status"], status); entry["status"] = status
        return updated, "decision", target_id
    raise _MutationError("PLAN_OPERATION_INVALID")


def _do_create(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = args.get("workspace_id") if isinstance(args, dict) else None
    try:
        workspace_id, context, root = _authorize(workspace_id)
        values = _validate_create(args)
        base = _plan_root(root)
        plan_id = generate_plan_id()
        for _ in range(3):
            if not (base / plan_id).exists(): break
            plan_id = generate_plan_id()
        else: raise _MutationError("PLAN_ALREADY_EXISTS")
        assert_no_unresolved_plan_audit(workspace_id=workspace_id, plan_id=plan_id)
        plan = create_plan(plan_id=plan_id, **{key: values[key] for key in ("title", "goal", "planning_depth", "architect_gate", "delivery_path", "non_goals", "current_state")})
        plan["next_action"] = values["next_action"]; plan["plan_sha256"] = compute_plan_sha256(plan); plan = validate_plan(plan)
        projection = render_plan_markdown(plan)
        try:
            audit = prepare_plan_mutation_audit(context=context, workspace_id=workspace_id, plan_id=plan_id, operation="create_plan", target_type="plan", target_id=plan_id, previous_revision=None, expected_revision=None)
        except PlanAuditError as exc: raise _MutationError("PLAN_AUDIT_GAP") from exc
        temporary = Path(tempfile.mkdtemp(dir=base, prefix=".plan_create_"))
        try:
            _write_file(temporary / PLAN_FILE, _canonical_json(plan)); _write_file(temporary / PROJECTION_FILE, projection)
            os.replace(temporary, base / plan_id)
        except Exception:
            try: shutil.rmtree(temporary)
            except OSError: pass
            raise _MutationError(_abort_or_gap(workspace_id, audit["audit_event_id"]))
        try:
            commit_plan_mutation_audit(workspace_id=workspace_id, audit_event_id=audit["audit_event_id"], new_revision=1)
        except PlanAuditError:
            _reconcile(workspace_id, audit["audit_event_id"])
            return {"status": "audit_reconciliation_required", "workspace_id": workspace_id, "plan_id": plan_id, "previous_revision": None, "revision": 1, "canonical_updated": True, "audit_committed": False}
        return {"status": "created", "workspace_id": workspace_id, "plan_id": plan_id, "revision": 1, "plan_status": "draft", "planning_depth": plan["planning_depth"], "architect_gate": plan["architect_gate"], "delivery_path": plan["delivery_path"], "projection_status": "current"}
    except _MutationError as exc: return _error(exc.code, workspace_id if isinstance(workspace_id, str) else None)
    except PlanAuditError: return _error("PLAN_AUDIT_GAP", workspace_id if isinstance(workspace_id, str) else None)
    except Exception: return _error("PLAN_WRITE_FAILED", workspace_id if isinstance(workspace_id, str) else None)


def _do_update(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = args.get("workspace_id") if isinstance(args, dict) else None; plan_id = args.get("plan_id") if isinstance(args, dict) else None
    try:
        workspace_id, context, root = _authorize(workspace_id)
        try: plan_id = validate_plan_id(plan_id)
        except PlanError as exc: raise _MutationError("PLAN_ID_INVALID") from exc
        expected = args.get("expected_revision")
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1: raise _MutationError("PLAN_REVISION_CONFLICT")
        operation = args.get("operation")
        if operation not in UPDATE_SCHEMA["parameters"]["properties"]["operation"]["enum"]: raise _MutationError("PLAN_OPERATION_INVALID")
        payload = args.get("payload")
        try:
            plan_file = _resolve_plan_file(root, plan_id); plan = _verify_and_validate(_read_plan_json(plan_file), plan_id)
        except _PlanOpenError as exc: raise _MutationError(exc.code) from exc
        if plan["revision"] != expected: raise _MutationError("PLAN_REVISION_CONFLICT")
        assert_no_unresolved_plan_audit(workspace_id=workspace_id, plan_id=plan_id)
        updated, target_type, target_id = _operation(plan, operation, payload)
        if updated is plan:
            return {"status": "already_linked", "workspace_id": workspace_id, "plan_id": plan_id, "operation": operation, "previous_revision": plan["revision"], "revision": plan["revision"], "target_id": target_id, "projection_status": "current"}
        updated["revision"] = plan["revision"] + 1; updated["updated_at"] = utc_now(); updated["plan_sha256"] = compute_plan_sha256(updated)
        try: updated = validate_plan(updated)
        except PlanError as exc: raise _MutationError(_map_plan_error(exc)) from exc
        projection = render_plan_markdown(updated)
        try:
            audit = prepare_plan_mutation_audit(context=context, workspace_id=workspace_id, plan_id=plan_id, operation=operation, target_type=target_type, target_id=target_id, previous_revision=plan["revision"], expected_revision=expected)
        except PlanAuditError as exc: raise _MutationError("PLAN_AUDIT_GAP") from exc
        try: _write_file(plan_file, _canonical_json(updated))
        except Exception: raise _MutationError(_abort_or_gap(workspace_id, audit["audit_event_id"]))
        try: commit_plan_mutation_audit(workspace_id=workspace_id, audit_event_id=audit["audit_event_id"], new_revision=updated["revision"])
        except PlanAuditError:
            _reconcile(workspace_id, audit["audit_event_id"])
            return {"status": "audit_reconciliation_required", "workspace_id": workspace_id, "plan_id": plan_id, "previous_revision": plan["revision"], "revision": updated["revision"], "canonical_updated": True, "audit_committed": False}
        try: _write_file(plan_file.parent / PROJECTION_FILE, projection)
        except Exception: return {"status": "updated_projection_stale", "workspace_id": workspace_id, "plan_id": plan_id, "previous_revision": plan["revision"], "revision": updated["revision"], "canonical_updated": True, "audit_committed": True, "projection_updated": False}
        return {"status": "updated", "workspace_id": workspace_id, "plan_id": plan_id, "operation": operation, "previous_revision": plan["revision"], "revision": updated["revision"], "target_id": target_id, "projection_status": "current"}
    except _MutationError as exc: return _error(exc.code, workspace_id if isinstance(workspace_id, str) else None, plan_id if isinstance(plan_id, str) else None)
    except PlanAuditError as exc: return _error("PLAN_AUDIT_RECONCILIATION_REQUIRED" if exc.error_code == "PLAN_AUDIT_RECONCILIATION_REQUIRED" else "PLAN_AUDIT_GAP", workspace_id if isinstance(workspace_id, str) else None, plan_id if isinstance(plan_id, str) else None)
    except Exception: return _error("PLAN_WRITE_FAILED", workspace_id if isinstance(workspace_id, str) else None, plan_id if isinstance(plan_id, str) else None)


def handle_create(args: dict, **_kwargs: Any) -> str:
    return json.dumps(_do_create(args), ensure_ascii=False, sort_keys=True)


def handle_update(args: dict, **_kwargs: Any) -> str:
    return json.dumps(_do_update(args), ensure_ascii=False, sort_keys=True)


def run_isolated_smoke() -> dict[str, str]:
    """Temporary registered-workspace smoke; controlled env is restored on exit."""
    from . import _workspace
    prior_registry = _workspace._REGISTRY_PATH
    names = ("AOTA_TRUSTED_PRINCIPAL", "AOTA_TRUSTED_AUTHORITIES", "AOTA_TRUSTED_WORKSPACE_ID", "AOTA_PROFILE_TASK_ID")
    prior_env = {name: os.environ.get(name) for name in names}
    with tempfile.TemporaryDirectory() as raw:
        root, registry = Path(raw) / "workspace", Path(raw) / "workspaces.json"; root.mkdir(); registry.write_text(json.dumps({"fixture": {"candidates": [str(root)]}}), encoding="utf-8")
        _workspace._REGISTRY_PATH = registry
        try:
            for name in names: os.environ.pop(name, None)
            assert _do_create({"workspace_id": "fixture", "title": "x", "planning_depth": "P1", "architect_gate": "A1", "delivery_path": "standard", "goal": "x"})["error_code"] == "ORCHESTRATOR_CONTEXT_UNAVAILABLE"
            os.environ.update({"AOTA_TRUSTED_PRINCIPAL": "task-main", "AOTA_TRUSTED_AUTHORITIES": "plan_write", "AOTA_TRUSTED_WORKSPACE_ID": "fixture"})
            created = _do_create({"workspace_id": "fixture", "title": "Plan", "planning_depth": "P1", "architect_gate": "A1", "delivery_path": "standard", "goal": "Goal"}); assert created["status"] == "created"
            plan_id = created["plan_id"]; updated = _do_update({"workspace_id": "fixture", "plan_id": plan_id, "expected_revision": 1, "operation": "update_current_state", "payload": {"current_state": "state"}}); assert updated["status"] == "updated" and updated["revision"] == 2
            assert _do_update({"workspace_id": "fixture", "plan_id": plan_id, "expected_revision": 1, "operation": "update_current_state", "payload": {"current_state": "again"}})["error_code"] == "PLAN_REVISION_CONFLICT"
            assert _do_create({"workspace_id": "fixture", "title": "x", "planning_depth": "P0", "architect_gate": "A0", "delivery_path": "fast", "goal": "x"})["error_code"] == "PLAN_NOT_APPLICABLE_FOR_P0"
            os.environ["AOTA_PROFILE_TASK_ID"] = "pt_fixture"
            assert _do_update({"workspace_id": "fixture", "plan_id": plan_id, "expected_revision": 2, "operation": "set_plan_next_action", "payload": {"next_action": "x"}})["error_code"] == "PLAN_WRITE_FORBIDDEN_FOR_WORKER"
        finally:
            _workspace._REGISTRY_PATH = prior_registry
            for name, value in prior_env.items():
                if value is None: os.environ.pop(name, None)
                else: os.environ[name] = value
    return {"status": "PASS", "runtime_activation": "NOT_CONFIGURED"}
