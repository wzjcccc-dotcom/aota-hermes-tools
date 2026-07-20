#!/usr/bin/env python3
"""Isolated runtime fixture for active-task artifact access boundaries."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "aota-tools"
TASK_ID = "pt_20260719T010101_deadbeef"


def load_plugin() -> None:
    spec = importlib.util.spec_from_file_location("aota_tools", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)])
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["aota_tools"] = module
    spec.loader.exec_module(module)


def result(value: str) -> dict:
    return json.loads(value)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="aota-active-artifact-") as raw:
        root = Path(raw)
        task_dir = root / "profile-tasks" / "fixture" / TASK_ID
        task_dir.mkdir(parents=True)
        spec_text = "# Frozen SPEC\n\nread_scope: src/**\nAPI_KEY=should-not-leak\n"
        (task_dir / "SPEC.md").write_text(spec_text, encoding="utf-8")
        scope = {"workspace_id": "fixture", "task_id": TASK_ID, "read_scope": ["src/**"], "write_scope": [], "forbidden_scope": ["src/private/**"], "created_at": "2026-07-19T01:01:01Z", "spec_sha256": hashlib.sha256(spec_text.encode()).hexdigest(), "revision": 1}
        (task_dir / "scope.json").write_text(json.dumps(scope), encoding="utf-8")
        meta = {"contract_version": 1, "task_id": TASK_ID, "spec_id": TASK_ID, "workspace_id": "fixture", "project_id": "fixture-project", "work_item_id": "wi-fixture", "task_kind": "implementation", "spec_kind": "implementation", "resolved_profile": "coder", "revision": 1, "status": "running", "spec_hash": "a" * 64, "spec_sha256": scope["spec_sha256"], "execution": {"start_id": TASK_ID, "profile": "coder", "spec_revision": 1, "spec_hash": "a" * 64, "spec_sha256": scope["spec_sha256"]}, "spec": {"spec_id": TASK_ID, "spec_hash": "a" * 64}}
        (task_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        os.environ.update({"AOTA_PROFILE_TASK_ROOT": str(root / "profile-tasks"), "AOTA_PROFILE_TASK_WORKSPACE_ID": "fixture", "AOTA_PROFILE_TASK_ID": TASK_ID, "AOTA_PROFILE_TASK_START_ID": TASK_ID, "AOTA_PROFILE_TASK_PROFILE": "coder"})
        load_plugin()
        reader = importlib.import_module("aota_tools._active_task_artifact_open")
        context = importlib.import_module("aota_tools._active_task_context")
        writers = importlib.import_module("aota_tools._coder_report_submit")

        own_spec = result(reader.handle({"artifact": "SPEC"}))
        assert own_spec["status"] == "ok" and "should-not-leak" not in own_spec["content"]
        print("AOTA_ACTIVE_TASK_SPEC_READ_PASS")
        own_scope = result(reader.handle({"artifact": "SCOPE"}))
        assert own_scope["status"] == "ok" and own_scope["content"]["task_id"] == TASK_ID
        print("AOTA_ACTIVE_TASK_SCOPE_READ_PASS")
        binding = result(reader.handle({"artifact": "BINDING"}))
        assert binding["status"] == "ok" and set(binding["content"]) == {"workspace_id", "project_id", "work_item_id", "task_id", "start_id", "spec_id", "spec_revision", "spec_hash", "spec_sha256", "resolved_profile", "subject_task_id", "architecture_mode", "task_kind", "spec_kind", "status"}
        print("AOTA_ACTIVE_TASK_BINDING_READ_PASS")

        saved = {name: os.environ.get(name) for name in ("AOTA_PROFILE_TASK_ROOT", "AOTA_PROFILE_TASK_WORKSPACE_ID", "AOTA_PROFILE_TASK_ID", "AOTA_PROFILE_TASK_START_ID", "AOTA_PROFILE_TASK_PROFILE")}
        os.environ.pop("AOTA_PROFILE_TASK_ROOT")
        assert result(reader.handle({"artifact": "SPEC"}))["error_code"] == "active_task_context_missing"
        print("AOTA_ACTIVE_TASK_MISSING_ENV_DENY_PASS")
        os.environ.update(saved)
        os.environ["AOTA_PROFILE_TASK_START_ID"] = "pt_20260719T010102_deadbeef"
        assert result(reader.handle({"artifact": "SPEC"}))["error_code"] == "active_task_binding_invalid"
        print("AOTA_ACTIVE_TASK_ENV_MISMATCH_DENY_PASS")
        os.environ["AOTA_PROFILE_TASK_START_ID"] = TASK_ID
        assert result(reader.handle({"artifact": "SPEC", "task_id": TASK_ID}))["error_code"] == "active_task_artifact_not_allowed"
        print("AOTA_ACTIVE_TASK_OTHER_TASK_DENY_PASS")

        external = root / "external.md"; external.write_text("outside\n", encoding="utf-8")
        (task_dir / "SPEC.md").unlink(); (task_dir / "SPEC.md").symlink_to(external)
        assert result(reader.handle({"artifact": "SPEC"}))["error_code"] == "active_task_artifact_invalid"
        print("AOTA_ACTIVE_TASK_SYMLINK_ESCAPE_DENY_PASS")
        (task_dir / "SPEC.md").unlink(); (task_dir / "SPEC.md").write_text("x" * (64 * 1024 + 1), encoding="utf-8")
        assert result(reader.handle({"artifact": "SPEC"}))["error_code"] == "active_task_artifact_too_large"
        print("AOTA_ACTIVE_TASK_BOUNDED_OUTPUT_PASS")
        (task_dir / "SPEC.md").write_text(spec_text, encoding="utf-8")
        assert context.assert_artifact_target(task_dir, "SPEC.md", allowed={"CARD.json", "RESULT.md"}) if False else True
        try:
            context.assert_artifact_target(task_dir, "SPEC.md", allowed={"CARD.json", "RESULT.md"})
            raise AssertionError("immutable artifact was accepted")
        except context.ActiveTaskError:
            pass
        print("AOTA_ACTIVE_TASK_IMMUTABLE_ARTIFACT_DENY_PASS")

        card_result = result(writers.handle({"summary": "bounded writer fixture"}))
        assert card_result["status"] == "submitted" and (task_dir / "CARD.json").is_file() and (task_dir / "RESULT.md").is_file()
        print("AOTA_ACTIVE_TASK_CODER_WRITER_PASS")
        role_names = {"coder": ("CARD.json", "RESULT.md"), "debugger": ("DIAGNOSIS_CARD.json", "DIAGNOSIS.md"), "reviewer": ("REVIEW_CARD.json", "REVIEW.md"), "architect": ("ARCHITECT_CARD.json", "ARCHITECT_REVIEW.md"), "project-steward": ("STEWARD_CARD.json", "STEWARD_RESULT.md")}
        for role, names in role_names.items():
            for name in names:
                assert context.assert_artifact_target(task_dir, name, allowed=set(names)).name == name
            for forbidden in {"SPEC.md", "scope.json", "meta.json", "completion.json", "handoff.json"}:
                try:
                    context.assert_artifact_target(task_dir, forbidden, allowed=set(names))
                except context.ActiveTaskError:
                    continue
                raise AssertionError(f"{role} accepted immutable artifact")
            print(f"AOTA_ACTIVE_TASK_{role.upper().replace('-', '_')}_WRITER_BOUNDARY_PASS")
        print("AOTA_ACTIVE_TASK_ROLE_WRITER_BOUNDARY_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
