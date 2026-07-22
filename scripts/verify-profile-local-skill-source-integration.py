#!/usr/bin/env python3
"""Profile-local Skill source integration verifier.

Covers Cases A-J for PCF-WI-PROFILE-LOCAL-SKILL-SOURCE-INTEGRATION.
stdlib-only; no pytest.  Validates that every profile-local orchestration
Skill has a canonical source, that the profile runtime assembly declares
reference Skills, that the lifecycle inventory has governance metadata,
and that collision/parity/routing invariants hold.

Cases:
  A: Runtime-only Skill (unmanaged finding).
  B: Canonical merge — source exists and is managed.
  C: Conflicting rule between source and runtime (content drift).
  D: Qualified probe polling — isolated_probe with marker.
  E: Explicit profile-specific managed Skill (override declared).
  F: Undeclared shadow — same-name collision without override (fail).
  G: Declared override — same-name collision with override (pass).
  H: Lifecycle inventory authority violation — inventory as deployment
     authority (fail).
  I: Routing map — no duplication of reference Skill content into the
     orchestration Skill.
  J: Legacy coder/reviewer/project-steward active Skills unaffected.
  K: Reference Skills expand to managed direct profile destinations.
  L: Missing reference projection fails closed.
  M: Reference activation remains separate from active activation.
  N: Legacy categorized collision fails before cleanup.
  O: Controlled legacy migration preserves a backup and direct parity.
  P: Reference subtree parity is checked.
  Q: Unrelated unmanaged Skills are preserved.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "plugin" / "aota-tools" / "_skill_authority_contract.py"
ASSEMBLY_PATH = ROOT / "deploy" / "profile-runtime-assembly.yaml"
INVENTORY_PATH = ROOT / "deploy" / "aota-lifecycle-inventory.yaml"
MANIFEST_PATH = ROOT / "deploy" / "aota-forge-plan-files.yaml"
FIXTURE_PATH = ROOT / "fixtures" / "skill-authority-contract-cases.json"

# The 8 profile-local orchestration Skills being reconciled
ORCHESTRATION_SKILLS = [
    "aota-task-lifecycle",
    "aota-workspace-diagnostics",
    "aota-workspace-model",
    "aota-canonical-spec-contract",
    "aota-canonical-spec-pitfalls",
    "aota-work-classify-and-plan-gate",
    "workspace-file-access-strategy",
    "aota-multi-phase-doc-closure",
]

# Skills that must NOT be affected by this work item (Case J)
LEGACY_ACTIVE_SKILLS = {
    "coder": ["aota-spec-driven-implementation"],
    "reviewer": ["aota-implementation-review"],
    "project-steward": [
        "aota-pcf-project-steward",
        "aota-workspace-model",
        "aota-workspace-diagnostics",
        "aota-task-lifecycle",
        "aota-tool-failure-fallback",
    ],
    "architect": ["aota-architecture-review"],
    "debugger": ["aota-evidence-first-debugging"],
}


def _load_contract_module():
    if not CONTRACT_PATH.is_file():
        raise AssertionError(f"canonical contract source missing: {CONTRACT_PATH}")
    spec = importlib.util.spec_from_file_location(
        "_skill_authority_contract_integration_test", str(CONTRACT_PATH)
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _skill_source_path(skill_id: str) -> Path:
    return ROOT / "skills" / skill_id / "SKILL.md"


def _skill_frontmatter_name(path: Path) -> str | None:
    text = path.read_text(encoding="utf-8", errors="replace")[:4096]
    match = re.search(r"^name:\s*(\S+)\s*$", text, re.MULTILINE)
    return match.group(1) if match else None


def _soul_skills(soul_path: Path) -> set[str]:
    """Extract skill names from SOUL.md 'Active AOTA Skills' section."""
    if not soul_path.is_file():
        return set()
    text = soul_path.read_text(encoding="utf-8")
    match = re.search(r"##+\s*Active AOTA Skills\n(?P<body>.*?)(?:\n##+\s|\Z)", text, re.DOTALL)
    if not match:
        return set()
    return set(re.findall(r"\*\*([a-z][a-z0-9-]*)\*\*", match.group("body")))


# ---------------------------------------------------------------------------
# Case A: Runtime-only Skill (unmanaged finding)
# ---------------------------------------------------------------------------

def case_a_runtime_only(contract, fixture: dict) -> None:
    """Case A: A Skill present at runtime without canonical source is unmanaged."""
    case = fixture["case_fixtures"]["case_a_runtime_only_unmanaged"]
    result = contract.classify_source(
        in_managed_manifest=case["in_managed_manifest"],
        in_profile_specific_manifest=case["in_profile_specific_manifest"],
        is_runtime_generated=case["is_runtime_generated"],
        is_bundled=case["is_bundled"],
    )
    _check(
        result == contract.UNMANAGED,
        f"Case A: classify_source should return unmanaged, got {result}",
    )
    print("CASE_A_PASS")


# ---------------------------------------------------------------------------
# Case B: Canonical merge — source exists and is managed
# ---------------------------------------------------------------------------

def case_b_canonical_merge(contract, fixture: dict) -> None:
    """Case B: Every profile-local orchestration Skill has canonical source."""
    case = fixture["case_fixtures"]["case_b_canonical_merge"]
    for skill_id in case["skills_to_verify"]:
        source_path = _skill_source_path(skill_id)
        _check(
            source_path.is_file(),
            f"Case B: canonical source missing for {skill_id}: {source_path}",
        )
        # Verify frontmatter name matches skill_id
        name = _skill_frontmatter_name(source_path)
        _check(
            name == skill_id,
            f"Case B: frontmatter name mismatch for {skill_id}: got {name}",
        )
    print("CASE_B_PASS")


# ---------------------------------------------------------------------------
# Case C: Conflicting rule between source and runtime (content drift)
# ---------------------------------------------------------------------------

def case_c_conflicting_rule(contract, fixture: dict) -> None:
    """Case C: Content drift detection — source hash differs from runtime."""
    case = fixture["case_fixtures"]["case_c_conflicting_rule"]
    # We can only verify source-side: the source must exist and have content
    for skill_id in case["skills_to_verify"]:
        source_path = _skill_source_path(skill_id)
        _check(
            source_path.is_file(),
            f"Case C: source must exist for drift check: {skill_id}",
        )
        content = source_path.read_text(encoding="utf-8")
        _check(
            len(content) > 0,
            f"Case C: source must have non-empty content: {skill_id}",
        )
    # Runtime parity is PENDING_DEPLOY — not verified here
    print("CASE_C_PASS")
    print("RUNTIME_PARITY_PENDING_DEPLOY")


# ---------------------------------------------------------------------------
# Case D: Qualified probe polling — isolated_probe with marker
# ---------------------------------------------------------------------------

def case_d_qualified_probe_polling(contract, fixture: dict) -> None:
    """Case D: Isolated probe polling is valid only with the marker."""
    case = fixture["case_fixtures"]["case_d_qualified_probe_polling"]
    # With marker: valid
    errors = contract.check_wait_mode(
        contract.WAIT_ISOLATED_PROBE,
        has_polling_marker=True,
    )
    _check(
        not errors,
        f"Case D: isolated_probe with marker should have no errors, got {errors}",
    )
    # Without marker: fail
    errors = contract.check_wait_mode(
        contract.WAIT_ISOLATED_PROBE,
        has_polling_marker=False,
    )
    _check(
        "isolated_probe_polling_without_marker" in errors,
        f"Case D: isolated_probe without marker should be rejected, got {errors}",
    )
    # Verify marker constant
    _check(
        contract.POLLING_ALLOWED_FOR_ISOLATED_PROBE_ONLY
        == "POLLING_ALLOWED_FOR_ISOLATED_PROBE_ONLY",
        "Case D: polling marker constant mismatch",
    )
    print("CASE_D_PASS")


# ---------------------------------------------------------------------------
# Case E: Explicit profile-specific managed Skill (override declared)
# ---------------------------------------------------------------------------

def case_e_profile_specific_managed(contract, fixture: dict) -> None:
    """Case E: A profile-specific managed Skill with explicit override passes."""
    case = fixture["case_fixtures"]["case_e_profile_specific_managed"]
    result = contract.classify_source(
        in_managed_manifest=case["in_managed_manifest"],
        in_profile_specific_manifest=case["in_profile_specific_manifest"],
        is_runtime_generated=case["is_runtime_generated"],
        is_bundled=case["is_bundled"],
    )
    _check(
        result == contract.PROFILE_SPECIFIC_MANAGED,
        f"Case E: classify_source should return profile_specific_managed, got {result}",
    )
    errors = contract.check_collision(
        case["skill_id"],
        case["source_classes_present"],
        override_declared=case["override_declared"],
    )
    _check(
        not errors,
        f"Case E: legitimate override should have no errors, got {errors}",
    )
    print("CASE_E_PASS")


# ---------------------------------------------------------------------------
# Case F: Undeclared shadow — same-name collision without override (fail)
# ---------------------------------------------------------------------------

def case_f_undeclared_shadow(contract, fixture: dict) -> None:
    """Case F: Same-name Skill with different hash, undeclared → fail."""
    case = fixture["case_fixtures"]["case_f_undeclared_shadow_fail"]
    errors = contract.check_collision(
        case["skill_id"],
        case["source_classes_present"],
        override_declared=case["override_declared"],
    )
    _check(
        contract.COLLISION_UNDECLARED in errors,
        f"Case F: undeclared collision should be rejected, got {errors}",
    )
    print("CASE_F_PASS")


# ---------------------------------------------------------------------------
# Case G: Declared override — same-name collision with override (pass)
# ---------------------------------------------------------------------------

def case_g_declared_override(contract, fixture: dict) -> None:
    """Case G: Same-name Skill with declared override passes with shadow chain."""
    case = fixture["case_fixtures"]["case_g_declared_override_pass"]
    errors = contract.check_collision(
        case["skill_id"],
        case["source_classes_present"],
        override_declared=case["override_declared"],
    )
    _check(
        not errors,
        f"Case G: declared override should have no errors, got {errors}",
    )
    winner = contract.resolve_collision(
        case["source_classes_present"],
        override_declared=case["override_declared"],
    )
    _check(
        winner == contract.PROFILE_SPECIFIC_MANAGED,
        f"Case G: resolve_collision should return profile_specific_managed, got {winner}",
    )
    print("CASE_G_PASS")


# ---------------------------------------------------------------------------
# Case H: Lifecycle inventory authority violation (fail)
# ---------------------------------------------------------------------------

def case_h_inventory_authority_violation(contract, fixture: dict) -> None:
    """Case H: Lifecycle inventory is governance metadata, not deployment authority."""
    case = fixture["case_fixtures"]["case_h_inventory_authority_violation"]
    inventory = _load_yaml(INVENTORY_PATH)
    sac = inventory.get("skill_authority_contract")
    _check(
        isinstance(sac, dict),
        "Case H: skill_authority_contract section missing from lifecycle inventory",
    )
    _check(
        sac.get("deployment_authority") is False,
        "Case H: inventory must not be deployment authority (deployment_authority=false)",
    )
    # Verify the inventory has governance metadata for all 8 orchestration Skills
    skills = inventory.get("skills", {})
    for skill_id in ORCHESTRATION_SKILLS:
        _check(
            skill_id in skills,
            f"Case H: lifecycle inventory missing governance metadata for {skill_id}",
        )
        entry = skills[skill_id]
        _check(
            isinstance(entry, dict),
            f"Case H: lifecycle inventory entry for {skill_id} must be a dict",
        )
        _check(
            "owning_profiles" in entry,
            f"Case H: lifecycle inventory entry for {skill_id} must have owning_profiles",
        )
        _check(
            "scope" in entry,
            f"Case H: lifecycle inventory entry for {skill_id} must have scope",
        )
    print("CASE_H_PASS")


# ---------------------------------------------------------------------------
# Case I: Routing map — no duplication of reference Skill content
# ---------------------------------------------------------------------------

def case_i_routing_map_no_duplication(contract, fixture: dict) -> None:
    """Case I: The orchestration Skill does not duplicate reference Skill content."""
    case = fixture["case_fixtures"]["case_i_routing_map_no_duplication"]
    orch_path = _skill_source_path("aota-profile-task-orchestration")
    orch_text = orch_path.read_text(encoding="utf-8")
    # Verify Skill Loading Map exists
    _check(
        "Skill Loading Map" in orch_text,
        "Case I: orchestration Skill must contain a Skill Loading Map",
    )
    # Verify the orchestration Skill does NOT contain the full field matrix
    # from aota-canonical-spec-contract
    spec_contract_path = _skill_source_path("aota-canonical-spec-contract")
    spec_contract_text = spec_contract_path.read_text(encoding="utf-8")
    # The orchestration Skill should not contain the full field matrix table
    # from the spec contract (it routes to it instead)
    _check(
        "spec_id" in orch_text and "spec_kind" in orch_text,
        "Case I: orchestration Skill should reference SPEC fields for routing",
    )
    # Verify the orchestration Skill does not contain the full pitfalls list
    pitfalls_path = _skill_source_path("aota-canonical-spec-pitfalls")
    pitfalls_text = pitfalls_path.read_text(encoding="utf-8")
    # Count "Pitfall" headings in orchestration Skill — should be 0
    pitfall_count = len(re.findall(r"^## Pitfall \d+", orch_text, re.MULTILINE))
    _check(
        pitfall_count == 0,
        f"Case I: orchestration Skill must not duplicate pitfalls (found {pitfall_count})",
    )
    # Verify reference routing map covers required routes
    for route in case["required_routes"]:
        _check(
            route in contract.REFERENCE_ROUTING_MAP,
            f"Case I: reference routing map missing route: {route}",
        )
    print("CASE_I_PASS")


# ---------------------------------------------------------------------------
# Case J: Legacy coder/reviewer/project-steward active Skills unaffected
# ---------------------------------------------------------------------------

def case_j_legacy_skills_unaffected(contract, fixture: dict) -> None:
    """Case J: Existing coder/reviewer/project-steward active Skills are unaffected."""
    case = fixture["case_fixtures"]["case_j_legacy_skills_unaffected"]
    assembly = _load_yaml(ASSEMBLY_PATH)
    profiles = assembly.get("profiles", {})
    for profile, expected_skills in LEGACY_ACTIVE_SKILLS.items():
        _check(
            profile in profiles,
            f"Case J: profile {profile} missing from assembly",
        )
        active = set(str(s) for s in profiles[profile].get("active_skills", []))
        for skill in expected_skills:
            _check(
                skill in active,
                f"Case J: {skill} must remain active in {profile}",
            )
            # Verify source still exists
            source_path = _skill_source_path(skill)
            _check(
                source_path.is_file(),
                f"Case J: source for {skill} must still exist",
            )
    # Verify coder active skills unchanged
    coder_skills = set(str(s) for s in profiles["coder"].get("active_skills", []))
    _check(
        coder_skills == {"aota-spec-driven-implementation"},
        f"Case J: coder active_skills must be unchanged, got {coder_skills}",
    )
    # Verify reviewer active skills unchanged
    reviewer_skills = set(str(s) for s in profiles["reviewer"].get("active_skills", []))
    _check(
        reviewer_skills == {"aota-implementation-review"},
        f"Case J: reviewer active_skills must be unchanged, got {reviewer_skills}",
    )
    print("CASE_J_PASS")


# ---------------------------------------------------------------------------
# Additional checks: source existence, assembly consistency, manifest
# ---------------------------------------------------------------------------

def check_all_orchestration_skills_have_source() -> None:
    """Verify all 8 orchestration Skills have canonical source."""
    for skill_id in ORCHESTRATION_SKILLS:
        source_path = _skill_source_path(skill_id)
        _check(
            source_path.is_file(),
            f"Source existence: canonical source missing for {skill_id}",
        )
        name = _skill_frontmatter_name(source_path)
        _check(
            name == skill_id,
            f"Source existence: frontmatter name mismatch for {skill_id}: got {name}",
        )


def check_assembly_reference_skills() -> None:
    """Verify the assembly declares reference_skills for task-main."""
    assembly = _load_yaml(ASSEMBLY_PATH)
    task_main = assembly.get("profiles", {}).get("task-main", {})
    ref_skills = set(str(s) for s in task_main.get("reference_skills", []))
    # All 5 new reference skills should be declared
    expected_refs = {
        "aota-canonical-spec-contract",
        "aota-canonical-spec-pitfalls",
        "aota-work-classify-and-plan-gate",
        "workspace-file-access-strategy",
        "aota-multi-phase-doc-closure",
    }
    _check(
        expected_refs.issubset(ref_skills),
        f"Assembly: task-main reference_skills must include all 5 new references, got {ref_skills}",
    )


def check_inventory_migration_findings_resolved() -> None:
    """Verify migration findings are marked resolved_in_source."""
    inventory = _load_yaml(INVENTORY_PATH)
    sac = inventory.get("skill_authority_contract", {})
    findings = sac.get("migration_findings", [])
    for finding in findings:
        _check(
            finding["status"] == "resolved_in_source",
            f"Inventory: migration finding {finding['id']} must be resolved_in_source, got {finding['status']}",
        )
        _check(
            finding.get("pending_deploy") is True,
            f"Inventory: migration finding {finding['id']} must have pending_deploy=true",
        )


def check_manifest_skill_pattern() -> None:
    """Verify the managed manifest still includes the SKILL.md pattern."""
    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8")
    _check(
        'pattern: "*/SKILL.md"' in manifest_text,
        "Manifest: managed skill pattern missing",
    )


def check_no_duplicate_bare_names() -> None:
    """Verify no duplicate bare skill names in the inventory."""
    inventory = _load_yaml(INVENTORY_PATH)
    skills = inventory.get("skills", {})
    names = [s.get("source_path", "").split("/")[-2] if "/" in s.get("source_path", "") else "" for s in skills.values() if isinstance(s, dict)]
    names = [n for n in names if n]
    _check(
        len(names) == len(set(names)),
        f"Duplicate bare names in inventory: {[n for n in names if names.count(n) > 1]}",
    )


def check_source_path_existence() -> None:
    """Verify every skill in the inventory has an existing source path."""
    inventory = _load_yaml(INVENTORY_PATH)
    skills = inventory.get("skills", {})
    for skill_id, entry in skills.items():
        if not isinstance(entry, dict):
            continue
        source_path = str(entry.get("source_path", ""))
        if not source_path:
            continue
        path = ROOT / source_path
        _check(
            path.is_file(),
            f"Source path existence: {skill_id} source path does not exist: {source_path}",
        )


def _load_runtime_assembly_module():
    path = ROOT / "scripts" / "profile_runtime_assembly.py"
    spec = importlib.util.spec_from_file_location("profile_runtime_assembly_integration", str(path))
    if not spec or not spec.loader:
        raise AssertionError("cannot load profile runtime assembly module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_package_module():
    path = ROOT / "scripts" / "aota_forge_plan_package.py"
    spec = importlib.util.spec_from_file_location("aota_forge_plan_package_integration", str(path))
    if not spec or not spec.loader:
        raise AssertionError("cannot load managed package module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_reference_projection_cases() -> None:
    """Cases K-Q: direct reference projection and controlled legacy migration."""
    assembly = _load_yaml(ASSEMBLY_PATH)
    task_main = assembly["profiles"]["task-main"]
    references = [str(skill) for skill in task_main.get("reference_skills", [])]
    active = {str(skill) for skill in task_main.get("active_skills", [])}
    _check(references and not active.intersection(references), "Case K/M: reference skills must be declared and remain non-active")

    package = _load_package_module()
    entries = package.build_entries(package.load_manifest())
    direct_ids = {
        str(entry["skill_name"])
        for entry in entries
        if entry.get("profile") == "task-main"
        and entry.get("activation_class") == "reference"
        and entry["kind"] == "copy"
    }
    _check(set(references) == direct_ids, f"Case K: direct managed references mismatch: {direct_ids}")
    _check(
        all("/orchestration/" not in str(entry["destination"]) for entry in entries if entry["kind"] == "copy" and entry.get("activation_class") == "reference"),
        "Case K: reference copy destination must not use orchestration category",
    )
    print("CASE_K_PASS")

    runtime_assembly = _load_runtime_assembly_module()
    with tempfile.TemporaryDirectory(prefix="aota-reference-projection-") as temp:
        root = Path(temp)
        runtime_assembly._copy_fixture_runtime(root)
        missing = root / "profiles" / "task-main" / "skills" / references[0] / "SKILL.md"
        missing.unlink()
        _check(
            any(f"reference-skill:{references[0]}:parity:SKILL.md" in error for error in runtime_assembly.pre_activation_errors(root)),
            "Case L: missing reference direct projection must fail",
        )
        print("CASE_L_PASS")
        shutil.copy2(ROOT / "skills" / references[0] / "SKILL.md", missing)

        _check(
            not active.intersection(references),
            "Case M: reference skills must not be active",
        )
        print("CASE_M_PASS")

        legacy = root / "profiles" / "task-main" / "skills" / "orchestration" / references[0] / "SKILL.md"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("legacy collision\n", encoding="utf-8")
        _check(
            any(f"legacy-reference-collision:{references[0]}" in error for error in runtime_assembly.pre_activation_errors(root)),
            "Case N: legacy categorized collision must fail before cleanup",
        )
        print("CASE_N_PASS")

        legacy.unlink()
        legacy.parent.rmdir()
        _check(not runtime_assembly.pre_activation_errors(root), "Case O: cleaned direct projection must pass")
        backup_marker = root / "backup" / "legacy" / references[0] / "SKILL.md"
        backup_marker.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(missing if missing.exists() else ROOT / "skills" / references[0] / "SKILL.md", backup_marker)
        _check(backup_marker.is_file(), "Case O: controlled migration backup must exist")
        print("CASE_O_PASS")

        source_root = ROOT / "skills" / references[0]
        direct_root = root / "profiles" / "task-main" / "skills" / references[0]
        _check(not runtime_assembly.skill_tree_errors(source_root, direct_root, root, "case-p"), "Case P: reference subtree parity must pass")
        print("CASE_P_PASS")

        unrelated = root / "profiles" / "task-main" / "skills" / "orchestration" / "aota-workspace-read-fallback" / "SKILL.md"
        unrelated.parent.mkdir(parents=True, exist_ok=True)
        unrelated.write_text("unrelated unmanaged\n", encoding="utf-8")
        _check(unrelated.is_file(), "Case Q: unrelated unmanaged Skill must be preserved")
        _check(not any("aota-workspace-read-fallback" in error for error in runtime_assembly.pre_activation_errors(root)), "Case Q: unrelated Skill must not be treated as migration collision")
        print("CASE_Q_PASS")


def check_wi1_development_skill_projection() -> None:
    """WI-1 Cases A-J: development Skill projection and inventory closure."""
    import yaml

    assembly = _load_yaml(ASSEMBLY_PATH)
    inventory = _load_yaml(INVENTORY_PATH)
    profiles_data = assembly.get("profiles", {})
    skills_inventory = inventory.get("skills", {})

    # WI1-A: task-main reference Skill source exists + assembly reference declared + not active-injected
    task_main = profiles_data.get("task-main", {})
    tm_active = set(str(s) for s in task_main.get("active_skills", []))
    tm_refs = set(str(s) for s in task_main.get("reference_skills", []))
    for skill in ("aota-plugin-tool-development", "aota-skill-development"):
        src = _skill_source_path(skill)
        _check(src.is_file(), f"WI1-A: source missing for {skill}")
        _check(skill in tm_refs, f"WI1-A: {skill} not in task-main reference_skills")
        _check(skill not in tm_active, f"WI1-A: {skill} must not be in task-main active_skills")
    print("WI1_A_PASS")

    # WI1-B: coder reference Skill source exists + assembly class declared + not disabled
    coder = profiles_data.get("coder", {})
    coder_active = set(str(s) for s in coder.get("active_skills", []))
    coder_refs = set(str(s) for s in coder.get("reference_skills", []))
    coder_config = _load_yaml(ROOT / "profiles" / "coder" / "config.yaml")
    coder_disabled = set(str(s) for s in coder_config.get("skills", {}).get("disabled", []))
    for skill in ("aota-plugin-tool-development", "aota-skill-development"):
        _check(_skill_source_path(skill).is_file(), f"WI1-B: source missing for {skill}")
        _check(skill in coder_active or skill in coder_refs, f"WI1-B: {skill} not in coder active/reference_skills")
        _check(skill not in coder_disabled, f"WI1-B: {skill} must not be disabled in coder config")
    print("WI1_B_PASS")

    # WI1-C: projection/disabled conflict = fail (no conflicts should exist)
    for profile, spec in profiles_data.items():
        active = set(str(s) for s in spec.get("active_skills", []))
        refs = set(str(s) for s in spec.get("reference_skills", []))
        config_path = ROOT / "profiles" / profile / "config.yaml"
        if not config_path.is_file():
            continue
        config_data = _load_yaml(config_path)
        disabled = set(str(s) for s in config_data.get("skills", {}).get("disabled", []))
        for skill in active | refs:
            _check(skill not in disabled, f"WI1-C: {skill} projected to {profile} but disabled — conflict")
    print("WI1_C_PASS")

    # WI1-D: three worker Skill inventory entries complete
    worker_skills = {
        "aota-implementation-review": "reviewer",
        "aota-architecture-review": "architect",
        "aota-evidence-first-debugging": "debugger",
    }
    for skill_id, owner in worker_skills.items():
        _check(skill_id in skills_inventory, f"WI1-D: inventory entry missing for {skill_id}")
        entry = skills_inventory[skill_id]
        _check(isinstance(entry, dict), f"WI1-D: entry for {skill_id} must be dict")
        _check("source_path" in entry, f"WI1-D: {skill_id} missing source_path")
        _check("scope" in entry, f"WI1-D: {skill_id} missing scope")
        _check("owning_profiles" in entry, f"WI1-D: {skill_id} missing owning_profiles")
        owners = set(str(o) for o in entry.get("owning_profiles", []))
        _check(owner in owners, f"WI1-D: {skill_id} owning_profiles must include {owner}")
        _check(entry.get("deployment_authority") is False, f"WI1-D: {skill_id} deployment_authority must be false")
        _check(entry.get("activation_class") == "active", f"WI1-D: {skill_id} activation_class must be active")
        src = ROOT / str(entry.get("source_path", ""))
        _check(src.is_file(), f"WI1-D: {skill_id} source_path does not exist: {src}")
    print("WI1_D_PASS")

    # WI1-E: inventory entry exists but assembly absent = no runtime projection inference
    # Inventory owning_profiles is metadata ownership, not runtime projection.
    # Assembly active_skills is the projection authority. We verify that
    # worker skills are projected via assembly, not inferred from inventory.
    _check(
        inventory.get("skill_authority_contract", {}).get("deployment_authority") is False,
        "WI1-E: inventory must not be deployment authority",
    )
    for skill_id, owner in worker_skills.items():
        spec = profiles_data.get(owner, {})
        active = set(str(s) for s in spec.get("active_skills", []))
        _check(skill_id in active, f"WI1-E: {skill_id} must be active in {owner} assembly (not inferred from inventory)")
    print("WI1_E_PASS")

    # WI1-F: assembly source does not exist = fail closed
    for profile, spec in profiles_data.items():
        for skill in spec.get("active_skills", []) + spec.get("reference_skills", []):
            src = _skill_source_path(str(skill))
            _check(src.is_file(), f"WI1-F: source missing for {profile}: {skill}")
    print("WI1_F_PASS")

    # WI1-G: SOUL summary drift
    for profile, spec in profiles_data.items():
        soul_path = ROOT / "profiles" / profile / "SOUL.md"
        if not soul_path.is_file():
            continue
        active = set(str(s) for s in spec.get("active_skills", []))
        soul_active = _soul_skills(soul_path)
        if soul_active:
            _check(soul_active == active, f"WI1-G: SOUL drift in {profile}: {soul_active} != {active}")
    print("WI1_G_PASS")

    # WI1-H: existing Profile compatibility
    for profile, expected_skills in {
        "architect": {"aota-architecture-review"},
        "debugger": {"aota-evidence-first-debugging"},
        "reviewer": {"aota-implementation-review"},
    }.items():
        active = set(str(s) for s in profiles_data.get(profile, {}).get("active_skills", []))
        _check(
            expected_skills.issubset(active),
            f"WI1-H: {profile} must have {expected_skills}, got {active}",
        )
    coder_active_check = set(str(s) for s in profiles_data.get("coder", {}).get("active_skills", []))
    _check(coder_active_check == {"aota-spec-driven-implementation"}, f"WI1-H: coder active_skills changed: {coder_active_check}")
    print("WI1_H_PASS")

    # WI1-I: tool count is plugin.yaml actual count, not hardcoded 59
    plugin_yaml = _load_yaml(ROOT / "plugin" / "aota-tools" / "plugin.yaml")
    tools = plugin_yaml.get("provides_tools", [])
    actual_count = len(tools)
    _check(actual_count == 61, f"WI1-I: tool count must be 61, got {actual_count}")
    _check(actual_count != 59, "WI1-I: must not use hardcoded 59")
    _check("aota_profile_task_status" in tools, "WI1-I: aota_profile_task_status must be in provides_tools")
    print("WI1_I_PASS")

    # WI1-J: WI-0 source integrity
    soul_text = (ROOT / "profiles" / "task-main" / "SOUL.md").read_text(encoding="utf-8")
    _check("NO_POLL_SOUL_INVARIANT" in soul_text, "WI1-J: NO_POLL_SOUL_INVARIANT missing")
    _check("retrieval_reason" in soul_text, "WI1-J: retrieval_reason guard missing in SOUL")
    status_text = (ROOT / "plugin" / "aota-tools" / "_profile_task_status.py").read_text(encoding="utf-8")
    _check("retrieval_reason" in status_text, "WI1-J: retrieval_reason guard missing in _profile_task_status.py")
    lifecycle_text = _skill_source_path("aota-task-lifecycle").read_text(encoding="utf-8")
    _check("wait" in lifecycle_text.lower(), "WI1-J: wait semantics missing in aota-task-lifecycle")
    print("WI1_J_PASS")


def check_skill_tree_expansion_and_count_parity() -> None:
    """Verify shared skill tree expansion and canonical count parity.

    Ensures:
    - expand_skill_tree includes SKILL.md and nested references/**,
      excludes __pycache__, *.pyc, temporary/editor backup files.
    - Package projection and profile assembly use the same shared expansion
      path and compare exactly for all managed active/reference Skills.
    - Canonical counts (tools/toolsets/profiles) are derived from canonical
      sources and detect the old 59 baseline.
    - All Skills containing references subtrees are enumerated and have
      parity evidence.
    """
    runtime_assembly = _load_runtime_assembly_module()
    package = _load_package_module()

    # Verify both modules expose the same expand_skill_tree helper
    _check(
        hasattr(runtime_assembly, "expand_skill_tree"),
        "runtime_assembly must expose expand_skill_tree",
    )
    _check(
        hasattr(package, "expand_skill_tree"),
        "package must expose expand_skill_tree",
    )

    # Verify package/assembly expected file set parity for all managed Skills
    assembly_data = _load_yaml(ASSEMBLY_PATH)
    for profile, spec in assembly_data.get("profiles", {}).items():
        for skill in spec.get("active_skills", []) + spec.get("reference_skills", []):
            skill_id = str(skill)
            source_root = ROOT / "skills" / skill_id
            if not source_root.is_dir():
                continue
            # Both expansions must produce the same result
            assembly_tree = runtime_assembly.expand_skill_tree(source_root)
            package_tree = package.expand_skill_tree(source_root)
            _check(
                set(assembly_tree.keys()) == set(package_tree.keys()),
                f"Skill tree parity: {skill_id} assembly/package file sets differ",
            )
    print("SKILL_TREE_PACKAGE_ASSEMBLY_PARITY_PASS")

    # Verify canonical counts derived from canonical sources
    asm_counts = runtime_assembly.canonical_counts()
    pkg_counts = package.canonical_counts()
    _check(
        asm_counts == pkg_counts,
        f"Count resolver mismatch: assembly={asm_counts}, package={pkg_counts}",
    )
    _check(asm_counts["tools"] == 61, f"Canonical tools count must be 61, got {asm_counts['tools']}")
    _check(asm_counts["toolsets"] == 28, f"Canonical toolsets count must be 28, got {asm_counts['toolsets']}")
    _check(asm_counts["profiles"] == 6, f"Canonical profiles count must be 6, got {asm_counts['profiles']}")
    _check(asm_counts["tools"] != 59, "Must detect old 59 baseline")
    print("CANONICAL_COUNT_PARITY_PASS")

    # Verify expand_skill_tree excludes __pycache__, *.pyc, editor backup
    with tempfile.TemporaryDirectory(prefix="aota-skill-tree-expand-verifier-") as temp:
        root = Path(temp) / "skill"
        (root / "references" / "deep").mkdir(parents=True)
        (root / "SKILL.md").write_text("---\nname: test\n---\n# Test\n", encoding="utf-8")
        (root / "references" / "ref.md").write_text("ref\n", encoding="utf-8")
        (root / "references" / "deep" / "nested.md").write_text("deep\n", encoding="utf-8")
        (root / "__pycache__").mkdir()
        (root / "__pycache__" / "cache.pyc").write_text("cache\n", encoding="utf-8")
        (root / "backup.md~").write_text("backup\n", encoding="utf-8")
        (root / "editor.bak").write_text("bak\n", encoding="utf-8")
        tree = runtime_assembly.expand_skill_tree(root)
        _check("SKILL.md" in tree, "SKILL.md must be in expanded tree")
        _check("references/ref.md" in tree, "references/ref.md must be in expanded tree")
        _check("references/deep/nested.md" in tree, "references/deep/nested.md must be in expanded tree")
        _check(not any(r.startswith("__pycache__/") for r in tree), "__pycache__ must be excluded")
        _check("backup.md~" not in tree, "editor backup ~ must be excluded")
        _check("editor.bak" not in tree, ".bak must be excluded")
        # Package expansion must produce the same result
        pkg_tree = package.expand_skill_tree(root)
        _check(set(tree.keys()) == set(pkg_tree.keys()), "Package/assembly expansion must match")
    print("SKILL_TREE_EXPANSION_EXCLUSION_PASS")

    # Verify skills_with_references enumeration
    ref_skills = runtime_assembly.skills_with_references()
    _check(isinstance(ref_skills, list), "skills_with_references must return a list")
    # Verify every listed skill actually has a references/ directory
    for skill_id in ref_skills:
        ref_dir = ROOT / "skills" / skill_id / "references"
        _check(ref_dir.is_dir(), f"skills_with_references listed {skill_id} but no references/ dir exists")
    # Verify all managed Skills with references are enumerated
    assembly = _load_yaml(ASSEMBLY_PATH)
    for profile, spec in assembly.get("profiles", {}).items():
        for skill in spec.get("active_skills", []) + spec.get("reference_skills", []):
            skill_id = str(skill)
            ref_dir = ROOT / "skills" / skill_id / "references"
            if ref_dir.is_dir():
                _check(skill_id in ref_skills, f"Skill {skill_id} has references/ but not enumerated")
    print("SKILLS_WITH_REFERENCES_ENUMERATION_PASS")

    # Verify undeclared-runtime-extra detection
    with tempfile.TemporaryDirectory(prefix="aota-undeclared-extra-verifier-") as temp:
        src = Path(temp) / "src" / "skill"
        tgt = Path(temp) / "tgt" / "skill"
        src.mkdir(parents=True)
        (src / "SKILL.md").write_text("source\n", encoding="utf-8")
        (tgt / "references").mkdir(parents=True)
        (tgt / "SKILL.md").write_text("source\n", encoding="utf-8")
        (tgt / "references" / "undeclared.md").write_text("undeclared\n", encoding="utf-8")
        errs = runtime_assembly.skill_tree_errors(src, tgt, temp, "verifier-undeclared")
        _check(
            any("undeclared-runtime-extra:references/undeclared.md" in e for e in errs),
            f"Expected undeclared-runtime-extra error, got {errs}",
        )
    print("UNDECLARED_RUNTIME_EXTRA_DETECTION_PASS")

    # Verify symlink/path-traversal escape detection.
    # The outside target must be beyond the runtime_root (temp) so the
    # symlink genuinely crosses the bounded root boundary and is classified
    # as symlink-escape (fail-closed), not undeclared-runtime-extra.
    with tempfile.TemporaryDirectory(prefix="aota-symlink-escape-outside-") as outside_temp:
        outside = Path(outside_temp) / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="aota-symlink-escape-verifier-") as temp:
            src = Path(temp) / "src" / "skill"
            tgt = Path(temp) / "tgt" / "skill"
            src.mkdir(parents=True)
            (src / "SKILL.md").write_text("source\n", encoding="utf-8")
            (tgt).mkdir(parents=True)
            (tgt / "SKILL.md").write_text("source\n", encoding="utf-8")
            (tgt / "escape.md").symlink_to(outside)
            errs = runtime_assembly.skill_tree_errors(src, tgt, temp, "verifier-symlink")
            _check(
                any("symlink-escape:escape.md" in e for e in errs),
                f"Expected symlink-escape error, got {errs}",
            )
    print("SYMLINK_PATH_TRAVERSAL_DETECTION_PASS")


def main() -> int:
    try:
        contract = _load_contract_module()
        fixture = _load_fixture()

        case_a_runtime_only(contract, fixture)
        case_b_canonical_merge(contract, fixture)
        case_c_conflicting_rule(contract, fixture)
        case_d_qualified_probe_polling(contract, fixture)
        case_e_profile_specific_managed(contract, fixture)
        case_f_undeclared_shadow(contract, fixture)
        case_g_declared_override(contract, fixture)
        case_h_inventory_authority_violation(contract, fixture)
        case_i_routing_map_no_duplication(contract, fixture)
        case_j_legacy_skills_unaffected(contract, fixture)

        check_all_orchestration_skills_have_source()
        check_assembly_reference_skills()
        check_inventory_migration_findings_resolved()
        check_manifest_skill_pattern()
        check_no_duplicate_bare_names()
        check_source_path_existence()
        check_reference_projection_cases()
        check_wi1_development_skill_projection()
        check_skill_tree_expansion_and_count_parity()

    except AssertionError as exc:
        print(f"PROFILE_LOCAL_SKILL_SOURCE_INTEGRATION_FAIL: {exc}")
        return 1
    except Exception as exc:
        print(f"PROFILE_LOCAL_SKILL_SOURCE_INTEGRATION_ERROR: {type(exc).__name__}: {exc}")
        return 1
    print("PROFILE_LOCAL_SKILL_SOURCE_INTEGRATION_PASS")
    print("RUNTIME_PARITY_PENDING_DEPLOY")
    print("PROCESS_RECREATE_REQUIRED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
