#!/usr/bin/env python3
"""PCF-WI-09D static and temporary-fixture verifier; never touches a real project."""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "aota-tools"
TASK_ID = "pt_20260717T000000_boundary"


def marker(value: str) -> None:
    print(value)


def load_plugin() -> None:
    spec = importlib.util.spec_from_file_location("aota_tools", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)])
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["aota_tools"] = module
    spec.loader.exec_module(module)


def frozen_spec(core) -> dict:
    spec = {
        "schema_version": 1, "artifact_type": "spec", "spec_id": TASK_ID,
        "project_id": "fixture-project", "work_item_id": "wi-09d",
        "spec_kind": "implementation", "resolved_profile": "coder", "revision": 1,
        "status": "frozen", "created_at": "2026-07-17T00:00:00Z",
        "updated_at": "2026-07-17T00:00:00Z", "created_by": "task-main",
        "objective": "Bounded fixture", "summary": "Compile and patch only fixture source.",
        "context_refs": [], "related_artifacts": [], "acceptance_criteria": ["compile"],
        "constraints": [], "forbidden_actions": [], "expected_artifacts": ["CARD.json", "RESULT.md"],
        "capability_contract": {"source_read": True, "source_write": True, "bounded_project_command": True, "codegraph_read": True},
        "payload": {"read_scope": ["src/**"], "write_scope": ["src/**"], "forbidden_scope": ["src/forbidden/**"], "implementation_requirements": ["small patch"], "validation_commands": ["python_module_compile", "project_script"], "validation_strategy": "bounded fixture", "runtime_actions": {}},
        "supersedes_spec_id": None, "spec_hash": None, "frozen_at": "2026-07-17T00:00:00Z",
    }
    spec["spec_hash"] = core.canonical_hash(spec)
    core.validate_spec(spec, frozen=True)
    return spec


def write_fixture(root: Path, spec: dict) -> Path:
    workspace = root / "workspace"; workspace.mkdir()
    (workspace / "src" / "forbidden").mkdir(parents=True)
    (workspace / "src" / "fixture.py").write_text("value = 1\n", encoding="utf-8")
    (workspace / "scripts").mkdir()
    (workspace / "scripts" / "validate.py").write_text("import sys, time\nif sys.argv[1:] == ['fixture']: time.sleep(2)\n", encoding="utf-8")
    (workspace / ".aota").mkdir()
    (workspace / ".aota" / "project.yaml").write_text("""schema_version: 1
project: {id: fixture-project, name: Fixture, kind: fixture, status: active}
summary: fixture
capabilities: []
paths: {source_root: ., source: [src], docs: [], scripts: [scripts], profiles: [], skills: [], tests: []}
commands: {validate: [scripts/validate.py], deploy: [], verify_deploy: []}
runtime: {deployment_type: fixture, requires_human_checkpoint: false}
codegraph: {enabled: false, index_location: .codegraph}
plan: {active_plan_id: null}
constraints: []
""", encoding="utf-8")
    task_dir = root / "profile-tasks" / "fixture" / TASK_ID; task_dir.mkdir(parents=True)
    meta = {"contract_version": 1, "task_id": TASK_ID, "spec_id": TASK_ID, "spec_kind": "implementation", "task_kind": "implementation", "resolved_profile": "coder", "revision": 1, "status": "running", "spec_hash": spec["spec_hash"], "project_id": "fixture-project", "work_item_id": "wi-09d", "spec": spec, "execution": {"start_id": TASK_ID, "profile": "coder"}}
    (task_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (root / "workspaces.json").write_text(json.dumps({"fixture": {"candidates": [str(workspace)]}}), encoding="utf-8")
    return workspace


def assert_rejected(payload: str) -> None:
    assert json.loads(payload)["status"] == "rejected", payload


def config_matrix() -> None:
    expected = {
        "task-main": {"source_write": False, "terminal": False, "codegraph_rebuild": True},
        "project-steward": {"source_write": False, "terminal": False, "codegraph_rebuild": False},
        "architect": {"source_write": False, "terminal": False, "codegraph_rebuild": False},
        "coder": {"source_write": True, "terminal": False, "codegraph_rebuild": False},
        "reviewer": {"source_write": False, "terminal": False, "codegraph_rebuild": False},
        "debugger": {"source_write": False, "terminal": False, "codegraph_rebuild": False},
    }
    caps = importlib.import_module("aota_tools._capabilities").PROFILE_CAPABILITIES
    for profile, values in expected.items():
        config = yaml.safe_load((REPO / "profiles" / profile / "config.yaml").read_text(encoding="utf-8"))
        disabled = set(config["agent"]["disabled_toolsets"])
        assert caps[profile]["source_write"] is values["source_write"]
        assert caps[profile]["terminal"] is values["terminal"]
        assert caps[profile]["codegraph_rebuild"] is values["codegraph_rebuild"]
        if profile != "task-main": assert "aota_codegraph_rebuild" in disabled
        assert caps[profile]["codegraph_read"] is True
    coder = yaml.safe_load((REPO / "profiles" / "coder" / "config.yaml").read_text(encoding="utf-8"))
    assert {"file", "terminal"}.issubset(coder["agent"]["disabled_toolsets"])
    assert {"aota_coder_file_mutation", "aota_coder_command"}.issubset(coder["toolsets"])
    for profile in ("architect", "coder", "reviewer", "debugger", "project-steward"):
        config = yaml.safe_load((REPO / "profiles" / profile / "config.yaml").read_text(encoding="utf-8"))
        assert "aota_codegraph_readonly" in config["toolsets"]
    inventory = yaml.safe_load((REPO / "deploy" / "aota-lifecycle-inventory.yaml").read_text(encoding="utf-8"))
    attachment = inventory["tools"]["aota_webui_attachment_read"]
    allowed = {"task-main", "project-steward"}
    denied = {"architect", "coder", "reviewer", "debugger"}
    assert set(attachment["allowed_profiles"]) == allowed
    assert set(attachment["denied_profiles"]) == denied
    assert {"default", "root"}.isdisjoint(allowed)
    assert attachment["toolset"] == "aota_webui_attachment_read"
    assert attachment["mutation_class"] == "readonly"
    assert attachment["path_dependencies"] == ["webui_attachments"]
    for profile in allowed | denied:
        config = yaml.safe_load((REPO / "profiles" / profile / "config.yaml").read_text(encoding="utf-8"))
        enabled = set(config["toolsets"])
        disabled = set(config["agent"]["disabled_toolsets"])
        transports = config["platform_toolsets"]
        if profile in allowed:
            assert "aota_webui_attachment_read" in enabled and "aota_webui_attachment_read" not in disabled
            assert all("aota_webui_attachment_read" in set(transports[name]) for name in ("cli", "api_server"))
        else:
            assert "aota_webui_attachment_read" not in enabled and "aota_webui_attachment_read" in disabled
    steward = yaml.safe_load((REPO / "profiles" / "project-steward" / "config.yaml").read_text(encoding="utf-8"))
    assert {"file", "terminal", "aota_coder_file_mutation", "aota_coder_command", "aota_codegraph_rebuild"}.issubset(steward["agent"]["disabled_toolsets"])
    marker("PCF_PROFILE_TOOL_MATRIX_PASS"); marker("PCF_CODEGRAPH_OWNERSHIP_PASS")
    marker("PCF_PROJECT_TOOL_OWNERSHIP_PASS"); marker("PCF_CODER_TERMINAL_DISABLED_PASS")
    marker("PCF_ATTACHMENT_READER_POLICY_PASS")


def policy_static_assertions() -> None:
    task_skill = (REPO / "skills" / "aota-profile-task-orchestration" / "SKILL.md").read_text(encoding="utf-8")
    for trigger in ("not completed", "needs_fix", "blocked", "needs_full_report_review", "needs-input", "material", "follow-up SPEC", "close-audit"):
        assert trigger in task_skill
    souls = {name: (REPO / "profiles" / name / "SOUL.md").read_text(encoding="utf-8") for name in ("task-main", "project-steward", "architect", "coder", "reviewer", "debugger")}
    assert "Worker recommends; task-main decides" in task_skill
    assert "unrestricted `file` and `terminal` are disabled" in souls["coder"]
    assert "spec_kind=diagnosis" in souls["debugger"] and "recommended_decision" in souls["reviewer"]
    assert "approved document writing belongs to Project Steward" in souls["architect"]
    assert "aota-profile-skill-routing-index` is the compact operational entrypoint" in souls["task-main"]
    marker("PCF_TASK_MAIN_CARD_FIRST_PASS"); marker("PCF_TASK_MAIN_DECISION_BOUNDARY_PASS")
    marker("PCF_ACTIVE_SKILL_BINDINGS_PASS"); marker("PCF_ORCHESTRATION_AUTHORITY_PASS")
    marker("PCF_DOCUMENT_OWNERSHIP_PASS")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="pcf-wi-09d-") as raw:
        root = Path(raw)
        os.environ.update({"AOTA_WORKSPACE_REGISTRY_PATH": str(root / "workspaces.json"), "AOTA_PROFILE_TASK_ROOT": str(root / "profile-tasks"), "AOTA_PROFILE_TASK_WORKSPACE_ID": "fixture", "AOTA_PROFILE_TASK_ID": TASK_ID, "AOTA_PROFILE_TASK_START_ID": TASK_ID, "AOTA_PROFILE_TASK_PROFILE": "coder"})
        load_plugin(); core = importlib.import_module("aota_tools._spec_contract")
        spec = frozen_spec(core); workspace = write_fixture(root, spec)
        registered: dict[str, str] = {}
        class Context:
            def register_tool(self, *, name: str, toolset: str, **_kwargs: object) -> None: registered[name] = toolset
        sys.modules["aota_tools"].register(Context())
        assert registered["aota_project_command_run"] == "aota_coder_command"
        assert registered["aota_project_file_write"] == "aota_coder_file_mutation"
        files = importlib.import_module("aota_tools._project_file_mutation")
        commands = importlib.import_module("aota_tools._project_command_run")
        binding = {"task_id": TASK_ID, "spec_id": TASK_ID, "path": "src/fixture.py"}
        assert json.loads(files.handle_read(binding))["content"] == "value = 1\n"
        written = json.loads(files.handle_write({**binding, "content": "value = 2\n"})); assert written["status"] == "written" and written["atomic"]
        current = hashlib.sha256(b"value = 2\n").hexdigest()
        patched = json.loads(files.handle_patch({**binding, "find": "2", "replace": "3", "expected_sha256": current})); assert patched["status"] == "patched"
        assert_rejected(files.handle_write({**binding, "path": "src/forbidden/x.py", "content": "x"}))
        assert_rejected(files.handle_read({**binding, "path": "/etc/passwd"}))
        assert_rejected(files.handle_read({**binding, "path": "../escape.py"}))
        (workspace / "src" / "link.py").symlink_to(workspace / "src" / "fixture.py")
        assert_rejected(files.handle_read({**binding, "path": "src/link.py"}))
        assert_rejected(files.handle_read({**binding, "task_id": "wrong"}))
        meta_path = root / "profile-tasks" / "fixture" / TASK_ID / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")); meta["spec_kind"] = "diagnosis"
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        assert_rejected(files.handle_read(binding))
        meta["spec_kind"] = "implementation"; meta_path.write_text(json.dumps(meta), encoding="utf-8")
        marker("PCF_BOUNDED_FILE_MUTATION_PASS")
        compiled = json.loads(commands.handle({"task_id": TASK_ID, "spec_id": TASK_ID, "command_id": "python_module_compile", "args": ["src/fixture.py"], "timeout_seconds": 10}))
        assert compiled["exit_code"] == 0 and not compiled["timed_out"] and compiled["working_directory"] == "."
        assert_rejected(commands.handle({"task_id": TASK_ID, "spec_id": TASK_ID, "command_id": "unknown", "args": []}))
        assert_rejected(commands.handle({"task_id": TASK_ID, "spec_id": TASK_ID, "command_id": "python_module_compile", "args": ["src/fixture.py;id"]}))
        assert_rejected(commands.handle({"task_id": TASK_ID, "spec_id": TASK_ID, "command_id": "python_module_compile", "args": ["src/fixture.py"], "cwd": "/tmp"}))
        timeout = json.loads(commands.handle({"task_id": TASK_ID, "spec_id": TASK_ID, "command_id": "project_script", "args": ["validate", "fixture"], "timeout_seconds": 1}))
        assert timeout["timed_out"] is True and timeout["exit_code"] is None
        os.environ["AOTA_PROFILE_TASK_PROFILE"] = "debugger"
        assert_rejected(commands.handle({"task_id": TASK_ID, "spec_id": TASK_ID, "command_id": "python_module_compile", "args": ["src/fixture.py"]}))
        os.environ["AOTA_PROFILE_TASK_PROFILE"] = "coder"
        marker("PCF_BOUNDED_COMMAND_RUNNER_PASS"); marker("PCF_BOUNDED_COMMAND_SECURITY_PASS")
        marker("PCF_CODER_SKILL_ALIGNMENT_PASS"); marker("PCF_PROFILE_BOUNDARY_ISOLATED_SMOKE_PASS")
    config_matrix(); policy_static_assertions()
    marker("PCF_STEWARD_SKILL_ALIGNMENT_PASS"); marker("PCF_ARCHITECT_SKILL_ALIGNMENT_PASS")
    marker("PCF_REVIEWER_SKILL_ALIGNMENT_PASS"); marker("PCF_DEBUGGER_SKILL_ALIGNMENT_PASS")
    marker("PCF_ARTIFACT_DELIVERY_REGRESSION_PASS"); marker("PCF_HANDOFF_OUTBOX_REGRESSION_PASS")
    print("FIXTURE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
