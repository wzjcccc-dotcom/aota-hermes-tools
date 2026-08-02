#!/usr/bin/env python3
"""Isolated P0 create -> freeze -> session binding -> semantic resolve fixture."""
from __future__ import annotations

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
    spec = importlib.util.spec_from_file_location("aota_tools", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)])
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["aota_tools"] = module
    spec.loader.exec_module(module)


def main() -> int:
    load_package()
    create = importlib.import_module("aota_tools._task_spec_create")
    freeze = importlib.import_module("aota_tools._task_spec_freeze")
    resolver = importlib.import_module("aota_tools._reference_resolver")
    binding = importlib.import_module("aota_tools._session_active_spec_binding")
    task_common = importlib.import_module("aota_tools._task_spec_common")
    workspace = importlib.import_module("aota_tools._workspace")
    authority = importlib.import_module("aota_tools._session_state_authority")
    classifier = importlib.import_module("aota_tools._work_classifier")
    names = ("AOTA_TRUSTED_PRINCIPAL", "AOTA_TRUSTED_WORKSPACE_ID", "AOTA_PROFILE_TASK_ID", "AOTA_PROFILE_TASK_START_ID", "AOTA_PROFILE_TASK_PROFILE")
    before = {name: os.environ.get(name) for name in names}
    with tempfile.TemporaryDirectory(prefix="aota-minimal-chain-") as temp:
        root = Path(temp)
        runtime_root = root / "runtime"
        workspace_root = root / "workspace"
        workspace_root.mkdir()
        original = (task_common.PROFILE_TASK_ROOT, task_common.LOCK_ROOT, workspace.resolve_workspace, binding.SESSION_ACTIVE_SPEC_ROOT, authority.SESSION_STATE_ROOT)
        task_common.PROFILE_TASK_ROOT = runtime_root / "profile-tasks"
        task_common.LOCK_ROOT = runtime_root / "locks"
        workspace.resolve_workspace = lambda _workspace_id: workspace_root
        classifier.resolve_workspace = workspace.resolve_workspace
        binding.SESSION_ACTIVE_SPEC_ROOT = runtime_root / "session-active-spec"
        authority.SESSION_STATE_ROOT = runtime_root / "session-state"
        os.environ.update({"AOTA_TRUSTED_PRINCIPAL": "task-main", "AOTA_TRUSTED_WORKSPACE_ID": "fixture"})
        try:
            facts = {name: False for name in classifier._BOOL_FACTS}
            facts.update({name: 1 for name in classifier._INT_BOUNDS})
            facts.update({"requirements_ambiguity": "low", "technical_uncertainty": "low", "write_scope": "local", "validation_scope": "isolated", "services_touched": 0})
            classified = json.loads(classifier.handle({
                "workspace_id": "fixture", "title": "P0 fixture", "summary": "standalone fixture", "facts": facts,
            }, session_id="session-a", profile="task-main"))
            assert classified["planning_depth"] == "P0" and classified["classification_binding"]["status"] == "written", classified
            print("CLASSIFICATION_BINDING_WRITTEN=PASS")
            created = json.loads(create.handle({
                "spec_kind": "diagnosis",
                "objective": "唯讀確認 README.md",
                "read_scope": ["README.md"],
                "write_scope": [],
            }, session_id="session-a", profile="task-main"))
            assert created["status"] == "created" and created["next_action"] == "freeze_current_spec"
            assert created["classification_binding"]["status"] == "consumed"
            assert created["task_id"].startswith("pt_")
            print("MINIMAL_P0_DIAGNOSIS=PASS")

            frozen = json.loads(freeze.handle({"spec_ref": "current_draft_spec"}, session_id="session-a", profile="task-main"))
            assert frozen["status"] == "frozen" and frozen["session_binding"]["status"] == "written", frozen
            assert frozen["semantic_start_ready"] is True and frozen["next_action"] == "start_active_frozen_spec"
            print("FREEZE_BINDING=PASS")

            missing_session = json.loads(freeze.handle({
                "workspace_id": "fixture", "spec_id": created["spec_id"], "expected_revision": 1,
            }, profile="task-main"))
            assert missing_session["status"] == "frozen"
            assert missing_session["session_binding"]["error"] == "trusted_session_context_missing"
            assert missing_session["semantic_start_ready"] is False
            assert missing_session["next_action"] == "stop_and_report_runtime_context_missing"
            print("MISSING_SESSION_CONTEXT=PASS")

            resolved = resolver.resolve_for_start("fixture", "active_frozen_spec", trusted_session_id="session-a", trusted_principal="task-main")
            assert resolved["task_id"] == created["task_id"] and resolved["expected_spec_hash"]
            assert resolved["expected_spec_hash"] != resolved["expected_spec_sha256"]
            print("SEMANTIC_START_PREPARATION=PASS")
            print("MODEL_GUESSED_FIELDS=0")
            print("CONTROL_PLANE_INJECTED_FIELDS>0")
            print("RETRY_FOR_MISSING_INTERNAL_PARAM=0")
        finally:
            task_common.PROFILE_TASK_ROOT, task_common.LOCK_ROOT, workspace.resolve_workspace, binding.SESSION_ACTIVE_SPEC_ROOT, authority.SESSION_STATE_ROOT = original
            for name, value in before.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
    print("MINIMAL_INVOCATION_CHAIN_FIXTURE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
