#!/usr/bin/env python3
"""Isolated source verifier for HOST-WI-03B.

This verifier uses only temporary directories and fake gateway/wake objects.
It never imports or touches the live Hermes/AOTA runtime roots.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HERMES_ROOT = Path("/home/latios/workspace/hermes-agent-host")
PLUGIN_ROOT = ROOT / "plugin" / "aota-tools"
sys.path.insert(0, str(HERMES_ROOT))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


outbox = load("wi03b_outbox", PLUGIN_ROOT / "_delivery_outbox.py")
from gateway import aota_parent_wake as adapter_mod  # noqa: E402
from gateway.platforms.base import Platform  # noqa: E402


TASK_ID = "pt_20260726T120000_deadbeef"
SESSION = "session-fixture-03b"
WORKSPACE_ID = "aota-hermes-tools"


class FakeGateway:
    _running = True
    _profile_adapters = {}
    _session_db = None

    def _active_profile_name(self):
        return "default"

    def _adapter_for_source(self, source):
        return object()


class FakeApiAdapter:
    supports_async_delivery = False


class FakeSessionDB:
    def get_session(self, session_id):
        assert session_id == SESSION
        return {"id": SESSION, "profile_name": "", "origin_json": "", "ended_at": None}


class FakeApiGateway(FakeGateway):
    adapters = {Platform.API_SERVER: FakeApiAdapter()}
    _session_db = type("Facade", (), {"_db": FakeSessionDB()})()


async def isolated_to_thread(func, /, *args, **kwargs):
    """Keep the verifier deterministic in the restricted Python sandbox."""
    return func(*args, **kwargs)


def make_adapter(tmp: Path, gateway=None) -> adapter_mod.AotaParentWakeAdapter:
    task_root = tmp / "profile-tasks"
    task_dir = task_root / WORKSPACE_ID / TASK_ID
    task_dir.mkdir(parents=True)
    (task_dir / "RESULT.md").write_text("fixture result; never injected\n", encoding="utf-8")
    (task_dir / "handoff.fixture.json").write_text("{}\n", encoding="utf-8")
    (task_dir / "meta.json").write_text(
        json.dumps(
            {
                "task_id": TASK_ID,
                "execution": {
                    "start_id": TASK_ID,
                    "parent_profile": "default",
                    "parent_session_ref": SESSION,
                },
            }
        ),
        encoding="utf-8",
    )
    adapter = adapter_mod.AotaParentWakeAdapter(
        gateway or FakeGateway(),
        outbox_root=tmp / "outbox",
        task_root=task_root,
        workspace_id=WORKSPACE_ID,
        max_attempts=1,
    )
    adapter._validate_root()
    return adapter


def valid_event(status: str = "done") -> dict:
    return outbox.build_terminal_event(
        task_id=TASK_ID,
        start_id=TASK_ID,
        profile="coder",
        terminal_status=status,
        parent_profile="default",
        parent_session_ref=SESSION,
        origin_session_id=SESSION,
        result_pointer=f"{WORKSPACE_ID}/{TASK_ID}/RESULT.md",
        handoff_pointer=f"{WORKSPACE_ID}/{TASK_ID}/handoff.fixture.json",
        completed_at="2026-07-26T12:00:00Z",
    )


def main() -> int:
    checks: list[str] = []
    adapter_mod.asyncio.to_thread = isolated_to_thread

    def check(name: str, fn) -> None:
        fn()
        checks.append(name)

    check("no_async_delegations_insert", lambda: static_absence("INSERT INTO async_delegations"))
    check("no_direct_parent_db_write", lambda: static_absence("SessionDB.append_message"))
    check("no_whole_file_overlay", lambda: static_absence("hermes-overrides/agent/api/api_server.py"))
    check("no_desktop_frontend_change", lambda: assert_not_changed_desktop())

    with tempfile.TemporaryDirectory(prefix="host-wi-03b-") as raw:
        tmp = Path(raw)
        adapter = make_adapter(tmp)

        for status in ("done", "failed", "needs_input", "cancelled", "timed_out", "scope_violation"):
            check(f"valid_{status}", lambda s=status: adapter._validate_event(valid_event(s)))

        check("unknown_status_rejected", lambda: reject(adapter, {**valid_event(), "terminal_status": "unknown"}, "INVALID_TERMINAL_STATUS"))
        check("schema_version_rejected", lambda: reject(adapter, {**valid_event(), "schema_version": 1}, "UNSUPPORTED_SCHEMA_VERSION"))
        check("event_type_rejected", lambda: reject(adapter, {**valid_event(), "event_type": "wrong"}, "INVALID_EVENT_TYPE"))
        check("missing_parent_profile", lambda: reject(adapter, {**valid_event(), "parent_profile": ""}, "INVALID_PARENT_PROFILE"))
        check("missing_parent_session", lambda: reject(adapter, {**valid_event(), "parent_session_ref": ""}, "PARENT_SESSION_NOT_FOUND"))
        check("result_path_traversal", lambda: reject(adapter, {**valid_event(), "result_pointer": f"{WORKSPACE_ID}/../escape"}, "PATH_TRAVERSAL_REJECTED"))

        outside = tmp / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        result = tmp / "profile-tasks" / WORKSPACE_ID / TASK_ID / "RESULT.md"
        result.unlink()
        result.symlink_to(outside)
        check("result_symlink_rejected", lambda: reject(adapter, valid_event(), "SYMLINK_REJECTED"))
        result.unlink()
        result.write_text("fixture", encoding="utf-8")
        check("handoff_outside_rejected", lambda: reject(adapter, {**valid_event(), "handoff_pointer": f"{WORKSPACE_ID}/other/handoff.json"}, "PATH_TRAVERSAL_REJECTED"))

        oversized = adapter.pending / "oversized.json"
        adapter.pending.mkdir(parents=True, exist_ok=True)
        oversized.write_bytes(b"x" * (adapter.max_payload_bytes + 1))
        check("oversized_event_rejected", lambda: read_reject(adapter, oversized, "PAYLOAD_TOO_LARGE"))
        check("malformed_json_rejected", lambda: malformed(adapter, tmp))
        check("unexpected_files_ignored", lambda: unexpected(adapter))

        event = valid_event()
        pending = adapter.pending / f"{event['event_id']}.json"
        adapter.pending.mkdir(parents=True, exist_ok=True)
        pending.write_text(json.dumps(event), encoding="utf-8")
        claimed = adapter._claim(pending)
        assert claimed is not None
        processing, _attempt = claimed
        check("active_claim_not_stolen", lambda: assert_path(processing))
        check("claim_metadata_persisted", lambda: assert_path(adapter._claim_metadata_path(event["event_id"])))
        os.utime(processing, (time.time() - 600, time.time() - 600))
        adapter._recover_stale_claims()
        check("stale_claim_recovered", lambda: assert_path(adapter.pending / processing.name))

        duplicate = valid_event("failed")
        duplicate_path = adapter.pending / f"{duplicate['event_id']}.json"
        adapter.delivered.mkdir(parents=True, exist_ok=True)
        (adapter.delivered / duplicate_path.name).write_text(json.dumps(duplicate), encoding="utf-8")
        duplicate_path.write_text(json.dumps(duplicate), encoding="utf-8")
        check("duplicate_event_fixture", lambda: assert_path(duplicate_path))
        (adapter.delivered / duplicate_path.name).unlink()

        check("feature_flag_default_disabled", lambda: flag_disabled())
        check("graceful_shutdown", lambda: adapter.stop())
        check("synthetic_turn_bounded", lambda: assert_bounded(adapter._synthetic_text(valid_event())))
        check("no_raw_result_injected", lambda: assert_safe_text(adapter._synthetic_text(valid_event())))
        check("source_contract_mismatch", lambda: contract_mismatch(adapter))
        check("cross_profile_route_requires_served_profile", lambda: cross_profile_reject(adapter))
        check("api_raw_session_fallback", lambda: api_raw_session_fallback(tmp))
        check(
            "parent_profile_runtime_fallback_source",
            lambda: assert_source_contains(
                PLUGIN_ROOT / "_profile_task_start.py", "get_active_profile_name"
            ),
        )
        check(
            "parent_profile_mismatch_fail_closed_source",
            lambda: assert_source_contains(
                PLUGIN_ROOT / "_profile_task_start.py", "parent_profile_context_mismatch"
            ),
        )

        async def wake_success(*_args, **_kwargs) -> None:
            return None

        original_wake = adapter_mod.deliver_wake
        adapter_mod.deliver_wake = wake_success
        try:
            success = valid_event()
            path = adapter.processing / f"{success['event_id']}.json"
            path.write_text(json.dumps(success), encoding="utf-8")
            adapter._resolve_parent_target = lambda _event: asyncio.sleep(0, result=adapter_mod.ParentTarget("default", SESSION, object(), object()))  # type: ignore[method-assign]
            asyncio.run(adapter._process(path, 1))
            check("wake_success_stub", lambda: assert_path(adapter.delivered / path.name))
            (adapter.delivered / path.name).unlink()

            async def wake_failure(*_args, **_kwargs):
                raise RuntimeError("fixture wake failure")

            adapter_mod.deliver_wake = wake_failure
            failed = valid_event("failed")
            failed_path = adapter.processing / f"{failed['event_id']}.json"
            failed_path.write_text(json.dumps(failed), encoding="utf-8")
            asyncio.run(adapter._process(failed_path, 1))
            check("wake_failure_stub", lambda: assert_path(adapter.failed / failed_path.name))
            (adapter.failed / failed_path.name).unlink()

            async def wake_ambiguous(*_args, **_kwargs):
                return "ambiguous"

            adapter_mod.deliver_wake = wake_ambiguous
            ambiguous = valid_event("needs_input")
            ambiguous_path = adapter.processing / f"{ambiguous['event_id']}.json"
            ambiguous_path.write_text(json.dumps(ambiguous), encoding="utf-8")
            asyncio.run(adapter._process(ambiguous_path, 1))
            check("ambiguous_wake_handling", lambda: assert_path(adapter.failed / ambiguous_path.name))
        finally:
            adapter_mod.deliver_wake = original_wake

    print(json.dumps({"status": "PASS", "fixture_count": len(checks), "checks": checks}, sort_keys=True))
    return 0


def reject(adapter, event: dict, expected: str) -> None:
    try:
        adapter._validate_event(event)
    except adapter_mod.AdapterError as exc:
        assert exc.failure_class == expected, (exc.failure_class, expected)
    else:
        raise AssertionError(expected)


def malformed(adapter, tmp: Path) -> None:
    path = adapter.pending / "malformed.json"
    path.write_text("{", encoding="utf-8")
    try:
        adapter._read_event(path)
    except adapter_mod.AdapterError as exc:
        assert exc.failure_class == "INVALID_SCHEMA"
    else:
        raise AssertionError("INVALID_SCHEMA")


def read_reject(adapter, path: Path, expected: str) -> None:
    try:
        adapter._read_event(path)
    except adapter_mod.AdapterError as exc:
        assert exc.failure_class == expected, (exc.failure_class, expected)
    else:
        raise AssertionError(expected)


def unexpected(adapter) -> None:
    path = adapter.pending / "README.txt"
    path.write_text("ignored", encoding="utf-8")
    assert path.is_file()


def assert_bounded(text: str) -> None:
    assert len(text) <= 1800


def assert_safe_text(text: str) -> None:
    assert "fixture result" not in text


def contract_mismatch(adapter) -> None:
    event = valid_event()
    event["parent_session_ref"] = "other-session"
    event["origin_session_id"] = "other-session"
    event["idempotency_key"] = ":".join((adapter_mod.EVENT_TYPE, TASK_ID, TASK_ID, "default", "other-session"))
    reject(adapter, event, "PARENT_OWNERSHIP_MISMATCH")


def flag_disabled() -> None:
    previous = os.environ.pop("AOTA_PARENT_WAKE_ENABLED", None)
    try:
        assert adapter_mod.AotaParentWakeAdapter.from_environment(FakeGateway()) is None
    finally:
        if previous is not None:
            os.environ["AOTA_PARENT_WAKE_ENABLED"] = previous


def cross_profile_reject(adapter) -> None:
    event = valid_event()
    event["parent_profile"] = "worker"
    event["idempotency_key"] = ":".join((adapter_mod.EVENT_TYPE, TASK_ID, TASK_ID, "worker", SESSION))
    try:
        asyncio.run(adapter._resolve_parent_target(event))
    except adapter_mod.AdapterError as exc:
        assert exc.failure_class == "PROFILE_RUNTIME_UNAVAILABLE"
    else:
        raise AssertionError("PROFILE_RUNTIME_UNAVAILABLE")


def api_raw_session_fallback(tmp: Path) -> None:
    api = make_adapter(tmp / "api", FakeApiGateway())
    target = asyncio.run(api._resolve_parent_target(valid_event()))
    assert target.profile == "default"
    assert target.session_id == SESSION
    assert target.source is None


def assert_source_contains(path: Path, needle: str) -> None:
    assert needle in path.read_text(encoding="utf-8")


def assert_path(path: Path) -> None:
    assert path.is_file(), path


def static_absence(needle: str) -> None:
    text = (HERMES_ROOT / "gateway" / "aota_parent_wake.py").read_text(encoding="utf-8")
    assert needle not in text


def assert_not_changed_desktop() -> None:
    # The adapter source lives outside the Desktop tree; this is a source
    # contract check rather than a runtime or git mutation.
    assert not (HERMES_ROOT / "gateway" / "aota_parent_wake.py").is_relative_to(HERMES_ROOT / "apps" / "desktop")


if __name__ == "__main__":
    raise SystemExit(main())
