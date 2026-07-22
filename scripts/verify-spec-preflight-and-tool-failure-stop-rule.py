#!/usr/bin/env python3
"""PCF-WI-SPEC-PREFLIGHT-AND-TOOL-FAILURE-STOP-RULE verifier.

Covers Cases A-F from the SPEC pre-submit checklist and tool failure
stop-rule work item.  stdlib-only; no managed runtime or worker is used.
Validates that the Skill files contain the required sections and that the
fixture cases are structurally correct.

Cases:
  A: Valid spec passes pre-submit checklist (Skill section present + key items).
  B: Missing task_kind/spec_kind distinction (checklist item 1).
  C: Unknown payload field failure with accepted field list (checklist items 11-12).
  D: Review missing subject_spec ref (checklist item 8).
  E: Artifact ref as string rejected with object shape (checklist items 9-10).
  F: Repeated schema error detected (fallback Skill rules present).
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "fixtures" / "spec-preflight-and-tool-failure-stop-rule-cases.json"

SKILL_PATHS = {
    "canonical_spec_contract": ROOT / "skills" / "aota-canonical-spec-contract" / "SKILL.md",
    "tool_failure_fallback": ROOT / "skills" / "aota-tool-failure-fallback" / "SKILL.md",
    "canonical_spec_pitfalls": ROOT / "skills" / "aota-canonical-spec-pitfalls" / "SKILL.md",
    "profile_task_orchestration": ROOT / "skills" / "aota-profile-task-orchestration" / "SKILL.md",
}

CONTRACT_PATH = ROOT / "plugin" / "aota-tools" / "_spec_contract.py"


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _load_skill_text(name: str) -> str:
    path = SKILL_PATHS[name]
    _check(path.is_file(), f"Skill source missing: {path}")
    return path.read_text(encoding="utf-8")


def _load_fixture() -> dict:
    _check(FIXTURE_PATH.is_file(), f"Fixture missing: {FIXTURE_PATH}")
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    _check(isinstance(data, dict), "Fixture must be a JSON object")
    _check(data.get("schema_version") == 1, "Fixture schema_version must be 1")
    return data


def _load_contract_module():
    """Load the canonical spec contract module. stdlib-only; no runtime env."""
    spec = importlib.util.spec_from_file_location(
        "_spec_contract_test", str(CONTRACT_PATH)
    )
    if not spec or not spec.loader:
        raise AssertionError("Cannot load contract module spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Case A: Valid spec passes pre-submit checklist
# ---------------------------------------------------------------------------

def case_a_pre_submit_checklist_present(contract, fixture: dict) -> None:
    """Case A: The Pre-submit Checklist section exists in canonical-spec-contract
    and key checklist items are present."""
    case = fixture["cases"]["case_a_valid_spec_passes_pre_submit_checklist"]
    text = _load_skill_text("canonical_spec_contract")

    # Section heading present
    _check(
        "## Pre-submit Checklist" in text,
        "Case A: Pre-submit Checklist section heading missing",
    )

    # Key checklist items must be present
    checklist_items = {
        "spec_kind only, no task_kind": r"spec_kind.*only.*no.*task_kind",
        "capability_contract must include": r"capability_contract.*must include",
        "runtime_actions must remain false": r"runtime_actions.*must.*false",
        "read_scope items must be workspace-relative": r"read_scope.*workspace-relative",
        "write_scope items must be workspace-relative": r"write_scope.*workspace-relative",
        "forbidden_scope must not overlap": r"forbidden_scope.*not overlap",
        "subject_task_id is top-level for review": r"subject_task_id.*top-level.*review",
        "context_refs must include subject_spec": r"context_refs.*subject_spec",
        "artifact ref must use ref_type": r"ref_type.*REF_TYPES",
        "artifact ref must be an object": r"never a string",
        "review payload only": r"review payload.*only accepts.*review-specific",
        "implementation payload only": r"implementation payload.*only accepts.*implementation-specific",
        "expected_spec_hash distinction": r"expected_spec_hash.*expected_spec_sha256",
        "human_checkpoints enum": r"human_checkpoints.*enum",
        "validation_tier must be 0-4": "validation_tier.*0[-\u2013]4",
        "process_path must be": r"process_path.*fast.*standard.*deep",
        "workspace_decision_id": r"workspace_decision_id.*authority",
        "summary non-empty": r"summary.*non-empty",
        "objective non-empty": r"objective.*non-empty",
        "acceptance_criteria non-empty list": r"acceptance_criteria.*non-empty list",
    }

    for item_name, pattern in checklist_items.items():
        _check(
            bool(re.search(pattern, text, re.IGNORECASE | re.DOTALL)),
            f"Case A: checklist item '{item_name}' not found in Skill",
        )

    # Verify fixture spec has correct shape
    spec = case["spec"]
    _check(spec["spec_kind"] == "implementation", "Case A fixture: must be implementation")
    _check("task_kind" not in spec, "Case A fixture: must not have task_kind")
    _check(spec["payload"]["runtime_actions"] == {}, "Case A fixture: runtime_actions must be empty")
    # Capability values must be booleans
    for k, v in spec["capability_contract"].items():
        _check(isinstance(v, bool), f"Case A fixture: capability {k} must be bool")

    _check(case["expected_result"] == "pass", "Case A fixture: expected_result must be pass")
    print("CASE_A_PASS")


# ---------------------------------------------------------------------------
# Case B: Missing spec_kind failure
# ---------------------------------------------------------------------------

def case_b_missing_spec_kind(contract, fixture: dict) -> None:
    """Case B: A SPEC without spec_kind (only task_kind) fails with clear message."""
    case = fixture["cases"]["case_b_missing_task_kind_failure_with_correction"]
    spec = case["spec"]

    _check("spec_kind" not in spec, "Case B fixture: must not have spec_kind")
    _check("task_kind" in spec, "Case B fixture: must have task_kind")
    _check(spec["task_kind"] == "review", "Case B fixture: task_kind must be review")

    # Verify the contract will reject it
    _check(
        spec["task_kind"] in contract.SPEC_KINDS,
        "Case B: task_kind value must be a valid kind for the contract to test",
    )

    _check(case["expected_result"] == "fail", "Case B fixture: expected_result must be fail")
    _check(
        "unknown or missing spec_kind" in case["expected_error"],
        "Case B fixture: expected_error must mention spec_kind",
    )

    # Verify Skill checklist item 1 covers this
    text = _load_skill_text("canonical_spec_contract")
    _check(
        "spec_kind" in text and "task_kind" in text,
        "Case B: canonical-spec-contract must reference both spec_kind and task_kind",
    )

    print("CASE_B_PASS")


# ---------------------------------------------------------------------------
# Case C: Unknown payload field failure
# ---------------------------------------------------------------------------

def case_c_unknown_payload_field(contract, fixture: dict) -> None:
    """Case C: An implementation SPEC with a diagnosis-only field fails."""
    case = fixture["cases"]["case_c_unknown_payload_field_failure_with_accepted_field_list"]
    spec = case["spec"]

    _check(spec["spec_kind"] == "implementation", "Case C fixture: must be implementation")
    _check("mutation_allowed" in spec["payload"], "Case C fixture: must have diagnosis-only field")

    # Verify mutation_allowed is NOT in implementation payload fields
    impl_fields = contract.PAYLOAD_FIELDS.get("implementation", frozenset())
    _check(
        "mutation_allowed" not in impl_fields,
        "Case C: mutation_allowed must not be in implementation PAYLOAD_FIELDS",
    )

    _check(case["expected_result"] == "fail", "Case C fixture: expected_result must be fail")
    _check(
        "cross-role" in case["expected_error"].lower() or "unknown" in case["expected_error"].lower(),
        "Case C fixture: expected_error must mention unknown or cross-role",
    )

    # Verify accepted fields are listed in fixture
    accepted = case["accepted_fields_for_implementation"]
    _check("read_scope" in accepted, "Case C: accepted fields must list read_scope")
    _check("write_scope" in accepted, "Case C: accepted fields must list write_scope")

    # Verify Skill checklist items 11-12 cover this
    text = _load_skill_text("canonical_spec_contract")
    _check(
        "implementation payload only accepts" in text.lower()
        or "implementation-specific" in text.lower(),
        "Case C: canonical-spec-contract must document implementation-only payload fields",
    )

    print("CASE_C_PASS")


# ---------------------------------------------------------------------------
# Case D: Review missing subject_spec ref
# ---------------------------------------------------------------------------

def case_d_review_missing_subject_spec_ref(contract, fixture: dict) -> None:
    """Case D: A review SPEC without subject_spec in context_refs fails."""
    case = fixture["cases"]["case_d_review_missing_subject_spec_ref"]
    spec = case["spec"]

    _check(spec["spec_kind"] == "review", "Case D fixture: must be review")
    _check(spec["context_refs"] == [], "Case D fixture: context_refs must be empty")
    _check(
        "subject_task_id" in spec and spec["subject_task_id"] is not None,
        "Case D fixture: must have subject_task_id for review",
    )

    _check(case["expected_result"] == "fail", "Case D fixture: expected_result must be fail")
    _check(
        "subject_spec" in case["expected_error"],
        "Case D fixture: expected_error must mention subject_spec",
    )

    # Verify Skill checklist item 8 covers this
    text = _load_skill_text("canonical_spec_contract")
    _check(
        "context_refs" in text and "subject_spec" in text,
        "Case D: canonical-spec-contract must document review context_refs subject_spec requirement",
    )

    print("CASE_D_PASS")


# ---------------------------------------------------------------------------
# Case E: Artifact ref as string rejected
# ---------------------------------------------------------------------------

def case_e_artifact_ref_as_string_rejected(contract, fixture: dict) -> None:
    """Case E: A string artifact ref is rejected; object shape required."""
    case = fixture["cases"]["case_e_artifact_ref_as_string_rejected_with_object_shape"]
    spec = case["spec"]

    _check(spec["spec_kind"] == "review", "Case E fixture: must be review")
    _check(
        isinstance(spec["context_refs"], list) and len(spec["context_refs"]) > 0,
        "Case E fixture: context_refs must have entries",
    )
    # At least one ref must be a string (not dict)
    has_string_ref = any(not isinstance(r, dict) for r in spec["context_refs"])
    _check(has_string_ref, "Case E fixture: must have a string artifact ref")

    _check(case["expected_result"] == "fail", "Case E fixture: expected_result must be fail")
    _check(
        "object" in case["expected_error"].lower(),
        "Case E fixture: expected_error must mention object",
    )

    # Verify Skill checklist items 9-10 cover this
    text = _load_skill_text("canonical_spec_contract")
    _check(
        "artifact ref must be an object" in text.lower()
        or "never a string" in text.lower(),
        "Case E: canonical-spec-contract must document artifact ref object requirement",
    )
    _check(
        "ref_type" in text and "REF_TYPES" in text,
        "Case E: canonical-spec-contract must reference REF_TYPES",
    )

    print("CASE_E_PASS")


# ---------------------------------------------------------------------------
# Case F: Repeated schema error detected
# ---------------------------------------------------------------------------

def case_f_repeated_schema_error_detected(contract, fixture: dict) -> None:
    """Case F: Two consecutive structured errors trigger repeated_schema_guess_detected."""
    case = fixture["cases"]["case_f_repeated_schema_error_detected"]
    scenario = case["worker_scenario"]

    _check(
        scenario["call_1"]["error_class"] == "structured",
        "Case F fixture: call_1 must be structured error",
    )
    _check(
        scenario["call_2"]["error_class"] == "structured",
        "Case F fixture: call_2 must be structured error",
    )

    _check(
        case["expected_result"] == "stop",
        "Case F fixture: expected_result must be stop",
    )
    _check(
        case["expected_stop_condition"] == "repeated_schema_guess_detected",
        "Case F fixture: stop condition must be repeated_schema_guess_detected",
    )

    # Verify tool-failure-fallback Skill has the required rules
    fallback_text = _load_skill_text("tool_failure_fallback")
    _check(
        "TOOL_STRUCTURED_ERROR_REQUIRES_CONTRACT_RELOAD_BEFORE_RETRY" in fallback_text,
        "Case F: tool-failure-fallback must contain TOOL_STRUCTURED_ERROR_REQUIRES_CONTRACT_RELOAD_BEFORE_RETRY invariant",
    )
    _check(
        "stop retrying" in fallback_text.lower(),
        "Case F: tool-failure-fallback must include 'stop retrying' step",
    )
    _check(
        "retry at most once" in fallback_text.lower(),
        "Case F: tool-failure-fallback must include 'retry at most once' rule",
    )
    _check(
        "needs_input" in fallback_text.lower(),
        "Case F: tool-failure-fallback must route to needs_input on second failure",
    )
    _check(
        "No Continuous Parameter Guessing" in fallback_text
        or "repeated_schema_guess_detected" in fallback_text,
        "Case F: tool-failure-fallback must contain repeated_schema_guess_detected rule",
    )
    _check(
        "no third blind retry" in fallback_text.lower(),
        "Case F: tool-failure-fallback must prohibit third blind retry",
    )

    # Verify pitfalls Skill has routing references
    pitfalls_text = _load_skill_text("canonical_spec_pitfalls")
    _check(
        "Pitfall 13" in pitfalls_text or "Structured-error blind retry" in pitfalls_text,
        "Case F: canonical-spec-pitfalls must contain Pitfall 13 about structured errors",
    )
    _check(
        "Pitfall 14" in pitfalls_text or "Repeated schema guessing" in pitfalls_text,
        "Case F: canonical-spec-pitfalls must contain Pitfall 14 about repeated guessing",
    )
    _check(
        "aota-tool-failure-fallback" in pitfalls_text,
        "Case F: canonical-spec-pitfalls pitfall 13/14 must reference aota-tool-failure-fallback",
    )

    # Verify orchestration Skill Loading Map has tool failure routing
    orchestration_text = _load_skill_text("profile_task_orchestration")
    _check(
        "Tool failure" in orchestration_text or "structured error" in orchestration_text,
        "Case F: orchestration Skill Loading Map must have tool failure routing",
    )
    _check(
        "aota-tool-failure-fallback" in orchestration_text,
        "Case F: orchestration Skill Loading Map must reference tool-failure-fallback",
    )
    _check(
        "aota-canonical-spec-pitfalls" in orchestration_text,
        "Case F: orchestration Skill Loading Map must reference canonical-spec-pitfalls",
    )
    _check(
        "do not wait for repeated failures" in orchestration_text.lower(),
        "Case F: orchestration Skill Loading Map must not wait for repeated failures",
    )

    print("CASE_F_PASS")


# ---------------------------------------------------------------------------
# Cross-cutting: Skill authority consistency
# ---------------------------------------------------------------------------

def check_skill_frontmatter(contract) -> None:
    """Verify all modified Skills have valid frontmatter."""
    for name, path in SKILL_PATHS.items():
        text = path.read_text(encoding="utf-8")
        _check(
            text.startswith("---"),
            f"Skill {name}: missing frontmatter",
        )
        # Must have name field
        _check(
            "name:" in text[:200],
            f"Skill {name}: missing name in frontmatter",
        )


def check_no_forbidden_content(contract) -> None:
    """Verify no tool source modifications leaked into Skills."""
    for name, path in SKILL_PATHS.items():
        text = path.read_text(encoding="utf-8")
        # Skills must not contain raw Python handler code
        _check(
            "def handle(" not in text or name == "canonical_spec_contract",
            f"Skill {name}: must not contain handler code",
        )
        # Skills must not duplicate full contract field matrix
        _check(
            "SPEC_KINDS = " not in text,
            f"Skill {name}: must not duplicate contract constants",
        )


def check_spec_contract_preserved(contract) -> None:
    """Verify the canonical contract source is intact (read-only check)."""
    _check(hasattr(contract, "SPEC_KINDS"), "Contract: SPEC_KINDS missing")
    _check(hasattr(contract, "ROUTING"), "Contract: ROUTING missing")
    _check(hasattr(contract, "PAYLOAD_FIELDS"), "Contract: PAYLOAD_FIELDS missing")
    _check(hasattr(contract, "REF_TYPES"), "Contract: REF_TYPES missing")
    _check(hasattr(contract, "validate_spec"), "Contract: validate_spec missing")
    _check(hasattr(contract, "canonical_hash"), "Contract: canonical_hash missing")

    # Verify SPEC_KINDS unchanged
    expected_kinds = ("implementation", "diagnosis", "review", "architecture", "stewardship")
    _check(
        contract.SPEC_KINDS == expected_kinds,
        f"Contract: SPEC_KINDS must be {expected_kinds}",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        fixture = _load_fixture()
        contract = _load_contract_module()

        case_a_pre_submit_checklist_present(contract, fixture)
        case_b_missing_spec_kind(contract, fixture)
        case_c_unknown_payload_field(contract, fixture)
        case_d_review_missing_subject_spec_ref(contract, fixture)
        case_e_artifact_ref_as_string_rejected(contract, fixture)
        case_f_repeated_schema_error_detected(contract, fixture)

        check_skill_frontmatter(contract)
        check_no_forbidden_content(contract)
        check_spec_contract_preserved(contract)

    except AssertionError as exc:
        print(f"SPEC_PREFLIGHT_AND_TOOL_FAILURE_STOP_RULE_FAIL: {exc}")
        return 1
    except Exception as exc:
        print(f"SPEC_PREFLIGHT_AND_TOOL_FAILURE_STOP_RULE_ERROR: {type(exc).__name__}: {exc}")
        return 1

    print("SPEC_PREFLIGHT_AND_TOOL_FAILURE_STOP_RULE_PASS")
    print("RUNTIME_REGISTRY_CHECK_REQUIRED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
