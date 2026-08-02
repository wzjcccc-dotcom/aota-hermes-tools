#!/usr/bin/env python3
"""Verifier for PCF-WI-PROFILE-TASK-PROGRESS-POLLING-ENFORCEMENT-MINIMUM.

Validates legacy Cases A-J plus the bounded completion-delivery cases using stdlib + importlib to load the
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
  K-R: start wait contract, cross-tool completion guard, one recovery, and
       operator/non-wakeup compatibility.
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


def load_handoff_list_module():
    path = PLUGIN / "_handoff_list.py"
    spec = importlib.util.spec_from_file_location(
        "aota_tools._handoff_list", str(path)
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_fixture() -> dict:
    if not FIXTURE_PATH.is_file():
        raise AssertionError(f"fixture file missing: {FIXTURE_PATH}")
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise AssertionError("fixture must be a JSON object")
    return data


def write_meta(
    root: Path,
    task_id: str,
    status: str,
    *,
    completion_expected: bool = False,
    recovery_allowed_after: str = "",
    recovery_consumed: bool = False,
) -> Path:
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
            "spec_revision": 1,
            "spec_sha256": "testhash",
            "spec_hash": "testhash",
            "completion_receipt_path": f"completion.{task_id}.json",
            "completion_transport": "terminal_background",
            "completion_delivery_expected": completion_expected,
            "delivery_state": "pending" if completion_expected else "not_expected",
            "next_action": "wait_for_completion_delivery" if completion_expected else "retrieve_after_reentry",
            "recovery_allowed_after": recovery_allowed_after,
            "recovery_consumed": recovery_consumed,
        },
    }
    if completion_expected:
        meta["origin_session_id"] = "origin-session"
    meta_path = task_dir / "meta.json"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return task_dir


def write_receipt(task_dir: Path, task_id: str, *, outcome: str = "completed") -> None:
    receipt = {
        "task_id": task_id,
        "workspace_id": WORKSPACE_ID,
        "start_id": task_id,
        "profile": "coder",
        "spec_revision": 1,
        "spec_sha256": "testhash",
        "spec_hash": "testhash",
        "status": "done" if outcome == "completed" else "failed",
        "outcome": outcome,
        "exit_code": 0 if outcome == "completed" else 1,
        "completed_at": "2026-07-21T00:01:00Z",
    }
    (task_dir / f"completion.{task_id}.json").write_text(
        json.dumps(receipt), encoding="utf-8"
    )


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


def _completion_task_id(suffix: str) -> str:
    return f"pt_20260721T0000{suffix}_aabbccdd"


def _recovery_args(task_id: str, reason: str = "completion_notification_timeout") -> dict:
    return {
        "workspace_id": WORKSPACE_ID,
        "task_id": task_id,
        "retrieval_reason": reason,
    }


def case_k_start_wait_contract(status_mod, root: Path) -> None:
    from aota_tools import _completion_observation as observation_mod

    wait = observation_mod.build_completion_wait_contract(
        started_at="2026-07-21T00:00:00Z",
        completion_transport="terminal_background",
        completion_delivery_expected=True,
    )
    _check(wait["completion_transport"] == "terminal_background", "Case K: transport missing")
    _check(wait["completion_delivery_expected"] is True, "Case K: delivery expectation missing")
    _check(wait["next_action"] == "wait_for_completion_delivery", "Case K: start must tell task-main to wait")
    _check(wait["recovery_allowed_after"] == "2026-07-21T00:00:30Z", "Case K: recovery deadline must be control-plane computed")
    marker("CASE_K_PASS")


def case_l_cross_tool_pending_guard(status_mod, handoff_mod, root: Path) -> None:
    task_id = _completion_task_id("01")
    write_meta(
        root,
        task_id,
        "running",
        completion_expected=True,
        recovery_allowed_after="2099-01-01T00:00:00Z",
    )
    status_result = status_mod._do_status(
        {"workspace_id": WORKSPACE_ID, "task_id": task_id},
        observer_session_id="origin-session",
        observer_principal="task-main",
    )
    list_result = handoff_mod._do_list(
        {"workspace_id": WORKSPACE_ID, "task_id": task_id, "observation_purpose": "completion"},
        observer_session_id="origin-session",
        observer_principal="task-main",
    )
    for label, result in (("status", status_result), ("handoff", list_result)):
        _check(result.get("status") == "rejected", f"Case L: {label} must reject pending observation")
        _check(result.get("error") == "completion_delivery_pending", f"Case L: {label} bypassed pending guard: {result}")
        _check(result.get("next_action") == "wait_for_completion_delivery", f"Case L: {label} must return wait action")
    marker("CASE_L_PASS")


def case_m_first_recovery_terminal_and_second_rejected(status_mod, root: Path) -> None:
    task_id = _completion_task_id("02")
    task_dir = write_meta(
        root,
        task_id,
        "running",
        completion_expected=True,
        recovery_allowed_after="2000-01-01T00:00:00Z",
    )
    write_receipt(task_dir, task_id)
    first = status_mod._do_status(
        _recovery_args(task_id),
        observer_session_id="origin-session",
        observer_principal="task-main",
    )
    _check(first.get("status") == "done", f"Case M: first recovery must aggregate terminal evidence: {first}")
    _check(first.get("next_action") == "open_completion_handoff", f"Case M: terminal next action wrong: {first}")
    second = status_mod._do_status(
        _recovery_args(task_id, "lost_completion_delivery"),
        observer_session_id="origin-session",
        observer_principal="task-main",
    )
    _check(second.get("error") == "recovery_already_consumed", f"Case M: second recovery bypassed consumed guard: {second}")
    marker("CASE_M_PASS")


def case_n_first_recovery_running_then_consumed(status_mod, root: Path) -> None:
    task_id = _completion_task_id("03")
    write_meta(
        root,
        task_id,
        "running",
        completion_expected=True,
        recovery_allowed_after="2000-01-01T00:00:00Z",
    )
    first = status_mod._do_status(
        _recovery_args(task_id),
        observer_session_id="origin-session",
        observer_principal="task-main",
    )
    _check(first.get("status") == "running", f"Case N: missing receipt should remain running: {first}")
    _check(first.get("next_action") == "wait_for_completion_delivery", f"Case N: running recovery must return wait: {first}")
    second = status_mod._do_status(
        _recovery_args(task_id),
        observer_session_id="origin-session",
        observer_principal="task-main",
    )
    _check(second.get("error") == "recovery_already_consumed", f"Case N: repeated recovery was allowed: {second}")
    marker("CASE_N_PASS")


def case_o_origin_and_principal_guard(status_mod, root: Path) -> None:
    task_id = _completion_task_id("04")
    write_meta(root, task_id, "running", completion_expected=True, recovery_allowed_after="2000-01-01T00:00:00Z")
    result = status_mod._do_status(
        _recovery_args(task_id),
        observer_session_id="origin-session",
        observer_principal="coder",
    )
    _check(result.get("error") == "origin_session_recovery_forbidden", f"Case O: non-orchestrator consumed recovery: {result}")
    marker("CASE_O_PASS")


def case_p_delivery_and_operator_inbox(handoff_mod, status_mod, root: Path) -> None:
    task_id = _completion_task_id("05")
    write_meta(root, task_id, "done", completion_expected=True)
    delivered = handoff_mod._do_list({"workspace_id": WORKSPACE_ID, "task_id": task_id, "observation_purpose": "completion"})
    operator = handoff_mod._do_list({"workspace_id": WORKSPACE_ID})
    _check(delivered.get("status") == "ok", f"Case P: delivery-complete handoff list must be allowed: {delivered}")
    _check(operator.get("status") == "ok", f"Case P: operator inbox list changed: {operator}")
    marker("CASE_P_PASS")


def case_q_non_wakeup_compatibility(status_mod, root: Path) -> None:
    task_id = _completion_task_id("06")
    write_meta(root, task_id, "running", completion_expected=False)
    result = status_mod._do_status(
        {"workspace_id": WORKSPACE_ID, "task_id": task_id, "retrieval_reason": "user_requested"}
    )
    _check(result.get("status") in {"running", "done", "failed"}, f"Case Q: non-wakeup retrieval path changed: {result}")
    marker("CASE_Q_PASS")


def case_r_schema_and_reason_scope(status_mod, handoff_mod) -> None:
    task_id = _completion_task_id("07")
    _check("task_id" not in handoff_mod.SCHEMA["parameters"]["properties"], "Case R: task ID must remain handler-only")
    _check("workspace_id" not in handoff_mod.SCHEMA["parameters"]["properties"], "Case R: workspace ID must remain handler-only")
    guarded = handoff_mod._do_list({"workspace_id": WORKSPACE_ID, "task_id": task_id, "observation_purpose": "completion"})
    _check(guarded.get("status") in {"ok", "rejected"}, f"Case R: legacy handler guard unavailable: {guarded}")
    _check(status_mod.BOUNDED_RECOVERY_REASONS == {"completion_notification_timeout", "lost_completion_delivery"}, "Case R: recovery reasons drifted")
    marker("CASE_R_PASS")


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
            handoff_mod = load_handoff_list_module()

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
            case_k_start_wait_contract(status_mod, root)
            case_l_cross_tool_pending_guard(status_mod, handoff_mod, root)
            case_m_first_recovery_terminal_and_second_rejected(status_mod, root)
            case_n_first_recovery_running_then_consumed(status_mod, root)
            case_o_origin_and_principal_guard(status_mod, root)
            case_p_delivery_and_operator_inbox(handoff_mod, status_mod, root)
            case_q_non_wakeup_compatibility(status_mod, root)
            case_r_schema_and_reason_scope(status_mod, handoff_mod)

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
