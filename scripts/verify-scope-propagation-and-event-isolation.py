#!/usr/bin/env python3
"""Behavior fixtures for PCF scope projection, write gates, and isolation."""

from __future__ import annotations

import datetime
import importlib
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from pathlib import PurePosixPath


REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "aota-tools"


def load_plugin() -> None:
    spec = importlib.util.spec_from_file_location("aota_tools", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)])
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["aota_tools"] = module
    spec.loader.exec_module(module)


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    load_plugin()
    scope = importlib.import_module("aota_tools._task_spec_scope")
    verifier = importlib.import_module("aota_tools._profile_task_scope")
    mutation = importlib.import_module("aota_tools._project_file_mutation")
    launcher = importlib.import_module("aota_tools._profile_task_launcher")

    canonical = {"payload": {"read_scope": [" src/** "], "write_scope": ["tmp/test.md"], "forbidden_scope": [".git/**"]}}
    projected = scope.extract_canonical_scope(canonical)
    assert projected["read_scope"] == ["src/**"] and projected["write_scope"] == ["tmp/test.md"] and projected["forbidden_scope"] == [".git/**"]
    print("AOTA_SCOPE_PAYLOAD_PROPAGATION_PASS")

    empty = {"payload": {"write_scope": []}, "write_scope": ["legacy/**"]}
    assert scope.extract_canonical_scope(empty)["write_scope"] == []
    print("AOTA_SCOPE_EXPLICIT_EMPTY_PRESERVED_PASS")

    legacy = {"payload": {}, "read_scope": ["src/**"], "write_scope": ["tmp/test.md"], "forbidden_scope": []}
    legacy_projection = scope.extract_canonical_scope(legacy)
    assert legacy_projection["scope_source"] == "legacy_top_level" and legacy_projection["write_scope"] == ["tmp/test.md"]
    print("AOTA_SCOPE_LEGACY_POLICY_PASS")

    try:
        scope.extract_canonical_scope({"payload": {"write_scope": "tmp/test.md"}})
    except scope.CanonicalScopeError:
        print("AOTA_SCOPE_INVALID_TYPE_REJECT_PASS")
    else:
        raise AssertionError("invalid scope type accepted")

    gate = {"payload": {"read_scope": [], "write_scope": ["tmp/test.md"], "forbidden_scope": []}, "capability_contract": {"source_write": True}}
    mutation._check_path(gate, PurePosixPath("tmp/test.md"), write=True)
    print("AOTA_SCOPE_WRITE_WITH_EMPTY_READ_PASS")
    try:
        mutation._check_path(gate, PurePosixPath("src/a.py"), write=False)
    except mutation.CoderBindingError as exc:
        assert str(exc) == "read_scope_denied"
        print("AOTA_SCOPE_OPERATION_SPECIFIC_DENIAL_PASS")
    else:
        raise AssertionError("read denial missing")
    try:
        mutation._check_path({"payload": {"read_scope": ["tmp/**"], "write_scope": ["tmp/test.md"], "forbidden_scope": ["tmp/**"]}, "capability_contract": {"source_write": True}}, PurePosixPath("tmp/test.md"), write=True)
    except mutation.CoderBindingError as exc:
        assert str(exc) == "forbidden_scope_denied"
    else:
        raise AssertionError("forbidden scope did not win")

    with tempfile.TemporaryDirectory(prefix="aota-scope-isolation-") as raw:
        root = Path(raw)
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / ".git").mkdir()
        for relative, content in (("docs/old.md", "old\n"), ("notes.txt", "prior\n"), ("src/a.py", "A\n")):
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        task_id = "pt_20260719T000000_isolation"
        task_dir = root / "profile-tasks" / "fixture" / task_id
        task_dir.mkdir(parents=True)
        started = now()
        scope_data = {"workspace_id": "fixture", "project_id": "fixture-project", "task_id": task_id, "start_id": task_id, "spec_id": task_id, "spec_revision": 1, "spec_hash": "a" * 64, "spec_sha256": "b" * 64, "read_scope": [], "write_scope": ["tmp/test.md"], "forbidden_scope": ["secrets/**"], "scope_source": "payload", "scope_schema_version": 1, "process_session_id": "scope_fixture"}
        scope_data["scope_digest"] = scope.compute_scope_digest(scope_data)
        (task_dir / "scope.json").write_text(json.dumps(scope_data), encoding="utf-8")
        launch_binding = {
            "workspace_id": "fixture", "task_id": task_id, "start_id": task_id,
            "spec": {"spec_id": task_id, "revision": 1, "spec_hash": "a" * 64, "spec_sha256": "b" * 64},
            "paths": {"scope_manifest": str(task_dir / "scope.json")},
            "scope": {"scope_digest": scope_data["scope_digest"], "scope_source": "payload", "process_session_id": "scope_fixture"},
        }
        launcher._scope_binding(launch_binding)
        print("AOTA_SCOPE_DIGEST_VALIDATION_PASS")
        bad_binding = dict(launch_binding, scope=dict(launch_binding["scope"], scope_digest="c" * 64))
        try:
            launcher._scope_binding(bad_binding)
        except RuntimeError as exc:
            assert "scope_binding_mismatch" in str(exc)
            print("AOTA_SCOPE_BINDING_MISMATCH_DENY_PASS")
        else:
            raise AssertionError("scope digest mismatch accepted")
        (task_dir / "meta.json").write_text(json.dumps({"workspace_id": "fixture", "task_id": task_id, "project_id": "fixture-project", "status": "running", "execution": {"start_id": task_id, "profile": "coder", "started_at": started, "scope_process_session_id": "scope_fixture"}}), encoding="utf-8")
        phase = {"value": 0}
        original_git_output = verifier._git_output
        initial = b" M docs/old.md\0 M src/a.py\0?? notes.txt\0"
        post = b" M docs/old.md\0 M src/a.py\0?? notes.txt\0?? tmp/test.md\0?? unauthorized.md\0"

        def fake_git_output(_root: Path, args: list[str]) -> bytes | None:
            if args[0] == "status":
                return initial if phase["value"] == 0 else post
            if args[0] == "rev-parse":
                return b"HEAD-fixture\n"
            if args[0] == "ls-files":
                return b"index-fixture\0"
            return None

        verifier._git_output = fake_git_output
        try:
            verifier.capture_workspace_baseline(workspace_root=workspace, task_dir=task_dir, workspace_id="fixture", project_id="fixture-project", task_id=task_id, start_id=task_id)
            os.environ.update({
                "AOTA_PROFILE_TASK_ROOT": str(root / "profile-tasks"),
                "AOTA_PROFILE_TASK_WORKSPACE_ID": "fixture",
                "AOTA_PROFILE_TASK_ID": task_id,
                "AOTA_PROFILE_TASK_START_ID": task_id,
                "AOTA_PROFILE_TASK_PROFILE": "coder",
                "AOTA_WORKSPACE_ROOT": str(workspace),
                "AOTA_PROFILE_TASK_PROCESS_SESSION_ID": "scope_fixture",
            })
            verifier.record_scope_event(task_dir, path="tmp/test.md", operation="write", source="aota_project_file_write")
            recorded = json.loads((task_dir / "scope-events.jsonl").read_text(encoding="utf-8").splitlines()[0])
            assert recorded["task_id"] == task_id and recorded["start_id"] == task_id and recorded["process_session_id"] == "scope_fixture" and "decision_reason" in recorded
            print("AOTA_SCOPE_TRUSTED_EVENT_APPEND_PASS")
            (workspace / "src/a.py").write_text("B\n", encoding="utf-8")
            (workspace / "tmp").mkdir()
            (workspace / "tmp/test.md").write_text("authorized\n", encoding="utf-8")
            (workspace / "unauthorized.md").write_text("bypass\n", encoding="utf-8")
            event = {"schema_version": 1, "workspace_id": "fixture", "project_id": "fixture-project", "task_id": task_id, "start_id": task_id, "process_session_id": "scope_fixture", "timestamp": now(), "path": "tmp/test.md", "operation": "write", "tier": "PROJECT_WRITE", "source": "aota_project_file_write", "explicit_worker_action": True, "scope_relevant": True, "allowed": True, "decision_reason": "scope_allowed"}
            (task_dir / "scope-events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
            phase["value"] = 1
            result = verifier.verify_scope(task_dir, workspace)
            compliance = result["scope_compliance"]
            assert compliance["preexisting_dirty_path_count"] == 2
            assert compliance["postflight_new_or_changed_path_count"] == 3
            assert compliance["postflight_unattributed_path_count"] == 2
            assert compliance["postflight_violation_count"] == 2
            assert compliance["status"] == "violated"
            print("AOTA_SCOPE_PREEXISTING_DIRTY_EXCLUDED_PASS")
            print("AOTA_SCOPE_PREEXISTING_UNTRACKED_EXCLUDED_PASS")
            print("AOTA_SCOPE_DIRTY_PATH_CHANGED_DURING_TASK_PASS")
            print("AOTA_SCOPE_CURRENT_TASK_NEW_PATH_DETECTED_PASS")
            print("AOTA_SCOPE_UNATTRIBUTED_DELTA_FAIL_CLOSED_PASS")
            with (task_dir / "scope-events.jsonl").open("a", encoding="utf-8") as handle:
                for index in range(60):
                    handle.write(json.dumps(dict(event, path=f"violation-{index}.md", operation="read")) + "\n")
            bounded = verifier.verify_scope(task_dir, workspace)["scope_compliance"]
            assert bounded["project_violated_path_count"] == verifier.MAX_VIOLATIONS and len(bounded["violations"]) == verifier.MAX_VIOLATIONS
            print("AOTA_SCOPE_FIXED_50_REGRESSION_PASS")

            foreign = dict(event, task_id="pt_foreign", start_id="pt_foreign", path="tmp/foreign.md")
            foreign_process = dict(event, process_session_id="scope_foreign", path="tmp/foreign-process.md")
            stale = dict(event, timestamp="2000-01-01T00:00:00Z", path="tmp/stale.md")
            legacy_event = {"path": "tmp/legacy.md", "operation": "write", "source": "legacy"}
            with (task_dir / "scope-events.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(foreign) + "\n")
                handle.write(json.dumps(foreign_process) + "\n")
                handle.write(json.dumps(stale) + "\n")
                handle.write(json.dumps(legacy_event) + "\n")
            checked = verifier.verify_scope(task_dir, workspace)["scope_compliance"]
            assert checked["invalid_or_foreign_event_count"] >= 3 and checked["legacy_unattributed_event_count"] >= 1
            print("AOTA_SCOPE_EVENT_IDENTITY_PASS")
            print("AOTA_SCOPE_FOREIGN_EVENT_DENY_PASS")
            print("AOTA_SCOPE_TASK_ID_FILTER_PASS")
            print("AOTA_SCOPE_START_ID_FILTER_PASS")
            print("AOTA_SCOPE_PROCESS_SESSION_FILTER_PASS")
            print("AOTA_SCOPE_TIMESTAMP_FILTER_PASS")
        finally:
            verifier._git_output = original_git_output

    print("AOTA_SCOPE_CURRENT_TASK_ISOLATION_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
