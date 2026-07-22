#!/usr/bin/env python3
"""Isolated canonical review subject-binding regression fixture.

This fixture uses temporary workspace/runtime roots only.  It never invokes the
profile launcher, a provider, Hermes, Docker, or a managed service.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "aota-tools"
WORKSPACE = "fixture"
PROJECT = "fixture-project"


def load_plugin() -> None:
    for name in list(sys.modules):
        if name == "aota_tools" or name.startswith("aota_tools."):
            del sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        "aota_tools", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)]
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["aota_tools"] = module
    spec.loader.exec_module(module)


def data(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    assert isinstance(parsed, dict), parsed
    return parsed


def rejected(value: str) -> dict[str, Any]:
    parsed = data(value)
    assert parsed.get("status") in {"rejected", "error"}, parsed
    return parsed


def ref(ref_type: str, artifact_id: str) -> dict[str, str]:
    return {
        "ref_type": ref_type,
        "artifact_id": artifact_id,
        "artifact_path": f"artifacts/{artifact_id}.json",
    }


def args_for(kind: str, *, workspace_id: str = WORKSPACE, project_id: str = PROJECT,
             subject_task_id: str | None = None) -> dict[str, Any]:
    if kind == "implementation":
        payload = {
            "read_scope": ["src/**"],
            "write_scope": ["src/**"],
            "forbidden_scope": ["secrets/**"],
            "implementation_requirements": ["fixture"],
            "validation_strategy": "compile",
        }
        refs: list[dict[str, Any]] = []
        caps = {"source_read": True, "source_write": True}
    elif kind == "review":
        reference_id = subject_task_id or "missing-subject"
        refs = [ref("subject_spec", reference_id), ref("subject_result", f"{reference_id}-result")]
        payload = {
            "subject_spec_ref": refs[0],
            "subject_result_ref": refs[1],
            "review_dimensions": ["scope"],
            "required_evidence": [],
        }
        caps = {"source_read": True}
    elif kind == "diagnosis":
        refs = []
        payload = {"symptom": "fixture", "evidence_required": ["SPEC.md"], "mutation_allowed": False}
        caps = {"source_read": True}
    else:
        refs = []
        payload = {
            "operation": "context_prepare",
            "project_context_questions": ["fixture"],
            "allowed_project_artifacts": ["README.md"],
        }
        caps = {"source_read": True}
    result = {
        "workspace_id": workspace_id,
        "spec_kind": kind,
        "project_id": project_id,
        "work_item_id": "wi-review-binding",
        "objective": f"fixture {kind}",
        "summary": "canonical review subject binding fixture",
        "context_refs": refs,
        "related_artifacts": [],
        "acceptance_criteria": ["fixture passes"],
        "constraints": [],
        "forbidden_actions": ["provider request"],
        "expected_artifacts": [],
        "capability_contract": caps,
        "payload": payload,
    }
    if subject_task_id is not None:
        result["subject_task_id"] = subject_task_id
    return result


def make_task(create: Any, freeze: Any, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    made = data(create.handle(args))
    assert made.get("status") == "created", made
    frozen = data(freeze.handle({
        "workspace_id": args["workspace_id"],
        "spec_id": made["task_id"],
        "expected_revision": 1,
    }))
    assert frozen.get("status") == "frozen", frozen
    task_dir = Path(os.environ["AOTA_PROFILE_TASK_ROOT"]) / args["workspace_id"] / made["task_id"]
    meta = json.loads((task_dir / "meta.json").read_text(encoding="utf-8"))
    return made["task_id"], meta


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="aota-review-binding-") as raw:
        root = Path(raw)
        workspace_root = root / "workspace"
        other_workspace_root = root / "other-workspace"
        workspace_root.mkdir()
        other_workspace_root.mkdir()
        registry = root / "workspaces.json"
        registry.write_text(json.dumps({
            WORKSPACE: {"candidates": [str(workspace_root)]},
            "other": {"candidates": [str(other_workspace_root)]},
        }), encoding="utf-8")
        os.environ.update({
            "AOTA_WORKSPACE_REGISTRY_PATH": str(registry),
            "AOTA_PROFILE_TASK_ROOT": str(root / "profile-tasks"),
            "AOTA_RUNTIME_ROOT": str(root / "runtime"),
        })
        load_plugin()
        create = importlib.import_module("aota_tools._task_spec_create")
        freeze = importlib.import_module("aota_tools._task_spec_freeze")
        start = importlib.import_module("aota_tools._profile_task_start")
        contract = importlib.import_module("aota_tools._spec_contract")
        active = importlib.import_module("aota_tools._active_task_artifact_open")
        subject_artifact = importlib.import_module("aota_tools._subject_task_artifact_open")

        subject_id, subject_meta = make_task(create, freeze, args_for("implementation"))
        subject_path = Path(os.environ["AOTA_PROFILE_TASK_ROOT"]) / WORKSPACE / subject_id / "meta.json"
        subject_meta["status"] = "done"
        subject_path.write_text(json.dumps(subject_meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (subject_path.parent / "scope.json").write_text(json.dumps({
            "read_scope": ["src/**"],
            "write_scope": [],
            "forbidden_scope": ["secrets/**"],
        }, sort_keys=True), encoding="utf-8")
        subject_snapshot = subject_path.read_bytes()

        missing = args_for("review")
        rejected(create.handle(missing))
        print("AOTA_CANONICAL_REVIEW_MISSING_SUBJECT_DENY_PASS")

        rejected(create.handle(args_for("review", subject_task_id="missing-subject")))
        print("AOTA_CANONICAL_REVIEW_NONEXISTENT_SUBJECT_DENY_PASS")

        other_id, _ = make_task(create, freeze, args_for("implementation", workspace_id="other"))
        rejected(create.handle(args_for("review", subject_task_id=other_id)))
        print("AOTA_CANONICAL_REVIEW_CROSS_WORKSPACE_DENY_PASS")

        cross_project_id, cross_project_meta = make_task(
            create, freeze, args_for("implementation", project_id="other-project")
        )
        cross_project_meta["status"] = "done"
        cross_project_path = Path(os.environ["AOTA_PROFILE_TASK_ROOT"]) / WORKSPACE / cross_project_id / "meta.json"
        cross_project_path.write_text(json.dumps(cross_project_meta, sort_keys=True), encoding="utf-8")
        rejected(create.handle(args_for("review", subject_task_id=cross_project_id)))
        print("AOTA_CANONICAL_REVIEW_CROSS_PROJECT_DENY_PASS")

        review_id, review_meta = make_task(create, freeze, args_for("review", subject_task_id=subject_id))
        review_dir = Path(os.environ["AOTA_PROFILE_TASK_ROOT"]) / WORKSPACE / review_id
        spec = json.loads((review_dir / "SPEC.md").read_text(encoding="utf-8"))
        assert spec["subject_task_id"] == subject_id
        assert review_meta["subject_task_id"] == subject_id
        assert review_meta["spec"]["subject_task_id"] == subject_id
        assert review_meta["subject_task_id"] == review_meta["spec"]["subject_task_id"]
        print("AOTA_CANONICAL_REVIEW_SUBJECT_CREATE_PASS")
        print("AOTA_CANONICAL_REVIEW_SUBJECT_SPEC_PASS")
        print("AOTA_CANONICAL_REVIEW_SUBJECT_META_PASS")

        frozen_hash = review_meta["spec_hash"]
        assert spec["spec_hash"] == frozen_hash
        assert review_meta["spec"]["subject_task_id"] == subject_id
        print("AOTA_CANONICAL_REVIEW_SUBJECT_FREEZE_PASS")
        print("AOTA_REVIEW_SUBJECT_SPEC_META_PARITY_PASS")
        print("AOTA_REVIEW_SUBJECT_FROZEN_PARITY_PASS")

        validated = contract.validate_task_binding(review_meta, 1, frozen_hash)
        assert validated["subject_task_id"] == subject_id
        start._verify_subject_reviewable(WORKSPACE, subject_id, expected_project_id=PROJECT)
        print("AOTA_CANONICAL_REVIEW_SUBJECT_START_VALIDATION_PASS")

        running_meta = dict(review_meta)
        running_meta.update({
            "status": "running",
            "execution": {"start_id": review_id, "profile": "reviewer"},
        })
        meta_path = review_dir / "meta.json"
        meta_path.write_text(json.dumps(running_meta, sort_keys=True), encoding="utf-8")
        os.environ.update({
            "AOTA_PROFILE_TASK_WORKSPACE_ID": WORKSPACE,
            "AOTA_PROFILE_TASK_ID": review_id,
            "AOTA_PROFILE_TASK_START_ID": review_id,
            "AOTA_PROFILE_TASK_PROFILE": "reviewer",
        })
        opened = data(active.handle({"artifact": "BINDING"}))
        assert opened["status"] == "ok" and opened["content"]["subject_task_id"] == subject_id
        for artifact_name in ("SPEC.md", "scope.json", "meta.json"):
            subject_opened = data(subject_artifact.handle({
                "subject_task_id": subject_id,
                "artifact_name": artifact_name,
                "max_bytes": 65536,
            }))
            assert subject_opened["status"] == "ok", subject_opened
            assert subject_opened["subject_task_id"] == subject_id
            assert subject_opened["artifact_name"] == artifact_name
        assert rejected(subject_artifact.handle({
            "subject_task_id": subject_id,
            "artifact_name": "../../SPEC.md",
        }))
        assert rejected(subject_artifact.handle({
            "subject_task_id": subject_id,
            "artifact_name": "SPEC.md",
            "path": "SPEC.md",
        }))
        assert rejected(subject_artifact.handle({
            "subject_task_id": subject_id,
            "artifact_name": "SPEC.md",
            "max_bytes": 1,
        }))
        subject_spec_path = subject_path.parent / "SPEC.md"
        subject_spec_bytes = subject_spec_path.read_bytes()
        outside_subject = root / "outside-subject-spec.md"
        outside_subject.write_bytes(subject_spec_bytes)
        subject_spec_path.unlink()
        subject_spec_path.symlink_to(outside_subject)
        assert rejected(subject_artifact.handle({
            "subject_task_id": subject_id,
            "artifact_name": "SPEC.md",
        }))
        subject_spec_path.unlink()
        subject_spec_path.write_bytes(subject_spec_bytes)
        print("AOTA_SUBJECT_TASK_BOUNDED_ARTIFACTS_PASS")
        meta_path.write_text(json.dumps(review_meta, sort_keys=True), encoding="utf-8")
        print("AOTA_CANONICAL_REVIEW_SUBJECT_OPEN_PASS")

        stable = contract.canonical_hash(review_meta["spec"])
        reordered = contract.canonical_hash(dict(reversed(list(review_meta["spec"].items()))))
        assert stable == reordered == frozen_hash
        print("AOTA_CANONICAL_REVIEW_HASH_STABILITY_PASS")

        second_id, second_meta = make_task(create, freeze, args_for("implementation"))
        second_meta["status"] = "done"
        second_path = Path(os.environ["AOTA_PROFILE_TASK_ROOT"]) / WORKSPACE / second_id / "meta.json"
        second_path.write_text(json.dumps(second_meta, sort_keys=True), encoding="utf-8")
        second_review = data(create.handle(args_for("review", subject_task_id=second_id)))
        second_spec = json.loads((Path(os.environ["AOTA_PROFILE_TASK_ROOT"]) / WORKSPACE / second_review["task_id"] / "meta.json").read_text(encoding="utf-8"))["spec"]
        assert contract.canonical_hash(second_spec) != stable
        print("AOTA_CANONICAL_REVIEW_SUBJECT_HASH_BINDING_PASS")

        for kind in ("diagnosis", "stewardship"):
            non_review_id, non_review_meta = make_task(create, freeze, args_for(kind))
            assert "subject_task_id" not in non_review_meta["spec"]
        print("AOTA_NON_REVIEW_SUBJECT_COMPATIBILITY_PASS")

        legacy = contract.normalize_legacy({"task_kind": "review", "subject_task_id": subject_id})
        assert legacy["spec_kind"] == "review" and legacy["subject_task_id"] == subject_id
        print("AOTA_LEGACY_REVIEW_SUBJECT_COMPATIBILITY_PASS")

        draft_id, _ = make_task(create, freeze, args_for("implementation"))
        draft_path = Path(os.environ["AOTA_PROFILE_TASK_ROOT"]) / WORKSPACE / draft_id / "meta.json"
        draft_meta = json.loads(draft_path.read_text(encoding="utf-8"))
        draft_meta["status"] = "draft"
        draft_path.write_text(json.dumps(draft_meta, sort_keys=True), encoding="utf-8")
        try:
            start._verify_subject_reviewable(WORKSPACE, draft_id, expected_project_id=PROJECT)
        except Exception as exc:
            assert "subject_not_reviewable" in str(exc)
        else:
            raise AssertionError("non-terminal subject accepted")
        print("AOTA_CANONICAL_REVIEW_INVALID_SUBJECT_DENY_PASS")

        assert subject_path.read_bytes() == subject_snapshot
        print("AOTA_CANONICAL_REVIEW_SUBJECT_NO_EXISTING_TASK_MUTATION_PASS")
        print("FIXTURE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
