#!/usr/bin/env python3
"""Verify classifier session binding and semantic SPEC consumption in isolation."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace

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


def facts(classifier) -> dict:
    value = {name: False for name in classifier._BOOL_FACTS}
    value.update({name: 1 for name in classifier._INT_BOUNDS})
    value.update({
        "requirements_ambiguity": "low", "technical_uncertainty": "low",
        "write_scope": "local", "validation_scope": "isolated",
        "services_touched": 0,
    })
    return value


def classify(classifier, session: str, *, title: str = "P0 fixture") -> dict:
    return json.loads(classifier.handle({
        "workspace_id": "fixture", "title": title, "summary": "isolated fixture",
        "facts": facts(classifier),
    }, session_id=session, profile="task-main", workspace_id="fixture"))


def create_kind(create, classifier, kind: str, session: str, **extra) -> dict:
    binding = classify(classifier, session, title=f"P0 {kind}")
    assert binding["classification_status"] == "classified", binding
    assert binding["planning_depth"] == "P0"
    args = {"spec_kind": kind, "objective": f"P0 {kind} fixture", **extra}
    return json.loads(create.handle(args, session_id=session, profile="task-main", workspace_id="fixture"))


def main() -> int:
    load_package()
    from aota_tools import _session_state_authority as authority
    from aota_tools import _task_spec_common as task_common
    from aota_tools import _task_spec_create as create
    from aota_tools import _reference_resolver as references
    from aota_tools import _work_classifier as classifier
    from aota_tools import _workspace as workspace

    with tempfile.TemporaryDirectory(prefix="aota-classification-binding-") as raw:
        root = Path(raw)
        workspace_root = root / "workspace"
        workspace_root.mkdir()
        runtime_root = root / "runtime"
        old = (
            task_common.PROFILE_TASK_ROOT, task_common.LOCK_ROOT,
            workspace.resolve_workspace, classifier.resolve_workspace,
            authority.SESSION_STATE_ROOT,
        )
        task_common.PROFILE_TASK_ROOT = runtime_root / "profile-tasks"
        task_common.LOCK_ROOT = runtime_root / "locks"
        workspace.resolve_workspace = lambda _workspace_id: workspace_root
        classifier.resolve_workspace = workspace.resolve_workspace
        authority.SESSION_STATE_ROOT = runtime_root / "session-state"
        context = SimpleNamespace(
            session_id="session-a", principal="task-main", profile="task-main",
            workspace_id="fixture", project_id="", worker_context=False,
        )
        context_other = SimpleNamespace(
            session_id="session-b", principal="task-main", profile="task-main",
            workspace_id="fixture", project_id="", worker_context=False,
        )
        try:
            first = classify(classifier, "session-a")
            assert first["classification_binding"]["status"] == "written"
            second = classify(classifier, "session-a")
            assert second["classification_binding"]["idempotent"] is True
            print("CLASSIFIER_WRITES_P0=PASS")
            print("CLASSIFIER_IDEMPOTENT=PASS")

            # A project-bound result supersedes the standalone current result
            # through the same session authority; the old pointer is consumed.
            p1, _path, _same = authority.write_current_work_classification_pointer(
                context, classification="P1", authority="A0", execution_depth="standard",
                classifier_result_digest="a" * 64, project_id="project-a",
            )
            assert p1["project_id"] == "project-a"
            old_pointer = authority.read_pointer(
                authority.POINTER_KIND_CURRENT_WORK_CLASSIFICATION,
                "fixture", "standalone", "session-a", root=authority.SESSION_STATE_ROOT,
            )
            assert old_pointer and old_pointer["state"] == "consumed"
            assert authority.read_current_work_classification_pointer(
                context, root=authority.SESSION_STATE_ROOT,
            )["project_id"] == "project-a"
            print("CLASSIFIER_SUPERSEDE_P0_TO_P1=PASS")
            assert authority.read_current_work_classification_pointer(
                context_other, root=authority.SESSION_STATE_ROOT,
            ) is None
            print("CROSS_SESSION_BLOCKED=PASS")

            # Restore a P0 binding for the canonical create cases.
            classify(classifier, "session-a", title="P0 reset")
            seed = create_kind(
                create, classifier, "diagnosis", "session-a",
                read_scope=["README.md"], write_scope=[],
            )
            assert seed["status"] == "created" and seed["classification_binding"]["status"] == "consumed", seed
            print("P0_DIAGNOSIS_CONSUMES=PASS")
            reused = json.loads(create.handle({
                "spec_kind": "diagnosis", "objective": "reuse without classify",
                "read_scope": ["README.md"], "write_scope": [],
            }, session_id="session-a", profile="task-main", workspace_id="fixture"))
            assert reused["error"] == "classification_context_consumed", reused
            print("CONSUMED_REUSE_BLOCKED=PASS")

            implementation = create_kind(
                create, classifier, "implementation", "session-a",
                payload={"write_scope": ["src"], "forbidden_scope": ["outside-src"], "validation_strategy": "compile"},
            )
            architecture = create_kind(
                create, classifier, "architecture", "session-a",
                payload={"review_mode": "design_review", "subject_spec_ref": {"ref_type": "subject_spec", "artifact_id": seed["spec_id"]}, "challenge_questions": ["q"]},
            )
            stewardship = create_kind(
                create, classifier, "stewardship", "session-a",
                payload={"operation": "context_prepare", "project_context_questions": ["q"]},
            )
            review = create_kind(
                create, classifier, "review", "session-a", subject_task_id=seed["task_id"],
                context_refs=[{"ref_type": "subject_spec", "artifact_id": seed["spec_id"]}],
                payload={
                    "subject_spec_ref": {"ref_type": "subject_spec", "artifact_id": seed["spec_id"]},
                    "subject_result_ref": {"ref_type": "subject_result", "artifact_id": "result-1"},
                    "review_dimensions": ["scope"],
                },
            )
            assert all(item["status"] == "created" and item["classification_binding"]["status"] == "consumed" for item in (implementation, architecture, stewardship, review)), (implementation, architecture, stewardship, review)
            assert all(
                item["flow_disposition"] == "continue"
                and item["allowed_next_tool"] == "aota_task_spec_freeze"
                and item["allowed_next_arguments"] == {"spec_ref": "current_draft_spec"}
                for item in (implementation, architecture, stewardship, review)
            )
            print("ALL_P0_SPEC_KINDS_STANDALONE=PASS")
            print("SPEC_CREATE_FLOW_CONTRACT=PASS")

            project_context = SimpleNamespace(
                session_id="session-a", principal="task-main", profile="task-main",
                workspace_id="fixture", project_id="project-a", worker_context=False,
            )
            authority.write_current_work_classification_pointer(
                project_context, classification="P1", authority="A0", execution_depth="standard",
                classifier_result_digest="b" * 64, project_id="project-a",
            )
            original_plan_resolver = create.resolve_current_plan
            original_snapshot_builder = create.build_trusted_snapshot
            original_traceability_validator = create.validate_traceability_input
            try:
                def missing_plan(*_args, **_kwargs):
                    raise references.ReferenceError("reference_missing", detail="active_work_item_missing")
                create.resolve_current_plan = missing_plan
                missing_plan_result = json.loads(create.handle({
                    "spec_kind": "diagnosis", "objective": "P1 missing plan",
                    "read_scope": ["README.md"], "write_scope": [],
                }, session_id="session-a", profile="task-main", workspace_id="fixture", project_id="project-a"))
                assert missing_plan_result["error"] == "active_work_item_missing"
                active_after_gate = authority.read_current_work_classification_pointer(
                    project_context, project_id="project-a", root=authority.SESSION_STATE_ROOT,
                )
                assert active_after_gate and active_after_gate["state"] == "active"
                print("P1_MISSING_PLAN_DOES_NOT_CONSUME=PASS")

                def fake_plan(*_args, **_kwargs):
                    return {
                        "plan_id": "plan-fixture", "active_milestone_id": "milestone-fixture",
                        "active_work_item_id": "work-item-fixture",
                        "workspace_context": {"project_id": "project-a"},
                    }
                create.resolve_current_plan = fake_plan
                create.validate_traceability_input = lambda value, **_kwargs: {
                    "plan_id": "plan-fixture", "milestone_id": "milestone-fixture", "work_item_id": "work-item-fixture", "architect_review_id": None,
                }
                create.build_trusted_snapshot = lambda *_args, **_kwargs: {
                    "source_type": "aota_forge_plan", "plan_id": "plan-fixture", "plan_revision": 1,
                    "plan_sha256": "c" * 64, "milestone_id": "milestone-fixture", "work_item_id": "work-item-fixture",
                    "architect_review_id": None, "verified_at": "2026-07-29T00:00:00Z", "verification_status": "verified",
                }
                authority.write_current_work_classification_pointer(
                    project_context, classification="P1", authority="A0", execution_depth="standard",
                    classifier_result_digest="c" * 64, project_id="project-a",
                )
                complete_p1 = json.loads(create.handle({
                    "spec_kind": "diagnosis", "objective": "P1 linked fixture",
                    "read_scope": ["README.md"], "write_scope": [],
                }, session_id="session-a", profile="task-main", workspace_id="fixture", project_id="project-a"))
                assert complete_p1["status"] == "created" and complete_p1["classification_binding"]["status"] == "consumed"
                linked_meta = task_common.load_meta(task_common.get_task_dir("fixture", complete_p1["task_id"]))
                assert linked_meta.get("source_traceability", {}).get("plan_id") == "plan-fixture"
                print("P1_COMPLETE_TRACEABILITY_AND_CONSUME=PASS")
                authority.write_current_work_classification_pointer(
                    project_context, classification="P2", authority="A0", execution_depth="standard",
                    classifier_result_digest="d" * 64, project_id="project-a",
                )
                complete_p2 = json.loads(create.handle({
                    "spec_kind": "diagnosis", "objective": "P2 linked fixture",
                    "read_scope": ["README.md"], "write_scope": [],
                }, session_id="session-a", profile="task-main", workspace_id="fixture", project_id="project-a"))
                assert complete_p2["status"] == "created" and complete_p2["classification"] == "P2" and complete_p2["classification_binding"]["status"] == "consumed"
                print("P2_COMPLETE_TRACEABILITY_AND_CONSUME=PASS")
            finally:
                create.resolve_current_plan = original_plan_resolver
                create.build_trusted_snapshot = original_snapshot_builder
                create.validate_traceability_input = original_traceability_validator

            classify(classifier, "session-a", title="failure fixture")
            original_writer = create.write_current_draft_spec_pointer
            def fail_writer(*_args, **_kwargs):
                raise authority.SessionStateError("session_state_path_invalid", "fixture_write_failure")
            create.write_current_draft_spec_pointer = fail_writer
            try:
                failed = json.loads(create.handle({
                    "spec_kind": "diagnosis", "objective": "draft pointer failure",
                    "read_scope": ["README.md"], "write_scope": [],
                }, session_id="session-a", profile="task-main", workspace_id="fixture"))
            finally:
                create.write_current_draft_spec_pointer = original_writer
            assert failed["status"] == "failed" and failed["classification_binding"]["consumed"] is False
            active = authority.read_current_work_classification_pointer(context, root=authority.SESSION_STATE_ROOT)
            assert active and active["state"] == "active"
            print("DRAFT_WRITE_FAILURE_DOES_NOT_CONSUME=PASS")
        finally:
            (
                task_common.PROFILE_TASK_ROOT, task_common.LOCK_ROOT,
                workspace.resolve_workspace, classifier.resolve_workspace,
                authority.SESSION_STATE_ROOT,
            ) = old
    print("AOTA_WORK_CLASSIFICATION_SESSION_BINDING_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
