#!/usr/bin/env python3
"""Zero-LLM fixtures for the R4 AOTA Profile efficiency contracts."""
from __future__ import annotations

import copy
import importlib
import importlib.util
import json
import os
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
        "aota_tools",
        PLUGIN / "__init__.py",
        submodule_search_locations=[str(PLUGIN)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["aota_tools"] = module
    spec.loader.exec_module(module)


def classification_facts(classifier: object) -> dict[str, object]:
    facts: dict[str, object] = {name: False for name in classifier._BOOL_FACTS}
    facts.update({name: 1 for name in classifier._INT_BOUNDS})
    facts.update({
        "services_touched": 0,
        "requirements_ambiguity": "low",
        "technical_uncertainty": "low",
        "write_scope": "none",
        "validation_scope": "syntax",
        "requested_architect_review": True,
    })
    return facts


def main() -> int:
    load_package()
    create = importlib.import_module("aota_tools._task_spec_create")
    freeze = importlib.import_module("aota_tools._task_spec_freeze")
    task_common = importlib.import_module("aota_tools._task_spec_common")
    workspace = importlib.import_module("aota_tools._workspace")
    binding = importlib.import_module("aota_tools._session_active_spec_binding")
    authority = importlib.import_module("aota_tools._session_state_authority")
    classifier = importlib.import_module("aota_tools._work_classifier")
    phase4 = importlib.import_module("aota_tools._phase4_control_plane")
    phase3 = importlib.import_module("aota_tools._phase3_control_plane")
    start = importlib.import_module("aota_tools._profile_task_start")

    env_names = (
        "AOTA_TRUSTED_PRINCIPAL",
        "AOTA_TRUSTED_WORKSPACE_ID",
        "AOTA_PROFILE_TASK_ID",
        "AOTA_PROFILE_TASK_START_ID",
        "AOTA_PROFILE_TASK_PROFILE",
    )
    before_env = {name: os.environ.get(name) for name in env_names}

    with tempfile.TemporaryDirectory(prefix="aota-profile-efficiency-r4-") as temp:
        root = Path(temp)
        runtime_root = root / "runtime"
        workspace_root = root / "workspace"
        workspace_root.mkdir()
        (workspace_root / "README.md").write_text("# Fixture\n", encoding="utf-8")

        original = (
            task_common.PROFILE_TASK_ROOT,
            task_common.LOCK_ROOT,
            workspace.resolve_workspace,
            binding.SESSION_ACTIVE_SPEC_ROOT,
            authority.SESSION_STATE_ROOT,
        )
        task_common.PROFILE_TASK_ROOT = runtime_root / "profile-tasks"
        task_common.LOCK_ROOT = runtime_root / "locks"
        workspace.resolve_workspace = lambda _workspace_id: workspace_root
        classifier.resolve_workspace = workspace.resolve_workspace
        binding.SESSION_ACTIVE_SPEC_ROOT = runtime_root / "session-active-spec"
        authority.SESSION_STATE_ROOT = runtime_root / "session-state"
        os.environ.update({
            "AOTA_TRUSTED_PRINCIPAL": "task-main",
            "AOTA_TRUSTED_WORKSPACE_ID": "fixture",
        })

        try:
            classified = json.loads(classifier.handle({
                "workspace_id": "fixture",
                "title": "P0 architecture fixture",
                "summary": "Read one exact README path through an architect task.",
                "facts": classification_facts(classifier),
            }, session_id="session-r4", profile="task-main"))
            assert classified["planning_depth"] == "P0", classified
            assert classified["classification_binding"]["status"] == "written", classified
            assert classified["allowed_next_tool"] == "aota_profile_task_dispatch", classified
            assert classified["allowed_next_tool_schema"]["required"] == ["spec_kind", "objective"], classified
            assert classified["next_action"] == "dispatch_p0_semantic_spec", classified
            print("NEXT_SCHEMA_AFTER_CLASSIFY=PASS")

            invalid = json.loads(create.handle({
                "spec_kind": "architecture",
                "objective": "Read README.md line one and submit an architecture finding.",
                "subject_ref": "current_work_classification",
                "read_scope": ["README.md"],
                "write_scope": [],
                "payload": {
                    "review_mode": "unsupported",
                    "challenge_questions": ["Does README.md provide the requested evidence?"],
                },
            }, session_id="session-r4", profile="task-main"))
            assert invalid["retryable"] is True, invalid
            assert invalid["same_call_retryable"] is False, invalid
            assert invalid["retry_scope"] == "changed_arguments_only", invalid
            assert invalid["repairable_fields"] == ["payload.review_mode"], invalid
            assert invalid["allowed_next_tool"] == "aota_task_spec_create", invalid
            assert invalid["failure_fingerprint"].startswith("ff_"), invalid
            print("SCOPED_REPAIR_CONTRACT=PASS")

            created = json.loads(create.handle({
                "spec_kind": "architecture",
                "objective": "Read README.md line one and submit an architecture finding.",
                "subject_ref": "current_work_classification",
                "read_scope": ["README.md"],
                "write_scope": [],
            }, session_id="session-r4", profile="task-main"))
            assert created["status"] == "created", created
            assert created["classification_binding"]["status"] == "consumed", created
            assert created["allowed_next_tool"] == "aota_task_spec_freeze", created
            assert created["allowed_next_tool_schema"]["required"] == ["spec_ref"], created
            task_dir = task_common.get_task_dir("fixture", created["task_id"])
            meta = task_common.load_meta(task_dir)
            subject = meta["spec"]["payload"]["subject_work_classification_ref"]
            assert subject == {
                "ref_type": "work_classification",
                "artifact_id": classified["classification_binding"]["artifact_digest"],
            }, subject
            assert subject in meta["spec"]["context_refs"], meta["spec"]["context_refs"]
            print("SEMANTIC_ARCHITECTURE_SUBJECT=PASS")

            legacy = json.loads(create.handle({
                "workspace_id": "fixture",
                "project_id": "fixture-project",
                "work_item_id": "WI-legacy",
                "spec_kind": "architecture",
                "objective": "Preserve trusted legacy subject-ref compatibility.",
                "payload": {
                    "review_mode": "design_review",
                    "subject_plan_ref": {"ref_type": "plan", "artifact_id": "plan-legacy"},
                    "challenge_questions": ["Does the existing plan remain the subject?"],
                },
            }, session_id="session-legacy", profile="task-main"))
            assert legacy["status"] == "created" and legacy["error"] is None, legacy
            print("LEGACY_TRUSTED_SUBJECT_COMPATIBILITY=PASS")

            frozen = json.loads(freeze.handle(
                {"spec_ref": "current_draft_spec"},
                session_id="session-r4",
                profile="task-main",
            ))
            assert frozen["status"] == "frozen", frozen
            assert frozen["allowed_next_tool"] == "aota_profile_task_start", frozen
            assert frozen["allowed_next_arguments"] == {"task_ref": "active_frozen_spec"}, frozen
            assert frozen["allowed_next_tool_schema"]["required"] == ["task_ref"], frozen
            print("NEXT_SCHEMA_THROUGH_FREEZE=PASS")

            frozen_meta = task_common.load_meta(task_dir)
            verified_subject = start._verify_p0_work_classification_subject(frozen_meta)
            assert verified_subject == subject, verified_subject

            def expect_architecture_subject_error(candidate: dict, code: str) -> None:
                try:
                    start._verify_p0_work_classification_subject(candidate)
                except start.WorkspaceError as exc:
                    assert str(exc).startswith(code), exc
                else:
                    raise AssertionError(f"expected {code}")

            missing_ref = copy.deepcopy(frozen_meta)
            missing_ref["spec"]["context_refs"] = []
            expect_architecture_subject_error(missing_ref, "architecture_subject_binding_invalid")
            wrong_digest = copy.deepcopy(frozen_meta)
            wrong_digest["classification_binding"]["artifact_digest"] = "bd_wrong"
            expect_architecture_subject_error(wrong_digest, "architecture_subject_binding_invalid")
            legacy_without_subject = copy.deepcopy(frozen_meta)
            legacy_without_subject.pop("contract_version", None)
            expect_architecture_subject_error(legacy_without_subject, "architecture_missing_subject")
            print("SEMANTIC_ARCHITECTURE_START_GATE=PASS")

            read_description = phase4.PHASE4_SCHEMAS["aota_read_file"]["description"]
            search_description = phase4.PHASE4_SCHEMAS["aota_search_files"]["description"]
            assert "first domain call" in read_description, read_description
            assert "named literal, enum, or schema probe" in read_description, read_description
            assert "exactly one" in search_description, search_description
            assert "exact file" in search_description, search_description
            skill = (ROOT / "skills" / "workspace-file-access-strategy" / "SKILL.md").read_text(encoding="utf-8")
            coder = (ROOT / "profiles" / "coder" / "SOUL.md").read_text(encoding="utf-8")
            coder_config = (ROOT / "profiles" / "coder" / "config.yaml").read_text(encoding="utf-8")
            debugger = (ROOT / "profiles" / "debugger" / "SOUL.md").read_text(encoding="utf-8")
            steward = (ROOT / "skills" / "aota-pcf-project-steward" / "SKILL.md").read_text(encoding="utf-8")
            routing = (ROOT / "skills" / "aota-profile-skill-routing-index" / "SKILL.md").read_text(encoding="utf-8")
            assert "search scoped to the exact file" in skill, skill
            assert 'aota_read_file({"path":"<path>"})' in skill, skill
            assert "aota_read_file" in coder and "aota_search_files" in coder, coder
            assert "named literal, enum, or schema fragment" in coder, coder
            assert "exactly" in coder and "one `aota_search_files`" in coder, coder
            assert "projects `aota_read_file` and `aota_search_files`" in coder, coder
            assert "Do not call `tool_search` or `tool_describe`" in coder, coder
            assert "always_visible_tools:" in coder_config, coder_config
            assert "- aota_read_file" in coder_config and "- aota_search_files" in coder_config, coder_config
            assert 'tool_call(name="aota_read_file"' in debugger and "tool_search" in debugger, debugger
            assert 'aota_project_registry_refresh({"operation":"register_current_project"})' in steward, steward
            assert 'aota_project_open({"project_ref":"current_project","include_observed":true})' in steward, steward
            assert "aota_workspace_open({})" in routing and "allowed_next_tool_schema" in routing, routing
            route: dict[str, object] = {}
            phase3._attach_current_project_setup_route(
                route,
                name="aota_project_registry_refresh",
                args={"operation": "register_current_project"},
            )
            contract = route["route_contract"]
            assert contract["route_id"] == "raw_current_project_setup_v1", route
            assert contract["max_domain_calls"] == 2, route
            assert contract["allowed_next_tool"] == "aota_project_open", route
            print("EXACT_PATH_CONTENT_VS_LITERAL_ROUTE=PASS")

            sample_plan = json.loads((ROOT / "fixtures" / "aota-profile-efficiency-r5-30-sample-plan.json").read_text(encoding="utf-8"))
            assert sample_plan["model"] == "glm-5.2", sample_plan
            assert sample_plan["per_task_llm_plus_tool_limit"] == 100, sample_plan
            assert sample_plan["base_case_count"] == len(sample_plan["base_cases"]) == 6, sample_plan
            assert sample_plan["repetitions_per_profile"] == 5, sample_plan
            assert sample_plan["total_sample_count"] == len(sample_plan["base_cases"]) * sample_plan["repetitions_per_profile"] == 30, sample_plan
            assert {case["profile"] for case in sample_plan["base_cases"]} == {
                "project-steward", "task-main", "architect", "reviewer", "coder", "debugger",
            }, sample_plan
            assert all(case["prompt_tool_limit"] < 100 for case in sample_plan["base_cases"]), sample_plan
            assert sum(value for key, value in sample_plan["scoring"].items() if key != "target_percent") == 1.0, sample_plan
            assert sample_plan["execution"]["managed_deploy_required"] is True, sample_plan
            assert sample_plan["execution"]["explicit_user_consent_after_fix_report_required"] is True, sample_plan
            assert sample_plan["route_contracts"]["coder"]["exact_content"]["first_domain_tool"] == "aota_read_file", sample_plan
            assert sample_plan["route_contracts"]["coder"]["exact_schema_probe"]["first_domain_tool"] == "aota_search_files", sample_plan
            assert sample_plan["route_contracts"]["coder"]["exact_schema_probe"]["direct_tools"] == ["aota_search_files", "aota_read_file"], sample_plan
            assert set(sample_plan["route_contracts"]["coder"]["exact_schema_probe"]["forbidden_meta_tools"]) == {"tool_search", "tool_describe"}, sample_plan
            assert sample_plan["route_contracts"]["project-steward"]["raw_current_project_setup"]["route_id"] == "raw_current_project_setup_v1", sample_plan
            assert sample_plan["route_contracts"]["task-main"]["p0"]["first_dispatch_tool"] == "aota_profile_task_dispatch", sample_plan
            coder_case = next(item for item in sample_plan["base_cases"] if item["profile"] == "coder")
            assert "exact-file schema probe" in coder_case["prompt"], coder_case
            assert "最多一次 scoped aota_search_files" in coder_case["prompt"], coder_case
            print("THIRTY_SAMPLE_PLAN=PASS")
        finally:
            (
                task_common.PROFILE_TASK_ROOT,
                task_common.LOCK_ROOT,
                workspace.resolve_workspace,
                binding.SESSION_ACTIVE_SPEC_ROOT,
                authority.SESSION_STATE_ROOT,
            ) = original
            for name, value in before_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    print("ZERO_LLM_PROFILE_EFFICIENCY_FIXTURES=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
