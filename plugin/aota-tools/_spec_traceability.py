"""Trusted Plan → Task SPEC traceability contract.

This module owns only the bounded reference contract.  It reuses the Plan
reader/validator and never calls MCP tool handlers or mutates Plan artifacts.
"""
from __future__ import annotations

import hmac
from typing import Any, Mapping

from ._plan_common import PlanError, _identifier
from ._plan_open import _PlanOpenError, _read_plan_json, _resolve_plan_file, _verify_and_validate
from ._workspace import WorkspaceError, resolve_workspace
from ._task_spec_common import utc_now_iso

TRACEABILITY_MODES = frozenset({"standalone", "plan_linked"})
_TRACE_FIELDS = frozenset({
    "source_type", "plan_id", "plan_revision", "plan_sha256", "milestone_id",
    "work_item_id", "architect_review_id", "verified_at", "verification_status",
})
_CREATE_DRAFT_PLAN = frozenset({"draft", "approved", "in_progress", "human_checkpoint", "blocked"})
_CREATE_DRAFT_MILESTONE = frozenset({"planned", "ready", "in_progress", "human_checkpoint", "blocked"})
_CREATE_DRAFT_WORK_ITEM = frozenset({"planned", "ready", "in_progress", "needs_fix", "needs_input", "human_checkpoint", "blocked"})
_FREEZE_PLAN = frozenset({"approved", "in_progress", "blocked"})
_FREEZE_MILESTONE = frozenset({"ready", "in_progress", "blocked"})
_FREEZE_WORK_ITEM = frozenset({"ready", "in_progress", "needs_fix", "blocked"})


def _fail(code: str) -> None:
    raise WorkspaceError(code)


def _map_plan_error(error: _PlanOpenError) -> None:
    mapping = {
        "PLAN_NOT_FOUND": "SPEC_SOURCE_PLAN_NOT_FOUND",
        "PLAN_SHA_MISMATCH": "SPEC_SOURCE_PLAN_SHA_MISMATCH",
        "PLAN_ID_MISMATCH": "SPEC_SOURCE_PLAN_ID_MISMATCH",
    }
    _fail(mapping.get(error.code, "SPEC_SOURCE_PLAN_INVALID"))


def _read_validated_plan(workspace_id: str, plan_id: str) -> dict[str, Any]:
    try:
        root = resolve_workspace(workspace_id)
        plan = _verify_and_validate(_read_plan_json(_resolve_plan_file(root, plan_id)), plan_id)
    except _PlanOpenError as error:
        _map_plan_error(error)
    except (PlanError, WorkspaceError):
        raise
    except Exception as error:
        raise WorkspaceError("SPEC_SOURCE_PLAN_INVALID") from error
    return plan


def _find_source(plan: Mapping[str, Any], milestone_id: str, work_item_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    milestone = next((entry for entry in plan["milestones"] if entry["milestone_id"] == milestone_id), None)
    if milestone is None:
        _fail("SPEC_SOURCE_MILESTONE_NOT_FOUND")
    work_item = next((entry for entry in plan["work_items"] if entry["work_item_id"] == work_item_id), None)
    if work_item is None:
        _fail("SPEC_SOURCE_WORK_ITEM_NOT_FOUND")
    if work_item["milestone_id"] != milestone_id:
        _fail("SPEC_SOURCE_WORK_ITEM_MILESTONE_MISMATCH")
    return milestone, work_item


def _require_states(plan: Mapping[str, Any], milestone: Mapping[str, Any], work_item: Mapping[str, Any], *, freeze: bool) -> None:
    plan_states = _FREEZE_PLAN if freeze else _CREATE_DRAFT_PLAN
    milestone_states = _FREEZE_MILESTONE if freeze else _CREATE_DRAFT_MILESTONE
    work_item_states = _FREEZE_WORK_ITEM if freeze else _CREATE_DRAFT_WORK_ITEM
    if plan["status"] not in plan_states:
        _fail("SPEC_SOURCE_PLAN_STATE_INVALID")
    if milestone["status"] not in milestone_states:
        _fail("SPEC_SOURCE_MILESTONE_STATE_INVALID")
    if work_item["status"] not in work_item_states:
        _fail("SPEC_SOURCE_WORK_ITEM_STATE_INVALID")


def _validate_review_id(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return _identifier(value, "architect_review_id")
    except PlanError as error:
        raise WorkspaceError("SPEC_SOURCE_ARCHITECT_REVIEW_ID_INVALID") from error


def validate_traceability_input(value: Any, *, required: bool = False) -> dict[str, Any] | None:
    if value is None:
        if required:
            _fail("SPEC_TRACEABILITY_REQUIRED_FIELDS_MISSING")
        return None
    if not isinstance(value, dict):
        _fail("SPEC_TRACEABILITY_PAYLOAD_INVALID")
    mode = value.get("mode")
    if mode not in TRACEABILITY_MODES:
        _fail("SPEC_TRACEABILITY_MODE_INVALID")
    if mode == "standalone":
        if set(value) != {"mode"}:
            _fail("SPEC_TRACEABILITY_FORBIDDEN_FOR_STANDALONE")
        return None
    required_fields = {"mode", "plan_id", "milestone_id", "work_item_id"}
    if set(value) not in (required_fields, required_fields | {"architect_review_id"}):
        _fail("SPEC_TRACEABILITY_PAYLOAD_INVALID")
    try:
        return {
            "plan_id": _identifier(value["plan_id"], "plan_id", "plan"),
            "milestone_id": _identifier(value["milestone_id"], "milestone_id", "milestone"),
            "work_item_id": _identifier(value["work_item_id"], "work_item_id", "work_item"),
            "architect_review_id": _validate_review_id(value.get("architect_review_id")),
        }
    except KeyError as error:
        raise WorkspaceError("SPEC_TRACEABILITY_REQUIRED_FIELDS_MISSING") from error
    except PlanError as error:
        raise WorkspaceError("SPEC_TRACEABILITY_PAYLOAD_INVALID") from error


def build_trusted_snapshot(workspace_id: str, traceability: dict[str, Any]) -> dict[str, Any]:
    plan = _read_validated_plan(workspace_id, traceability["plan_id"])
    milestone, work_item = _find_source(plan, traceability["milestone_id"], traceability["work_item_id"])
    _require_states(plan, milestone, work_item, freeze=False)
    return {
        "source_type": "aota_forge_plan",
        "plan_id": plan["plan_id"],
        "plan_revision": plan["revision"],
        "plan_sha256": plan["plan_sha256"],
        "milestone_id": milestone["milestone_id"],
        "work_item_id": work_item["work_item_id"],
        "architect_review_id": traceability["architect_review_id"],
        "verified_at": utc_now_iso(),
        "verification_status": "verified",
    }


def validate_snapshot_for_freeze(workspace_id: str, snapshot: Any) -> None:
    if not isinstance(snapshot, dict) or set(snapshot) != _TRACE_FIELDS:
        _fail("SPEC_SOURCE_PLAN_INVALID")
    if snapshot.get("source_type") != "aota_forge_plan" or snapshot.get("verification_status") != "verified":
        _fail("SPEC_SOURCE_PLAN_INVALID")
    traceability = validate_traceability_input({
        "mode": "plan_linked", "plan_id": snapshot.get("plan_id"),
        "milestone_id": snapshot.get("milestone_id"), "work_item_id": snapshot.get("work_item_id"),
        "architect_review_id": snapshot.get("architect_review_id"),
    }, required=True)
    assert traceability is not None
    plan = _read_validated_plan(workspace_id, traceability["plan_id"])
    milestone, work_item = _find_source(plan, traceability["milestone_id"], traceability["work_item_id"])
    _require_states(plan, milestone, work_item, freeze=True)
    if work_item["spec_preflight"] == "required" and not snapshot["architect_review_id"]:
        _fail("SPEC_SOURCE_ARCHITECT_REVIEW_REQUIRED")
    if plan["revision"] != snapshot.get("plan_revision") or not hmac.compare_digest(plan["plan_sha256"], snapshot.get("plan_sha256", "")):
        _fail("SPEC_SOURCE_PLAN_ADVANCED")


def traceability_mode(snapshot: Any) -> str:
    return "plan_linked" if snapshot is not None else "standalone"


def task_lineage(snapshot: Any) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return {key: snapshot[key] for key in ("plan_id", "plan_revision", "milestone_id", "work_item_id")}


def run_isolated_smoke() -> dict[str, str]:
    from ._plan_common import compute_plan_sha256, create_plan
    plan = create_plan(title="Trace", goal="Trace smoke", plan_id="plan_trace", timestamp="2026-07-14T00:00:00Z")
    milestone = {"milestone_id": "ms_one", "title": "M", "status": "ready", "objective": "O", "dependencies": [], "acceptance_criteria": [], "evidence": [], "architect_review_id": None, "created_at": "2026-07-14T00:00:00Z", "updated_at": "2026-07-14T00:00:00Z"}
    item = {"work_item_id": "wi_one", "milestone_id": "ms_one", "title": "W", "goal": "G", "status": "ready", "risk_level": "high", "architect_gate": "A2", "architect_review_id": None, "spec_preflight": "required", "dependencies": [], "linked_task_ids": [], "evidence": [], "next_action": None, "created_at": "2026-07-14T00:00:00Z", "updated_at": "2026-07-14T00:00:00Z"}
    plan.update(status="approved", milestones=[milestone], work_items=[item], active_milestone_id="ms_one", active_work_item_id="wi_one")
    plan["plan_sha256"] = compute_plan_sha256(plan)
    assert traceability_mode(None) == "standalone"
    assert validate_traceability_input({"mode": "standalone"}) is None
    try:
        validate_traceability_input({"mode": "standalone", "plan_id": "plan_trace"})
        raise AssertionError("standalone accepted Plan fields")
    except WorkspaceError as error:
        assert str(error) == "SPEC_TRACEABILITY_FORBIDDEN_FOR_STANDALONE"
    return {"status": "PASS", "scope": "traceability_input_and_snapshot_contract"}
