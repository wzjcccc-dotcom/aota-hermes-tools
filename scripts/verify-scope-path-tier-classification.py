#!/usr/bin/env python3
"""Isolated fixture for tiered scope classification and receipt shape."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import datetime
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "aota-tools"
TASK_ID = "pt_20260719T020202_feedface"


def load_scope():
    spec = importlib.util.spec_from_file_location("aota_scope_fixture", PLUGIN / "_profile_task_scope.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    scope = load_scope()
    with tempfile.TemporaryDirectory(prefix="aota-scope-tier-") as raw:
        root = Path(raw)
        workspace = root / "workspace"; workspace.mkdir()
        task_dir = root / "profile-tasks" / "fixture" / TASK_ID; task_dir.mkdir(parents=True)
        (task_dir / "scope.json").write_text(json.dumps({"workspace_id": "fixture", "project_id": "", "task_id": TASK_ID, "start_id": TASK_ID, "read_scope": ["src/**"], "write_scope": ["tmp/test.md"], "forbidden_scope": ["docs/private.md"]}), encoding="utf-8")
        os.environ["AOTA_PROFILE_TASK_ROOT"] = str(root / "profile-tasks")

        def event(path: str, operation: str, source: str, explicit: bool = True, task_id: str = TASK_ID) -> dict:
            return {"schema_version": 1, "workspace_id": "fixture", "project_id": "", "task_id": task_id, "start_id": task_id, "process_session_id": "", "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "path": path, "operation": operation, "source": source, "explicit_worker_action": explicit}

        valid_read = scope.classify_path("src/a.py", workspace_root=workspace, operation="read", source="aota_project_file_read", explicit_worker_action=True, active_task_root=root / "profile-tasks", active_task_dir=task_dir)
        assert valid_read["tier"] == "PROJECT_READ" and valid_read["scope_relevant"]
        print("AOTA_SCOPE_PROJECT_READ_CLASSIFICATION_PASS")
        valid_write = scope.classify_path("tmp/test.md", workspace_root=workspace, operation="write", source="aota_project_file_write", explicit_worker_action=True, active_task_root=root / "profile-tasks", active_task_dir=task_dir)
        assert valid_write["tier"] == "PROJECT_WRITE"
        print("AOTA_SCOPE_PROJECT_WRITE_CLASSIFICATION_PASS")
        active = scope.classify_path(str(task_dir / "SPEC.md"), workspace_root=workspace, operation="read", source="aota_active_task_artifact_open", explicit_worker_action=True, active_task_root=root / "profile-tasks", active_task_dir=task_dir)
        assert active["tier"] == "ACTIVE_TASK_READ" and not active["scope_relevant"]
        print("AOTA_SCOPE_ACTIVE_TASK_TIER_PASS")
        runtime = scope.classify_path("/aota-runtime/process-registry.json", workspace_root=workspace, operation="read", source="launcher", active_task_root=root / "profile-tasks", active_task_dir=task_dir)
        assert runtime["tier"] == "RUNTIME_CONTROL_PLANE" and not runtime["scope_relevant"]
        print("AOTA_SCOPE_RUNTIME_TIER_PASS")
        profile = scope.classify_path("/home/latios/.hermes/profiles/coder/config.yaml", workspace_root=workspace, operation="read", source="runtime", active_task_root=root / "profile-tasks", active_task_dir=task_dir)
        assert profile["tier"] == "PROFILE_RUNTIME"
        print("AOTA_SCOPE_PROFILE_RUNTIME_TIER_PASS")
        credential = scope.classify_path("/home/latios/.hermes/.env", workspace_root=workspace, operation="metadata", source="credential", active_task_root=root / "profile-tasks", active_task_dir=task_dir)
        assert credential["tier"] == "CREDENTIAL_AUTHORITY"
        print("AOTA_SCOPE_CREDENTIAL_TIER_PASS")

        events = [event("src/a.py", "read", "aota_project_file_read"), event("docs/private.md", "read", "aota_project_file_read"), event("tmp/test.md", "write", "aota_project_file_write"), event("src/a.py", "write", "aota_project_file_write"), event(str(task_dir / "SPEC.md"), "read", "aota_active_task_artifact_open"), event("/aota-runtime/worker.log", "write", "launcher", False), event("/home/latios/.hermes/profiles/coder/SOUL.md", "read", "runtime", False), event("/home/latios/.hermes/auth.json", "metadata", "credential", False)]
        (task_dir / "scope-events.jsonl").write_text("\n".join(json.dumps(item) for item in events) + "\n", encoding="utf-8")
        checked = scope.verify_scope(task_dir, workspace)
        compliance = checked["scope_compliance"]
        assert compliance["status"] == "violated" and compliance["project_checked_path_count"] == 4 and compliance["project_violated_path_count"] == 2
        assert compliance["active_task_read_count"] == 1 and compliance["ignored_runtime_path_count"] >= 3
        print("AOTA_SCOPE_RECEIPT_SCHEMA_PASS")
        print("AOTA_SCOPE_BOUNDED_VIOLATION_OUTPUT_PASS")

        empty_dir = root / "profile-tasks" / "fixture" / "empty"; empty_dir.mkdir()
        (empty_dir / "scope.json").write_text(json.dumps({"workspace_id": "fixture", "project_id": "", "task_id": "empty", "start_id": "empty", "read_scope": [], "write_scope": [], "forbidden_scope": []}), encoding="utf-8")
        (empty_dir / "scope-events.jsonl").write_text("\n".join(json.dumps(event(f"/aota-runtime/dependency-{n}.so", "read", "python-import", False, "empty")) for n in range(171)) + "\n", encoding="utf-8")
        empty = scope.verify_scope(empty_dir, workspace)
        assert empty["scope_compliance"]["status"] in {"compliant", "not_applicable"} and empty["scope_compliance"]["project_violated_path_count"] == 0
        print("AOTA_SCOPE_EMPTY_PROJECT_SCOPE_PASS")
        print("AOTA_SCOPE_FIXED_171_REGRESSION_PASS")

        unknown_dir = root / "profile-tasks" / "fixture" / "unknown"; unknown_dir.mkdir()
        (unknown_dir / "scope.json").write_text(json.dumps({"workspace_id": "fixture", "project_id": "", "task_id": "unknown", "start_id": "unknown", "read_scope": [], "write_scope": [], "forbidden_scope": []}), encoding="utf-8")
        (unknown_dir / "scope-events.jsonl").write_text(json.dumps(event("/opt/unclassified/external.dat", "read", "worker", True, "unknown")) + "\n", encoding="utf-8")
        unknown = scope.verify_scope(unknown_dir, workspace)
        assert unknown["scope_compliance"]["status"] == "unknown" and unknown["unknown_external_path_count"] == 1
        print("AOTA_SCOPE_UNKNOWN_EXTERNAL_FAIL_CLOSED_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
