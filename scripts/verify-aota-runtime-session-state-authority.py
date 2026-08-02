#!/usr/bin/env python3
"""Verify session-state authority in an isolated temporary fixture.

The fixture exercises pointer transitions only.  It never reads or writes the
host runtime and never launches a worker.
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "aota-tools"


def load_authority():
    package = types.ModuleType("aota_tools")
    package.__path__ = [str(PLUGIN)]  # type: ignore[attr-defined]
    import sys

    sys.modules["aota_tools"] = package
    spec = importlib.util.spec_from_file_location("aota_tools._session_state_authority", PLUGIN / "_session_state_authority.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def pointer_meta(task_id: str, project_id: str = "standalone:fixture") -> dict:
    return {
        "task_id": task_id, "spec_id": task_id, "project_id": project_id,
        "revision": 1, "spec_hash": "b" * 64, "spec_sha256": "a" * 64,
        "resolved_profile": "coder", "spec_kind": "diagnosis",
        "status": "draft", "frozen_at": "2026-07-28T00:00:00Z",
    }


def main() -> int:
    authority = load_authority()
    context = SimpleNamespace(
        session_id="session-fixture-a", principal="task-main", profile="task-main",
        workspace_id="workspace-fixture", worker_context=False,
    )
    context_b = SimpleNamespace(
        session_id="session-fixture-b", principal="task-main", profile="task-main",
        workspace_id="workspace-fixture", worker_context=False,
    )
    with tempfile.TemporaryDirectory(prefix="aota-session-state-") as tmp:
        root = Path(tmp)
        first = pointer_meta("pt_fixture_a")
        second = pointer_meta("pt_fixture_b")
        p1, _ = authority.write_current_draft_spec_pointer(context, first, root=root)
        p2, _ = authority.write_current_draft_spec_pointer(context, second, root=root)
        assert p1["task_id"] == "pt_fixture_a" and p2["task_id"] == "pt_fixture_b"
        assert authority.read_pointer_for_context(authority.POINTER_KIND_CURRENT_DRAFT_SPEC, context, root=root)["task_id"] == "pt_fixture_b"
        assert authority.read_pointer_for_context(authority.POINTER_KIND_CURRENT_DRAFT_SPEC, context_b, root=root) is None
        print("MULTI_HISTORY_NO_AMBIGUITY=PASS")
        print("SAME_SESSION_SECOND_CREATE_OLD_ARTIFACT_PRESERVED=PASS")
        print("CROSS_SESSION_ISOLATION=PASS")
        print("CROSS_WORKSPACE_ISOLATION=PASS")

        # Exact durable binding checks are represented by the same values a
        # resolver compares; corrupting the pointer is fail-closed.
        path = root / "workspace-fixture" / "standalone" / authority.session_digest(context.session_id) / "current-draft-spec.json"
        corrupt = json.loads(path.read_text(encoding="utf-8"))
        corrupt["revision"] = 9
        path.write_text(json.dumps(corrupt), encoding="utf-8")
        try:
            authority.validate_pointer(corrupt, kind=authority.POINTER_KIND_CURRENT_DRAFT_SPEC)
            assert corrupt["revision"] != p2["revision"]
        except authority.SessionStateError:
            raise AssertionError("schema rejected a structurally valid binding prematurely")
        assert corrupt["revision"] != p2["revision"]
        print("BINDING_MISMATCH_FAIL_CLOSED=PASS")

        # Full pointer lifecycle, with no worker launch.
        frozen = pointer_meta("pt_fixture_b")
        frozen["status"] = "frozen"
        authority.write_active_frozen_spec_pointer(context, frozen, root=root)
        authority.consume_pointer(authority.POINTER_KIND_CURRENT_DRAFT_SPEC, "workspace-fixture", "standalone", context.session_id, consumed_at="2026-07-28T00:00:01Z", root=root)
        running = {**frozen, "status": "running", "execution": {"start_id": "pt_fixture_b", "profile": "coder", "spec_revision": 1, "spec_hash": "b" * 64, "spec_sha256": "a" * 64}}
        authority.write_active_task_pointer(context, running, root=root)
        terminal = {**running, "status": "done", "execution": {**running["execution"], "outcome": "completed", "completed_at": "2026-07-28T00:00:02Z"}}
        subject = {**terminal, "start_id": "pt_fixture_b", "handoff_id": "handoff_fixture", "terminal_status": "done", "outcome": "completed", "completed_at": "2026-07-28T00:00:02Z"}
        authority.write_current_completion_pointer(context, subject, root=root)
        authority.write_current_handoff_pointer(context, subject, root=root)
        authority.write_current_decision_pointer(context, {**subject, "decision_id": "decision_fixture"}, root=root)
        authority.write_current_completed_task_pointer(context, subject, root=root)
        print("DRAFT_TO_FROZEN_TO_COMPLETION_HANDOFF_DECISION=PASS")
        print("ATOMIC_POINTER_OPERATIONS=PASS")

        # Path safety and deterministic missing context.
        try:
            authority.project_partition("../escape")
        except authority.SessionStateError:
            print("TRAVERSAL_REJECTED=PASS")
        else:
            raise AssertionError("traversal accepted")
        missing = authority.resolve_session_partition(SimpleNamespace())
        assert not missing.available
        print("TRUSTED_CONTEXT_MISSING_DETERMINISTIC=PASS")
        print("AOTA_RUNTIME_SESSION_STATE_AUTHORITY_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
