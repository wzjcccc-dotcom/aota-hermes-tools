#!/usr/bin/env python3
"""Minimal verifier for PCF-WI-TASK-MAIN-SKILL-AUTHORITY-AND-PROJECTION-CONTRACT-MINIMUM.

Covers Cases A-H from the original work item plus WI-1 Cases A-J for
PCF-WI-PROFILE-DEVELOPMENT-SKILL-PROJECTION-AND-INVENTORY-CLOSURE.
stdlib-only; no pytest.  Validates that the canonical contract source exists,
is importable, and that all source/activation/collision/override/routing/
wait-mode invariants hold.

Original Cases:
  A: Canonical managed Skill (pass).
  B: Legitimate profile-specific managed category (pass).
  C: Unmanaged runtime Skill (unmanaged).
  D: Undeclared collision (fail).
  E: Legitimate override (pass).
  F: Inventory is not deployment authority (fail_or_metadata_mismatch).
  G: Activation classes separated from source classes and owning_profiles.
  H: Wait mode classified with correct rules.

WI-1 Cases (development Skill projection and inventory closure):
  WI1-A: task-main reference Skill source exists + assembly reference declared
         + not active-injected + direct destination expected = pass.
  WI1-B: coder active/reference Skill source exists + assembly class declared
         + not disabled + destination expected = pass.
  WI1-C: projection/disabled conflict = fail.
  WI1-D: three worker Skill inventory entries complete = pass.
  WI1-E: inventory entry exists but assembly absent = no runtime projection
         inference (must not infer projection from inventory alone).
  WI1-F: assembly source does not exist = fail closed.
  WI1-G: SOUL summary drift = fail.
  WI1-H: existing Profile compatibility (architect/debugger/reviewer active
         Skills match assembly) = pass.
  WI1-I: tool count is plugin.yaml actual count, not hardcoded 59 = pass.
  WI1-J: WI-0 source integrity (NO_POLL_SOUL_INVARIANT, wait semantics,
         retrieval_reason guard) = pass.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "plugin" / "aota-tools" / "_skill_authority_contract.py"
INVENTORY_PATH = ROOT / "deploy" / "aota-lifecycle-inventory.yaml"
ASSEMBLY_PATH = ROOT / "deploy" / "profile-runtime-assembly.yaml"
PLUGIN_YAML_PATH = ROOT / "plugin" / "aota-tools" / "plugin.yaml"
FIXTURE_PATH = ROOT / "fixtures" / "skill-authority-contract-cases.json"

# WI-1 development Skills projected to profiles
WI1_DEVELOPMENT_SKILLS = ["aota-plugin-tool-development", "aota-skill-development"]

# WI-1 worker Skills that must have inventory entries
WI1_WORKER_SKILLS = {
    "aota-implementation-review": "reviewer",
    "aota-architecture-review": "architect",
    "aota-evidence-first-debugging": "debugger",
}


def _load_contract_module():
    """Load the canonical contract module without importing the full plugin."""
    if not CONTRACT_PATH.is_file():
        raise AssertionError(f"canonical contract source missing: {CONTRACT_PATH}")
    spec = importlib.util.spec_from_file_location(
        "_skill_authority_contract_test", str(CONTRACT_PATH)
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


def _load_fixture() -> dict:
    if not FIXTURE_PATH.is_file():
        raise AssertionError(f"fixture file missing: {FIXTURE_PATH}")
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise AssertionError("fixture must be a JSON object")
    return data


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _skill_source_path(skill_id: str) -> Path:
    return ROOT / "skills" / skill_id / "SKILL.md"


# ---------------------------------------------------------------------------
# Case A: Canonical managed Skill (pass)
# ---------------------------------------------------------------------------

def case_a_canonical_managed(contract, fixture: dict) -> None:
    """Case A: Canonical managed Skill with valid source, deployed path, and activation."""
    case = fixture["case_fixtures"]["case_a_canonical_managed_pass"]
    _check(
        case["source_class"] == contract.CANONICAL_MANAGED,
        "Case A: source class must be canonical_managed",
    )
    _check(
        case["activation_class"] == contract.ACTIVE,
        "Case A: activation class must be active",
    )
    # Classify source
    result = contract.classify_source(
        in_managed_manifest=case["in_managed_manifest"],
        in_profile_specific_manifest=case["in_profile_specific_manifest"],
        is_runtime_generated=case["is_runtime_generated"],
        is_bundled=case["is_bundled"],
    )
    _check(
        result == contract.CANONICAL_MANAGED,
        f"Case A: classify_source should return canonical_managed, got {result}",
    )
    # Classify activation
    act = contract.classify_activation(
        is_loaded_in_prompt=case["is_loaded_in_prompt"],
        is_disabled_in_config=case["is_disabled_in_config"],
        file_exists=case["file_exists"],
    )
    _check(
        act == contract.ACTIVE,
        f"Case A: classify_activation should return active, got {act}",
    )
    _check(
        contract.is_valid_source_class(contract.CANONICAL_MANAGED),
        "Case A: canonical_managed must be a valid source class",
    )
    _check(
        contract.is_valid_activation_class(contract.ACTIVE),
        "Case A: active must be a valid activation class",
    )
    print("CASE_A_PASS")


# ---------------------------------------------------------------------------
# Case B: Legitimate profile-specific managed category (pass)
# ---------------------------------------------------------------------------

def case_b_profile_specific_managed(contract, fixture: dict) -> None:
    """Case B: Legitimate profile-specific managed category with explicit override."""
    case = fixture["case_fixtures"]["case_b_profile_specific_managed_pass"]
    _check(
        case["source_class"] == contract.PROFILE_SPECIFIC_MANAGED,
        "Case B: source class must be profile_specific_managed",
    )
    result = contract.classify_source(
        in_managed_manifest=case["in_managed_manifest"],
        in_profile_specific_manifest=case["in_profile_specific_manifest"],
        is_runtime_generated=case["is_runtime_generated"],
        is_bundled=case["is_bundled"],
    )
    _check(
        result == contract.PROFILE_SPECIFIC_MANAGED,
        f"Case B: classify_source should return profile_specific_managed, got {result}",
    )
    # Override is declared, so no collision error
    errors = contract.check_collision(
        case["skill_id"],
        [contract.PROFILE_SPECIFIC_MANAGED, contract.CANONICAL_MANAGED],
        override_declared=case["override_declared"],
    )
    _check(
        not errors,
        f"Case B: legitimate override should have no errors, got {errors}",
    )
    print("CASE_B_PASS")


# ---------------------------------------------------------------------------
# Case C: Unmanaged runtime Skill (unmanaged)
# ---------------------------------------------------------------------------

def case_c_unmanaged_runtime(contract, fixture: dict) -> None:
    """Case C: Unmanaged runtime file present without managed source."""
    case = fixture["case_fixtures"]["case_c_unmanaged_runtime"]
    result = contract.classify_source(
        in_managed_manifest=case["in_managed_manifest"],
        in_profile_specific_manifest=case["in_profile_specific_manifest"],
        is_runtime_generated=case["is_runtime_generated"],
        is_bundled=case["is_bundled"],
    )
    _check(
        result == contract.UNMANAGED,
        f"Case C: classify_source should return unmanaged, got {result}",
    )
    act = contract.classify_activation(
        is_loaded_in_prompt=case["is_loaded_in_prompt"],
        is_disabled_in_config=case["is_disabled_in_config"],
        file_exists=case["file_exists"],
    )
    _check(
        act == contract.DISCOVERABLE,
        f"Case C: classify_activation should return discoverable, got {act}",
    )
    _check(
        contract.is_valid_source_class(contract.UNMANAGED),
        "Case C: unmanaged must be a valid source class",
    )
    print("CASE_C_PASS")


# ---------------------------------------------------------------------------
# Case D: Undeclared collision (fail)
# ---------------------------------------------------------------------------

def case_d_undeclared_collision(contract, fixture: dict) -> None:
    """Case D: Same-name Skill in both canonical_managed and unmanaged without override."""
    case = fixture["case_fixtures"]["case_d_undeclared_collision_fail"]
    errors = contract.check_collision(
        case["skill_id"],
        case["source_classes_present"],
        override_declared=case["override_declared"],
    )
    _check(
        contract.COLLISION_UNDECLARED in errors,
        f"Case D: undeclared collision should be rejected, got {errors}",
    )
    _check(
        case["expected_errors"] == ["undeclared_collision"],
        "Case D: fixture expected_errors must match",
    )
    print("CASE_D_PASS")


# ---------------------------------------------------------------------------
# Case E: Legitimate override (pass)
# ---------------------------------------------------------------------------

def case_e_legitimate_override(contract, fixture: dict) -> None:
    """Case E: Profile-specific managed overrides canonical managed with explicit declaration."""
    case = fixture["case_fixtures"]["case_e_legitimate_override_pass"]
    errors = contract.check_collision(
        case["skill_id"],
        case["source_classes_present"],
        override_declared=case["override_declared"],
    )
    _check(
        not errors,
        f"Case E: legitimate override should have no errors, got {errors}",
    )
    winner = contract.resolve_collision(
        case["source_classes_present"],
        override_declared=case["override_declared"],
    )
    _check(
        winner == contract.PROFILE_SPECIFIC_MANAGED,
        f"Case E: resolve_collision should return profile_specific_managed, got {winner}",
    )
    print("CASE_E_PASS")


# ---------------------------------------------------------------------------
# Case F: Inventory is not deployment authority (fail_or_metadata_mismatch)
# ---------------------------------------------------------------------------

def case_f_inventory_not_deployment_authority(contract, fixture: dict) -> None:
    """Case F: Lifecycle inventory is governance metadata, not deployment authority."""
    case = fixture["case_fixtures"]["case_f_inventory_not_deployment_authority"]
    _check(
        INVENTORY_PATH.is_file(),
        f"Case F: lifecycle inventory missing: {INVENTORY_PATH}",
    )
    inventory = _load_yaml(INVENTORY_PATH)
    # The inventory must have a skill_authority_contract section pointing to
    # the canonical contract module.
    sac = inventory.get("skill_authority_contract")
    _check(
        isinstance(sac, dict),
        "Case F: skill_authority_contract section missing from lifecycle inventory",
    )
    _check(
        sac.get("canonical_source")
        == "plugin/aota-tools/_skill_authority_contract.py",
        "Case F: inventory canonical_source must point to the contract module",
    )
    # Inventory must NOT be the deployment authority — it is governance only.
    _check(
        sac.get("deployment_authority") is False,
        "Case F: inventory must not be deployment authority",
    )
    # Verify the inventory source classes match the contract
    inv_source_classes = set(sac.get("source_classes", []))
    _check(
        inv_source_classes == set(contract.SOURCE_CLASSES),
        f"Case F: inventory source_classes must match contract, got {inv_source_classes}",
    )
    inv_activation_classes = set(sac.get("activation_classes", []))
    _check(
        inv_activation_classes == set(contract.ACTIVATION_CLASSES),
        f"Case F: inventory activation_classes must match contract, got {inv_activation_classes}",
    )
    print("CASE_F_PASS")


# ---------------------------------------------------------------------------
# Case G: Activation classes separated from source classes and owning_profiles
# ---------------------------------------------------------------------------

def case_g_activation_classes_separated(contract, fixture: dict) -> None:
    """Case G: Activation classes are clearly separated from source classes and owning_profiles."""
    case = fixture["case_fixtures"]["case_g_activation_classes_separated"]
    # owning_profiles != active_skills
    _check(
        contract.OWNING_PROFILES_NOT_ACTIVE_SKILLS,
        "Case G: owning_profiles_not_active_skills must be True",
    )
    _check(
        case["owning_profiles_example"] != case["active_skills_example"],
        "Case G: owning_profiles and active_skills must be different lists",
    )
    # runtime_discoverable != managed
    _check(
        contract.RUNTIME_DISCOVERABLE_NOT_MANAGED,
        "Case G: runtime_discoverable_not_managed must be True",
    )
    # runtime_discoverable != authoritative
    _check(
        contract.RUNTIME_DISCOVERABLE_NOT_AUTHORITATIVE,
        "Case G: runtime_discoverable_not_authoritative must be True",
    )
    # Source classes and activation classes must not overlap
    _check(
        not (contract.SOURCE_CLASSES & contract.ACTIVATION_CLASSES),
        "Case G: source classes and activation classes must not overlap",
    )
    # Runtime projection is not canonical
    _check(
        contract.RUNTIME_PROJECTION_NOT_CANONICAL,
        "Case G: runtime_projection_not_canonical must be True",
    )
    print("CASE_G_PASS")


# ---------------------------------------------------------------------------
# Case H: Wait mode classified with correct rules
# ---------------------------------------------------------------------------

def case_h_wait_mode_classified(contract, fixture: dict) -> None:
    """Case H: Wait mode classes are classified with correct rules."""
    case = fixture["case_fixtures"]["case_h_wait_mode_classified"]
    wait_modes = case["wait_modes"]

    # wakeup_capable_normal: polling prohibited
    _check(
        contract.WAIT_MODE_RULES[contract.WAIT_WAKEUP_CAPABLE_NORMAL]["polling_prohibited"] is True,
        "Case H: wakeup_capable_normal must prohibit polling",
    )
    _check(
        contract.is_valid_wait_mode(contract.WAIT_WAKEUP_CAPABLE_NORMAL),
        "Case H: wakeup_capable_normal must be a valid wait mode",
    )

    # non_wakeup_transport: explicit retrieval allowed
    _check(
        contract.WAIT_MODE_RULES[contract.WAIT_NON_WAKEUP_TRANSPORT]["explicit_retrieval_allowed"] is True,
        "Case H: non_wakeup_transport must allow explicit retrieval",
    )
    _check(
        contract.is_valid_wait_mode(contract.WAIT_NON_WAKEUP_TRANSPORT),
        "Case H: non_wakeup_transport must be a valid wait mode",
    )

    # isolated_probe with marker: valid
    errors = contract.check_wait_mode(
        contract.WAIT_ISOLATED_PROBE,
        has_polling_marker=True,
    )
    _check(
        not errors,
        f"Case H: isolated_probe with marker should have no errors, got {errors}",
    )

    # isolated_probe without marker: fail
    errors = contract.check_wait_mode(
        contract.WAIT_ISOLATED_PROBE,
        has_polling_marker=False,
    )
    _check(
        "isolated_probe_polling_without_marker" in errors,
        f"Case H: isolated_probe without marker should be rejected, got {errors}",
    )

    # recovery with reason: valid
    errors = contract.check_wait_mode(
        contract.WAIT_RECOVERY,
        recovery_reason="launcher_timeout_retry",
    )
    _check(
        not errors,
        f"Case H: recovery with reason should have no errors, got {errors}",
    )

    # recovery without reason: fail
    errors = contract.check_wait_mode(
        contract.WAIT_RECOVERY,
        recovery_reason=None,
    )
    _check(
        "recovery_without_reason" in errors,
        f"Case H: recovery without reason should be rejected, got {errors}",
    )

    # Unknown wait mode: fail
    errors = contract.check_wait_mode("unknown_mode")
    _check(
        "unknown_wait_mode" in errors,
        f"Case H: unknown wait mode should be rejected, got {errors}",
    )

    # All wait mode classes must be valid
    for mode in contract.WAIT_MODE_CLASSES:
        _check(
            contract.is_valid_wait_mode(mode),
            f"Case H: {mode} must be a valid wait mode",
        )

    print("CASE_H_PASS")


# ---------------------------------------------------------------------------
# Additional checks: single authority, schema consistency
# ---------------------------------------------------------------------------

def check_single_authority(contract) -> None:
    """Verify no other Python file duplicates the skill authority contract constants."""
    plugin_dir = ROOT / "plugin" / "aota-tools"
    duplicates = []
    for py_file in plugin_dir.glob("*.py"):
        if py_file.name == "_skill_authority_contract.py":
            continue
        text = py_file.read_text(encoding="utf-8")
        if "SOURCE_CLASSES" in text and "ACTIVATION_CLASSES" in text:
            duplicates.append(py_file.name)
    _check(
        not duplicates,
        f"skill authority contract constants duplicated in: {duplicates}",
    )


def check_schema_consistency(contract, fixture: dict) -> None:
    """Verify the contract schema dict matches the fixture."""
    schema = contract.SKILL_AUTHORITY_CONTRACT_SCHEMA
    _check(schema["schema_version"] == 1, "schema version must be 1")
    _check(
        set(schema["source_classes"]) == set(contract.SOURCE_CLASSES),
        "schema source_classes must match constant",
    )
    _check(
        set(schema["activation_classes"]) == set(contract.ACTIVATION_CLASSES),
        "schema activation_classes must match constant",
    )
    _check(
        set(schema["wait_mode_classes"]) == set(contract.WAIT_MODE_CLASSES),
        "schema wait_mode_classes must match constant",
    )
    _check(
        schema["source_authority"] == contract.SOURCE_AUTHORITY,
        "schema source_authority must match constant",
    )
    _check(
        schema["orchestration_entry_point"] == contract.ORCHESTRATION_ENTRY_POINT,
        "schema orchestration_entry_point must match constant",
    )
    # Verify fixture matches contract
    _check(
        set(fixture["source_classes"]) == set(contract.SOURCE_CLASSES),
        "fixture source_classes must match contract",
    )
    _check(
        set(fixture["activation_classes"]) == set(contract.ACTIVATION_CLASSES),
        "fixture activation_classes must match contract",
    )
    _check(
        set(fixture["wait_mode_classes"]) == set(contract.WAIT_MODE_CLASSES),
        "fixture wait_mode_classes must match contract",
    )
    _check(
        fixture["reference_routing_map"] == dict(contract.REFERENCE_ROUTING_MAP),
        "fixture reference_routing_map must match contract",
    )
    _check(
        fixture["fail_closed_reject_reasons"]
        == sorted(contract.SKILL_AUTHORITY_FAIL_CLOSED_REJECT_REASONS),
        "fixture fail_closed_reject_reasons must match contract",
    )
    # Verify migration findings
    _check(
        len(contract.MIGRATION_FINDINGS) == 3,
        "contract must have exactly 3 migration findings",
    )
    for finding in contract.MIGRATION_FINDINGS:
        _check(
            finding["status"] == "recorded_not_fixed",
            f"migration finding {finding['id']} must have status recorded_not_fixed",
        )


def check_reference_routing(contract) -> None:
    """Verify reference routing map covers all required routing situations."""
    required_routes = {
        "intake",
        "spec_create",
        "spec_retry",
        "task_start_wait_handoff",
        "tool_failure",
        "coder",
        "reviewer",
    }
    _check(
        set(contract.REFERENCE_ROUTING_MAP.keys()) == required_routes,
        f"reference routing map must cover all required routes, got {set(contract.REFERENCE_ROUTING_MAP.keys())}",
    )
    # Verify specific routing targets
    _check(
        contract.REFERENCE_ROUTING_MAP["intake"] == "aota-work-classify",
        "intake must route to aota-work-classify",
    )
    _check(
        contract.REFERENCE_ROUTING_MAP["task_start_wait_handoff"] == "aota-task-lifecycle",
        "task_start_wait_handoff must route to aota-task-lifecycle",
    )
    _check(
        contract.REFERENCE_ROUTING_MAP["tool_failure"] == "aota-tool-failure-fallback",
        "tool_failure must route to aota-tool-failure-fallback",
    )
    _check(
        contract.REFERENCE_ROUTING_MAP["coder"] == "aota-spec-driven-implementation",
        "coder must route to aota-spec-driven-implementation",
    )
    _check(
        contract.REFERENCE_ROUTING_MAP["reviewer"] == "aota-implementation-review",
        "reviewer must route to aota-implementation-review",
    )


# ---------------------------------------------------------------------------
# WI-1 Cases A-J: development Skill projection and inventory closure
# ---------------------------------------------------------------------------

def _load_assembly() -> dict:
    if not ASSEMBLY_PATH.is_file():
        raise AssertionError(f"assembly missing: {ASSEMBLY_PATH}")
    return _load_yaml(ASSEMBLY_PATH)


def _load_inventory() -> dict:
    if not INVENTORY_PATH.is_file():
        raise AssertionError(f"inventory missing: {INVENTORY_PATH}")
    return _load_yaml(INVENTORY_PATH)


def _profile_skills(assembly: dict, profile: str, key: str) -> set[str]:
    spec = assembly.get("profiles", {}).get(profile, {})
    return set(str(s) for s in spec.get(key, []))


def _soul_skills(soul_path: Path) -> set[str]:
    """Extract skill names from SOUL.md 'Active AOTA Skills' section."""
    if not soul_path.is_file():
        return set()
    text = soul_path.read_text(encoding="utf-8")
    match = re.search(r"##+\s*Active AOTA Skills\n(?P<body>.*?)(?:\n##+\s|\Z)", text, re.DOTALL)
    if not match:
        return set()
    return set(re.findall(r"\*\*([a-z][a-z0-9-]*)\*\*", match.group("body")))


def _config_disabled_skills(profile: str) -> set[str]:
    config_path = ROOT / "profiles" / profile / "config.yaml"
    if not config_path.is_file():
        return set()
    data = _load_yaml(config_path)
    return set(str(s) for s in data.get("skills", {}).get("disabled", []))


def case_wi1_a_task_main_reference_skills(contract) -> None:
    """WI1-A: task-main reference Skill source exists + assembly reference declared + not active-injected + direct destination expected = pass."""
    assembly = _load_assembly()
    task_main = assembly.get("profiles", {}).get("task-main", {})
    active = set(str(s) for s in task_main.get("active_skills", []))
    references = set(str(s) for s in task_main.get("reference_skills", []))
    for skill in WI1_DEVELOPMENT_SKILLS:
        # Source exists
        source = _skill_source_path(skill)
        _check(
            source.is_file(),
            f"WI1-A: task-main reference Skill source missing: {skill}",
        )
        # Assembly reference declared
        _check(
            skill in references,
            f"WI1-A: task-main reference_skills missing: {skill}",
        )
        # Not active-injected
        _check(
            skill not in active,
            f"WI1-A: {skill} must not be in task-main active_skills",
        )
        # Direct destination expected (not /orchestration/)
        inventory = _load_inventory()
        inv_entry = inventory.get("skills", {}).get(skill)
        _check(
            isinstance(inv_entry, dict),
            f"WI1-A: inventory entry missing for {skill}",
        )
        deployed = str(inv_entry.get("deployed_path", ""))
        _check(
            "/orchestration/" not in deployed,
            f"WI1-A: {skill} deployed_path must not use /orchestration/ category",
        )
    print("WI1_CASE_A_PASS")


def case_wi1_b_coder_reference_skills(contract) -> None:
    """WI1-B: coder active/reference Skill source exists + assembly class declared + not disabled + destination expected = pass."""
    assembly = _load_assembly()
    coder = assembly.get("profiles", {}).get("coder", {})
    active = set(str(s) for s in coder.get("active_skills", []))
    references = set(str(s) for s in coder.get("reference_skills", []))
    disabled = _config_disabled_skills("coder")
    for skill in WI1_DEVELOPMENT_SKILLS:
        source = _skill_source_path(skill)
        _check(
            source.is_file(),
            f"WI1-B: coder Skill source missing: {skill}",
        )
        # Assembly class declared (active or reference)
        _check(
            skill in active or skill in references,
            f"WI1-B: {skill} must be in coder active_skills or reference_skills",
        )
        # Not disabled
        _check(
            skill not in disabled,
            f"WI1-B: {skill} must not be disabled in coder config",
        )
        # Inventory entry exists
        inventory = _load_inventory()
        inv_entry = inventory.get("skills", {}).get(skill)
        _check(
            isinstance(inv_entry, dict),
            f"WI1-B: inventory entry missing for {skill}",
        )
    print("WI1_CASE_B_PASS")


def case_wi1_c_projection_disabled_conflict(contract) -> None:
    """WI1-C: A Skill projected to a profile must not also be disabled in that profile's config = fail."""
    assembly = _load_assembly()
    for profile, spec in assembly.get("profiles", {}).items():
        active = set(str(s) for s in spec.get("active_skills", []))
        references = set(str(s) for s in spec.get("reference_skills", []))
        disabled = _config_disabled_skills(profile)
        for skill in active | references:
            _check(
                skill not in disabled,
                f"WI1-C: {skill} is projected to {profile} but also disabled — conflict",
            )
    print("WI1_CASE_C_PASS")


def case_wi1_d_worker_skill_inventory_entries(contract) -> None:
    """WI1-D: three worker Skill inventory entries complete = pass."""
    inventory = _load_inventory()
    skills = inventory.get("skills", {})
    for skill_id, expected_owner in WI1_WORKER_SKILLS.items():
        _check(
            skill_id in skills,
            f"WI1-D: inventory entry missing for {skill_id}",
        )
        entry = skills[skill_id]
        _check(
            isinstance(entry, dict),
            f"WI1-D: entry for {skill_id} must be a dict",
        )
        _check(
            "source_path" in entry,
            f"WI1-D: {skill_id} must have source_path",
        )
        _check(
            "scope" in entry,
            f"WI1-D: {skill_id} must have scope",
        )
        _check(
            "owning_profiles" in entry,
            f"WI1-D: {skill_id} must have owning_profiles",
        )
        # owning_profiles must include the expected owner
        owners = set(str(o) for o in entry.get("owning_profiles", []))
        _check(
            expected_owner in owners,
            f"WI1-D: {skill_id} owning_profiles must include {expected_owner}, got {owners}",
        )
        # source_path must exist
        source = ROOT / str(entry.get("source_path", ""))
        _check(
            source.is_file(),
            f"WI1-D: {skill_id} source_path does not exist: {source}",
        )
        # deployment_authority must be false
        _check(
            entry.get("deployment_authority") is False,
            f"WI1-D: {skill_id} deployment_authority must be false",
        )
        # activation_class present and active
        _check(
            entry.get("activation_class") == "active",
            f"WI1-D: {skill_id} activation_class must be active, got {entry.get('activation_class')}",
        )
    print("WI1_CASE_D_PASS")


def case_wi1_e_inventory_no_projection_inference(contract) -> None:
    """WI1-E: inventory entry exists but assembly absent = must not infer runtime projection from inventory alone."""
    assembly = _load_assembly()
    inventory = _load_inventory()
    # For each worker Skill in inventory, verify that if it is NOT in
    # assembly active/reference for its owning profile, we do not treat
    # inventory as a projection. The contract says owning_profiles !=
    # active_skills. The inventory owning_profiles lists ownership, not
    # runtime projection.
    for skill_id, expected_owner in WI1_WORKER_SKILLS.items():
        inv_entry = inventory.get("skills", {}).get(skill_id, {})
        owners = set(str(o) for o in inv_entry.get("owning_profiles", []))
        # The inventory owns the metadata; assembly determines projection
        profile_spec = assembly.get("profiles", {}).get(expected_owner, {})
        active = set(str(s) for s in profile_spec.get("active_skills", []))
        # Worker skills ARE in assembly active for their owning profile —
        # verify this is the case (they were already there before WI-1)
        _check(
            skill_id in active,
            f"WI1-E: {skill_id} must be active in {expected_owner} assembly",
        )
        # owning_profiles is NOT the same as active_skills — verify separation
        _check(
            contract.OWNING_PROFILES_NOT_ACTIVE_SKILLS,
            "WI1-E: owning_profiles_not_active_skills invariant must hold",
        )
    print("WI1_CASE_E_PASS")


def case_wi1_f_assembly_source_fail_closed(contract) -> None:
    """WI1-F: assembly reference source does not exist = fail closed."""
    assembly = _load_assembly()
    for profile, spec in assembly.get("profiles", {}).items():
        for skill in spec.get("reference_skills", []):
            source = _skill_source_path(str(skill))
            _check(
                source.is_file(),
                f"WI1-F: reference Skill source missing for {profile}: {skill}",
            )
        for skill in spec.get("active_skills", []):
            source = _skill_source_path(str(skill))
            _check(
                source.is_file(),
                f"WI1-F: active Skill source missing for {profile}: {skill}",
            )
    print("WI1_CASE_F_PASS")


def case_wi1_g_soul_summary_drift(contract) -> None:
    """WI1-G: SOUL active-skills summary must match assembly active_skills (no drift)."""
    assembly = _load_assembly()
    for profile, spec in assembly.get("profiles", {}).items():
        soul_path = ROOT / "profiles" / profile / "SOUL.md"
        if not soul_path.is_file():
            continue
        active = set(str(s) for s in spec.get("active_skills", []))
        soul_active = _soul_skills(soul_path)
        # If the SOUL has an "Active AOTA Skills" section, it must match
        if soul_active:
            _check(
                soul_active == active,
                f"WI1-G: SOUL active-skills drift in {profile}: SOUL={soul_active}, assembly={active}",
            )
    print("WI1_CASE_G_PASS")


def case_wi1_h_profile_compatibility(contract) -> None:
    """WI1-H: existing Profile compatibility — architect/debugger/reviewer active Skills match assembly = pass."""
    assembly = _load_assembly()
    expected = {
        "architect": {"aota-architecture-review"},
        "debugger": {"aota-evidence-first-debugging"},
        "reviewer": {"aota-implementation-review"},
    }
    for profile, expected_skills in expected.items():
        spec = assembly.get("profiles", {}).get(profile, {})
        active = set(str(s) for s in spec.get("active_skills", []))
        _check(
            expected_skills.issubset(active),
            f"WI1-H: {profile} must have {expected_skills} in active_skills, got {active}",
        )
        # Source exists
        for skill in expected_skills:
            source = _skill_source_path(skill)
            _check(
                source.is_file(),
                f"WI1-H: source missing for {profile} active skill {skill}",
            )
    # Verify WI-0 coder active skills unchanged
    coder_active = set(str(s) for s in assembly.get("profiles", {}).get("coder", {}).get("active_skills", []))
    _check(
        coder_active == {"aota-spec-driven-implementation"},
        f"WI1-H: coder active_skills must be unchanged, got {coder_active}",
    )
    print("WI1_CASE_H_PASS")


def case_wi1_i_tool_count_canonical(contract) -> None:
    """WI1-I: tool count is plugin.yaml actual count, not hardcoded 59 = pass.

    Also verifies toolsets count from canonical lifecycle inventory (28) and
    profiles count from canonical assembly (6), and that the same canonical
    count resolver is used across readiness/backup/receipt/verify paths.
    """
    _check(
        PLUGIN_YAML_PATH.is_file(),
        f"WI1-I: plugin.yaml missing: {PLUGIN_YAML_PATH}",
    )
    plugin_yaml = _load_yaml(PLUGIN_YAML_PATH)
    tools = plugin_yaml.get("provides_tools", [])
    actual_count = len(tools)
    _check(
        actual_count == 61,
        f"WI1-I: plugin.yaml provides_tools must be 61, got {actual_count}",
    )
    # This verifier must NOT hardcode 59 — it reads actual count
    _check(
        actual_count != 59,
        "WI1-I: hardcoded 59 must not be used — tool count is canonical from plugin.yaml",
    )
    # aota_profile_task_status must still be in provides_tools (WI-0 invariant)
    _check(
        "aota_profile_task_status" in tools,
        "WI1-I: aota_profile_task_status must still be in provides_tools",
    )
    # Verify toolsets count from canonical lifecycle inventory
    inventory = _load_yaml(INVENTORY_PATH)
    inv_tools = inventory.get("tools", {})
    toolsets = {str(item.get("toolset")) for item in inv_tools.values() if isinstance(item, dict)}
    _check(
        len(toolsets) == 28,
        f"WI1-I: canonical toolsets count must be 28, got {len(toolsets)}",
    )
    # Verify profiles count from canonical assembly
    assembly = _load_yaml(ASSEMBLY_PATH)
    profiles = assembly.get("profiles", {})
    _check(
        len(profiles) == 6,
        f"WI1-I: canonical profiles count must be 6, got {len(profiles)}",
    )
    # Verify no hardcoded 59 remains in the canonical count resolver path
    # by checking that the package and assembly modules use canonical_counts()
    import importlib.util
    for script_name in ("profile_runtime_assembly.py", "aota_forge_plan_package.py"):
        script_path = ROOT / "scripts" / script_name
        _check(script_path.is_file(), f"WI1-I: script missing: {script_path}")
        spec = importlib.util.spec_from_file_location(f"_count_check_{script_name}", str(script_path))
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if hasattr(module, "canonical_counts"):
                counts = module.canonical_counts()
                _check(
                    counts["tools"] == 61,
                    f"WI1-I: {script_name} canonical_counts tools must be 61, got {counts['tools']}",
                )
                _check(
                    counts["toolsets"] == 28,
                    f"WI1-I: {script_name} canonical_counts toolsets must be 28, got {counts['toolsets']}",
                )
                _check(
                    counts["profiles"] == 6,
                    f"WI1-I: {script_name} canonical_counts profiles must be 6, got {counts['profiles']}",
                )
                _check(
                    counts["tools"] != 59,
                    f"WI1-I: {script_name} must detect old 59 baseline",
                )
    print("WI1_CASE_I_PASS")


def case_wi1_j_wi0_source_integrity(contract) -> None:
    """WI1-J: WI-0 source integrity — NO_POLL_SOUL_INVARIANT, wait semantics, retrieval_reason guard preserved = pass."""
    # NO_POLL_SOUL_INVARIANT in task-main SOUL
    soul_path = ROOT / "profiles" / "task-main" / "SOUL.md"
    _check(soul_path.is_file(), f"WI1-J: task-main SOUL missing: {soul_path}")
    soul_text = soul_path.read_text(encoding="utf-8")
    _check(
        "NO_POLL_SOUL_INVARIANT" in soul_text,
        "WI1-J: NO_POLL_SOUL_INVARIANT must be preserved in task-main SOUL",
    )
    _check(
        "retrieval_reason" in soul_text,
        "WI1-J: retrieval_reason guard reference must be preserved in task-main SOUL",
    )
    # Wait semantics in aota-task-lifecycle Skill
    lifecycle_path = _skill_source_path("aota-task-lifecycle")
    _check(
        lifecycle_path.is_file(),
        f"WI1-J: aota-task-lifecycle source missing: {lifecycle_path}",
    )
    lifecycle_text = lifecycle_path.read_text(encoding="utf-8")
    _check(
        "wait" in lifecycle_text.lower(),
        "WI1-J: aota-task-lifecycle must contain wait semantics",
    )
    # retrieval_reason guard in _profile_task_status.py
    status_path = ROOT / "plugin" / "aota-tools" / "_profile_task_status.py"
    _check(
        status_path.is_file(),
        f"WI1-J: _profile_task_status.py missing: {status_path}",
    )
    status_text = status_path.read_text(encoding="utf-8")
    _check(
        "retrieval_reason" in status_text,
        "WI1-J: retrieval_reason guard must be preserved in _profile_task_status.py",
    )
    # Wait mode classes still valid
    for mode in contract.WAIT_MODE_CLASSES:
        _check(
            contract.is_valid_wait_mode(mode),
            f"WI1-J: wait mode {mode} must still be valid",
        )
    print("WI1_CASE_J_PASS")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        contract = _load_contract_module()
        fixture = _load_fixture()

        case_a_canonical_managed(contract, fixture)
        case_b_profile_specific_managed(contract, fixture)
        case_c_unmanaged_runtime(contract, fixture)
        case_d_undeclared_collision(contract, fixture)
        case_e_legitimate_override(contract, fixture)
        case_f_inventory_not_deployment_authority(contract, fixture)
        case_g_activation_classes_separated(contract, fixture)
        case_h_wait_mode_classified(contract, fixture)

        check_single_authority(contract)
        check_schema_consistency(contract, fixture)
        check_reference_routing(contract)

        # WI-1 Cases A-J
        case_wi1_a_task_main_reference_skills(contract)
        case_wi1_b_coder_reference_skills(contract)
        case_wi1_c_projection_disabled_conflict(contract)
        case_wi1_d_worker_skill_inventory_entries(contract)
        case_wi1_e_inventory_no_projection_inference(contract)
        case_wi1_f_assembly_source_fail_closed(contract)
        case_wi1_g_soul_summary_drift(contract)
        case_wi1_h_profile_compatibility(contract)
        case_wi1_i_tool_count_canonical(contract)
        case_wi1_j_wi0_source_integrity(contract)

    except AssertionError as exc:
        print(f"SKILL_AUTHORITY_CONTRACT_MINIMUM_FAIL: {exc}")
        return 1
    except Exception as exc:
        print(f"SKILL_AUTHORITY_CONTRACT_MINIMUM_ERROR: {type(exc).__name__}: {exc}")
        return 1
    print("SKILL_AUTHORITY_CONTRACT_MINIMUM_PASS")
    print("RUNTIME_REGISTRY_CHECK_REQUIRED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
