#!/usr/bin/env python3
"""Static governance checks for AOTA Skill lifecycle declarations."""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PROFILE_IDS = {"task-main", "architect", "reviewer", "coder", "debugger", "project-steward"}
TARGETS = {"hermes-agent", "hermes-webui", "new-session"}


def load_yaml(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"mapping required: {path}")
    return value


def errors_for(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    try:
        inventory = load_yaml(root / "deploy" / "aota-lifecycle-inventory.yaml")
    except Exception as exc:
        return [f"inventory:{type(exc).__name__}"]
    skills = inventory.get("skills")
    if inventory.get("schema_version") != 1 or not isinstance(skills, dict) or not skills:
        return ["inventory-schema"]
    manifest = (root / "deploy" / "aota-forge-plan-files.yaml").read_text(encoding="utf-8")
    if 'pattern: "*/SKILL.md"' not in manifest:
        errors.append("managed-skill-pattern")
    required = {
        "aota-profile-skill-routing-index", "aota-profile-task-orchestration",
        "aota-pcf-project-steward",
        "aota-spec-driven-implementation", "aota-plugin-tool-development",
        "aota-skill-development",
    }
    if not required.issubset(skills):
        errors.append("representative-skills")
    references = {
        root / "skills" / "aota-spec-driven-implementation" / "SKILL.md": "aota-plugin-tool-development",
        root / "skills" / "aota-profile-task-orchestration" / "SKILL.md": "aota-skill-development",
    }
    for path, reference in references.items():
        if not path.is_file() or reference not in path.read_text(encoding="utf-8"):
            errors.append(f"skill-reference:{path.parent.name}")
    for skill_id, item in skills.items():
        if not isinstance(item, dict):
            errors.append(f"skill-shape:{skill_id}")
            continue
        required_fields = {"scope", "owning_profiles", "source_path", "deployed_path", "activation_targets", "behavior_smoke_required"}
        if not required_fields.issubset(item):
            errors.append(f"skill-fields:{skill_id}")
            continue
        if item["scope"] not in {"global", "profile_local"}:
            errors.append(f"scope:{skill_id}")
        source = Path(str(item["source_path"]))
        if source.is_absolute() or ".." in source.parts or source.name != "SKILL.md":
            errors.append(f"source-path:{skill_id}")
        elif not (root / source).is_file():
            errors.append(f"skill-source:{skill_id}")
        if "SOUL" in source.parts or str(item["source_path"]).endswith("SOUL.md"):
            errors.append(f"soul-only:{skill_id}")
        deployed = str(item["deployed_path"])
        if item["scope"] == "global" and deployed != f"~/.hermes/skills/{skill_id}/SKILL.md":
            errors.append(f"deployed-path:{skill_id}")
        owners = set(item["owning_profiles"])
        if not owners or not owners.issubset(PROFILE_IDS):
            errors.append(f"owner:{skill_id}")
        if set(item["activation_targets"]) != TARGETS:
            errors.append(f"activation:{skill_id}")
        if item["behavior_smoke_required"] is not True:
            errors.append(f"behavior-smoke:{skill_id}")
        for owner in owners:
            config_path = root / "profiles" / owner / "config.yaml"
            if not config_path.is_file():
                errors.append(f"owner-profile:{skill_id}:{owner}")
                continue
            disabled = set(load_yaml(config_path).get("skills", {}).get("disabled", []))
            if skill_id in disabled:
                errors.append(f"visibility-disabled:{skill_id}:{owner}")
    return errors


def fixture() -> int:
    def mutate(path: Path, change) -> None:
        data = load_yaml(path)
        change(data)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def run_case(name: str, change, expected: str) -> None:
        with tempfile.TemporaryDirectory(prefix="aota-skill-lifecycle-") as temp:
            fixture_root = Path(temp) / "repo"
            shutil.copytree(ROOT, fixture_root, ignore=shutil.ignore_patterns(".git", "__pycache__", ".deploy-backups", ".deploy-receipts"))
            change(fixture_root)
            observed = errors_for(fixture_root)
            if not any(value.startswith(expected) for value in observed):
                raise AssertionError(f"{name}: expected {expected}, got {observed}")

    def run_pass(name: str, change) -> None:
        with tempfile.TemporaryDirectory(prefix="aota-skill-lifecycle-") as temp:
            fixture_root = Path(temp) / "repo"
            shutil.copytree(ROOT, fixture_root, ignore=shutil.ignore_patterns(".git", "__pycache__", ".deploy-backups", ".deploy-receipts"))
            change(fixture_root)
            observed = errors_for(fixture_root)
            if observed:
                raise AssertionError(f"{name}: unexpected {observed}")

    run_pass("valid-global", lambda root: None)
    run_pass("valid-profile-local", lambda root: mutate(root / "deploy/aota-lifecycle-inventory.yaml", lambda data: data["skills"]["aota-skill-development"].update(scope="profile_local", deployed_path="~/.hermes/profiles/task-main/skills/aota-skill-development/SKILL.md")))
    run_case("missing-skill", lambda root: mutate(root / "deploy/aota-lifecycle-inventory.yaml", lambda data: data["skills"]["aota-skill-development"].update(source_path="skills/missing/SKILL.md")), "skill-source:aota-skill-development")
    run_case("unknown-owner", lambda root: mutate(root / "deploy/aota-lifecycle-inventory.yaml", lambda data: data["skills"]["aota-skill-development"].update(owning_profiles=["unknown"])), "owner:aota-skill-development")
    run_case("missing-manifest", lambda root: (root / "deploy/aota-forge-plan-files.yaml").write_text((root / "deploy/aota-forge-plan-files.yaml").read_text(encoding="utf-8").replace('pattern: "*/SKILL.md"', 'pattern: "*/SKILL.rst"', 1), encoding="utf-8"), "managed-skill-pattern")
    run_case("soul-only", lambda root: mutate(root / "deploy/aota-lifecycle-inventory.yaml", lambda data: data["skills"]["aota-skill-development"].update(source_path="profiles/task-main/SOUL.md")), "source-path:aota-skill-development")
    run_case("disabled-conflict", lambda root: mutate(root / "profiles/task-main/config.yaml", lambda data: data["skills"]["disabled"].append("aota-plugin-tool-development")), "visibility-disabled:aota-plugin-tool-development:task-main")
    run_case("missing-behavior-smoke", lambda root: mutate(root / "deploy/aota-lifecycle-inventory.yaml", lambda data: data["skills"]["aota-skill-development"].update(behavior_smoke_required=False)), "behavior-smoke:aota-skill-development")
    print("AOTA_SKILL_LIFECYCLE_ISOLATED_SMOKE_PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", action="store_true")
    args = parser.parse_args()
    if args.fixture:
        return fixture()
    errors = errors_for()
    if errors:
        print("AOTA_SKILL_LIFECYCLE_SOURCE_FAIL")
        for error in sorted(set(errors)):
            print(error)
        return 1
    for marker in (
        "AOTA_SKILL_INVENTORY_PASS", "AOTA_SKILL_SOURCE_PASS", "AOTA_SKILL_PROFILE_BINDING_PASS",
        "AOTA_SKILL_VISIBILITY_POLICY_PASS", "AOTA_SKILL_MANAGED_MANIFEST_PASS",
        "AOTA_SKILL_ACTIVATION_TARGET_PASS", "AOTA_EXISTING_SKILL_REFERENCE_PASS",
        "AOTA_SKILL_LIFECYCLE_SOURCE_PASS",
    ):
        print(marker)
    print("SKILL_PROMPT_CONTEXT_CHECK_REQUIRED")
    print("SKILL_BEHAVIOR_SMOKE_CHECK_REQUIRED")
    print("PROCESS_RECREATE_REQUIRED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
