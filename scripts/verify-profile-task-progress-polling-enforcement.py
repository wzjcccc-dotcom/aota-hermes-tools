#!/usr/bin/env python3
"""Verifier for PCF-WI-PROFILE-TASK-PROGRESS-POLLING-ENFORCEMENT-MINIMUM.

Validates Cases A-J from the work item using stdlib + importlib to load the
plugin module (_profile_task_status.py) directly, with tempfile fixture tasks
that never touch a real project.

Cases:
  A: Terminal status without retrieval_reason succeeds.
  B: Draft status without retrieval_reason succeeds.
  C: Running without retrieval_reason → normal_path_progress_poll_forbidden.
  D: Running with valid retrieval_reason passes the missing-reason guard.
  E: Running with invalid retrieval_reason → error.
  F: Running repeated query within interval → repeated_progress_poll_forbidden.
  G: Terminal with retrieval_reason still works.
  H: Not-found task without retrieval_reason → not_found.
  I: All six retrieval_reason values are valid.
  J: SCHEMA declares retrieval_reason as an optional parameter.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "aota-tools"
FIXTURE_PATH = REPO / "fixtures" / "profile-task-progress-polling-enforcement-cases.json"
TASK_ID = "pt_20260721T000000_aabbccdd"
WORKSPACE_ID = "polltest"


def marker(value: str) -> None:
    print(value)


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_plugin() -> object:
    """Register a lightweight aota_tools package without importing tool modules."""
    import types
    module = types.ModuleType("aota_tools")
    module.__path__ = [str(PLUGIN)]
    module.__package__ = "aota_tools"
    module.__file__ = str(PLUGIN / "__init__.py")
    sys.modules["aota_tools"] = module
    return module


def load_status_module():
    """Load _profile_task_status module directly for SCHEMA and constant access."""
    status_path = PLUGIN / "_profile_task_status.py"
    spec = importlib.util.spec_from_file_location(
        "aota_tools._profile_task_status", str(status_path),
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # We need the parent package for relative imports to work.
    # Register a minimal aota_tools package first.
    if "aota_tools" not in sys.modules:
        pkg_spec = importlib.util.spec_from_file_location(
            "aota_tools",
            PLUGIN / "__init__.py",
            submodule_search_locations=[str(PLUGIN)],
        )
        assert pkg_spec and pkg_spec.loader
        pkg_mod = importlib.util.module_from_spec(pkg_spec)
        sys.modules["aota_tools"] = pkg_mod
        pkg_spec.loader.exec_module(pkg_mod)
    spec.loader.exec_module(module)
    return module


def load_fixture() -> dict:
    if not FIXTURE_PATH.is_file():
        raise AssertionError(f"fixture file missing: {FIXTURE_PATH}")
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise AssertionError("fixture must be a JSON object")
    return data


def write_meta(root: Path, task_id: str, status: str) -> Path:
    """Write a fixture meta.json for a task with the given status."""
    task_dir = root / "profile-tasks" / WORKSPACE_ID / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "contract_version": 1,
        "task_id": task_id,
        "spec_id": task_id,
        "spec_kind": "implementation",
        "task_kind": "implementation",
        "resolved_profile": "coder",
        "revision": 1,
        "status": status,
        "spec_hash": "testhash",
        "project_id": "fixture-project",
        "work_item_id": "wi-poll",
        "workspace_id": WORKSPACE_ID,
        "execution": {
            "start_id": task_id,
            "profile": "coder",
            "started_at": "2026-07-21T00:00:00Z",
        },
    }
    meta_path = task_dir / "meta.json"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return task_dir


def setup_env(root: Path) -> None:
    """Set environment variables so task_spec_common resolves to fixture root."""
    os.environ["AOTA_PROFILE_TASK_ROOT"] = str(root / "profile-tasks")
    os.environ["AOTA_RUNTIME_ROOT"] = str(root / "aota-runtime")
    os.environ["AOTA_PROFILE_TASK_WORKSPACE_ID"] = WORKSPACE_ID
    os.environ["AOTA_PROFILE_TASK_ID"] = TASK_ID
    os.environ["AOTA_PROFILE_TASK_START_ID"] = TASK_ID
    os.environ["AOTA_PROFILE_TASK_PROFILE"] = "coder"


# ---------------------------------------------------------------------------
# Individual case verifiers
# ---------------------------------------------------------------------------


def case_a_terminal_no_reason(fixture: dict, status_mod, root: Path) -> None:
    """Case A: Terminal (done) task without retrieval_reason succeeds."""
    case = fixture["case_fixtures"]["case_a_terminal_status_no_retrieval_reason_needed"]
    write_meta(root, TASK_ID, case["meta_status"])
    result = json.loads(
        status_mod.handle({
            "workspace_id": WORKSPACE_ID,
            "task_id": TASK_ID,
        })
    )
    _check(
        result.get("status") == case["expected_result_status"],
        f"Case A: expected status={case['expected_result_status']}, got {result.get('status')}, result={result}",
    )
    _check(
        result.get("reject_reason") is None,
        f"Case A: terminal status should not be rejected, got reject_reason={result.get('reject_reason')}",
    )
    marker("CASE_A_PASS")


def case_b_draft_no_reason(fixture: dict, status_mod, root: Path) -> None:
    """Case B: Draft task without retrieval_reason succeeds."""
    case = fixture["case_fixtures"]["case_b_draft_status_no_retrieval_reason_needed"]
    write_meta(root, TASK_ID, case["meta_status"])
    result = json.loads(
        status_mod.handle({
            "workspace_id": WORKSPACE_ID,
            "task_id": TASK_ID,
        })
    )
    _check(
        result.get("status") == case["expected_result_status"],
        f"Case B: expected status={case['expected_result_status']}, got {result.get('status')}",
    )
    _check(
        result.get("reject_reason") is None,
        f"Case B: draft status should not be rejected, got reject_reason={result.get('reject_reason')}",
    )
    marker("CASE_B_PASS")


def case_c_running_missing_reason(fixture: dict, status_mod, root: Path) -> None:
    """Case C: Running task without retrieval_reason is rejected."""
    case = fixture["case_fixtures"]["case_c_running_missing_retrieval_reason_rejected"]
    write_meta(root, TASK_ID, case["meta_status"])
    result = json.loads(
        status_mod.handle({
            "workspace_id": WORKSPACE_ID,
            "task_id": TASK_ID,
        })
    )
    _check(
        result.get("status") == "rejected",
        f"Case C: expected status=rejected, got {result.get('status')}",
    )
    _check(
        result.get("reject_reason") == case["expected_reject_reason"],
        f"Case C: expected reject_reason={case['expected_reject_reason']}, got {result.get('reject_reason')}",
    )
    marker("CASE_C_PASS")


def case_d_running_valid_reason(fixture: dict, status_mod, root: Path) -> None:
    """Case D: Running task with valid retrieval_reason passes the missing-reason guard."""
    case = fixture["case_fixtures"]["case_d_running_with_valid_retrieval_reason_passes"]
    write_meta(root, TASK_ID, case["meta_status"])
    result = json.loads(
        status_mod.handle({
            "workspace_id": WORKSPACE_ID,
            "task_id": TASK_ID,
            "retrieval_reason": case["retrieval_reason"],
        })
    )
    # The result should NOT be rejected with normal_path_progress_poll_forbidden.
    # It may be "running" (unresolved), "rejected" (repeated poll), or a
    # reconciled terminal status — but NOT the missing-reason rejection.
    _check(
        result.get("reject_reason") != case["expected_reject_reason_absent"],
        f"Case D: should not get normal_path_progress_poll_forbidden, got reject_reason={result.get('reject_reason')}",
    )
    if result.get("status") == "rejected":
        # If rejected, it must NOT be for the missing-reason guard
        _check(
            result.get("reject_reason") != "normal_path_progress_poll_forbidden",
            f"Case D: valid retrieval_reason should not trigger missing-reason rejection",
        )
    _check(
        result.get("status") in case["expected_status_in"],
        f"Case D: expected status in {case['expected_status_in']}, got {result.get('status')}, result={result}",
    )
    marker("CASE_D_PASS")


def case_e_running_invalid_reason(fixture: dict, status_mod, root: Path) -> None:
    """Case E: Running task with invalid retrieval_reason returns error."""
    case = fixture["case_fixtures"]["case_e_running_with_invalid_retrieval_reason_rejected"]
    write_meta(root, TASK_ID, "running")
    result = json.loads(
        status_mod.handle({
            "workspace_id": WORKSPACE_ID,
            "task_id": TASK_ID,
            "retrieval_reason": case["retrieval_reason"],
        })
    )
    _check(
        result.get("status") == "error",
        f"Case E: expected status=error, got {result.get('status')}, result={result}",
    )
    _check(
        "invalid_retrieval_reason" in result.get("error", ""),
        f"Case E: expected error to mention invalid_retrieval_reason, got: {result.get('error')}",
    )
    marker("CASE_E_PASS")


def case_f_running_repeated_poll(fixture: dict, status_mod, root: Path) -> None:
    """Case F: Repeated running query within interval is rejected."""
    case = fixture["case_fixtures"]["case_f_running_repeated_poll_rejected"]
    write_meta(root, TASK_ID, case["meta_status"])

    # First query — should pass the missing-reason guard (has valid reason)
    first = json.loads(
        status_mod.handle({
            "workspace_id": WORKSPACE_ID,
            "task_id": TASK_ID,
            "retrieval_reason": case["retrieval_reason"],
        })
    )
    _check(
        first.get("reject_reason") != "normal_path_progress_poll_forbidden",
        f"Case F: first query should not be rejected for missing reason",
    )

    # Second query immediately — should be rejected as repeated poll
    second = json.loads(
        status_mod.handle({
            "workspace_id": WORKSPACE_ID,
            "task_id": TASK_ID,
            "retrieval_reason": case["retrieval_reason"],
        })
    )
    _check(
        second.get("status") == "rejected",
        f"Case F: expected second query status=rejected, got {second.get('status')}",
    )
    _check(
        second.get("reject_reason") == case["second_query_expected_reject_reason"],
        f"Case F: expected reject_reason={case['second_query_expected_reject_reason']}, got {second.get('reject_reason')}",
    )
    marker("CASE_F_PASS")


def case_g_terminal_with_reason(fixture: dict, status_mod, root: Path) -> None:
    """Case G: Terminal task WITH retrieval_reason still succeeds."""
    case = fixture["case_fixtures"]["case_g_terminal_with_retrieval_reason_still_works"]
    write_meta(root, TASK_ID, case["meta_status"])
    result = json.loads(
        status_mod.handle({
            "workspace_id": WORKSPACE_ID,
            "task_id": TASK_ID,
            "retrieval_reason": case["retrieval_reason"],
        })
    )
    _check(
        result.get("status") == case["expected_result_status"],
        f"Case G: expected status={case['expected_result_status']}, got {result.get('status')}",
    )
    _check(
        result.get("reject_reason") is None,
        f"Case G: terminal with retrieval_reason should not be rejected",
    )
    marker("CASE_G_PASS")


def case_h_not_found(fixture: dict, status_mod, root: Path) -> None:
    """Case H: Non-existent task without retrieval_reason returns not_found."""
    case = fixture["case_fixtures"]["case_h_not_found_task_no_retrieval_reason_needed"]
    # Don't create any meta — task doesn't exist
    result = json.loads(
        status_mod.handle({
            "workspace_id": WORKSPACE_ID,
            "task_id": "pt_99999999T999999_deadbeef",
        })
    )
    _check(
        result.get("status") == "not_found",
        f"Case H: expected status=not_found, got {result.get('status')}",
    )
    marker("CASE_H_PASS")


def case_i_all_reasons_valid(fixture: dict, status_mod, root: Path) -> None:
    """Case I: All six retrieval_reason values are valid."""
    case = fixture["case_fixtures"]["case_i_all_retrieval_reasons_valid"]
    for reason in case["retrieval_reasons"]:
        err = status_mod._validate_retrieval_reason(reason)
        _check(
            err is None,
            f"Case I: retrieval_reason={reason!r} should be valid, got error: {err}",
        )
    # Verify the constant matches
    _check(
        set(status_mod.RETRIEVAL_REASONS) == set(case["retrieval_reasons"]),
        f"Case I: RETRIEVAL_REASONS={status_mod.RETRIEVAL_REASONS} must match fixture",
    )
    # Verify an invalid reason is rejected
    err = status_mod._validate_retrieval_reason("just_curious")
    _check(
        err is not None,
        "Case I: invalid retrieval_reason should return an error",
    )
    marker("CASE_I_PASS")


def case_j_schema_has_property(fixture: dict, status_mod, root: Path) -> None:
    """Case J: SCHEMA declares retrieval_reason as an optional parameter."""
    case = fixture["case_fixtures"]["case_j_schema_has_retrieval_reason_parameter"]
    schema = status_mod.SCHEMA
    properties = schema.get("parameters", {}).get("properties", {})
    _check(
        case["expected_schema_has_property"] in properties,
        f"Case J: SCHEMA must have property {case['expected_schema_has_property']}",
    )
    prop = properties[case["expected_schema_has_property"]]
    _check(
        prop.get("type") == case["expected_schema_property_type"],
        f"Case J: retrieval_reason type must be {case['expected_schema_property_type']}, got {prop.get('type')}",
    )
    required = schema.get("parameters", {}).get("required", [])
    _check(
        case["expected_schema_has_property"] not in required,
        f"Case J: retrieval_reason must NOT be in required list",
    )
    marker("CASE_J_PASS")


# ---------------------------------------------------------------------------
# Additional structural checks
# ---------------------------------------------------------------------------


def check_reject_reasons_constant(status_mod) -> None:
    """Verify the reject reason constants exist and are correct strings."""
    _check(
        hasattr(status_mod, "_REJECT_NORMAL_PATH_PROGRESS_POLL_FORBIDDEN"),
        "Missing _REJECT_NORMAL_PATH_PROGRESS_POLL_FORBIDDEN constant",
    )
    _check(
        status_mod._REJECT_NORMAL_PATH_PROGRESS_POLL_FORBIDDEN == "normal_path_progress_poll_forbidden",
        f"Wrong value for _REJECT_NORMAL_PATH_PROGRESS_POLL_FORBIDDEN: {status_mod._REJECT_NORMAL_PATH_PROGRESS_POLL_FORBIDDEN}",
    )
    _check(
        hasattr(status_mod, "_REJECT_REPEATED_PROGRESS_POLL_FORBIDDEN"),
        "Missing _REJECT_REPEATED_PROGRESS_POLL_FORBIDDEN constant",
    )
    _check(
        status_mod._REJECT_REPEATED_PROGRESS_POLL_FORBIDDEN == "repeated_progress_poll_forbidden",
        f"Wrong value for _REJECT_REPEATED_PROGRESS_POLL_FORBIDDEN: {status_mod._REJECT_REPEATED_PROGRESS_POLL_FORBIDDEN}",
    )
    marker("REJECT_REASON_CONSTANTS_PASS")


def check_tool_count_unchanged() -> None:
    """Verify plugin.yaml declares the current canonical 61 tools."""
    import yaml

    plugin_yaml = yaml.safe_load(
        (REPO / "plugin" / "aota-tools" / "plugin.yaml").read_text(encoding="utf-8")
    )
    tools = plugin_yaml.get("provides_tools", [])
    _check(
        len(tools) == 61,
        f"Tool count must be 61, got {len(tools)}",
    )
    _check(
        "aota_profile_task_status" in tools,
        "aota_profile_task_status must still be in provides_tools",
    )
    marker("TOOL_COUNT_INVARIANT_PASS")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    try:
        fixture = load_fixture()

        with tempfile.TemporaryDirectory(prefix="pcf-poll-enforce-") as raw:
            root = Path(raw)
            setup_env(root)

            # Load the plugin and status module in the fixture environment
            if "aota_tools" not in sys.modules:
                load_plugin()
            status_mod = load_status_module()

            # Run all cases A-J
            case_a_terminal_no_reason(fixture, status_mod, root)
            case_b_draft_no_reason(fixture, status_mod, root)
            case_c_running_missing_reason(fixture, status_mod, root)
            case_d_running_valid_reason(fixture, status_mod, root)
            case_e_running_invalid_reason(fixture, status_mod, root)
            case_f_running_repeated_poll(fixture, status_mod, root)
            case_g_terminal_with_reason(fixture, status_mod, root)
            case_h_not_found(fixture, status_mod, root)
            case_i_all_reasons_valid(fixture, status_mod, root)
            case_j_schema_has_property(fixture, status_mod, root)

            # Structural checks
            check_reject_reasons_constant(status_mod)

        # Tool count invariant check (outside tempfile — reads real repo)
        check_tool_count_unchanged()

    except AssertionError as exc:
        print(f"PROGRESS_POLLING_ENFORCEMENT_FAIL: {exc}")
        return 1
    except Exception as exc:
        print(f"PROGRESS_POLLING_ENFORCEMENT_ERROR: {type(exc).__name__}: {exc}")
        return 1

    print("PROGRESS_POLLING_ENFORCEMENT_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
