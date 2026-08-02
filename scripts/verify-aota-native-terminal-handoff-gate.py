#!/usr/bin/env python3
"""Isolated terminal-only start and historical completion-gate verifier.

This verifier creates only temporary task, handoff, receipt, card, and outbox
fixtures.  It never starts a Worker, mutates the live outbox, or calls a
Gateway/ProcessRegistry runtime.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "aota-tools"
HOST = Path("/home/latios/workspace/hermes-agent-host")
WS = "fixture-workspace"
TASK = "pt_20260730T000000_deadbeef"
START = TASK
HANDOFF = "ho_20260730T000000_deadbeef"
SESSION = "origin-session"


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
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def patch_roots(root: Path) -> tuple[Any, ...]:
    common = __import__("aota_tools._handoff_common", fromlist=["*"])
    task_common = __import__("aota_tools._task_spec_common", fromlist=["*"])
    resolver = __import__("aota_tools._completion_subject_resolver", fromlist=["*"])
    outbox = __import__("aota_tools._delivery_outbox", fromlist=["*"])
    old = (
        common.HANDOFF_ROOT, common.PROFILE_TASK_ROOT, task_common.PROFILE_TASK_ROOT,
        resolver.PROFILE_TASK_ROOT, outbox.OUTBOX_ROOT,
    )
    common.HANDOFF_ROOT = root / "handoffs"
    common.PROFILE_TASK_ROOT = root / "profile-tasks"
    task_common.PROFILE_TASK_ROOT = root / "profile-tasks"
    resolver.PROFILE_TASK_ROOT = root / "profile-tasks"
    outbox.OUTBOX_ROOT = root / "outbox"
    return old


def restore_roots(old: tuple[Any, ...]) -> None:
    common = __import__("aota_tools._handoff_common", fromlist=["*"])
    task_common = __import__("aota_tools._task_spec_common", fromlist=["*"])
    resolver = __import__("aota_tools._completion_subject_resolver", fromlist=["*"])
    outbox = __import__("aota_tools._delivery_outbox", fromlist=["*"])
    (
        common.HANDOFF_ROOT, common.PROFILE_TASK_ROOT, task_common.PROFILE_TASK_ROOT,
        resolver.PROFILE_TASK_ROOT, outbox.OUTBOX_ROOT,
    ) = old


def install_fixture(root: Path, *, transport: str, status_value: str = "done") -> None:
    task_dir = root / "profile-tasks" / WS / TASK
    task_dir.mkdir(parents=True, exist_ok=True)
    expected = transport == "legacy_durable_delivery"
    meta = {
        "task_id": TASK, "workspace_id": WS, "status": status_value,
        "task_kind": "diagnosis", "spec_kind": "diagnosis",
        "resolved_profile": "debugger", "profile_hint": "debugger",
        "revision": 1, "spec_hash": "canonical-hash", "spec_sha256": "raw-hash",
        "project_id": "fixture-project", "origin_session_id": SESSION,
        "execution": {
            "start_id": START, "profile": "debugger", "spec_revision": 1,
            "spec_hash": "canonical-hash", "spec_sha256": "raw-hash",
            "completion_receipt_path": f"completion.{START}.json",
            "completed_at": "2026-07-30T00:01:00Z", "exit_code": 0,
            "outcome": "completed", "transport": transport,
            "completion_transport": transport,
            "completion_delivery_expected": expected,
            "delivery_state": "pending" if expected else "not_expected",
            "reconciliation_state": "reconciled",
        },
    }
    write(task_dir / "meta.json", meta)
    write(task_dir / f"completion.{START}.json", {
        "status": status_value, "outcome": "completed", "exit_code": 0,
        "workspace_id": WS, "task_id": TASK, "start_id": START,
        "profile": "debugger", "spec_revision": 1,
        "spec_hash": "canonical-hash", "spec_sha256": "raw-hash",
        "project_id": "fixture-project", "transport": transport,
        "scope_compliance": {"status": "compliant", "violations": []},
    })
    write(task_dir / f"worker-outcome.{START}.json", {"outcome": "completed"})
    write(task_dir / "DIAGNOSIS_CARD.json", {
        "role": "debugger", "summary": "fixture card",
        "needs_full_report_review": False,
    })
    (task_dir / "DIAGNOSIS.md").write_text("fixture result\n", encoding="utf-8")
    write(root / "handoffs" / WS / "pending" / f"handoff.{HANDOFF}.json", {
        "handoff_id": HANDOFF, "workspace_id": WS, "task_id": TASK,
        "start_id": START, "profile": "debugger", "task_kind": "diagnosis",
        "terminal_status": status_value, "created_at": "2026-07-30T00:01:00Z",
        "state": "pending", "spec_id": TASK, "spec_revision": 1,
        "spec_hash": "canonical-hash", "project_id": "fixture-project",
        "receipt_ref": f"completion.{START}.json",
        "role_artifact": {
            "kind": "diagnosis", "card_name": "DIAGNOSIS_CARD.json",
            "full_name": "DIAGNOSIS.md",
        },
    })


def invoke(handoff_open: Any, *, session: str = SESSION) -> dict[str, Any]:
    return json.loads(handoff_open.handle({}, workspace_id=WS, session_id=session, profile="task-main"))


def put_outbox(root: Path, *, state: str) -> None:
    outbox = __import__("aota_tools._delivery_outbox", fromlist=["*"])
    event_id = outbox.build_event_id(TASK, START)
    write(root / "outbox" / WS / state / f"{event_id}.json", {"event_id": event_id})


def check_source_ordering() -> None:
    launcher = (PLUGIN / "_profile_task_launcher.py").read_text(encoding="utf-8")
    process = (HOST / "tools" / "process_registry.py").read_text(encoding="utf-8")
    assert launcher.index("_run_worker(") < launcher.index("_call_finalizer(") < launcher.index("return final_exit_code")
    move = process[process.index("def _move_to_finished"):]
    assert move.index("session._completion_event.set()") < move.index("if was_running and session.notify_on_complete")
    assert move.index("if was_running and session.notify_on_complete") < move.index("self.completion_queue.put({")
    print("NATIVE_ORDERING_WORKER_FINALIZER_EXIT_QUEUE=PASS")


def check_start_binding() -> None:
    start_source = (PLUGIN / "_profile_task_start.py").read_text(encoding="utf-8")
    assert "resolve_completion_transport()" in start_source
    assert "notify_on_complete=bool(completion_transport[\"notify_on_complete\"])" in start_source
    params = start_source[start_source.index('"parameters":'):start_source.index("def _validate_origin_session_id")]
    assert '"transport"' not in params and '"completion_transport"' not in params
    sys.path.insert(0, str(HOST))
    try:
        from gateway import session_context
        token = session_context._SESSION_ASYNC_DELIVERY.set(True)
        try:
            from aota_tools._completion_observation import (
                CompletionTransportContextError,
                resolve_completion_transport,
            )
            native = resolve_completion_transport()
        finally:
            session_context._SESSION_ASYNC_DELIVERY.reset(token)
        token = session_context._SESSION_ASYNC_DELIVERY.set(False)
        try:
            try:
                resolve_completion_transport()
            except CompletionTransportContextError as exc:
                rejected_transport_error = str(exc)
            else:
                raise AssertionError("stateless start unexpectedly selected a transport")
            start_module = __import__("aota_tools._profile_task_start", fromlist=["*"])
            public_rejection = json.loads(start_module.handle({}))
        finally:
            session_context._SESSION_ASYNC_DELIVERY.reset(token)
    finally:
        sys.path.pop(0)
    assert native["completion_transport"] == "terminal_background"
    assert native["completion_delivery_expected"] is False
    assert native["notify_on_complete"] is True
    assert rejected_transport_error == "terminal_background_required"
    assert public_rejection["status"] == "rejected"
    assert public_rejection["error"] == "terminal_background_required"
    assert public_rejection["retryable"] is False
    assert public_rejection["next_action"] == "continue_in_persistent_task_main_session"
    print("TERMINAL_BACKGROUND_ONLY_START_BINDING=PASS")


def check_finalizer_and_env_binding() -> None:
    finalizer = (PLUGIN / "_profile_task_finalize.py").read_text(encoding="utf-8")
    host_run = (HOST / "gateway" / "run.py").read_text(encoding="utf-8")
    finalizer_module = __import__("aota_tools._profile_task_finalize", fromlist=["*"])
    assert finalizer.count('"transport": completion_transport') >= 2
    assert "meta_execution.get(\"completion_transport\")" in finalizer
    assert finalizer.count("_legacy_delivery_outbox_required(") >= 3
    assert finalizer_module._legacy_delivery_outbox_required({
        "execution": {
            "completion_transport": "terminal_background",
            "completion_delivery_expected": False,
        }
    }) is False
    assert finalizer_module._legacy_delivery_outbox_required({
        "execution": {
            "completion_transport": "legacy_durable_delivery",
            "completion_delivery_expected": True,
        }
    }) is True
    for retired_token in (
        "gateway.aota_parent_wake",
        "_aota_parent_wake",
        "_load_aota_host_runtime_env",
        "AOTA_PARENT_WAKE_ENABLED",
    ):
        assert retired_token not in host_run
    print("NATIVE_OUTBOX_DISABLED_AND_HOST_ADAPTER_ABSENT=PASS")


def main() -> int:
    check_source_ordering()
    load_package()
    check_start_binding()
    check_finalizer_and_env_binding()
    handoff_open = __import__("aota_tools._handoff_open", fromlist=["*"])
    status = __import__("aota_tools._profile_task_status", fromlist=["*"])
    with tempfile.TemporaryDirectory(prefix="aota-native-gate-") as raw:
        root = Path(raw)
        old = patch_roots(root)
        try:
            for terminal_status in ("done", "failed", "needs_input", "cancelled", "timeout", "scope_violation"):
                install_fixture(root, transport="terminal_background", status_value=terminal_status)
                opened = invoke(handoff_open)
                assert opened["status"] == "opened", opened
                assert opened["next_action"] == "review_card_and_record_decision", opened
                for path in (root / "outbox").rglob("*.json") if (root / "outbox").exists() else ():
                    path.unlink()
            print("NATIVE_RESULT_READY_WITHOUT_OUTBOX=PASS")

            install_fixture(root, transport="terminal_background")
            mismatch = invoke(handoff_open, session="other-session")
            assert mismatch["error"] == "completion_subject_binding_mismatch", mismatch
            print("NATIVE_CROSS_SESSION_FAIL_CLOSED=PASS")

            install_fixture(root, transport="legacy_durable_delivery")
            pending = invoke(handoff_open)
            assert pending["error"] == "completion_delivery_pending", pending
            put_outbox(root, state="delivered")
            delivered = invoke(handoff_open)
            assert delivered["status"] == "opened", delivered
            print("HISTORICAL_LEGACY_DELIVERY_GATE_PRESERVED=PASS")

            install_fixture(root, transport="terminal_background")
            meta_path = root / "profile-tasks" / WS / TASK / "meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["execution"].pop("completion_transport")
            meta["execution"].pop("transport")
            write(meta_path, meta)
            missing = invoke(handoff_open)
            assert missing["error"] == "completion_transport_context_missing", missing
            print("MISSING_TRANSPORT_AUTHORITY_FAIL_CLOSED=PASS")

            for path in (root / "outbox").rglob("*.json"):
                path.unlink()
            install_fixture(root, transport="legacy_durable_delivery")
            status_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            projection = status._build_status_dict(status_meta, TASK, WS)
            assert projection["next_action"] == "wait_for_completion_delivery", projection
            put_outbox(root, state="delivered")
            projection = status._build_status_dict(status_meta, TASK, WS)
            assert projection["next_action"] == "open_completion_handoff", projection
            print("STATUS_LEGACY_DELIVERY_PROJECTION=PASS")
        finally:
            restore_roots(old)
    print("AOTA_NATIVE_TERMINAL_HANDOFF_GATE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
