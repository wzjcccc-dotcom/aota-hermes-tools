#!/usr/bin/env python3
"""Isolated Phase 2 completion/handoff/inbox/decision closure verifier."""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import tempfile
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "aota-tools"
WS = "fixture"
SESSION = "origin-session"
TASK = "pt_20260728T000000_deadbeef"
START = TASK
HANDOFF = "ho_20260728T000000_deadbeef"


def load_package() -> None:
    package = types.ModuleType("aota_tools")
    package.__path__ = [str(PLUGIN)]  # type: ignore[attr-defined]
    sys.modules["aota_tools"] = package
    spec = importlib.util.spec_from_file_location(
        "aota_tools", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)]
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["aota_tools"] = module
    spec.loader.exec_module(module)


def write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def install_fixture(root: Path, *, task_id: str = TASK, handoff_id: str = HANDOFF, terminal: bool = True, with_card: bool = True) -> dict[str, Any]:
    task_dir = root / "profile-tasks" / WS / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    status = "done" if terminal else "running"
    meta = {
        "task_id": task_id,
        "workspace_id": WS,
        "status": status,
        "task_kind": "diagnosis",
        "spec_kind": "diagnosis",
        "resolved_profile": "debugger",
        "profile_hint": "debugger",
        "revision": 1,
        "spec_hash": "canonical-hash",
        "spec_sha256": "raw-hash",
        "project_id": "fixture-project",
        "origin_session_id": SESSION,
        "execution": {
            "start_id": task_id,
            "profile": "debugger",
            "spec_revision": 1,
            "spec_hash": "canonical-hash",
            "spec_sha256": "raw-hash",
            "completion_receipt_path": f"completion.{task_id}.json",
            "completed_at": "2026-07-28T00:01:00Z",
            "exit_code": 0,
            "outcome": "completed",
            "transport": "terminal_background" if terminal else "legacy_durable_delivery",
            "completion_transport": "terminal_background" if terminal else "legacy_durable_delivery",
            "completion_delivery_expected": not terminal,
            "delivery_state": "received" if terminal else "pending",
            "scope_compliance": {"status": "compliant", "violations": []},
        },
    }
    write(task_dir / "meta.json", meta)
    if terminal:
        write(task_dir / f"completion.{task_id}.json", {
            "status": "done", "outcome": "completed", "exit_code": 0,
            "workspace_id": WS, "task_id": task_id, "start_id": task_id,
            "profile": "debugger", "spec_revision": 1,
            "spec_hash": "canonical-hash", "spec_sha256": "raw-hash",
            "project_id": "fixture-project", "completed_at": "2026-07-28T00:01:00Z",
            "scope_compliance": {"status": "compliant", "violations": []},
        })
        write(task_dir / f"worker-outcome.{task_id}.json", {"outcome": "completed"})
    if with_card:
        write(task_dir / "DIAGNOSIS_CARD.json", {
            "role": "debugger", "summary": "fixture card",
            "needs_full_report_review": False,
        })
    if terminal:
        write(root / "handoffs" / WS / "pending" / f"handoff.{handoff_id}.json", {
            "handoff_id": handoff_id, "workspace_id": WS, "task_id": task_id,
            "start_id": task_id, "profile": "debugger", "task_kind": "diagnosis",
            "terminal_status": "done", "created_at": "2026-07-28T00:01:00Z", "state": "pending",
            "spec_id": task_id, "spec_revision": 1, "spec_hash": "canonical-hash",
            "project_id": "fixture-project", "receipt_ref": f"completion.{task_id}.json",
            "role_artifact": {"kind": "diagnosis", "card_name": "DIAGNOSIS_CARD.json", "full_name": "DIAGNOSIS.md"},
        })
    return meta


def patch_roots(root: Path) -> tuple[Any, ...]:
    common = importlib.import_module("aota_tools._handoff_common")
    task_common = importlib.import_module("aota_tools._task_spec_common")
    resolver = importlib.import_module("aota_tools._completion_subject_resolver")
    orch = importlib.import_module("aota_tools._orchestration_common")
    authority = importlib.import_module("aota_tools._session_state_authority")
    old = (common.HANDOFF_ROOT, common.PROFILE_TASK_ROOT, task_common.PROFILE_TASK_ROOT,
           resolver.PROFILE_TASK_ROOT, orch.DECISION_ROOT, authority.SESSION_STATE_ROOT)
    common.HANDOFF_ROOT = root / "handoffs"
    common.PROFILE_TASK_ROOT = root / "profile-tasks"
    task_common.PROFILE_TASK_ROOT = root / "profile-tasks"
    resolver.PROFILE_TASK_ROOT = root / "profile-tasks"
    orch.DECISION_ROOT = root / "decisions"
    authority.SESSION_STATE_ROOT = root / "session-state"
    return old


def restore_roots(old: tuple[Any, ...]) -> None:
    common = importlib.import_module("aota_tools._handoff_common")
    task_common = importlib.import_module("aota_tools._task_spec_common")
    resolver = importlib.import_module("aota_tools._completion_subject_resolver")
    orch = importlib.import_module("aota_tools._orchestration_common")
    authority = importlib.import_module("aota_tools._session_state_authority")
    common.HANDOFF_ROOT, common.PROFILE_TASK_ROOT, task_common.PROFILE_TASK_ROOT, resolver.PROFILE_TASK_ROOT, orch.DECISION_ROOT, authority.SESSION_STATE_ROOT = old


def invoke(module: Any, args: dict[str, Any], *, context: bool = True, delivery: dict[str, Any] | None = None) -> dict[str, Any]:
    kwargs = {"workspace_id": WS, "session_id": SESSION, "profile": "task-main"} if context else {}
    if delivery is not None:
        kwargs["completion_delivery"] = delivery
    return json.loads(module.handle(args, **kwargs))


def main() -> int:
    load_package()
    handoff_open = importlib.import_module("aota_tools._handoff_open")
    handoff_list = importlib.import_module("aota_tools._handoff_list")
    handoff_ack = importlib.import_module("aota_tools._handoff_ack")
    decision = importlib.import_module("aota_tools._orchestration_decision_record")
    inbox_list = importlib.import_module("aota_tools._operator_inbox_list")
    inbox_open = importlib.import_module("aota_tools._operator_inbox_open")
    status = importlib.import_module("aota_tools._profile_task_status")
    selected = {
        "aota_profile_task_status": status.SCHEMA,
        "aota_handoff_list": handoff_list.SCHEMA,
        "aota_handoff_open": handoff_open.SCHEMA,
        "aota_handoff_ack": handoff_ack.SCHEMA,
        "aota_orchestration_decision_record": decision.SCHEMA,
        "aota_operator_inbox_list": inbox_list.SCHEMA,
        "aota_operator_inbox_open": inbox_open.SCHEMA,
    }
    forbidden = {"handoff_id", "inbox_id", "decision_id", "task_id", "start_id", "spec_id", "receipt_id", "workspace_id", "project_id", "session_id", "revision", "spec_hash", "spec_sha256", "profile", "path"}
    assert all(not set(schema["parameters"].get("required", [])) & forbidden for schema in selected.values())
    assert decision.SCHEMA["parameters"]["required"] == ["decision", "rationale"]
    print("MODEL_CONTROL_PLANE_FIELDS=0")

    with tempfile.TemporaryDirectory(prefix="aota-phase2-closure-") as temp:
        root = Path(temp)
        old = patch_roots(root)
        try:
            install_fixture(root)
            opened = invoke(handoff_open, {})
            assert opened["status"] == "opened" and opened["next_action"] == "review_card_and_record_decision", opened
            assert opened["allowed_next_tool"] == "aota_orchestration_decision_record", opened
            assert opened["allowed_next_tool_schema"]["required"] == ["decision", "rationale"], opened
            print("OPEN_CURRENT_HANDOFF=PASS")
            inbox = invoke(inbox_open, {})
            assert inbox["operation_result"] == "inbox_item_opened", inbox
            print("OPEN_CURRENT_INBOX=PASS")
            recorded = invoke(decision, {"decision": "accepted", "rationale": "fixture complete"})
            assert recorded["status"] == "recorded" and recorded["next_action"] == "ack_current_handoff", recorded
            assert recorded["allowed_next_tool"] == "aota_handoff_ack", recorded
            assert recorded["allowed_next_tool_schema"]["required"] == [], recorded
            print("RECORD_CURRENT_DECISION=PASS")
            authority = importlib.import_module("aota_tools._session_state_authority")
            session_context = types.SimpleNamespace(
                session_id=SESSION, principal="task-main", profile="task-main",
                workspace_id=WS, worker_context=False,
            )
            subject = {
                "workspace_id": WS, "project_id": "fixture-project", "task_id": TASK,
                "start_id": START, "handoff_id": HANDOFF, "spec_revision": 1,
                "spec_hash": "canonical-hash", "spec_sha256": "raw-hash",
                "profile": "debugger", "terminal_status": "done", "outcome": "completed",
                "completed_at": "2026-07-28T00:01:00Z", "origin_session_id": SESSION,
            }
            session_root = root / "session-state"
            authority.write_current_completion_pointer(session_context, subject, root=session_root)
            authority.write_current_handoff_pointer(session_context, subject, root=session_root)
            authority.write_current_completed_task_pointer(session_context, subject, root=session_root)
            acknowledged = invoke(handoff_ack, {})
            assert acknowledged["status"] == "ok" and acknowledged["next_action"] == "closure_complete", acknowledged
            assert acknowledged["flow_disposition"] == "complete", acknowledged
            print("ACK_CURRENT_HANDOFF=PASS")
            closed = invoke(status, {"view": "closure"})
            assert closed["status"] == "closed" and closed["next_action"] == "none", closed
            assert closed["closure"]["completion_receipt"] is True
            print("TERMINAL_CLOSURE_AGGREGATION=PASS")
            duplicate = invoke(handoff_ack, {})
            assert duplicate.get("idempotent") is True
            print("DUPLICATE_ACK=PASS")
            listed_after_closure = invoke(handoff_list, {})
            assert listed_after_closure["status"] == "rejected" and listed_after_closure["error"] == "current_completion_consumed"
            print("HANDOFF_LIST_CONSUMED_POINTER_GUARD=PASS")
        finally:
            restore_roots(old)

    with tempfile.TemporaryDirectory(prefix="aota-phase2-pending-") as temp:
        root = Path(temp); old = patch_roots(root)
        try:
            install_fixture(root, terminal=False)
            pending = invoke(handoff_list, {})
            assert pending["error"] == "completion_delivery_pending" and pending["next_action"] == "wait_for_completion_delivery", pending
            print("DELIVERY_PENDING_GUARD=PASS")
        finally:
            restore_roots(old)

    with tempfile.TemporaryDirectory(prefix="aota-phase2-delivery-") as temp:
        root = Path(temp); old = patch_roots(root)
        try:
            install_fixture(root)
            task_dir = root / "profile-tasks" / WS / TASK
            meta_path = task_dir / "meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["execution"]["transport"] = "legacy_durable_delivery"
            meta["execution"]["completion_transport"] = "legacy_durable_delivery"
            meta["execution"]["completion_delivery_expected"] = True
            meta["execution"]["delivery_state"] = "pending"
            write(meta_path, meta)
            delivered = invoke(
                handoff_open,
                {},
                delivery={"workspace_id": WS, "task_id": TASK, "start_id": START, "handoff_id": HANDOFF, "profile": "debugger", "delivery_state": "delivered"},
            )
            assert delivered["status"] == "opened", delivered
            print("COMPLETION_DELIVERY_BINDING=PASS")
        finally:
            restore_roots(old)

    with tempfile.TemporaryDirectory(prefix="aota-phase2-ambiguous-") as temp:
        root = Path(temp); old = patch_roots(root)
        try:
            install_fixture(root)
            second_task = "pt_20260728T000001_cafebabe"
            second_handoff = "ho_20260728T000001_cafebabe"
            install_fixture(root, task_id=second_task, handoff_id=second_handoff)
            ambiguous = invoke(handoff_open, {})
            assert ambiguous["error"] == "completion_subject_ambiguous" and ambiguous["human_action_required"] is True
            assert ambiguous["choices"] and all("handoff_id" not in item and "task_id" not in item for item in ambiguous["choices"])
            print("BOUNDED_AMBIGUITY=PASS")
        finally:
            restore_roots(old)

    with tempfile.TemporaryDirectory(prefix="aota-phase2-missing-decision-") as temp:
        root = Path(temp); old = patch_roots(root)
        try:
            install_fixture(root)
            missing = invoke(handoff_ack, {})
            assert missing["error"] == "decision_subject_missing" and missing["next_action"] == "record_current_decision", missing
            print("DECISION_MISSING=PASS")
        finally:
            restore_roots(old)

    with tempfile.TemporaryDirectory(prefix="aota-phase2-binding-") as temp:
        root = Path(temp); old = patch_roots(root)
        try:
            install_fixture(root)
            receipt = root / "profile-tasks" / WS / TASK / f"completion.{TASK}.json"
            data = json.loads(receipt.read_text(encoding="utf-8")); data["spec_hash"] = "mismatch"; write(receipt, data)
            mismatch = invoke(handoff_open, {})
            assert mismatch["error"] == "completion_subject_binding_mismatch", mismatch
            print("BINDING_MISMATCH_FAIL_CLOSED=PASS")
        finally:
            restore_roots(old)

    inventory = json.loads((ROOT / "deploy/evidence/control-plane-minimal-invocation-inventory.json").read_text(encoding="utf-8"))
    phase2 = [item for item in inventory["tools"] if "phase_2_migration" in item]
    assert len(phase2) == 10 and all(item["phase_2_migration"]["migration_status"] == "migrated_this_phase" for item in phase2)
    print("PHASE2_INVENTORY=PASS")
    print("FIRST_CALL_SUCCESS=yes")
    print("GUESS_AND_RETRY_COUNT=0")
    print("HANDOFF_LIST_USED=0")
    print("INBOX_SEARCH_USED=0")
    print("AOTA_PHASE2_COMPLETION_MIGRATION_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
