#!/usr/bin/env python3
"""Focused verifier for aota_project_initialize_core (PCF-WI-NEW-PROJECT-INITIALIZATION-CORE).

Covers all 8 idempotency cases plus invalid project.yaml/observed.json,
initialized_core receipt, illegal initialized receipt, scope violation,
non-task-main SPEC, and wrong profile.

Uses temp workspace/fixtures only. No pytest. Stdlib-only + PyYAML.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugin" / "aota-tools"

_passed = 0
_failed = 0
_failures: list[str] = []


def _check(condition: bool, message: str) -> None:
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        _failures.append(message)
        print(f"FAIL: {message}")


def _import_plugin():
    """Import plugin modules without going through __init__.py."""
    pkg = types.ModuleType("aota_tools")
    pkg.__path__ = [str(PLUGIN_DIR)]
    pkg.__package__ = "aota_tools"
    sys.modules["aota_tools"] = pkg

    def _import(name: str):
        mod_path = PLUGIN_DIR / f"{name}.py"
        spec = importlib.util.spec_from_file_location(f"aota_tools.{name}", str(mod_path))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"aota_tools.{name}"] = mod
        spec.loader.exec_module(mod)
        return mod

    _import("_project_common")
    _import("_workspace")
    _import("_spec_contract")
    _import("_project_lifecycle_contract")
    _import("_project_registry")
    _import("_project_initializer")
    return sys.modules["aota_tools._project_initializer"]


def _make_spec(
    project_id: str,
    created_by: str = "task-main",
    spec_kind: str = "stewardship",
    resolved_profile: str = "project-steward",
) -> dict:
    """Create a valid stewardship spec for testing."""
    spec: dict = {
        "schema_version": 1,
        "artifact_type": "spec",
        "spec_id": "test-spec-001",
        "project_id": project_id,
        "work_item_id": "test-wi-001",
        "spec_kind": spec_kind,
        "resolved_profile": resolved_profile,
        "revision": 1,
        "status": "frozen",
        "created_at": "2026-07-21T00:00:00Z",
        "updated_at": "2026-07-21T00:00:00Z",
        "created_by": created_by,
        "objective": "Test initialization core",
        "summary": "Test stewardship spec for project initialization core",
        "context_refs": [],
        "related_artifacts": [],
        "acceptance_criteria": ["Test criterion"],
        "constraints": [],
        "forbidden_actions": [],
        "expected_artifacts": [],
        "capability_contract": {
            "source_read": True,
            "project_metadata_write": True,
            "codegraph_read": True,
        },
        "payload": {
            "operation": "intake",
            "project_context_questions": ["What is the project?"],
        },
        "supersedes_spec_id": None,
    }
    from aota_tools._spec_contract import canonical_hash
    spec["spec_hash"] = canonical_hash(spec)
    spec["frozen_at"] = "2026-07-21T00:00:00Z"
    return spec


def _setup_env(
    workspace_root: Path,
    task_id: str,
    project_id: str,
    profile: str = "project-steward",
    spec: dict | None = None,
) -> None:
    """Set up environment for trusted steward binding."""
    if spec is None:
        spec = _make_spec(project_id)

    # Create workspaces.json
    registry_path = workspace_root.parent / "workspaces.json"
    registry = {"test-ws": {"candidates": [str(workspace_root)]}}
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    # Set env vars
    os.environ["AOTA_WORKSPACE_REGISTRY_PATH"] = str(registry_path)
    os.environ["AOTA_PROFILE_TASK_PROFILE"] = profile
    os.environ["AOTA_PROFILE_TASK_ID"] = task_id
    os.environ["AOTA_PROFILE_TASK_START_ID"] = task_id
    os.environ["AOTA_PROFILE_TASK_WORKSPACE_ID"] = "test-ws"

    # Create task root and meta.json
    task_root = workspace_root.parent / "tasks"
    task_dir = task_root / "test-ws" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    os.environ["AOTA_PROFILE_TASK_ROOT"] = str(task_root)

    meta = {
        "contract_version": 1,
        "status": "running",
        "spec_kind": spec.get("spec_kind", "stewardship"),
        "task_kind": spec.get("spec_kind", "stewardship"),
        "resolved_profile": "project-steward",
        "spec_hash": spec["spec_hash"],
        "execution": {"start_id": task_id},
        "spec": spec,
        "task_id": task_id,
    }
    (task_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    # Ensure HERMES_HOME is not set to agent context to avoid canonical root check
    os.environ.pop("HERMES_HOME", None)


def _run_init(init_module, args: dict) -> dict:
    """Run handle_initialize_core and return parsed result."""
    return json.loads(init_module.handle_initialize_core(args))


def _base_args(
    project_id: str = "test-project",
    project_root: str = "projects/test-project",
    project_name: str = "Test Project",
    project_kind: str = "test-project",
    summary: str = "Test summary",
    capabilities: list | None = None,
    source_root: str = "src",
) -> dict:
    return {
        "workspace_id": "test-ws",
        "project_id": project_id,
        "project_name": project_name,
        "project_kind": project_kind,
        "project_root": project_root,
        "summary": summary,
        "capabilities": capabilities or [],
        "source_root": source_root,
    }


# ---------------------------------------------------------------------------
# Case 1: First init
# ---------------------------------------------------------------------------

def test_first_init(init_module) -> None:
    """Case 1: First init should succeed with initialized_core state."""
    with tempfile.TemporaryDirectory(prefix="init-core-") as temp:
        ws_root = Path(temp) / "workspace"
        ws_root.mkdir()
        _setup_env(ws_root, "task-001", "test-project")
        result = _run_init(init_module, _base_args())
        _check(result["status"] == "initialized_core", f"first init status: {result.get('status')}")
        _check(result["final_state"] == "initialized_core", f"first init final_state: {result.get('final_state')}")
        _check(result["git"]["status"] == "not_requested", "git status not_requested")
        _check(result["codegraph"]["status"] == "not_requested", "codegraph status not_requested")
        _check(result["authority"] == "trusted_finalizer", "authority trusted_finalizer")
        receipt = result.get("receipt", {})
        _check(receipt.get("state") == "initialized_core", "receipt state initialized_core")
        _check(receipt.get("authority") == "trusted_finalizer", "receipt authority trusted_finalizer")
        _check(receipt.get("git_summary") == {"present": False, "status": "not_requested"}, "receipt git_summary")
        _check(receipt.get("codegraph_summary") == {"present": False, "status": "not_requested"}, "receipt codegraph_summary")
        # Check scaffold
        proj = ws_root / "projects" / "test-project"
        _check((proj / ".aota" / "project.yaml").is_file(), "project.yaml exists")
        _check((proj / ".aota" / "observed.json").is_file(), "observed.json exists")
        for d in ("docs", "scripts", "profiles", "skills", "tests", "src"):
            _check((proj / d).is_dir(), f"scaffold dir {d} exists")
        # Check registry
        reg_path = ws_root / ".aota" / "registry" / "projects.json"
        _check(reg_path.is_file(), "registry file exists")
        reg = json.loads(reg_path.read_text())
        _check(any(p["project_id"] == "test-project" for p in reg.get("projects", [])), "project in registry")
        print("CASE_1_FIRST_INIT_PASS")


# ---------------------------------------------------------------------------
# Case 2: Identical rerun (idempotent)
# ---------------------------------------------------------------------------

def test_identical_rerun(init_module) -> None:
    """Case 2: Identical rerun should succeed with idempotent_success."""
    with tempfile.TemporaryDirectory(prefix="init-core-") as temp:
        ws_root = Path(temp) / "workspace"
        ws_root.mkdir()
        _setup_env(ws_root, "task-002", "test-project")
        args = _base_args()
        r1 = _run_init(init_module, args)
        _check(r1["status"] == "initialized_core", "first init for rerun test")
        r2 = _run_init(init_module, args)
        _check(r2["status"] == "idempotent_success", f"rerun status: {r2.get('status')}")
        _check(r2["final_state"] == "initialized_core", "rerun final_state")
        _check(r2["changed_paths"] == [], "rerun changed_paths empty")
        _check(r2["git"]["status"] == "not_requested", "rerun git not_requested")
        _check(r2["codegraph"]["status"] == "not_requested", "rerun codegraph not_requested")
        _check(r2["receipt"]["state"] == "initialized_core", "rerun receipt state")
        print("CASE_2_IDENTICAL_RERUN_PASS")


# ---------------------------------------------------------------------------
# Case 3: Duplicate project ID
# ---------------------------------------------------------------------------

def test_duplicate_project_id(init_module) -> None:
    """Case 3: Duplicate project ID at different root should be rejected."""
    with tempfile.TemporaryDirectory(prefix="init-core-") as temp:
        ws_root = Path(temp) / "workspace"
        ws_root.mkdir()
        _setup_env(ws_root, "task-003", "test-project")
        r1 = _run_init(init_module, _base_args(project_id="test-project", project_root="projects/proj-a"))
        _check(r1["status"] == "initialized_core", "first init for dup ID test")
        r2 = _run_init(init_module, _base_args(project_id="test-project", project_root="projects/proj-b"))
        _check(r2["status"] == "rejected", f"dup ID status: {r2.get('status')}")
        _check("duplicate_project_id" in r2.get("error", ""), f"dup ID error: {r2.get('error')}")
        print("CASE_3_DUPLICATE_PROJECT_ID_PASS")


# ---------------------------------------------------------------------------
# Case 4: Duplicate root
# ---------------------------------------------------------------------------

def test_duplicate_root(init_module) -> None:
    """Case 4: Duplicate root with different project ID should be rejected."""
    with tempfile.TemporaryDirectory(prefix="init-core-") as temp:
        ws_root = Path(temp) / "workspace"
        ws_root.mkdir()
        _setup_env(ws_root, "task-004a", "proj-a")
        r1 = _run_init(init_module, _base_args(project_id="proj-a", project_root="projects/shared"))
        _check(r1["status"] == "initialized_core", "first init for dup root test")
        # Set up new spec with proj-b for the second init
        _setup_env(ws_root, "task-004b", "proj-b")
        r2 = _run_init(init_module, _base_args(project_id="proj-b", project_root="projects/shared"))
        _check(r2["status"] == "rejected", f"dup root status: {r2.get('status')}")
        _check("duplicate_root" in r2.get("error", ""), f"dup root error: {r2.get('error')}")
        print("CASE_4_DUPLICATE_ROOT_PASS")


# ---------------------------------------------------------------------------
# Case 5: Partial scaffold
# ---------------------------------------------------------------------------

def test_partial_scaffold(init_module) -> None:
    """Case 5: Partial scaffold should be rejected."""
    with tempfile.TemporaryDirectory(prefix="init-core-") as temp:
        ws_root = Path(temp) / "workspace"
        ws_root.mkdir()
        _setup_env(ws_root, "task-005", "test-project")
        proj_root = ws_root / "projects" / "test-project"
        (proj_root / "docs").mkdir(parents=True)
        r = _run_init(init_module, _base_args())
        _check(r["status"] == "rejected", f"partial scaffold status: {r.get('status')}")
        _check("partial_scaffold" in r.get("error", ""), f"partial scaffold error: {r.get('error')}")
        print("CASE_5_PARTIAL_SCAFFOLD_PASS")


# ---------------------------------------------------------------------------
# Case 6: Non-empty root
# ---------------------------------------------------------------------------

def test_non_empty_root(init_module) -> None:
    """Case 6: Non-empty root with non-scaffold content should be rejected."""
    with tempfile.TemporaryDirectory(prefix="init-core-") as temp:
        ws_root = Path(temp) / "workspace"
        ws_root.mkdir()
        _setup_env(ws_root, "task-006", "test-project")
        proj_root = ws_root / "projects" / "test-project"
        proj_root.mkdir(parents=True)
        (proj_root / "random-file.txt").write_text("random content", encoding="utf-8")
        r = _run_init(init_module, _base_args())
        _check(r["status"] == "rejected", f"non-empty status: {r.get('status')}")
        _check("non_empty_root" in r.get("error", ""), f"non-empty error: {r.get('error')}")
        print("CASE_6_NON_EMPTY_ROOT_PASS")


# ---------------------------------------------------------------------------
# Case 7: Symlink/traversal
# ---------------------------------------------------------------------------

def test_symlink_traversal(init_module) -> None:
    """Case 7: Symlink and path traversal should be rejected."""
    with tempfile.TemporaryDirectory(prefix="init-core-") as temp:
        ws_root = Path(temp) / "workspace"
        ws_root.mkdir()
        _setup_env(ws_root, "task-007", "test-project")
        # Path traversal
        r = _run_init(init_module, _base_args(project_root="../escape"))
        _check(r["status"] == "rejected", f"traversal status: {r.get('status')}")
        _check("invalid_project_root" in r.get("error", ""), f"traversal error: {r.get('error')}")
        # Absolute path
        r_abs = _run_init(init_module, _base_args(project_root="/etc"))
        _check(r_abs["status"] == "rejected", f"absolute path status: {r_abs.get('status')}")
        _check("invalid_project_root" in r_abs.get("error", ""), f"absolute path error: {r_abs.get('error')}")
        # Symlink
        proj_root = ws_root / "projects" / "test-project"
        proj_root.mkdir(parents=True)
        symlink_path = ws_root / "projects" / "symlink-target"
        symlink_path.symlink_to(proj_root)
        r_sym = _run_init(init_module, _base_args(project_root="projects/symlink-target"))
        _check(r_sym["status"] == "rejected", f"symlink status: {r_sym.get('status')}")
        _check("symlink_rejected" in r_sym.get("error", ""), f"symlink error: {r_sym.get('error')}")
        print("CASE_7_SYMLINK_TRAVERSAL_PASS")


# ---------------------------------------------------------------------------
# Case 8: Registry exact-match failure
# ---------------------------------------------------------------------------

def test_registry_exact_match_failure(init_module) -> None:
    """Case 8: Registry exact-match failure should be rejected on rerun."""
    with tempfile.TemporaryDirectory(prefix="init-core-") as temp:
        ws_root = Path(temp) / "workspace"
        ws_root.mkdir()
        _setup_env(ws_root, "task-008", "test-project")
        r1 = _run_init(init_module, _base_args())
        _check(r1["status"] == "initialized_core", "first init for registry match test")
        # Modify registry to have wrong root
        reg_path = ws_root / ".aota" / "registry" / "projects.json"
        reg = json.loads(reg_path.read_text())
        for p in reg.get("projects", []):
            if p["project_id"] == "test-project":
                p["root"] = "wrong/root"
        reg_path.write_text(json.dumps(reg), encoding="utf-8")
        r2 = _run_init(init_module, _base_args())
        _check(r2["status"] == "rejected", f"registry match status: {r2.get('status')}")
        _check("registry_exact_match_failure" in r2.get("error", ""), f"registry match error: {r2.get('error')}")
        print("CASE_8_REGISTRY_EXACT_MATCH_FAILURE_PASS")


# ---------------------------------------------------------------------------
# Case 9: Invalid project.yaml
# ---------------------------------------------------------------------------

def test_invalid_project_yaml(init_module) -> None:
    """Invalid project.yaml should be rejected as partial_scaffold."""
    with tempfile.TemporaryDirectory(prefix="init-core-") as temp:
        ws_root = Path(temp) / "workspace"
        ws_root.mkdir()
        _setup_env(ws_root, "task-009", "test-project")
        proj_root = ws_root / "projects" / "test-project"
        aota_dir = proj_root / ".aota"
        aota_dir.mkdir(parents=True)
        (aota_dir / "project.yaml").write_text("schema_version: 99\n", encoding="utf-8")
        (aota_dir / "observed.json").write_text("{}", encoding="utf-8")
        for d in ("docs", "scripts", "profiles", "skills", "tests", "src"):
            (proj_root / d).mkdir(exist_ok=True)
        r = _run_init(init_module, _base_args())
        _check(r["status"] == "rejected", f"invalid yaml status: {r.get('status')}")
        _check("partial_scaffold" in r.get("error", ""), f"invalid yaml error: {r.get('error')}")
        print("CASE_9_INVALID_PROJECT_YAML_PASS")


# ---------------------------------------------------------------------------
# Case 10: Invalid observed.json
# ---------------------------------------------------------------------------

def test_invalid_observed_json(init_module) -> None:
    """Invalid observed.json should be rejected as partial_scaffold."""
    with tempfile.TemporaryDirectory(prefix="init-core-") as temp:
        ws_root = Path(temp) / "workspace"
        ws_root.mkdir()
        _setup_env(ws_root, "task-010", "test-project")
        proj_root = ws_root / "projects" / "test-project"
        aota_dir = proj_root / ".aota"
        aota_dir.mkdir(parents=True)
        # Write a valid project.yaml
        import yaml
        project_yaml = {
            "schema_version": 1,
            "project": {"id": "test-project", "name": "Test", "kind": "test-project", "status": "planned"},
            "summary": "Test",
            "capabilities": [],
            "paths": {
                "source_root": "src",
                "source": ["src"],
                "docs": ["docs"],
                "scripts": ["scripts"],
                "profiles": ["profiles"],
                "skills": ["skills"],
                "tests": ["tests"],
            },
            "commands": {
                "validate": ["scripts/validate.py"],
                "deploy": ["scripts/deploy.py"],
                "verify_deploy": ["scripts/verify-deploy.py"],
            },
            "runtime": {"deployment_type": "manual", "requires_human_checkpoint": True},
            "codegraph": {"enabled": False, "index_location": ".codegraph"},
            "plan": {"active_plan_id": None},
            "constraints": [],
        }
        (aota_dir / "project.yaml").write_text(yaml.safe_dump(project_yaml, sort_keys=False), encoding="utf-8")
        (aota_dir / "observed.json").write_text("invalid json content", encoding="utf-8")
        for d in ("docs", "scripts", "profiles", "skills", "tests", "src"):
            (proj_root / d).mkdir(exist_ok=True)
        r = _run_init(init_module, _base_args())
        _check(r["status"] == "rejected", f"invalid observed status: {r.get('status')}")
        _check("partial_scaffold" in r.get("error", ""), f"invalid observed error: {r.get('error')}")
        print("CASE_10_INVALID_OBSERVED_JSON_PASS")


# ---------------------------------------------------------------------------
# Case 11: initialized_core receipt validation
# ---------------------------------------------------------------------------

def test_initialized_core_receipt(init_module) -> None:
    """initialized_core receipt should have correct structure and authority."""
    with tempfile.TemporaryDirectory(prefix="init-core-") as temp:
        ws_root = Path(temp) / "workspace"
        ws_root.mkdir()
        _setup_env(ws_root, "task-011", "test-project")
        r = _run_init(init_module, _base_args())
        receipt = r.get("receipt", {})
        _check(receipt.get("schema_version") == 1, "receipt schema_version")
        _check(receipt.get("project_id") == "test-project", "receipt project_id")
        _check(receipt.get("workspace_id") == "test-ws", "receipt workspace_id")
        _check(receipt.get("state") == "initialized_core", "receipt state")
        _check(receipt.get("authority") == "trusted_finalizer", "receipt authority")
        _check("git_summary" in receipt, "receipt has git_summary")
        _check("codegraph_summary" in receipt, "receipt has codegraph_summary")
        _check("completed_at" in receipt, "receipt has completed_at")
        # Verify tool never produces "initialized"
        _check(receipt.get("state") != "initialized", "receipt state is never initialized")
        _check(r.get("final_state") != "initialized", "final_state is never initialized")
        print("CASE_11_INITIALIZED_CORE_RECEIPT_PASS")


# ---------------------------------------------------------------------------
# Case 12: Illegal initialized receipt
# ---------------------------------------------------------------------------

def test_illegal_initialized_receipt(init_module) -> None:
    """Illegal initialized receipt (missing git/codegraph) should be rejected."""
    from aota_tools._project_lifecycle_contract import (
        INITIALIZED,
        INITIALIZED_CORE,
        validate_initialization_receipt,
    )
    # Receipt with state="initialized" but missing git/codegraph summaries
    illegal_receipt = {
        "schema_version": 1,
        "project_id": "test-project",
        "workspace_id": "test-ws",
        "state": INITIALIZED,
        "git_summary": None,
        "codegraph_summary": None,
        "authority": "trusted_finalizer",
        "completed_at": "2026-07-21T00:00:00Z",
    }
    errors = validate_initialization_receipt(illegal_receipt)
    _check(
        "initialized_receipt_missing_git_codegraph_summary" in errors,
        f"illegal initialized receipt rejected: {errors}",
    )
    # Tool never produces "initialized" state
    with tempfile.TemporaryDirectory(prefix="init-core-") as temp:
        ws_root = Path(temp) / "workspace"
        ws_root.mkdir()
        _setup_env(ws_root, "task-012", "test-project")
        r = _run_init(init_module, _base_args())
        _check(r.get("final_state") == INITIALIZED_CORE, "tool only produces initialized_core")
        _check(r.get("receipt", {}).get("state") == INITIALIZED_CORE, "receipt state is initialized_core")
        print("CASE_12_ILLEGAL_INITIALIZED_RECEIPT_PASS")


# ---------------------------------------------------------------------------
# Case 13: Scope violation (path traversal)
# ---------------------------------------------------------------------------

def test_scope_violation(init_module) -> None:
    """Scope violation - path traversal and absolute paths should be rejected."""
    with tempfile.TemporaryDirectory(prefix="init-core-") as temp:
        ws_root = Path(temp) / "workspace"
        ws_root.mkdir()
        _setup_env(ws_root, "task-013", "test-project")
        # Absolute path
        r = _run_init(init_module, _base_args(project_root="/etc/passwd"))
        _check(r["status"] == "rejected", f"absolute path status: {r.get('status')}")
        _check("invalid_project_root" in r.get("error", ""), f"absolute path error: {r.get('error')}")
        # Path with ..
        r2 = _run_init(init_module, _base_args(project_root="projects/../../escape"))
        _check(r2["status"] == "rejected", f"traversal status: {r2.get('status')}")
        _check("invalid_project_root" in r2.get("error", ""), f"traversal error: {r2.get('error')}")
        # NUL byte
        r3 = _run_init(init_module, _base_args(project_root="projects\x00test"))
        _check(r3["status"] == "rejected", f"nul byte status: {r3.get('status')}")
        print("CASE_13_SCOPE_VIOLATION_PASS")


# ---------------------------------------------------------------------------
# Case 14: Non-task-main SPEC
# ---------------------------------------------------------------------------

def test_non_task_main_spec(init_module) -> None:
    """SPEC with created_by != task-main should be rejected."""
    with tempfile.TemporaryDirectory(prefix="init-core-") as temp:
        ws_root = Path(temp) / "workspace"
        ws_root.mkdir()
        spec = _make_spec("test-project", created_by="not-task-main")
        _setup_env(ws_root, "task-014", "test-project", spec=spec)
        r = _run_init(init_module, _base_args())
        _check(r["status"] == "rejected", f"non-task-main status: {r.get('status')}")
        _check("invalid_stewardship_spec" in r.get("error", ""), f"non-task-main error: {r.get('error')}")
        print("CASE_14_NON_TASK_MAIN_SPEC_PASS")


# ---------------------------------------------------------------------------
# Case 15: Wrong profile
# ---------------------------------------------------------------------------

def test_wrong_profile(init_module) -> None:
    """Wrong profile (not project-steward) should be rejected."""
    with tempfile.TemporaryDirectory(prefix="init-core-") as temp:
        ws_root = Path(temp) / "workspace"
        ws_root.mkdir()
        _setup_env(ws_root, "task-015", "test-project", profile="coder")
        r = _run_init(init_module, _base_args())
        _check(r["status"] == "rejected", f"wrong profile status: {r.get('status')}")
        _check("profile_not_allowed" in r.get("error", ""), f"wrong profile error: {r.get('error')}")
        print("CASE_15_WRONG_PROFILE_PASS")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    global _failed, _failures

    try:
        init_module = _import_plugin()
    except Exception as exc:
        print(f"IMPORT_FAIL: {type(exc).__name__}: {exc}")
        return 1

    tests = [
        test_first_init,
        test_identical_rerun,
        test_duplicate_project_id,
        test_duplicate_root,
        test_partial_scaffold,
        test_non_empty_root,
        test_symlink_traversal,
        test_registry_exact_match_failure,
        test_invalid_project_yaml,
        test_invalid_observed_json,
        test_initialized_core_receipt,
        test_illegal_initialized_receipt,
        test_scope_violation,
        test_non_task_main_spec,
        test_wrong_profile,
    ]

    for test in tests:
        try:
            test(init_module)
        except Exception as exc:
            _failed += 1
            _failures.append(f"{test.__name__}: {type(exc).__name__}: {exc}")
            print(f"FAIL: {test.__name__}: {type(exc).__name__}: {exc}")

    if _failed:
        print(f"\nNEW_PROJECT_INITIALIZATION_CORE_FAIL: {_failed} failures, {_passed} passed")
        for f in _failures:
            print(f"  {f}")
        return 1

    print(f"\nNEW_PROJECT_INITIALIZATION_CORE_PASS: {_passed} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())