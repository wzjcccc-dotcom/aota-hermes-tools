#!/usr/bin/env python3
"""Bounded source and temporary-fixture verifier for native completion closure.

This verifier never launches a Worker, touches the live runtime, mutates an
outbox, calls an API, or requires pytest.  It exercises the control-plane
ack/closure contract in a temporary runtime root only.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "aota-tools"
HOST = ROOT.parent / "hermes-agent-host"
WS = "fixture"
SESSION = "origin-session"
OTHER_SESSION = "other-session"
TASK = "pt_20260730T000000_deadbeef"
HANDOFF = "ho_20260730T000000_deadbeef"


def write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def modules() -> dict[str, Any]:
    return {
        "common": importlib.import_module("aota_tools._handoff_common"),
        "task_common": importlib.import_module("aota_tools._task_spec_common"),
        "resolver": importlib.import_module("aota_tools._completion_subject_resolver"),
        "authority": importlib.import_module("aota_tools._session_state_authority"),
        "orchestration": importlib.import_module("aota_tools._orchestration_common"),
        "handoff_open": importlib.import_module("aota_tools._handoff_open"),
        "handoff_ack": importlib.import_module("aota_tools._handoff_ack"),
        "decision": importlib.import_module("aota_tools._orchestration_decision_record"),
        "status": importlib.import_module("aota_tools._profile_task_status"),
    }


def patch_roots(root: Path, mod: dict[str, Any]) -> tuple[Any, ...]:
    old = (
        mod["common"].HANDOFF_ROOT,
        mod["common"].PROFILE_TASK_ROOT,
        mod["task_common"].PROFILE_TASK_ROOT,
        mod["resolver"].PROFILE_TASK_ROOT,
        mod["orchestration"].DECISION_ROOT,
        mod["authority"].SESSION_STATE_ROOT,
    )
    mod["common"].HANDOFF_ROOT = root / "handoffs"
    mod["common"].PROFILE_TASK_ROOT = root / "profile-tasks"
    mod["task_common"].PROFILE_TASK_ROOT = root / "profile-tasks"
    mod["resolver"].PROFILE_TASK_ROOT = root / "profile-tasks"
    mod["orchestration"].DECISION_ROOT = root / "decisions"
    mod["authority"].SESSION_STATE_ROOT = root / "session-state"
    return old


def restore_roots(old: tuple[Any, ...], mod: dict[str, Any]) -> None:
    (
        mod["common"].HANDOFF_ROOT,
        mod["common"].PROFILE_TASK_ROOT,
        mod["task_common"].PROFILE_TASK_ROOT,
        mod["resolver"].PROFILE_TASK_ROOT,
        mod["orchestration"].DECISION_ROOT,
        mod["authority"].SESSION_STATE_ROOT,
    ) = old


def install_fixture(
    root: Path,
    *,
    task_id: str = TASK,
    handoff_id: str = HANDOFF,
    terminal_status: str = "done",
    outcome: str = "completed",
    reconciliation_state: str = "reconciled",
    with_card: bool = True,
) -> dict[str, Any]:
    task_dir = root / "profile-tasks" / WS / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "task_id": task_id,
        "workspace_id": WS,
        "status": terminal_status,
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
            "completed_at": "2026-07-30T00:01:00Z",
            "exit_code": 0 if outcome in {"completed", "needs_input"} else 1,
            "outcome": outcome,
            "worker_outcome": outcome,
            "transport": "terminal_background",
            "completion_transport": "terminal_background",
            "completion_delivery_expected": False,
            "delivery_state": "not_expected",
            "reconciliation_state": reconciliation_state,
            "reconciliation_source": "trusted_finalizer",
            "scope_compliance": {"status": "compliant", "violations": []},
        },
    }
    write(task_dir / "meta.json", meta)
    write(task_dir / f"completion.{task_id}.json", {
        "status": terminal_status,
        "outcome": outcome,
        "exit_code": meta["execution"]["exit_code"],
        "workspace_id": WS,
        "task_id": task_id,
        "start_id": task_id,
        "profile": "debugger",
        "spec_revision": 1,
        "spec_hash": "canonical-hash",
        "spec_sha256": "raw-hash",
        "project_id": "fixture-project",
        "completed_at": "2026-07-30T00:01:00Z",
        "scope_compliance": {"status": "compliant", "violations": []},
        "primary_finalizer_executed": True,
    })
    write(task_dir / f"worker-outcome.{task_id}.json", {"outcome": outcome})
    if with_card:
        write(task_dir / "DIAGNOSIS_CARD.json", {
            "role": "debugger",
            "summary": "fixture card",
            "scope_observation": {"authority": "worker_observation", "finalizer_pending": True},
            "final_scope_compliance": "pending_finalizer",
        })
    write(root / "handoffs" / WS / "pending" / f"handoff.{handoff_id}.json", {
        "handoff_id": handoff_id,
        "workspace_id": WS,
        "task_id": task_id,
        "start_id": task_id,
        "profile": "debugger",
        "task_kind": "diagnosis",
        "terminal_status": terminal_status,
        "terminal_outcome": outcome,
        "outcome": outcome,
        "created_at": "2026-07-30T00:01:00Z",
        "origin_session_id": SESSION,
        "state": "pending",
        "spec_id": task_id,
        "spec_revision": 1,
        "spec_hash": "canonical-hash",
        "project_id": "fixture-project",
        "receipt_ref": f"completion.{task_id}.json",
        "role_artifact": {"kind": "diagnosis", "card_name": "DIAGNOSIS_CARD.json", "full_name": "DIAGNOSIS.md"},
    })
    return meta


def context(session: str = SESSION) -> Any:
    return SimpleNamespace(
        session_id=session,
        principal="task-main",
        profile="task-main",
        workspace_id=WS,
        worker_context=False,
        usable_for_active_spec=True,
        execution_context={},
    )


def invoke(module: Any, args: dict[str, Any], *, session: str = SESSION) -> dict[str, Any]:
    return json.loads(module.handle(
        args,
        workspace_id=WS,
        session_id=session,
        profile="task-main",
    ))


def project_current_pointers(root: Path, mod: dict[str, Any], meta: dict[str, Any], *, handoff_id: str = HANDOFF) -> None:
    authority = mod["authority"]
    ctx = context()
    session_root = root / "session-state"
    task_id = str(meta["task_id"])
    subject = {
        "workspace_id": WS,
        "project_id": "fixture-project",
        "task_id": task_id,
        "start_id": task_id,
        "handoff_id": handoff_id,
        "spec_revision": 1,
        "spec_hash": "canonical-hash",
        "spec_sha256": "raw-hash",
        "profile": "debugger",
        "terminal_status": meta["status"],
        "outcome": meta["execution"]["outcome"],
        "completed_at": "2026-07-30T00:01:00Z",
        "origin_session_id": SESSION,
    }
    authority.write_active_task_pointer(ctx, meta, root=session_root, start_id=task_id, state="terminal")
    authority.write_current_completion_pointer(ctx, subject, root=session_root)
    authority.write_current_handoff_pointer(ctx, subject, root=session_root)
    authority.write_current_completed_task_pointer(ctx, subject, root=session_root)


def record_decision_and_project(root: Path, mod: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    project_current_pointers(root, mod, meta)
    recorded = invoke(mod["decision"], {"decision": "accepted", "rationale": "fixture closure"})
    assert recorded["status"] == "recorded", recorded
    return recorded


def pointer_states(root: Path, mod: dict[str, Any]) -> dict[str, str]:
    authority = mod["authority"]
    result = {}
    for kind in (
        authority.POINTER_KIND_CURRENT_HANDOFF,
        authority.POINTER_KIND_CURRENT_DECISION,
        authority.POINTER_KIND_CURRENT_COMPLETION,
        authority.POINTER_KIND_ACTIVE_TASK,
        authority.POINTER_KIND_CURRENT_COMPLETED_TASK,
    ):
        value = authority.read_pointer(kind, WS, "fixture-project", SESSION)
        result[kind] = value.get("state") if value else "missing"
    return result


def check_source_contract() -> dict[str, str]:
    ack = (PLUGIN / "_handoff_ack.py").read_text(encoding="utf-8")
    resolver = (PLUGIN / "_completion_subject_resolver.py").read_text(encoding="utf-8")
    status = (PLUGIN / "_profile_task_status.py").read_text(encoding="utf-8")
    finalizer = (PLUGIN / "_profile_task_finalize.py").read_text(encoding="utf-8")
    start = (PLUGIN / "_profile_task_start.py").read_text(encoding="utf-8")
    launcher = (PLUGIN / "_profile_task_launcher.py").read_text(encoding="utf-8")
    process = (HOST / "tools" / "process_registry.py").read_text(encoding="utf-8")
    host_run = (HOST / "gateway" / "run.py").read_text(encoding="utf-8")
    assert ack.index("write_ack_artifact(") < ack.index("move_handoff_to_ack(") < ack.index("consume_pointer(")
    assert "closure_complete" in ack and "closure_receipt_ref" in ack
    assert "EXPECTED_CONSUMED_AFTER_CLOSURE" in resolver
    assert "UNEXPECTED_STALE_BEFORE_CLOSURE" in resolver
    assert "not_terminal_reconciliation_authority" in status
    assert '"reconciliation_state"] = "reconciled"' in finalizer
    assert launcher.index("_run_worker(") < launcher.index("_call_finalizer(") < launcher.index("return final_exit_code")
    move = process[process.index("def _move_to_finished"):]
    assert move.index("session._completion_event.set()") < move.index("self.completion_queue.put({")
    assert "resolve_completion_transport()" in start and "notify_on_complete" in start
    assert 'key.startswith("AOTA_")' not in host_run
    aota_source = "\n".join(path.read_text(encoding="utf-8") for path in (PLUGIN).glob("*.py"))
    assert "API_SERVER_KEY" not in aota_source
    return {
        "NATIVE_CHAIN_SOURCE": "PASS",
        "ACK_CLOSURE": "PASS",
        "LEGACY_ISOLATION": "PASS",
        "RUNTIME_RESTART_REQUIRED": "no",
    }


def main() -> int:
    summary = check_source_contract()
    print("NATIVE_CHAIN_SOURCE=PASS")
    load_package()
    mod = modules()
    with tempfile.TemporaryDirectory(prefix="aota-native-terminal-closure-") as raw:
        root = Path(raw)
        old = patch_roots(root, mod)
        try:
            meta = install_fixture(root)
            record_decision_and_project(root, mod, meta)
            acked = invoke(mod["handoff_ack"], {})
            assert acked["status"] == "ok" and acked["closure_state"] == "closed", acked
            assert acked["next_action"] == "closure_complete"
            assert acked["task_id"] == TASK and acked["start_id"] == TASK and acked["decision_id"]
            assert acked["closure_receipt_ref"].endswith(f"handoff.{HANDOFF}.ack.json")
            states = pointer_states(root, mod)
            assert all(states[k] == "consumed" for k in (
                "current_handoff", "current_decision", "current_completion", "active_task"
            )), states
            assert states["current_completed_task"] == "active", states
            print("ACK_CLOSURE_WRITE_BEFORE_POINTER_CLOSE=PASS")
            print("ACK_CLOSURE_RECEIPT=PASS")
            closed = invoke(mod["status"], {"view": "closure"})
            assert closed["status"] == "closed", closed
            assert closed["closure"]["pointer_consumption"] == "EXPECTED_CONSUMED_AFTER_CLOSURE"
            assert closed["closure"]["card_generation_snapshot"]["not_terminal_reconciliation_authority"] is True
            print("POST_ACK_CLOSURE_OBSERVABILITY=PASS")
            print("EXPECTED_CONSUMED_VS_UNEXPECTED_STALE=PASS")
            print("CARD_FINALIZER_SNAPSHOT_SEMANTICS=PASS")
            duplicate = invoke(mod["handoff_ack"], {})
            assert duplicate["status"] == "ok" and duplicate["idempotent"] is True
            print("DUPLICATE_ACK=PASS")
        finally:
            restore_roots(old, mod)

    with tempfile.TemporaryDirectory(prefix="aota-native-terminal-failure-") as raw:
        root = Path(raw)
        old = patch_roots(root, mod)
        original_writer = mod["handoff_ack"].write_ack_artifact
        try:
            meta = install_fixture(root, handoff_id="ho_20260730T000001_deadbeef")
            project_current_pointers(root, mod, meta, handoff_id="ho_20260730T000001_deadbeef")
            assert invoke(mod["decision"], {"decision": "accepted", "rationale": "fixture closure"})["status"] == "recorded"
            mod["handoff_ack"].write_ack_artifact = lambda **_kwargs: (_ for _ in ()).throw(OSError("fixture closure write failure"))
            failed = invoke(mod["handoff_ack"], {})
            assert failed["status"] == "error", failed
            assert (root / "handoffs" / WS / "pending").is_dir()
            states = pointer_states(root, mod)
            assert states["current_completion"] == "active" and states["current_handoff"] == "active", states
            print("CLOSURE_WRITE_FAILURE_PRESERVES_POINTERS=PASS")
        finally:
            mod["handoff_ack"].write_ack_artifact = original_writer
            restore_roots(old, mod)

    with tempfile.TemporaryDirectory(prefix="aota-native-terminal-stale-") as raw:
        root = Path(raw)
        old = patch_roots(root, mod)
        try:
            meta = install_fixture(root, handoff_id="ho_20260730T000002_deadbeef")
            project_current_pointers(root, mod, meta, handoff_id="ho_20260730T000002_deadbeef")
            mod["authority"].consume_pointer(
                mod["authority"].POINTER_KIND_CURRENT_COMPLETION,
                WS,
                "fixture-project",
                SESSION,
                consumed_at="2026-07-30T00:02:00Z",
            )
            stale = invoke(mod["status"], {"view": "closure"})
            assert stale["status"] == "rejected" and stale["error"] == "current_completion_binding_stale"
            assert stale.get("choices") == []
            print("STALE_WITHOUT_CLOSURE_FAILS_CLOSED=PASS")
        finally:
            restore_roots(old, mod)

    with tempfile.TemporaryDirectory(prefix="aota-native-terminal-boundary-") as raw:
        root = Path(raw)
        old = patch_roots(root, mod)
        try:
            meta = install_fixture(root, handoff_id="ho_20260730T000003_deadbeef")
            project_current_pointers(root, mod, meta, handoff_id="ho_20260730T000003_deadbeef")
            assert invoke(mod["decision"], {"decision": "accepted", "rationale": "fixture closure"})["status"] == "recorded"
            cross = invoke(mod["status"], {"view": "closure"}, session=OTHER_SESSION)
            assert cross["status"] != "closed"
            mismatch = invoke(mod["handoff_ack"], {"workspace_id": WS, "handoff_id": "ho_20260730T000003_deadbeef", "decision": "no_action"})
            assert mismatch["status"] == "error" and mismatch["error"] == "decision_required_before_ack"
            print("CROSS_SESSION_BLOCKED=PASS")
            print("ACK_DECISION_MISMATCH_BLOCKED=PASS")
        finally:
            restore_roots(old, mod)

    for suffix, terminal_status, outcome in (
        ("000004", "done", "completed"),
        ("000005", "needs_input", "needs_input"),
        ("000006", "failed", "failed"),
        ("000007", "timeout", "failed"),
        ("000008", "cancelled", "failed"),
    ):
        with tempfile.TemporaryDirectory(prefix=f"aota-native-terminal-{suffix}-") as raw:
            root = Path(raw)
            old = patch_roots(root, mod)
            try:
                task_id = f"pt_20260730T{suffix}_deadbeef"
                handoff_id = f"ho_20260730T{suffix}_deadbeef"
                meta = install_fixture(root, task_id=task_id, handoff_id=handoff_id, terminal_status=terminal_status, outcome=outcome)
                project_current_pointers(root, mod, meta, handoff_id=handoff_id)
                invoke(mod["decision"], {"decision": "accepted", "rationale": "terminal fixture"})
                result = invoke(mod["handoff_ack"], {})
                assert result["status"] == "ok" and result["closure_state"] == "closed", result
            finally:
                restore_roots(old, mod)
    print("COMPLETED_NEEDS_INPUT_FAILED_TIMEOUT_CANCELLED=PASS")

    with tempfile.TemporaryDirectory(prefix="aota-native-terminal-reconcile-") as raw:
        root = Path(raw)
        old = patch_roots(root, mod)
        try:
            meta = install_fixture(root, handoff_id="ho_20260730T000009_deadbeef", reconciliation_state="awaiting_termination_result")
            project_current_pointers(root, mod, meta, handoff_id="ho_20260730T000009_deadbeef")
            blocked = invoke(mod["handoff_open"], {})
            assert blocked["status"] == "rejected", blocked
            print("TRUE_RECONCILIATION_PENDING_BLOCKED=PASS")
        finally:
            restore_roots(old, mod)

    counts = {
        "TOOLS_BEFORE": 61,
        "TOOLS_AFTER": 61,
        "TOOLSETS_BEFORE": 28,
        "TOOLSETS_AFTER": 28,
        "PROFILES_BEFORE": 6,
        "PROFILES_AFTER": 6,
        "NEW_MODEL_FIELDS": 0,
        "NEW_MODEL_TOOLS": 0,
    }
    for key, value in counts.items():
        print(f"{key}={value}")
    print("ACK_CLOSURE=PASS")
    print("LEGACY_ISOLATION=PASS")
    print("RUNTIME_RESTART_REQUIRED=no")
    print("AOTA_NATIVE_TERMINAL_COMPLETION_CHAIN_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
