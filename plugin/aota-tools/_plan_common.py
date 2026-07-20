"""Canonical Plan v1 domain contract (no MCP tool registration).

P0 flows may bypass Plan artifacts. P1/P2 flows use plan.json as canonical
machine state; PLAN.md is a deterministic projection only. Formal runtime-root
resolution is intentionally deferred to a later work item.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import tempfile
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = 1
PLAN_FILE = "plan.json"
PROJECTION_FILE = "PLAN.md"

PLAN_STATUSES = frozenset({
    "draft", "approved", "in_progress", "human_checkpoint", "blocked",
    "completed", "cancelled", "superseded",
})
MILESTONE_STATUSES = frozenset({
    "planned", "ready", "in_progress", "human_checkpoint", "blocked",
    "closed", "cancelled", "superseded",
})
WORK_ITEM_STATUSES = frozenset({
    "planned", "ready", "in_progress", "execution_completed",
    "review_required", "needs_fix", "needs_input", "human_checkpoint",
    "blocked", "closed", "cancelled", "superseded",
})
DECISION_STATUSES = frozenset({"proposed", "accepted", "rejected", "superseded"})
DECISION_SOURCES = frozenset({
    "user", "task_main", "architect_review", "reviewer", "runtime_evidence",
})
PLANNING_DEPTHS = frozenset({"P1", "P2"})
ARCHITECT_GATES = frozenset({"A0", "A1", "A2"})
DELIVERY_PATHS = frozenset({"fast", "standard", "deep"})
RISK_LEVELS = frozenset({"low", "medium", "high"})
SPEC_PREFLIGHTS = frozenset({"optional", "required", "not_required"})
EVIDENCE_TYPES = frozenset({
    "task", "handoff", "architect_review", "review", "runtime", "document",
})

_PLAN_FIELDS = frozenset({
    "schema_version", "plan_id", "title", "status", "planning_depth",
    "architect_gate", "delivery_path", "revision", "created_at", "updated_at",
    "plan_sha256", "goal", "non_goals", "current_state", "milestones",
    "work_items", "decisions", "active_milestone_id", "active_work_item_id",
    "next_action", "workspace_context",
})
_MILESTONE_FIELDS = frozenset({
    "milestone_id", "title", "status", "objective", "dependencies",
    "acceptance_criteria", "evidence", "architect_review_id", "created_at",
    "updated_at",
})
_WORK_ITEM_FIELDS = frozenset({
    "work_item_id", "milestone_id", "title", "goal", "status", "risk_level",
    "architect_gate", "architect_review_id", "spec_preflight", "dependencies",
    "linked_task_ids", "evidence", "next_action", "created_at", "updated_at",
})
_DECISION_FIELDS = frozenset({
    "decision_id", "summary", "rationale", "status", "source",
    "related_milestone_ids", "related_work_item_ids", "evidence", "created_at",
})
_EVIDENCE_FIELDS = frozenset({"evidence_type", "reference_id", "summary", "recorded_at"})

_MAX = {
    "id": 100, "title": 200, "goal": 8000, "current_state": 8000,
    "next_action": 1000, "summary": 4000, "rationale": 4000,
    "acceptance": 1000, "reference": 300, "array": 200, "milestones": 100,
    "work_items": 500, "decisions": 500, "evidence": 50,
}
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,99}$")
_PREFIX_RE = {
    "plan": re.compile(r"^plan_[A-Za-z0-9][A-Za-z0-9_-]{0,94}$"),
    "milestone": re.compile(r"^ms_[A-Za-z0-9][A-Za-z0-9_-]{0,96}$"),
    "work_item": re.compile(r"^wi_[A-Za-z0-9][A-Za-z0-9_-]{0,96}$"),
    "decision": re.compile(r"^pd_[A-Za-z0-9][A-Za-z0-9_-]{0,96}$"),
}

_PLAN_TRANSITIONS = {
    "draft": {"approved", "cancelled"},
    "approved": {"in_progress", "cancelled", "superseded"},
    "in_progress": {"human_checkpoint", "blocked", "completed", "cancelled", "superseded"},
    "human_checkpoint": {"in_progress", "cancelled"},
    "blocked": {"in_progress", "cancelled"},
    "completed": {"superseded"},
    "cancelled": set(), "superseded": set(),
}
_MILESTONE_TRANSITIONS = {
    "planned": {"ready", "cancelled"},
    "ready": {"in_progress", "blocked", "cancelled"},
    "in_progress": {"human_checkpoint", "blocked", "closed", "cancelled"},
    "human_checkpoint": {"in_progress", "blocked", "cancelled"},
    "blocked": {"ready", "cancelled"},
    "closed": {"superseded"}, "cancelled": set(), "superseded": set(),
}
_WORK_ITEM_TRANSITIONS = {
    "planned": {"ready", "cancelled"},
    "ready": {"in_progress", "blocked", "cancelled"},
    "in_progress": {"execution_completed", "needs_input", "human_checkpoint", "blocked", "cancelled"},
    "execution_completed": {"review_required", "closed", "blocked"},
    "review_required": {"closed", "needs_fix", "blocked"},
    "needs_fix": {"ready", "blocked", "cancelled"},
    "needs_input": {"ready", "blocked", "cancelled"},
    "human_checkpoint": {"in_progress", "blocked", "cancelled"},
    "blocked": {"ready", "cancelled"},
    "closed": {"superseded"}, "cancelled": set(), "superseded": set(),
}
_DECISION_TRANSITIONS = {
    "proposed": {"accepted", "rejected", "superseded"},
    "accepted": {"superseded"},
    "rejected": {"superseded"},
    "superseded": set(),
}


class PlanError(ValueError):
    """Plan contract validation failure."""


class PlanRevisionConflict(PlanError):
    """Optimistic revision mismatch; no mutation may be persisted."""

    def __init__(self) -> None:
        super().__init__("PLAN_REVISION_CONFLICT")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_plan_id() -> str:
    return f"plan_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S')}_{secrets.token_hex(4)}"


def _reject_control(value: str, name: str) -> None:
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise PlanError(f"{name} contains control characters")


def _text(value: Any, name: str, maximum: int, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise PlanError(f"{name} must be a string")
    if required and not value:
        raise PlanError(f"{name} is required")
    if len(value) > maximum:
        raise PlanError(f"{name} exceeds {maximum} characters")
    _reject_control(value, name)
    return value


def _nullable_text(value: Any, name: str, maximum: int) -> None:
    if value is not None:
        _text(value, name, maximum, required=False)


def _identifier(value: Any, name: str, kind: str | None = None) -> str:
    value = _text(value, name, _MAX["id"])
    pattern = _PREFIX_RE[kind] if kind else _ID_RE
    if not pattern.fullmatch(value):
        raise PlanError(f"invalid {name}")
    return value


def validate_plan_id(plan_id: Any) -> str:
    """Validate one externally supplied Plan ID against the v1 contract."""
    return _identifier(plan_id, "plan_id", "plan")


def _timestamp(value: Any, name: str) -> str:
    value = _text(value, name, 20)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise PlanError(f"invalid {name}") from exc
    if parsed.tzinfo is not None:
        raise PlanError(f"invalid {name}")
    return value


def _enum(value: Any, name: str, allowed: frozenset[str]) -> str:
    value = _text(value, name, 40)
    if value not in allowed:
        raise PlanError(f"unknown {name}: {value}")
    return value


def _strict_mapping(value: Any, name: str, fields: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PlanError(f"{name} must be an object")
    keys = set(value)
    if keys != fields:
        missing, extra = fields - keys, keys - fields
        raise PlanError(f"{name} fields mismatch: missing={sorted(missing)}, extra={sorted(extra)}")
    return value


def _list(value: Any, name: str, maximum: int) -> list[Any]:
    if not isinstance(value, list):
        raise PlanError(f"{name} must be an array")
    if len(value) > maximum:
        raise PlanError(f"{name} exceeds {maximum} items")
    return value


def _string_list(value: Any, name: str, maximum: int, item_maximum: int) -> list[str]:
    result = _list(value, name, maximum)
    return [_text(item, f"{name}[]", item_maximum) for item in result]


def _id_list(value: Any, name: str, maximum: int) -> list[str]:
    result = [_identifier(item, f"{name}[]") for item in _list(value, name, maximum)]
    if len(set(result)) != len(result):
        raise PlanError(f"{name} contains duplicate IDs")
    return result


def _validate_evidence(value: Any, name: str) -> None:
    entries = _list(value, name, _MAX["evidence"])
    for entry in entries:
        entry = _strict_mapping(entry, f"{name}[]", _EVIDENCE_FIELDS)
        _enum(entry["evidence_type"], "evidence_type", EVIDENCE_TYPES)
        _identifier(entry["reference_id"], "reference_id")
        _text(entry["summary"], "evidence summary", _MAX["summary"])
        _timestamp(entry["recorded_at"], "recorded_at")


def _validate_optional_reference(value: Any, name: str) -> None:
    if value is not None:
        _identifier(value, name)


def _unique_ids(entries: list[Any], key: str, name: str) -> set[str]:
    ids = [entry[key] for entry in entries]
    if len(set(ids)) != len(ids):
        raise PlanError(f"duplicate {name} ID")
    return set(ids)


def _validate_dag(entries: list[Mapping[str, Any]], id_key: str, label: str) -> None:
    graph = {entry[id_key]: set(entry["dependencies"]) for entry in entries}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise PlanError(f"{label} dependency cycle")
        if node in visited:
            return
        visiting.add(node)
        for parent in graph[node]:
            visit(parent)
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)


def validate_plan_transition(current: str, target: str) -> None:
    _enum(current, "current plan status", PLAN_STATUSES)
    _enum(target, "target plan status", PLAN_STATUSES)
    if target == current or target not in _PLAN_TRANSITIONS[current]:
        raise PlanError(f"illegal plan status transition: {current} -> {target}")


def validate_milestone_transition(current: str, target: str) -> None:
    _enum(current, "current milestone status", MILESTONE_STATUSES)
    _enum(target, "target milestone status", MILESTONE_STATUSES)
    if target == current or target not in _MILESTONE_TRANSITIONS[current]:
        raise PlanError(f"illegal milestone status transition: {current} -> {target}")


def validate_work_item_transition(current: str, target: str) -> None:
    _enum(current, "current work item status", WORK_ITEM_STATUSES)
    _enum(target, "target work item status", WORK_ITEM_STATUSES)
    if target == current or target not in _WORK_ITEM_TRANSITIONS[current]:
        raise PlanError(f"illegal work item status transition: {current} -> {target}")


def validate_decision_transition(current: str, target: str) -> None:
    """Validate one non-idempotent canonical Decision v1 status transition."""
    _enum(current, "current decision status", DECISION_STATUSES)
    _enum(target, "target decision status", DECISION_STATUSES)
    if target == current or target not in _DECISION_TRANSITIONS[current]:
        raise PlanError(f"illegal decision status transition: {current} -> {target}")


def _validate_milestone(value: Any) -> Mapping[str, Any]:
    entry = _strict_mapping(value, "milestone", _MILESTONE_FIELDS)
    _identifier(entry["milestone_id"], "milestone_id", "milestone")
    _text(entry["title"], "milestone title", _MAX["title"])
    _enum(entry["status"], "milestone status", MILESTONE_STATUSES)
    _text(entry["objective"], "milestone objective", _MAX["goal"])
    _id_list(entry["dependencies"], "milestone dependencies", _MAX["array"])
    _string_list(entry["acceptance_criteria"], "acceptance criteria", _MAX["array"], _MAX["acceptance"])
    _validate_evidence(entry["evidence"], "milestone evidence")
    _validate_optional_reference(entry["architect_review_id"], "architect_review_id")
    _timestamp(entry["created_at"], "milestone created_at")
    _timestamp(entry["updated_at"], "milestone updated_at")
    return entry


def _validate_work_item(value: Any) -> Mapping[str, Any]:
    entry = _strict_mapping(value, "work item", _WORK_ITEM_FIELDS)
    _identifier(entry["work_item_id"], "work_item_id", "work_item")
    _identifier(entry["milestone_id"], "work item milestone_id", "milestone")
    _text(entry["title"], "work item title", _MAX["title"])
    _text(entry["goal"], "work item goal", _MAX["goal"])
    _enum(entry["status"], "work item status", WORK_ITEM_STATUSES)
    _enum(entry["risk_level"], "risk_level", RISK_LEVELS)
    _enum(entry["architect_gate"], "work item architect_gate", ARCHITECT_GATES)
    _validate_optional_reference(entry["architect_review_id"], "work item architect_review_id")
    _enum(entry["spec_preflight"], "spec_preflight", SPEC_PREFLIGHTS)
    _id_list(entry["dependencies"], "work item dependencies", _MAX["array"])
    _id_list(entry["linked_task_ids"], "linked_task_ids", _MAX["array"])
    _validate_evidence(entry["evidence"], "work item evidence")
    _nullable_text(entry["next_action"], "work item next_action", _MAX["next_action"])
    _timestamp(entry["created_at"], "work item created_at")
    _timestamp(entry["updated_at"], "work item updated_at")
    return entry


def _validate_decision(value: Any) -> Mapping[str, Any]:
    entry = _strict_mapping(value, "decision", _DECISION_FIELDS)
    _identifier(entry["decision_id"], "decision_id", "decision")
    _text(entry["summary"], "decision summary", _MAX["summary"])
    _text(entry["rationale"], "decision rationale", _MAX["rationale"])
    _enum(entry["status"], "decision status", DECISION_STATUSES)
    _enum(entry["source"], "decision source", DECISION_SOURCES)
    _id_list(entry["related_milestone_ids"], "related_milestone_ids", _MAX["array"])
    _id_list(entry["related_work_item_ids"], "related_work_item_ids", _MAX["array"])
    _validate_evidence(entry["evidence"], "decision evidence")
    _timestamp(entry["created_at"], "decision created_at")
    return entry


def canonical_plan_payload(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached hash payload that excludes the self-referential digest."""
    if not isinstance(plan, Mapping):
        raise PlanError("plan must be an object")
    return {key: copy.deepcopy(value) for key, value in plan.items() if key != "plan_sha256"}


def canonical_plan_json(plan: Mapping[str, Any]) -> str:
    return json.dumps(canonical_plan_payload(plan), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compute_plan_sha256(plan: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_plan_json(plan).encode("utf-8")).hexdigest()


def validate_plan(plan: Any) -> dict[str, Any]:
    """Strictly validate canonical Plan v1 and its deterministic SHA."""
    entry = _strict_mapping(plan, "plan", _PLAN_FIELDS)
    if entry["schema_version"] != SCHEMA_VERSION:
        raise PlanError("unsupported schema version")
    _identifier(entry["plan_id"], "plan_id", "plan")
    _text(entry["title"], "title", _MAX["title"])
    _enum(entry["status"], "plan status", PLAN_STATUSES)
    _enum(entry["planning_depth"], "planning_depth", PLANNING_DEPTHS)
    _enum(entry["architect_gate"], "architect_gate", ARCHITECT_GATES)
    _enum(entry["delivery_path"], "delivery_path", DELIVERY_PATHS)
    if not isinstance(entry["revision"], int) or isinstance(entry["revision"], bool) or entry["revision"] < 1:
        raise PlanError("revision must be a positive integer")
    _timestamp(entry["created_at"], "created_at")
    _timestamp(entry["updated_at"], "updated_at")
    _text(entry["plan_sha256"], "plan_sha256", 64)
    if not re.fullmatch(r"[0-9a-f]{64}", entry["plan_sha256"]):
        raise PlanError("invalid plan_sha256")
    _text(entry["goal"], "goal", _MAX["goal"])
    _string_list(entry["non_goals"], "non_goals", _MAX["array"], _MAX["goal"])
    _text(entry["current_state"], "current_state", _MAX["current_state"], required=False)
    _nullable_text(entry["next_action"], "next_action", _MAX["next_action"])
    context = entry["workspace_context"]
    if context is not None:
        required_context = {"workspace_id", "project_id", "decision_id", "relationship"}
        if not isinstance(context, Mapping) or set(context) != required_context or not all(isinstance(context[key], str) and context[key] for key in required_context):
            raise PlanError("workspace_context invalid")

    milestones = [_validate_milestone(item) for item in _list(entry["milestones"], "milestones", _MAX["milestones"])]
    work_items = [_validate_work_item(item) for item in _list(entry["work_items"], "work_items", _MAX["work_items"])]
    decisions = [_validate_decision(item) for item in _list(entry["decisions"], "decisions", _MAX["decisions"])]
    milestone_ids = _unique_ids(milestones, "milestone_id", "milestone")
    work_item_ids = _unique_ids(work_items, "work_item_id", "work item")
    _unique_ids(decisions, "decision_id", "decision")

    for milestone in milestones:
        dependencies = set(milestone["dependencies"])
        if milestone["milestone_id"] in dependencies:
            raise PlanError("milestone self-dependency")
        missing = dependencies - milestone_ids
        if missing:
            raise PlanError(f"missing milestone dependency: {sorted(missing)}")
    _validate_dag(milestones, "milestone_id", "milestone")

    for item in work_items:
        if item["milestone_id"] not in milestone_ids:
            raise PlanError("work item references missing milestone")
        dependencies = set(item["dependencies"])
        if item["work_item_id"] in dependencies:
            raise PlanError("work item self-dependency")
        missing = dependencies - work_item_ids
        if missing:
            raise PlanError(f"missing work item dependency: {sorted(missing)}")
    _validate_dag(work_items, "work_item_id", "work item")

    for decision in decisions:
        if not set(decision["related_milestone_ids"]).issubset(milestone_ids):
            raise PlanError("decision references missing milestone")
        if not set(decision["related_work_item_ids"]).issubset(work_item_ids):
            raise PlanError("decision references missing work item")

    active_milestone_id = entry["active_milestone_id"]
    if active_milestone_id is not None:
        _identifier(active_milestone_id, "active_milestone_id", "milestone")
        active_milestone = next((x for x in milestones if x["milestone_id"] == active_milestone_id), None)
        if active_milestone is None:
            raise PlanError("active milestone does not exist")
        if active_milestone["status"] in {"closed", "cancelled", "superseded"}:
            raise PlanError("active milestone is terminal")

    active_work_item_id = entry["active_work_item_id"]
    if active_work_item_id is not None:
        _identifier(active_work_item_id, "active_work_item_id", "work_item")
        active_work_item = next((x for x in work_items if x["work_item_id"] == active_work_item_id), None)
        if active_work_item is None:
            raise PlanError("active work item does not exist")
        if active_work_item["status"] in {"closed", "cancelled", "superseded"}:
            raise PlanError("active work item is terminal")
        if active_milestone_id is not None and active_work_item["milestone_id"] != active_milestone_id:
            raise PlanError("active work item is outside active milestone")

    if entry["plan_sha256"] != compute_plan_sha256(entry):
        raise PlanError("plan_sha256 does not match canonical payload")
    return copy.deepcopy(dict(entry))


def create_plan(*, title: str, goal: str, planning_depth: str = "P1", architect_gate: str = "A1", delivery_path: str = "standard", non_goals: list[str] | None = None, current_state: str = "", workspace_context: dict[str, Any] | None = None, plan_id: str | None = None, timestamp: str | None = None) -> dict[str, Any]:
    """Create a minimal, validated canonical P1/P2 Plan at revision 1."""
    now = timestamp or utc_now()
    plan = {
        "schema_version": SCHEMA_VERSION,
        "plan_id": plan_id or generate_plan_id(),
        "title": title,
        "status": "draft",
        "planning_depth": planning_depth,
        "architect_gate": architect_gate,
        "delivery_path": delivery_path,
        "revision": 1,
        "created_at": now,
        "updated_at": now,
        "plan_sha256": "0" * 64,
        "goal": goal,
        "non_goals": [] if non_goals is None else non_goals,
        "current_state": current_state,
        "milestones": [], "work_items": [], "decisions": [],
        "active_milestone_id": None, "active_work_item_id": None, "next_action": None, "workspace_context": workspace_context,
    }
    plan["plan_sha256"] = compute_plan_sha256(plan)
    return validate_plan(plan)


def _next_revision(plan: Mapping[str, Any], expected_revision: int, mutate: Any, timestamp: str | None = None) -> dict[str, Any]:
    current = validate_plan(plan)
    if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision != current["revision"]:
        raise PlanRevisionConflict()
    updated = copy.deepcopy(current)
    mutate(updated)
    updated["revision"] = current["revision"] + 1
    updated["updated_at"] = timestamp or utc_now()
    updated["plan_sha256"] = compute_plan_sha256(updated)
    return validate_plan(updated)


def apply_plan_operation(plan: Mapping[str, Any], *, expected_revision: int, operation: str, value: Any, timestamp: str | None = None) -> dict[str, Any]:
    """Apply one bounded domain operation; arbitrary JSON replacement is forbidden."""
    if operation not in {
        "set_plan_status", "set_current_state", "set_next_action", "set_active_milestone",
        "set_active_work_item", "add_milestone", "add_work_item", "add_decision",
        "set_milestone_status", "set_work_item_status",
    }:
        raise PlanError("unknown bounded plan operation")

    def mutate(updated: dict[str, Any]) -> None:
        if operation == "set_plan_status":
            validate_plan_transition(updated["status"], value)
            updated["status"] = value
        elif operation == "set_current_state":
            _text(value, "current_state", _MAX["current_state"], required=False)
            updated["current_state"] = value
        elif operation == "set_next_action":
            _nullable_text(value, "next_action", _MAX["next_action"])
            updated["next_action"] = value
        elif operation == "set_active_milestone":
            if value is not None:
                _identifier(value, "active_milestone_id", "milestone")
            updated["active_milestone_id"] = value
        elif operation == "set_active_work_item":
            if value is not None:
                _identifier(value, "active_work_item_id", "work_item")
            updated["active_work_item_id"] = value
        elif operation == "add_milestone":
            updated["milestones"].append(copy.deepcopy(value))
        elif operation == "add_work_item":
            updated["work_items"].append(copy.deepcopy(value))
        elif operation == "add_decision":
            updated["decisions"].append(copy.deepcopy(value))
        else:
            if not isinstance(value, Mapping) or set(value) != {"id", "status"}:
                raise PlanError("status operation requires id and status only")
            entries = updated["milestones"] if operation == "set_milestone_status" else updated["work_items"]
            id_key = "milestone_id" if operation == "set_milestone_status" else "work_item_id"
            entry = next((item for item in entries if item[id_key] == value["id"]), None)
            if entry is None:
                raise PlanError("status operation references missing item")
            validator = validate_milestone_transition if operation == "set_milestone_status" else validate_work_item_transition
            validator(entry["status"], value["status"])
            entry["status"] = value["status"]
            entry["updated_at"] = timestamp or utc_now()

    return _next_revision(plan, expected_revision, mutate, timestamp)


def _atomic_write(path: Path, content: str) -> None:
    fd, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
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


def _trusted_artifact_dir(artifact_dir: Path) -> Path:
    if not isinstance(artifact_dir, Path) or not artifact_dir.is_dir():
        raise PlanError("trusted plan artifact directory must already exist")
    return artifact_dir


def write_plan_artifacts(artifact_dir: Path, plan: Mapping[str, Any]) -> None:
    """Atomically write canonical plan.json then its disposable PLAN.md projection.

    The caller must supply an already resolved, trusted directory. This helper
    does not accept model-provided paths and never creates directory trees.
    """
    artifact_dir = _trusted_artifact_dir(artifact_dir)
    validated = validate_plan(plan)
    canonical = json.dumps(validated, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    _atomic_write(artifact_dir / PLAN_FILE, canonical)
    _atomic_write(artifact_dir / PROJECTION_FILE, render_plan_markdown(validated))


def load_plan(artifact_dir: Path) -> dict[str, Any]:
    artifact_dir = _trusted_artifact_dir(artifact_dir)
    try:
        with (artifact_dir / PLAN_FILE).open("r", encoding="utf-8") as handle:
            return validate_plan(json.load(handle))
    except json.JSONDecodeError as exc:
        raise PlanError("canonical plan.json is invalid JSON") from exc


def _md(value: str) -> str:
    return value.replace("\\", "\\\\").replace("`", "\\`").replace("*", "\\*").replace("_", "\\_").replace("[", "\\[").replace("]", "\\]").replace("#", "\\#").replace("|", "\\|")


def _render_list(items: list[Any], empty: str = "- None") -> list[str]:
    return empty.splitlines() if not items else [f"- {_md(str(item))}" for item in items]


def render_plan_markdown(plan: Mapping[str, Any]) -> str:
    """Render a deterministic, one-way human projection from canonical JSON."""
    plan = validate_plan(plan)
    lines = [
        f"# {_md(plan['title'])}", "", f"- Plan ID: `{plan['plan_id']}`",
        f"- Status: `{plan['status']}`", f"- Planning Depth: `{plan['planning_depth']}`",
        f"- Architect Gate: `{plan['architect_gate']}`", f"- Delivery Path: `{plan['delivery_path']}`",
        f"- Revision: {plan['revision']}", f"- Updated At: `{plan['updated_at']}`", "",
        "## Goal", _md(plan["goal"]), "", "## Non-goals", *_render_list(plan["non_goals"]), "",
        "## Current State", _md(plan["current_state"]) if plan["current_state"] else "- None", "",
        "## Milestones",
    ]
    lines.extend(["- None"] if not plan["milestones"] else [f"- `{item['milestone_id']}` — {_md(item['title'])} ({item['status']})" for item in plan["milestones"]])
    lines.extend(["", "## Work Items"])
    lines.extend(["- None"] if not plan["work_items"] else [f"- `{item['work_item_id']}` — {_md(item['title'])} ({item['status']})" for item in plan["work_items"]])
    lines.extend(["", "## Decisions"])
    lines.extend(["- None"] if not plan["decisions"] else [f"- `{item['decision_id']}` — {_md(item['summary'])} ({item['status']})" for item in plan["decisions"]])
    lines.extend([
        "", "## Active Work", f"- Active Milestone: `{plan['active_milestone_id'] or 'None'}`",
        f"- Active Work Item: `{plan['active_work_item_id'] or 'None'}`", "", "## Next Action",
        _md(plan["next_action"]) if plan["next_action"] else "- None", "",
    ])
    return "\n".join(lines)


def _fixture_milestone(timestamp: str, status: str = "planned") -> dict[str, Any]:
    return {"milestone_id": "ms_one", "title": "Foundation", "status": status, "objective": "Establish contract", "dependencies": [], "acceptance_criteria": ["Contract validates"], "evidence": [], "architect_review_id": None, "created_at": timestamp, "updated_at": timestamp}


def _fixture_work_item(timestamp: str, status: str = "planned", dependencies: list[str] | None = None) -> dict[str, Any]:
    return {"work_item_id": "wi_one", "milestone_id": "ms_one", "title": "Contract", "goal": "Create Plan contract", "status": status, "risk_level": "high", "architect_gate": "A2", "architect_review_id": None, "spec_preflight": "required", "dependencies": dependencies or [], "linked_task_ids": [], "evidence": [], "next_action": None, "created_at": timestamp, "updated_at": timestamp}


def run_isolated_smoke() -> dict[str, str]:
    """Fixed-fixture source-level smoke; no runtime root, tool, or deployment."""
    timestamp = "2026-07-13T00:00:00Z"
    plan = create_plan(title="PF-WI-01", goal="Canonical Plan contract", plan_id="plan_fixture", timestamp=timestamp)
    assert plan["schema_version"] == 1 and plan["revision"] == 1
    assert compute_plan_sha256(plan) == plan["plan_sha256"]
    projection = render_plan_markdown(plan)
    assert projection == render_plan_markdown(plan)
    assert "## Milestones\n- None" in projection and "## Work Items\n- None" in projection
    assert create_plan(title="PF-WI-01", goal="Canonical Plan contract", plan_id="plan_fixture", timestamp=timestamp)["plan_sha256"] == plan["plan_sha256"]

    updated = apply_plan_operation(plan, expected_revision=1, operation="set_next_action", value="Review contract", timestamp="2026-07-13T00:01:00Z")
    assert updated["revision"] == 2 and updated["plan_sha256"] != plan["plan_sha256"]
    try:
        apply_plan_operation(updated, expected_revision=1, operation="set_next_action", value=None, timestamp=timestamp)
        raise AssertionError("revision conflict accepted")
    except PlanRevisionConflict:
        pass
    for immutable in ("plan_id", "created_at", "schema_version"):
        altered = copy.deepcopy(plan)
        altered[immutable] = "bad" if immutable != "schema_version" else 2
        try:
            validate_plan(altered)
            raise AssertionError(f"immutable {immutable} accepted")
        except PlanError:
            pass

    plan_with_items = apply_plan_operation(plan, expected_revision=1, operation="add_milestone", value=_fixture_milestone(timestamp), timestamp=timestamp)
    plan_with_items = apply_plan_operation(plan_with_items, expected_revision=2, operation="add_work_item", value=_fixture_work_item(timestamp), timestamp=timestamp)
    plan_with_items = apply_plan_operation(plan_with_items, expected_revision=3, operation="set_active_milestone", value="ms_one", timestamp=timestamp)
    plan_with_items = apply_plan_operation(plan_with_items, expected_revision=4, operation="set_active_work_item", value="wi_one", timestamp=timestamp)
    assert plan_with_items["work_items"][0]["status"] == "planned"
    try:
        apply_plan_operation(plan_with_items, expected_revision=5, operation="set_work_item_status", value={"id": "wi_one", "status": "in_progress"}, timestamp=timestamp)
        raise AssertionError("illegal transition accepted")
    except PlanError:
        pass
    ready = apply_plan_operation(plan_with_items, expected_revision=5, operation="set_work_item_status", value={"id": "wi_one", "status": "ready"}, timestamp=timestamp)
    running = apply_plan_operation(ready, expected_revision=6, operation="set_work_item_status", value={"id": "wi_one", "status": "in_progress"}, timestamp=timestamp)
    executed = apply_plan_operation(running, expected_revision=7, operation="set_work_item_status", value={"id": "wi_one", "status": "execution_completed"}, timestamp=timestamp)
    assert executed["work_items"][0]["status"] == "execution_completed"  # completion is not closure
    try:
        apply_plan_operation(executed, expected_revision=8, operation="set_active_work_item", value="wi_missing", timestamp=timestamp)
        raise AssertionError("missing active work item accepted")
    except PlanError:
        pass
    try:
        validate_work_item_transition("closed", "in_progress")
        raise AssertionError("closed work item reopened")
    except PlanError:
        pass
    cross_milestone = copy.deepcopy(plan_with_items)
    second_milestone = _fixture_milestone(timestamp)
    second_milestone["milestone_id"] = "ms_two"
    second_work_item = _fixture_work_item(timestamp)
    second_work_item["work_item_id"] = "wi_two"
    second_work_item["milestone_id"] = "ms_two"
    cross_milestone["milestones"].append(second_milestone)
    cross_milestone["work_items"].append(second_work_item)
    cross_milestone["plan_sha256"] = compute_plan_sha256(cross_milestone)
    validate_plan(cross_milestone)
    try:
        apply_plan_operation(cross_milestone, expected_revision=5, operation="set_active_work_item", value="wi_two", timestamp=timestamp)
        raise AssertionError("cross-milestone active work item accepted")
    except PlanError:
        pass
    duplicate = copy.deepcopy(plan_with_items)
    duplicate["milestones"].append(_fixture_milestone(timestamp))
    duplicate["plan_sha256"] = compute_plan_sha256(duplicate)
    try:
        validate_plan(duplicate)
        raise AssertionError("duplicate milestone accepted")
    except PlanError:
        pass
    duplicate_work_item = copy.deepcopy(plan_with_items)
    duplicate_work_item["work_items"].append(_fixture_work_item(timestamp))
    duplicate_work_item["plan_sha256"] = compute_plan_sha256(duplicate_work_item)
    try:
        validate_plan(duplicate_work_item)
        raise AssertionError("duplicate work item accepted")
    except PlanError:
        pass
    missing_milestone = copy.deepcopy(plan)
    missing_milestone["work_items"] = [_fixture_work_item(timestamp)]
    missing_milestone["plan_sha256"] = compute_plan_sha256(missing_milestone)
    try:
        validate_plan(missing_milestone)
        raise AssertionError("missing milestone reference accepted")
    except PlanError:
        pass
    cycle = copy.deepcopy(plan_with_items)
    second = _fixture_work_item(timestamp, dependencies=["wi_one"])
    second["work_item_id"] = "wi_two"
    cycle["work_items"].append(second)
    cycle["work_items"][0]["dependencies"] = ["wi_two"]
    cycle["plan_sha256"] = compute_plan_sha256(cycle)
    try:
        validate_plan(cycle)
        raise AssertionError("dependency cycle accepted")
    except PlanError:
        pass
    oversized = create_plan(title="PF-WI-01", goal="Canonical Plan contract", timestamp=timestamp)
    oversized["title"] = "x" * 201
    oversized["plan_sha256"] = compute_plan_sha256(oversized)
    try:
        validate_plan(oversized)
        raise AssertionError("oversized title accepted")
    except PlanError:
        pass
    controls = create_plan(title="PF-WI-01", goal="Canonical Plan contract", timestamp=timestamp)
    controls["goal"] = "contains\u0001control"
    controls["plan_sha256"] = compute_plan_sha256(controls)
    try:
        validate_plan(controls)
        raise AssertionError("control character accepted")
    except PlanError:
        pass
    too_many = create_plan(title="PF-WI-01", goal="Canonical Plan contract", timestamp=timestamp)
    too_many["non_goals"] = ["bounded"] * 201
    too_many["plan_sha256"] = compute_plan_sha256(too_many)
    try:
        validate_plan(too_many)
        raise AssertionError("oversized array accepted")
    except PlanError:
        pass
    p2 = create_plan(title="P2", goal="Deep plan", planning_depth="P2", architect_gate="A2", delivery_path="deep", timestamp=timestamp)
    assert p2["delivery_path"] == "deep"  # P0 intentionally has no artifact constructor.

    with tempfile.TemporaryDirectory() as raw_dir:
        root = Path(raw_dir)
        write_plan_artifacts(root, plan_with_items)
        assert load_plan(root) == plan_with_items
        original = (root / PLAN_FILE).read_text("utf-8")
        original_replace = os.replace
        try:
            os.replace = lambda *_args: (_ for _ in ()).throw(OSError("simulated replace failure"))  # type: ignore[assignment]
            try:
                write_plan_artifacts(root, updated)
                raise AssertionError("atomic failure was not raised")
            except OSError:
                pass
        finally:
            os.replace = original_replace  # type: ignore[assignment]
        assert json.loads((root / PLAN_FILE).read_text("utf-8")) and (root / PLAN_FILE).read_text("utf-8") == original
    return {"status": "PASS", "p0_policy": "P0 bypasses Plan artifact", "formal_runtime_root": "PLAN_RUNTIME_ROOT_DEFERRED"}


if __name__ == "__main__":
    print(json.dumps(run_isolated_smoke(), sort_keys=True))
