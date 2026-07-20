#!/usr/bin/env python3
"""Isolated PCF-WI post-write verification and artifact authority fixtures."""

from __future__ import annotations

import datetime as dt
import importlib
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "aota-tools"


def load_plugin() -> None:
    for name in list(sys.modules):
        if name == "aota_tools" or name.startswith("aota_tools."):
            del sys.modules[name]
    spec = importlib.util.spec_from_file_location("aota_tools", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)])
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["aota_tools"] = module
    spec.loader.exec_module(module)


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_task(root: Path, scope_mod, task_id: str, *, read_scope: list[str] | None = None) -> tuple[Path, dict]:
    task_dir = root / "profile-tasks" / "fixture" / task_id
    task_dir.mkdir(parents=True)
    scope = {
        "schema_version": 1, "workspace_id": "fixture", "project_id": "fixture-project",
        "task_id": task_id, "start_id": task_id, "spec_id": "spec-fixture",
        "spec_revision": 1, "spec_hash": "a" * 64,
        "read_scope": read_scope or [], "write_scope": ["tmp/a.md"], "forbidden_scope": ["secrets/**"],
        "scope_source": "payload", "scope_schema_version": 1, "created_at": now(),
        "process_session_id": f"scope_{task_id}",
    }
    scope["scope_digest"] = scope_mod.compute_scope_digest(scope)
    (task_dir / "scope.json").write_text(json.dumps(scope), encoding="utf-8")
    meta = {
        "workspace_id": "fixture", "project_id": "fixture-project", "task_id": task_id,
        "spec_id": "spec-fixture", "spec_hash": "a" * 64, "spec_sha256": "b" * 64,
        "revision": 1, "status": "running", "contract_version": 1, "spec_kind": "implementation",
        "resolved_profile": "coder", "work_item_id": "wi-fixture",
        "execution": {"start_id": task_id, "profile": "coder", "started_at": scope["created_at"], "scope_process_session_id": scope["process_session_id"]},
    }
    (task_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return task_dir, scope


def env_for(root: Path, task_id: str, workspace: Path | None = None) -> None:
    os.environ.update({
        "AOTA_PROFILE_TASK_ROOT": str(root / "profile-tasks"),
        "AOTA_PROFILE_TASK_WORKSPACE_ID": "fixture", "AOTA_PROFILE_TASK_ID": task_id,
        "AOTA_PROFILE_TASK_START_ID": task_id, "AOTA_PROFILE_TASK_PROFILE": "coder",
        "AOTA_PROFILE_TASK_PROCESS_SESSION_ID": f"scope_{task_id}",
    })
    if workspace is not None:
        os.environ["AOTA_WORKSPACE_ROOT"] = str(workspace)


def event(scope_mod, task_dir: Path, path: str, operation: str, *, allowed: bool, success: bool | None = None, attribution: str | None = None, authority: str | None = None, task_id: str | None = None) -> None:
    if task_id is not None:
        record = {
            "schema_version": 1, "workspace_id": "fixture", "project_id": "fixture-project",
            "task_id": task_id, "start_id": task_id, "process_session_id": f"scope_{task_id}",
            "timestamp": now(), "path": path, "operation": "write", "tier": "PROJECT_WRITE",
            "source": "aota_project_file_write", "explicit_worker_action": True,
            "scope_relevant": True, "allowed": allowed, "decision_reason": "scope_allowed" if allowed else "write_scope_denied",
        }
        if success is not None:
            record["success"] = success
        with (task_dir / "scope-events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        return
    scope_mod.record_scope_event(task_dir, path=path, operation=operation, source="aota_project_file_read" if operation == "read" else "aota_project_file_write", allowed=allowed, success=success, attribution=attribution, authority=authority, decision_reason="post_write_verification_allowed" if attribution else ("scope_allowed" if allowed else "read_scope_denied"))


def main() -> int:
    load_plugin()
    scope_mod = importlib.import_module("aota_tools._task_spec_scope")
    verifier = importlib.import_module("aota_tools._profile_task_scope")
    reader = importlib.import_module("aota_tools._active_task_artifact_open")
    contracts = importlib.import_module("aota_tools._spec_contract")
    handoff = importlib.import_module("aota_tools._handoff_common")

    with tempfile.TemporaryDirectory(prefix="aota-pcf-post-write-", dir=str(REPO / "tmp")) as raw:
        root = Path(raw)
        workspace = root / "workspace"
        workspace.mkdir()
        target = workspace / "tmp" / "a.md"
        target.parent.mkdir()
        target.write_text("written\n", encoding="utf-8")

        task_id = "pt_20260719T000001_aaaabbbb"
        task_dir, scope = make_task(root, scope_mod, task_id)
        env_for(root, task_id, workspace)
        event(verifier, task_dir, "tmp/a.md", "create", allowed=True, success=True)
        assert verifier.has_current_task_prior_write(task_dir, path="tmp/a.md", workspace_root=workspace, scope=scope)
        event(verifier, task_dir, "tmp/a.md", "read", allowed=True, attribution="post_write_verification", authority="current_task_prior_write")
        result = verifier.verify_scope(task_dir, workspace)["scope_compliance"]
        assert result["status"] == "compliant" and result["post_write_verification_read_count"] == 1 and result["project_violated_path_count"] == 0
        print("AOTA_POST_WRITE_VERIFICATION_READ_PASS")

        before_id = "pt_20260719T000002_bbbbcccc"
        before_dir, before_scope = make_task(root, scope_mod, before_id)
        env_for(root, before_id, workspace)
        event(verifier, before_dir, "tmp/a.md", "read", allowed=False)
        assert not verifier.has_current_task_prior_write(before_dir, path="tmp/a.md", workspace_root=workspace, scope=before_scope)
        assert verifier.verify_scope(before_dir, workspace)["scope_compliance"]["status"] == "violated"
        print("AOTA_POST_WRITE_READ_BEFORE_WRITE_DENY_PASS")

        other_path = "pt_20260719T000003_ccccdddd"
        other_dir, other_scope = make_task(root, scope_mod, other_path)
        env_for(root, other_path, workspace)
        event(verifier, other_dir, "tmp/a.md", "create", allowed=True, success=True)
        event(verifier, other_dir, "tmp/b.md", "read", allowed=False)
        assert not verifier.has_current_task_prior_write(other_dir, path="tmp/b.md", workspace_root=workspace, scope=other_scope)
        print("AOTA_POST_WRITE_OTHER_PATH_DENY_PASS")

        foreign_id = "pt_20260719T000004_ddddeeee"
        foreign_dir, foreign_scope = make_task(root, scope_mod, foreign_id)
        env_for(root, foreign_id, workspace)
        event(verifier, foreign_dir, "tmp/a.md", "create", allowed=True, success=True, task_id="pt_20260719T000005_eeeeffff")
        assert not verifier.has_current_task_prior_write(foreign_dir, path="tmp/a.md", workspace_root=workspace, scope=foreign_scope)
        print("AOTA_POST_WRITE_OTHER_TASK_DENY_PASS")

        failed_id = "pt_20260719T000006_ffff0000"
        failed_dir, failed_scope = make_task(root, scope_mod, failed_id)
        env_for(root, failed_id, workspace)
        event(verifier, failed_dir, "tmp/a.md", "write", allowed=False, success=False)
        assert not verifier.has_current_task_prior_write(failed_dir, path="tmp/a.md", workspace_root=workspace, scope=failed_scope)
        print("AOTA_POST_WRITE_FAILED_WRITE_DENY_PASS")

        link_id = "pt_20260719T000007_11112222"
        link_dir, link_scope = make_task(root, scope_mod, link_id)
        env_for(root, link_id, workspace)
        (workspace / "tmp" / "alias.md").symlink_to(target)
        event(verifier, link_dir, "tmp/a.md", "create", allowed=True, success=True)
        assert not verifier.has_current_task_prior_write(link_dir, path="tmp/alias.md", workspace_root=workspace, scope=link_scope)
        print("AOTA_POST_WRITE_SYMLINK_ESCAPE_DENY_PASS")

        current_scope = {
            "schema_version": 1, "workspace_id": "fixture", "project_id": "fixture-project",
            "task_id": task_id, "start_id": task_id, "spec_id": "spec-fixture", "spec_revision": 1,
            "spec_hash": "a" * 64, "read_scope": [], "write_scope": ["tmp/a.md"], "forbidden_scope": ["secrets/**"],
            "scope_source": "payload", "scope_schema_version": 1, "process_session_id": scope["process_session_id"], "scope_digest": scope["scope_digest"], "created_at": scope["created_at"],
        }
        env_for(root, task_id, workspace)
        (task_dir / "scope.json").write_text(json.dumps(current_scope), encoding="utf-8")
        opened = json.loads(reader.handle({"artifact": "SCOPE"}))
        assert opened["status"] == "ok" and opened["content"]["legacy_schema"] is False and opened["content"]["artifact_digest"]
        print("AOTA_ACTIVE_TASK_SCOPE_CURRENT_SCHEMA_PASS")
        (task_dir / "scope.json").write_text(json.dumps({**current_scope, "scope_digest": "0" * 64}), encoding="utf-8")
        assert json.loads(reader.handle({"artifact": "SCOPE"}))["error_code"] == "scope_digest_mismatch"
        print("AOTA_ACTIVE_TASK_SCOPE_DIGEST_VALIDATION_PASS")
        (task_dir / "scope.json").write_text(json.dumps({**current_scope, "scope_digest": scope["scope_digest"], "spec_id": "foreign-spec"}), encoding="utf-8")
        assert json.loads(reader.handle({"artifact": "SCOPE"}))["error_code"] == "active_task_binding_invalid"
        print("AOTA_ACTIVE_TASK_SCOPE_BINDING_VALIDATION_PASS")
        (task_dir / "scope.json").write_text(json.dumps({**current_scope, "scope_digest": scope["scope_digest"], "task_id": "pt_20260719T000008_33334444"}), encoding="utf-8")
        assert json.loads(reader.handle({"artifact": "SCOPE"}))["error_code"] == "active_task_binding_invalid"
        print("AOTA_ACTIVE_TASK_SCOPE_OTHER_TASK_DENY_PASS")

        card = contracts.apply_common_card({"contract_version": 1, "task_id": task_id, "spec_id": "spec-fixture", "revision": 1, "spec_hash": "a" * 64, "project_id": "fixture-project", "work_item_id": "wi-fixture", "spec_kind": "implementation", "resolved_profile": "coder", "status": "running"}, "coder", {"summary": "worker says zero", "outcome": "completed", "verdict": "pass"}, "RESULT.md")
        assert card["final_scope_compliance"] == "pending_finalizer" and card["scope_observation"]["authority"] == "worker_observation"
        (task_dir / "CARD.json").write_text(json.dumps(card), encoding="utf-8")
        (task_dir / "RESULT.md").write_text("worker report\n", encoding="utf-8")
        receipt_scope = {"status": "violated", "worker_action_violation_count": 1, "violations": [{"path": "tmp/a.md"}]}
        (task_dir / f"completion.{task_id}.json").write_text(json.dumps({"workspace_id": "fixture", "task_id": task_id, "start_id": task_id, "scope_compliance": receipt_scope}), encoding="utf-8")
        handoff_data = handoff.build_handoff_data("fixture", task_id, task_id, "coder", "implementation", "done", now(), task_dir=task_dir)
        assert handoff_data["scope_compliance"] == receipt_scope and handoff_data["scope_compliance_source"] == "completion_receipt"
        print("AOTA_CARD_SCOPE_NON_AUTHORITATIVE_PASS")
        print("AOTA_RECEIPT_SCOPE_AUTHORITATIVE_PASS")
        print("AOTA_HANDOFF_RECEIPT_SCOPE_SOURCE_PASS")
        print("AOTA_CARD_RECEIPT_COUNT_MISMATCH_HANDLED_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
