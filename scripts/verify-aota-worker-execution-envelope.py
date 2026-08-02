#!/usr/bin/env python3
"""Zero-LLM fixtures for the Worker Execution Envelope projection."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
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
    common = importlib.import_module("aota_tools._profile_task_common")
    scope = importlib.import_module("aota_tools._task_spec_scope")
    capabilities = importlib.import_module("aota_tools._capabilities")
    start_source = (PLUGIN / "_profile_task_start.py").read_text(encoding="utf-8")

    internal_task = "pt_20260801T000000_deadbeef"
    internal_hash = "a" * 64
    internal_path = "/aota-runtime/profile-tasks/fixture/" + internal_task + "/SPEC.md"
    spec = {
        "objective": "Inspect one bounded source file and report the finding.",
        "acceptance_criteria": ["The finding cites the requested source evidence."],
        "constraints": ["read-only", "no deployment"],
        "forbidden_actions": ["source_write"],
        "stop_conditions": ["required evidence is unavailable"],
        "evidence_required": ["source file content"],
        "process_path": "fast",
        "validation_tier": 0,
        "human_checkpoints": [],
        "payload": {
            "review_mode": "design_review",
            "challenge_questions": ["Does the source satisfy the objective?"],
            "subject_task_id": internal_task,
            "subject_spec_ref": {"artifact_id": internal_task, "hash": internal_hash, "artifact_path": internal_path},
            "validation_commands": ["bounded_fixture"],
            "validation_strategy": "static",
        },
        "role_contract": {
            "project_id": "internal-project",
            "work_item_id": "WI-internal",
            "review_questions": ["Is the evidence sufficient?"],
        },
    }
    prompt, digest, envelope = common.generate_worker_execution_envelope_prompt(
        spec=spec,
        task_kind="architecture",
        derived_profile="architect",
        read_scope=["src/module.py"],
        write_scope=[],
        forbidden_scope=["src/private/**"],
        architecture_mode="design_review",
    )
    prompt_again, digest_again, envelope_again = common.generate_worker_execution_envelope_prompt(
        spec=spec,
        task_kind="architecture",
        derived_profile="architect",
        read_scope=["src/module.py"],
        write_scope=[],
        forbidden_scope=["src/private/**"],
        architecture_mode="design_review",
    )
    assert prompt == prompt_again and digest == digest_again and envelope == envelope_again
    assert digest and len(digest) == 64
    print("ENVELOPE_DETERMINISTIC_HASH_PARITY=PASS")

    for forbidden in (internal_task, internal_hash, internal_path, "internal-project", "WI-internal"):
        assert forbidden not in prompt, forbidden
    for forbidden_key in ("subject_task_id", "subject_spec_ref", "artifact_id", "artifact_path", "hash", "project_id", "work_item_id"):
        assert forbidden_key not in json.dumps(envelope, ensure_ascii=False, sort_keys=True), forbidden_key
    assert "Task ID:" not in prompt and "Workspace:" not in prompt
    assert "meta.json" not in prompt and "SPEC.md" not in prompt
    assert "tool discovery" in prompt and "aota_worker_outcome_submit" in prompt
    print("ENVELOPE_NO_INTERNAL_BINDING_OR_PATH_LEAK=PASS")

    bad_scope = {"payload": {"read_scope": ["../escape"], "write_scope": [], "forbidden_scope": []}}
    try:
        scope.extract_canonical_scope(bad_scope)
    except scope.CanonicalScopeError:
        pass
    else:
        raise AssertionError("scope traversal was accepted")
    print("SCOPE_DENIAL_REMAINS_FAIL_CLOSED=PASS")

    expected_worker_tools = {
        "coder": {"aota_coder_report_submit", "aota_worker_outcome_submit"},
        "debugger": {"aota_debugger_report_submit", "aota_worker_outcome_submit"},
        "reviewer": {"aota_reviewer_report_submit", "aota_worker_outcome_submit"},
        "architect": {"aota_architect_report_submit", "aota_worker_outcome_submit"},
        "project-steward": {"aota_project_steward_report", "aota_worker_outcome_submit"},
    }
    for profile, required in expected_worker_tools.items():
        tools = set(capabilities.PROFILE_CAPABILITIES[profile]["allowed_tools"])
        assert required <= tools, (profile, required - tools)
        assert "aota_profile_task_dispatch" not in tools
        assert "tool_search" not in tools and "tool_describe" not in tools
    assert "generate_worker_execution_envelope_prompt" in start_source
    print("PROFILE_TOOL_SURFACE_UNCHANGED=PASS")
    print("AOTA_WORKER_EXECUTION_ENVELOPE_FIXTURE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
