#!/usr/bin/env python3
"""Minimal verifier for PCF-WI-PROJECT-LIFECYCLE-CONTRACT-MINIMUM.

Covers Cases A-H from work item section 11.3.  stdlib-only; no pytest.
Validates that the canonical contract source exists, is importable, and that
all fail-closed rules and single-authority invariants hold.

Cases:
  A: Dispatch invariant has exactly one canonical source.
  B: Protected mutation classes and runtime artifact exemptions are separated.
  C: Project Steward execution ownership and task-main dispatch authority are separated.
  D: initialized_core and initialized are clearly distinguished.
  E: Initialization receipt minimum fields follow the schema; receipt authority maintained.
  F: Codex escalation boundary distinguishes current fallback from target policy.
  G: Single authority -- no duplicated version lists across Python files.
  H: Fail-closed reject rules are present and match work item section 10.2.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "plugin" / "aota-tools" / "_project_lifecycle_contract.py"
INVENTORY_PATH = ROOT / "deploy" / "aota-lifecycle-inventory.yaml"


def _load_contract_module():
    """Load the canonical contract module without importing the full plugin."""
    if not CONTRACT_PATH.is_file():
        raise AssertionError(f"canonical contract source missing: {CONTRACT_PATH}")
    spec = importlib.util.spec_from_file_location(
        "_project_lifecycle_contract_test", str(CONTRACT_PATH)
    )
    if not spec or not spec.loader:
        raise AssertionError("cannot load contract module spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError:
        raise AssertionError("PyYAML not available for verifier")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def case_a_single_canonical_source(contract) -> None:
    """Case A: Dispatch invariant has exactly one canonical source."""
    _check(
        contract.DISPATCH_INVARIANT
        == "PROJECT_AND_PROFILE_MUTATION_REQUIRES_TASK_MAIN_DISPATCH",
        "dispatch invariant name mismatch",
    )
    _check(
        contract.DISPATCH_AUTHORITY_PROFILES == frozenset(("task-main",)),
        "dispatch authority must be task-main only",
    )
    _check(
        contract.EXECUTION_OWNER_PROFILES == frozenset(("project-steward",)),
        "execution owner must be project-steward only",
    )
    # Verify no other Python file defines the same invariant constant
    plugin_dir = ROOT / "plugin" / "aota-tools"
    duplicates = []
    for py_file in plugin_dir.glob("*.py"):
        if py_file.name == "_project_lifecycle_contract.py":
            continue
        text = py_file.read_text(encoding="utf-8")
        if "PROJECT_AND_PROFILE_MUTATION_REQUIRES_TASK_MAIN_DISPATCH" in text:
            duplicates.append(py_file.name)
    _check(
        not duplicates,
        f"dispatch invariant duplicated in: {duplicates}",
    )
    print("CASE_A_PASS")


def case_b_protected_vs_runtime_exemption(contract) -> None:
    """Case B: Protected mutation and runtime artifact exemptions are separated."""
    protected = contract.PROTECTED_MUTATION_CLASSES
    exemptions = contract.RUNTIME_ARTIFACT_WRITE_EXEMPTIONS
    _check(
        not (protected & exemptions),
        "protected mutation classes and runtime exemptions must not overlap",
    )
    _check(
        "project_initialization" in protected,
        "project_initialization must be a protected mutation class",
    )
    _check(
        "project_git_lifecycle" in protected,
        "project_git_lifecycle must be a protected mutation class",
    )
    _check(
        "project_codegraph_lifecycle" in protected,
        "project_codegraph_lifecycle must be a protected mutation class",
    )
    _check(
        "CARD" in exemptions,
        "CARD must be a runtime artifact exemption",
    )
    _check(
        "RESULT" in exemptions,
        "RESULT must be a runtime artifact exemption",
    )
    _check(
        "worker_outcome" in exemptions,
        "worker_outcome must be a runtime artifact exemption",
    )
    _check(
        "completion_receipt" in exemptions,
        "completion_receipt must be a runtime artifact exemption",
    )
    _check(
        contract.is_protected_mutation("project_initialization"),
        "is_protected_mutation should return True for project_initialization",
    )
    _check(
        not contract.is_protected_mutation("CARD"),
        "is_protected_mutation should return False for CARD",
    )
    _check(
        contract.is_runtime_artifact_exemption("CARD"),
        "is_runtime_artifact_exemption should return True for CARD",
    )
    _check(
        not contract.is_runtime_artifact_exemption("project_initialization"),
        "is_runtime_artifact_exemption should return False for project_initialization",
    )
    print("CASE_B_PASS")


def case_c_steward_vs_taskmain(contract) -> None:
    """Case C: Steward execution ownership and task-main dispatch authority separated."""
    task_main_auth = contract.TASK_MAIN_AUTHORITY
    steward_auth = contract.PROJECT_STEWARD_AUTHORITY
    _check(
        not (task_main_auth & steward_auth),
        "task-main and steward authority must not overlap",
    )
    _check(
        "dispatch" in task_main_auth and "dispatch" not in steward_auth,
        "dispatch authority belongs to task-main only",
    )
    _check(
        "spec_creation" in task_main_auth and "spec_creation" not in steward_auth,
        "spec_creation belongs to task-main only",
    )
    _check(
        "docs_update" in steward_auth and "docs_update" not in task_main_auth,
        "docs_update belongs to steward only",
    )
    _check(
        "artifact_link" in steward_auth and "artifact_link" not in task_main_auth,
        "artifact_link belongs to steward only",
    )
    _check(
        contract.is_dispatch_authority("task-main"),
        "task-main is a dispatch authority",
    )
    _check(
        not contract.is_dispatch_authority("project-steward"),
        "project-steward is NOT a dispatch authority",
    )
    _check(
        contract.is_execution_owner("project-steward"),
        "project-steward is an execution owner",
    )
    _check(
        not contract.is_execution_owner("task-main"),
        "task-main is NOT an execution owner",
    )
    # Steward cannot originate un-dispatched protected mutation
    errors = contract.check_steward_execution(
        "project-steward", "project_initialization",
        spec_frozen=True, binding_match=True,
    )
    _check(
        "non_task_main_protected_mutation_spec" in errors,
        "steward should be rejected for project_initialization",
    )
    # Steward CAN execute dispatched metadata mutation
    errors = contract.check_steward_execution(
        "project-steward", "project_metadata_mutation",
        spec_frozen=True, binding_match=True,
    )
    _check(
        not errors,
        f"steward should be allowed for project_metadata_mutation, got: {errors}",
    )
    print("CASE_C_PASS")


def case_d_initialization_states(contract) -> None:
    """Case D: initialized_core and initialized are distinguished."""
    _check(
        contract.INITIALIZED_CORE == "initialized_core",
        "initialized_core constant mismatch",
    )
    _check(
        contract.INITIALIZED == "initialized",
        "initialized constant mismatch",
    )
    _check(
        contract.INITIALIZATION_FAILED == "failed",
        "failed constant mismatch",
    )
    _check(
        contract.INITIALIZATION_STATES
        == frozenset(("initialized_core", "initialized", "failed")),
        "initialization states mismatch",
    )
    _check(
        contract.INITIALIZED_CORE != contract.INITIALIZED,
        "initialized_core and initialized must be distinct",
    )
    _check(
        contract.is_valid_initialization_state("initialized_core"),
        "initialized_core is a valid state",
    )
    _check(
        contract.is_valid_initialization_state("initialized"),
        "initialized is a valid state",
    )
    _check(
        contract.is_valid_initialization_state("failed"),
        "failed is a valid state",
    )
    _check(
        not contract.is_valid_initialization_state("unknown"),
        "unknown is not a valid state",
    )
    print("CASE_D_PASS")


def case_e_receipt_minimum(contract) -> None:
    """Case E: Initialization receipt minimum fields and authority."""
    required = contract.INITIALIZATION_RECEIPT_MINIMUM_FIELDS
    expected = {
        "schema_version", "project_id", "workspace_id", "state",
        "git_summary", "codegraph_summary", "authority", "completed_at",
    }
    _check(required == expected, f"receipt minimum fields mismatch: {required}")
    _check(
        "trusted_finalizer" in contract.RECEIPT_AUTHORITIES,
        "trusted_finalizer must be a receipt authority",
    )
    _check(
        "authoritative_receipt" in contract.RECEIPT_AUTHORITIES,
        "authoritative_receipt must be a receipt authority",
    )
    _check(
        contract.WORKER_OBSERVATION_ARTIFACTS == frozenset(("CARD", "RESULT")),
        "worker observation artifacts must be CARD and RESULT only",
    )
    # Project Steward cannot self-declare authoritative success
    _check(
        "project-steward" not in contract.RECEIPT_AUTHORITIES,
        "project-steward must NOT be a receipt authority",
    )
    _check(
        "worker" not in contract.RECEIPT_AUTHORITIES,
        "worker must NOT be a receipt authority",
    )
    # Valid initialized receipt
    valid_receipt = {
        "schema_version": 1,
        "project_id": "test-project",
        "workspace_id": "test-ws",
        "state": "initialized",
        "git_summary": {"present": True, "head": "abc123"},
        "codegraph_summary": {"present": True, "state": "ready"},
        "authority": "trusted_finalizer",
        "completed_at": "2026-07-20T12:00:00Z",
    }
    errors = contract.validate_initialization_receipt(valid_receipt)
    _check(not errors, f"valid receipt should have no errors, got: {errors}")
    # Invalid: missing git_summary for initialized state
    invalid_receipt = dict(valid_receipt)
    invalid_receipt["git_summary"] = None
    errors = contract.validate_initialization_receipt(invalid_receipt)
    _check(
        "initialized_receipt_missing_git_codegraph_summary" in errors,
        f"missing git_summary should be rejected, got: {errors}",
    )
    # Invalid: unknown state
    invalid_receipt2 = dict(valid_receipt)
    invalid_receipt2["state"] = "unknown"
    errors = contract.validate_initialization_receipt(invalid_receipt2)
    _check(
        "unknown_initialization_state" in errors,
        f"unknown state should be rejected, got: {errors}",
    )
    # Invalid: wrong authority
    invalid_receipt3 = dict(valid_receipt)
    invalid_receipt3["authority"] = "project-steward"
    errors = contract.validate_initialization_receipt(invalid_receipt3)
    _check(
        "receipt_authoritative_wrong_reconciliation_source" in errors,
        f"wrong authority should be rejected, got: {errors}",
    )
    print("CASE_E_PASS")


def case_f_codex_boundary(contract) -> None:
    """Case F: Codex escalation boundary distinguishes current from target."""
    _check(
        contract.CURRENT_CODEX_FALLBACK_DEPENDENCY == "REQUIRED",
        "current codex fallback must be REQUIRED",
    )
    _check(
        contract.TARGET_CODEX_DEFAULT_PROJECT_OPERATOR == "NOT_REQUIRED",
        "target codex default operator must be NOT_REQUIRED",
    )
    _check(
        contract.CURRENT_CODEX_FALLBACK_DEPENDENCY
        != contract.TARGET_CODEX_DEFAULT_PROJECT_OPERATOR,
        "current and target codex states must differ",
    )
    _check(
        contract.CODEX_BOUNDARY == "host_infra_escalation",
        "codex boundary must be host_infra_escalation",
    )
    print("CASE_F_PASS")


def case_g_single_authority(contract) -> None:
    """Case G: Single authority -- no duplicated version lists across Python files."""
    schema = contract.PROJECT_LIFECYCLE_CONTRACT_SCHEMA
    _check(schema["schema_version"] == 1, "schema version must be 1")
    _check(
        schema["dispatch_invariant"] == contract.DISPATCH_INVARIANT,
        "schema dispatch invariant must match constant",
    )
    _check(
        set(schema["protected_mutation_classes"])
        == set(contract.PROTECTED_MUTATION_CLASSES),
        "schema protected mutation classes must match constant",
    )
    _check(
        set(schema["runtime_artifact_write_exemptions"])
        == set(contract.RUNTIME_ARTIFACT_WRITE_EXEMPTIONS),
        "schema runtime exemptions must match constant",
    )
    _check(
        set(schema["initialization_states"])
        == set(contract.INITIALIZATION_STATES),
        "schema initialization states must match constant",
    )
    # Verify lifecycle inventory YAML has matching project_lifecycle_contract section
    _check(
        INVENTORY_PATH.is_file(),
        f"lifecycle inventory missing: {INVENTORY_PATH}",
    )
    inventory = _load_yaml(INVENTORY_PATH)
    plc = inventory.get("project_lifecycle_contract")
    _check(
        isinstance(plc, dict),
        "project_lifecycle_contract section missing from lifecycle inventory",
    )
    _check(
        plc.get("canonical_source")
        == "plugin/aota-tools/_project_lifecycle_contract.py",
        "inventory canonical_source must point to the contract module",
    )
    _check(
        plc.get("dispatch_invariant") == contract.DISPATCH_INVARIANT,
        "inventory dispatch invariant must match contract",
    )
    _check(
        set(plc.get("protected_mutation_classes", []))
        == set(contract.PROTECTED_MUTATION_CLASSES),
        "inventory protected mutation classes must match contract",
    )
    _check(
        set(plc.get("runtime_artifact_write_exemptions", []))
        == set(contract.RUNTIME_ARTIFACT_WRITE_EXEMPTIONS),
        "inventory runtime exemptions must match contract",
    )
    _check(
        set(plc.get("initialization_states", []))
        == set(contract.INITIALIZATION_STATES),
        "inventory initialization states must match contract",
    )
    print("CASE_G_PASS")


def case_h_fail_closed(contract) -> None:
    """Case H: Fail-closed reject rules present and match section 10.2."""
    expected_reasons = {
        "non_task_main_protected_mutation_spec",
        "unfrozen_spec",
        "binding_mismatch",
        "profile_mismatch",
        "artifact_exemption_used_for_project_source_write",
        "steward_mutation_missing_binding",
        "initialized_receipt_missing_git_codegraph_summary",
        "receipt_authoritative_wrong_reconciliation_source",
        "unknown_initialization_state",
        "unknown_protected_mutation_class",
    }
    _check(
        contract.FAIL_CLOSED_REJECT_REASONS == expected_reasons,
        f"fail-closed reject reasons mismatch: {contract.FAIL_CLOSED_REJECT_REASONS}",
    )
    # Test: non-task-main protected mutation
    errors = contract.check_dispatch(
        "coder", "project_initialization",
        spec_frozen=True, binding_match=True, profile_match=True,
    )
    _check(
        "non_task_main_protected_mutation_spec" in errors,
        f"coder should be rejected for protected mutation, got: {errors}",
    )
    # Test: unfrozen spec
    errors = contract.check_dispatch(
        "task-main", "project_initialization",
        spec_frozen=False, binding_match=True, profile_match=True,
    )
    _check(
        "unfrozen_spec" in errors,
        f"unfrozen spec should be rejected, got: {errors}",
    )
    # Test: binding mismatch
    errors = contract.check_dispatch(
        "task-main", "project_initialization",
        spec_frozen=True, binding_match=False, profile_match=True,
    )
    _check(
        "binding_mismatch" in errors,
        f"binding mismatch should be rejected, got: {errors}",
    )
    # Test: unknown mutation class
    errors = contract.check_dispatch(
        "task-main", "unknown_mutation",
        spec_frozen=True, binding_match=True, profile_match=True,
    )
    _check(
        "unknown_protected_mutation_class" in errors,
        f"unknown mutation class should be rejected, got: {errors}",
    )
    # Test: valid dispatch
    errors = contract.check_dispatch(
        "task-main", "project_initialization",
        spec_frozen=True, binding_match=True, profile_match=True,
    )
    _check(
        not errors,
        f"valid dispatch should have no errors, got: {errors}",
    )
    # Test: steward missing binding
    errors = contract.check_steward_execution(
        "project-steward", "project_metadata_mutation",
        spec_frozen=True, binding_match=False,
    )
    _check(
        "steward_mutation_missing_binding" in errors,
        f"steward missing binding should be rejected, got: {errors}",
    )
    print("CASE_H_PASS")


def main() -> int:
    try:
        contract = _load_contract_module()
        case_a_single_canonical_source(contract)
        case_b_protected_vs_runtime_exemption(contract)
        case_c_steward_vs_taskmain(contract)
        case_d_initialization_states(contract)
        case_e_receipt_minimum(contract)
        case_f_codex_boundary(contract)
        case_g_single_authority(contract)
        case_h_fail_closed(contract)
    except AssertionError as exc:
        print(f"PROJECT_LIFECYCLE_CONTRACT_MINIMUM_FAIL: {exc}")
        return 1
    except Exception as exc:
        print(f"PROJECT_LIFECYCLE_CONTRACT_MINIMUM_ERROR: {type(exc).__name__}: {exc}")
        return 1
    print("PROJECT_LIFECYCLE_CONTRACT_MINIMUM_PASS")
    print("RUNTIME_REGISTRY_CHECK_REQUIRED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())