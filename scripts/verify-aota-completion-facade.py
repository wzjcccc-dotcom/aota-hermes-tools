#!/usr/bin/env python3
"""Zero-LLM fixture for the current-completion CARD/RESULT facade."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import tempfile
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "aota-tools"


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


def main() -> int:
    load_package()
    handoff_open = importlib.import_module("aota_tools._handoff_open")

    task_id = "pt_20260801T010101_deadbeef"
    handoff_id = "ho_20260801T010101_deadbeef"
    card = {
        "schema_version": 1,
        "role": "coder",
        "outcome": "completed",
        "verdict": "pass",
        "summary": "Bounded fixture completed.",
        "needs_full_report_review": False,
        "recommended_next_action": "task-main decide",
    }
    with tempfile.TemporaryDirectory(prefix="aota-completion-facade-") as raw:
        task_dir = Path(raw)
        (task_dir / "CARD.json").write_text(json.dumps(card), encoding="utf-8")
        # The facade returns a reference only; it must not load this full report.
        (task_dir / "RESULT.md").write_text("full report body\n", encoding="utf-8")
        subject = {
            "workspace_id": "fixture",
            "task_id": task_id,
            "start_id": task_id,
            "handoff_id": handoff_id,
            "task_dir": task_dir,
            "profile": "coder",
            "task_kind": "implementation",
            "completed_at": "2026-08-01T01:02:03Z",
            "card_name": "CARD.json",
            "card": card,
            "receipt": {
                "status": "done",
                "exit_code": 0,
                "scope_compliance": {"status": "in_scope", "unexpected_paths": 0},
                "completed_at": "2026-08-01T01:02:03Z",
            },
            "outcome": {"outcome": "completed"},
            "meta": {"status": "done", "execution": {"reconciliation_state": "reconciled"}},
            "handoff": {
                "handoff_id": handoff_id,
                "workspace_id": "fixture",
                "task_id": task_id,
                "start_id": task_id,
                "profile": "coder",
                "task_kind": "implementation",
                "terminal_status": "done",
                "state": "pending",
                "outcome": "completed",
                "verdict": "pass",
                "summary": card["summary"],
                "artifact_card_ref": "CARD.json",
                "full_report_ref": "RESULT.md",
                "role_artifact": {
                    "kind": "coder",
                    "card_name": "CARD.json",
                    "full_name": "RESULT.md",
                    "card_exists": True,
                    "full_exists": True,
                },
                "needs_full_report_review": False,
                "recommended_next_action": "task-main decide",
            },
        }

        original_resolver = handoff_open.resolve_current_completion_subject
        original_context = handoff_open.resolved_context
        try:
            handoff_open.resolve_current_completion_subject = lambda *_args, **_kwargs: subject
            handoff_open.resolved_context = lambda *_args, **_kwargs: {
                "selector": "current_completion", "binding": "trusted"
            }
            result = json.loads(handoff_open.handle({}, session_id="session-1", profile="task-main"))
        finally:
            handoff_open.resolve_current_completion_subject = original_resolver
            handoff_open.resolved_context = original_context

    assert result["status"] == "opened", result
    assert result["next_action"] == "review_card_and_record_decision", result
    completion = result["completion"]
    assert completion["status"] == "ready", completion
    assert completion["worker_outcome"] == "completed", completion
    assert completion["terminal_status"] == "done", completion
    assert completion["card"]["ref"] == "current_role_card", completion
    assert completion["card"]["available"] is True, completion
    assert completion["result"] == {
        "ref": "current_role_result", "name": "RESULT.md", "available": True
    }, completion
    assert completion["receipt"]["ref"] == "current_completion_receipt", completion
    assert completion["receipt"]["authoritative"] is True, completion
    assert completion["receipt"]["exit_code"] == 0, completion
    print("COMPLETION_CARD_RESULT_RECEIPT_FACADE=PASS")

    nested = json.dumps(completion, ensure_ascii=False, sort_keys=True)
    for forbidden in (task_id, handoff_id, "spec_hash", "spec_sha256", "/aota-runtime"):
        assert forbidden not in nested, forbidden
    assert "full report body" not in nested
    print("COMPLETION_FACADE_NO_INTERNAL_BINDING_OR_FULL_REPORT=PASS")

    properties = handoff_open.SCHEMA["parameters"]["properties"]
    assert set(properties) == {"handoff_ref"}
    assert "aota_handoff_list" not in handoff_open.SCHEMA["description"]
    print("COMPLETION_FACADE_SEMANTIC_SCHEMA=PASS")
    print("AOTA_COMPLETION_FACADE_FIXTURE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
