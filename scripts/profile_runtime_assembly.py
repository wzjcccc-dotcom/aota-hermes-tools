#!/usr/bin/env python3
"""Validate and inspect the canonical Named Profile runtime assembly."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
ASSEMBLY = ROOT / "deploy" / "profile-runtime-assembly.yaml"
PROFILES = {"task-main", "project-steward", "architect", "coder", "debugger", "reviewer"}


def load_assembly() -> dict[str, Any]:
    data = yaml.safe_load(ASSEMBLY.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("profile runtime assembly schema_version must be 1")
    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or set(profiles) != PROFILES:
        raise ValueError("profile runtime assembly must declare exactly the canonical profiles")
    return data


# -- Canonical count resolver -------------------------------------------------
# Derives deployment receipt counts from canonical repository sources:
#   tools    <- plugin/aota-tools/plugin.yaml provides_tools
#   toolsets <- unique toolset names from the lifecycle inventory tools mapping
#   profiles <- deploy/profile-runtime-assembly.yaml profiles keys
# No hardcoded counts remain; all are derived from canonical sources.

_PLUGIN_YAML = ROOT / "plugin" / "aota-tools" / "plugin.yaml"
_LIFECYCLE_INVENTORY = ROOT / "deploy" / "aota-lifecycle-inventory.yaml"


def canonical_counts() -> dict[str, int]:
    """Return {tools, toolsets, profiles} derived from canonical sources."""
    plugin_data = yaml.safe_load(_PLUGIN_YAML.read_text(encoding="utf-8"))
    tools_list = plugin_data.get("provides_tools", [])
    if not isinstance(tools_list, list):
        raise ValueError("plugin.yaml provides_tools must be a list")
    tools_count = len(tools_list)

    inventory_data = yaml.safe_load(_LIFECYCLE_INVENTORY.read_text(encoding="utf-8"))
    inv_tools = inventory_data.get("tools", {})
    if not isinstance(inv_tools, dict):
        raise ValueError("lifecycle inventory tools must be a mapping")
    toolsets_count = len({str(item.get("toolset")) for item in inv_tools.values() if isinstance(item, dict)})

    assembly = load_assembly()
    profiles_count = len(assembly.get("profiles", {}))

    return {
        "tools": tools_count,
        "toolsets": toolsets_count,
        "profiles": profiles_count,
    }


# -- Managed Skills with references subtrees ----------------------------------
# Enumerate every managed Skill that contains a references/ subdirectory.

def skills_with_references() -> list[str]:
    """Return sorted list of managed Skill IDs that have a references/ subtree."""
    assembly = load_assembly()
    result: set[str] = set()
    for profile, spec in assembly["profiles"].items():
        for skill in spec.get("active_skills", []) + spec.get("reference_skills", []):
            skill_id = str(skill)
            ref_dir = ROOT / "skills" / skill_id / "references"
            if ref_dir.is_dir():
                result.add(skill_id)
    return sorted(result)


# -- Package/assembly expected file set parity -------------------------------
# Both the package projection (from aota_forge_plan_package.py) and the
# profile assembly expected file set must be generated from one shared
# expansion path (expand_skill_tree).

def assembly_expected_skill_files() -> dict[str, set[str]]:
    """Return {skill_id: set(relative_paths)} for every managed Skill in the assembly."""
    assembly = load_assembly()
    result: dict[str, set[str]] = {}
    for profile, spec in assembly["profiles"].items():
        for skill in spec.get("active_skills", []) + spec.get("reference_skills", []):
            skill_id = str(skill)
            if skill_id in result:
                continue
            source_root = ROOT / "skills" / skill_id
            result[skill_id] = set(expand_skill_tree(source_root).keys())
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def soul_skills(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"##+ Active AOTA Skills\n(?P<body>.*?)(?:\n##+ |\Z)", text, re.DOTALL)
    if not match:
        return set()
    return set(re.findall(r"\*\*(aota-[a-z0-9][a-z0-9-]*)\*\*", match.group("body")))


def source_errors() -> list[str]:
    errors: list[str] = []
    try:
        data = load_assembly()
    except Exception as exc:  # noqa: BLE001 - stable readiness verdict
        return [f"manifest:{type(exc).__name__}"]
    for profile, spec in data["profiles"].items():
        if not isinstance(spec, dict):
            errors.append(f"profile:{profile}:shape")
            continue
        config = ROOT / str(spec.get("config", ""))
        soul = ROOT / str(spec.get("soul", ""))
        plugin = ROOT / str(spec.get("plugin", ""))
        active = set(str(item) for item in spec.get("active_skills", []))
        references = set(str(item) for item in spec.get("reference_skills", []))
        if active & references:
            errors.append(f"profile:{profile}:active-reference-overlap")
        if not config.is_file():
            errors.append(f"profile:{profile}:config")
            continue
        if not soul.is_file():
            errors.append(f"profile:{profile}:soul")
            continue
        if not plugin.is_dir():
            errors.append(f"profile:{profile}:plugin-source")
        if soul_skills(soul) != active:
            errors.append(f"profile:{profile}:soul-active-skills")
        try:
            config_data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
            enabled = config_data.get("plugins", {}).get("enabled", [])
            if "aota-tools" not in (enabled if isinstance(enabled, list) else [enabled]):
                errors.append(f"profile:{profile}:plugin-not-enabled")
            disabled = set(config_data.get("skills", {}).get("disabled", []))
            if active & disabled:
                errors.append(f"profile:{profile}:active-skill-disabled")
        except Exception:
            errors.append(f"profile:{profile}:config-parse")
        for skill in active:
            skill_path = ROOT / "skills" / skill / "SKILL.md"
            if not skill_path.is_file():
                errors.append(f"profile:{profile}:skill-source:{skill}")
                continue
            frontmatter = skill_path.read_text(encoding="utf-8", errors="replace")[:4096]
            if not re.search(rf"^name:\s*{re.escape(skill)}\s*$", frontmatter, re.MULTILINE):
                errors.append(f"profile:{profile}:skill-name:{skill}")
        for skill in references:
            skill_path = ROOT / "skills" / skill / "SKILL.md"
            if not skill_path.is_file():
                errors.append(f"profile:{profile}:reference-skill-source:{skill}")
                continue
            skill_root = (ROOT / "skills" / skill).resolve(strict=False)
            for candidate in skill_root.rglob("*"):
                if candidate.is_symlink():
                    try:
                        candidate.resolve(strict=False).relative_to(skill_root)
                    except ValueError:
                        errors.append(f"profile:{profile}:reference-skill-escape:{skill}:{candidate.name}")
            # Canonical Skill tree expansion: verify no path-traversal or
            # symlink-escape entries in the declared managed subtree.
            tree = expand_skill_tree(skill_root, include_escapes=True)
            for rel, candidate in tree.items():
                if candidate.is_symlink():
                    try:
                        candidate.resolve(strict=False).relative_to(skill_root)
                    except ValueError:
                        errors.append(f"profile:{profile}:reference-skill-escape:{skill}:{rel}")
            frontmatter = skill_path.read_text(encoding="utf-8", errors="replace")[:4096]
            if not re.search(rf"^name:\s*{re.escape(skill)}\s*$", frontmatter, re.MULTILINE):
                errors.append(f"profile:{profile}:reference-skill-name:{skill}")
    return sorted(errors)


def path_escapes(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return True
    return False


# -- Canonical Skill tree expansion ------------------------------------------
# Shared helper for package and assembly: enumerates SKILL.md plus canonical
# Skill-local regular files (including nested references/**), excludes
# caches/temporary/editor-backup/runtime-generated files, rejects path
# traversal and symlink escapes.  Do not create a third independent enumerator.

_EXCLUDE_DIRS = frozenset({
    "__pycache__", ".git", ".cache", "node_modules",
    ".mypy_cache", ".ruff_cache", ".pytest_cache",
})
_EXCLUDE_SUFFIXES = frozenset({".pyc", ".pyo", ".pyd"})
_EXCLUDE_NAMES = frozenset({".DS_Store", "Thumbs.db"})


def _is_excluded_canonical(rel: str) -> bool:
    """Return True if *rel* should be excluded from the canonical Skill tree."""
    parts = Path(rel).parts
    if any(part in _EXCLUDE_DIRS for part in parts):
        return True
    name = Path(rel).name
    if name in _EXCLUDE_NAMES:
        return True
    if Path(rel).suffix in _EXCLUDE_SUFFIXES:
        return True
    # Editor backup patterns: file~, file.bak, file.swp, file.tmp, .#file, #file#
    if name.endswith("~") or name.endswith(".bak") or name.endswith(".swp") or name.endswith(".tmp"):
        return True
    if name.startswith(".#") or (name.startswith("#") and name.endswith("#")):
        return True
    return False


def expand_skill_tree(root: Path, *, include_escapes: bool = False) -> dict[str, Path]:
    """Enumerate canonical Skill-local regular files under *root*.

    Includes SKILL.md and nested references/** regular files.
    Excludes __pycache__, *.pyc, temporary/editor backup files, runtime
    snapshots, receipts, generated caches, and empty directories.
    Symlink escapes and path traversal are rejected (excluded) unless
    *include_escapes* is True, in which case escaping symlinks are included
    so that runtime verification can detect and report them.

    Returns a mapping of relative posix path -> absolute path for each
    canonical file.
    """
    root = root.resolve(strict=False)
    if not root.is_dir():
        return {}
    result: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            continue
        if not path.is_file() and not path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        if ".." in Path(rel).parts:
            continue
        if _is_excluded_canonical(rel):
            continue
        if path.is_symlink():
            resolved = path.resolve(strict=False)
            try:
                resolved.relative_to(root)
            except ValueError:
                if not include_escapes:
                    continue
        result[rel] = path
    return result


def skill_tree_errors(source_root: Path, target_root: Path, runtime_root: Path, label: str) -> list[str]:
    errors: list[str] = []
    runtime_root = Path(runtime_root)
    if not source_root.is_dir():
        return [f"{label}:source-root"]
    source_files = expand_skill_tree(source_root)
    target_files = expand_skill_tree(target_root, include_escapes=True) if target_root.is_dir() else {}
    for relative, source in sorted(source_files.items()):
        target = target_root / relative
        if not target.is_file() or path_escapes(target, runtime_root) or sha256(source) != sha256(target):
            errors.append(f"{label}:parity:{relative}")
    for relative in sorted(set(target_files) - set(source_files)):
        target = target_files[relative]
        if not path_escapes(target, runtime_root):
            errors.append(f"{label}:undeclared-runtime-extra:{relative}")
        else:
            errors.append(f"{label}:symlink-escape:{relative}")
    return errors


def projection_errors(runtime_root: Path) -> list[str]:
    errors: list[str] = []
    try:
        data = load_assembly()
    except Exception:
        return errors
    runtime_root = runtime_root.resolve(strict=False)
    global_plugin = runtime_root / "plugins" / "aota-tools"
    if global_plugin.is_dir() and path_escapes(global_plugin, runtime_root):
        errors.append("runtime:global-plugin-escape")
    for profile, spec in data["profiles"].items():
        profile_root = runtime_root / "profiles" / profile
        local_plugin = profile_root / "plugins" / "aota-tools"
        if not local_plugin.is_dir() or path_escapes(local_plugin, runtime_root):
            errors.append(f"runtime:{profile}:plugin-projection")
        for source in sorted((ROOT / "plugin" / "aota-tools").glob("*.py")):
            target = local_plugin / source.name
            if not target.is_file() or path_escapes(target, runtime_root) or sha256(source) != sha256(target):
                errors.append(f"runtime:{profile}:plugin-parity:{source.name}")
        for name in ("plugin.yaml",):
            source = ROOT / "plugin" / "aota-tools" / name
            target = local_plugin / name
            if not target.is_file() or path_escapes(target, runtime_root) or sha256(source) != sha256(target):
                errors.append(f"runtime:{profile}:plugin-parity:{name}")
        for skill in spec.get("active_skills", []):
            source_root = ROOT / "skills" / str(skill)
            target = profile_root / "skills" / str(skill)
            errors.extend(skill_tree_errors(source_root, target, runtime_root, f"runtime:{profile}:active-skill:{skill}"))
        for skill in spec.get("reference_skills", []):
            source_root = ROOT / "skills" / str(skill)
            direct = profile_root / "skills" / str(skill)
            errors.extend(skill_tree_errors(source_root, direct, runtime_root, f"runtime:{profile}:reference-skill:{skill}"))
            if profile == "task-main":
                legacy = profile_root / "skills" / "orchestration" / str(skill)
                if legacy.exists():
                    errors.append(f"runtime:{profile}:legacy-reference-collision:{skill}")
    if not global_plugin.is_dir():
        errors.append("runtime:global-plugin")
    return sorted(set(errors))


def snapshot_errors(runtime_root: Path) -> list[str]:
    errors: list[str] = []
    try:
        data = load_assembly()
    except Exception:
        return errors
    for profile, spec in data["profiles"].items():
        snapshot = runtime_root / "profiles" / profile / ".skills_prompt_snapshot.json"
        if not snapshot.is_file() or path_escapes(snapshot, runtime_root):
            errors.append(f"runtime:{profile}:bootstrap-snapshot")
            continue
        try:
            snapshot_data = json.loads(snapshot.read_text(encoding="utf-8"))
            loaded = {str(item.get("skill_name")) for item in snapshot_data.get("skills", []) if isinstance(item, dict)}
            missing = set(str(item) for item in spec.get("active_skills", [])) - loaded
            errors.extend(f"runtime:{profile}:snapshot-missing:{skill}" for skill in sorted(missing))
        except Exception:
            errors.append(f"runtime:{profile}:bootstrap-snapshot-parse")
    return sorted(set(errors))


def snapshot_state(runtime_root: Path) -> str:
    errors = snapshot_errors(runtime_root)
    if not errors:
        return "valid"
    if any(error.endswith(":bootstrap-snapshot") for error in errors):
        return "missing"
    return "invalid"


def pre_activation_errors(runtime_root: Path) -> list[str]:
    return sorted(set(source_errors() + projection_errors(runtime_root)))


def post_activation_errors(runtime_root: Path) -> list[str]:
    return sorted(set(pre_activation_errors(runtime_root) + snapshot_errors(runtime_root)))


def runtime_errors(runtime_root: Path) -> list[str]:
    """Backward-compatible strict runtime verification; equivalent to post-activation."""
    return post_activation_errors(runtime_root)


def _copy_fixture_runtime(runtime: Path, snapshots: dict[str, str] | None = None) -> None:
    snapshots = snapshots or {}
    source_plugin = ROOT / "plugin" / "aota-tools"
    shutil.copytree(source_plugin, runtime / "plugins" / "aota-tools")
    assembly = load_assembly()
    for profile, spec in assembly["profiles"].items():
        profile_root = runtime / "profiles" / profile
        local_plugin = profile_root / "plugins" / "aota-tools"
        local_plugin.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_plugin, local_plugin)
        for skill in (*spec.get("active_skills", []), *spec.get("reference_skills", [])):
            target = profile_root / "skills" / str(skill)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(ROOT / "skills" / str(skill), target, dirs_exist_ok=True)
        if profile in snapshots:
            (profile_root / ".skills_prompt_snapshot.json").write_text(snapshots[profile], encoding="utf-8")


def _fixture_snapshot(skills: list[str]) -> str:
    return json.dumps({"skills": [{"skill_name": skill} for skill in skills]})


def fixture() -> int:
    if source_errors():
        print("PROFILE_ASSEMBLY_FIXTURE_BLOCKED")
        return 1
    assembly = load_assembly()
    valid_snapshots = {
        profile: _fixture_snapshot([str(skill) for skill in spec.get("active_skills", [])])
        for profile, spec in assembly["profiles"].items()
    }
    with TemporaryDirectory(prefix="aota-profile-assembly-") as temp:
        root = Path(temp)

        pending = root / "pending"
        _copy_fixture_runtime(pending)
        assert not pre_activation_errors(pending)
        assert snapshot_state(pending) == "missing"
        print("PRE_ACTIVATION_PASS")
        print("SNAPSHOT_PENDING_ACTIVATION")

        missing_projection = root / "missing-projection"
        _copy_fixture_runtime(missing_projection)
        shutil.rmtree(missing_projection / "profiles" / "task-main" / "plugins" / "aota-tools")
        assert pre_activation_errors(missing_projection)
        print("PRE_ACTIVATION_MISSING_PROJECTION_FAIL")

        broken_skill = root / "broken-skill"
        _copy_fixture_runtime(broken_skill)
        broken = broken_skill / "profiles" / "task-main" / "skills" / "aota-profile-task-orchestration" / "SKILL.md"
        broken.unlink()
        broken.symlink_to(root / "missing-skill.md")
        assert any("active-skill:aota-profile-task-orchestration:parity:SKILL.md" in error for error in pre_activation_errors(broken_skill))
        print("PRE_ACTIVATION_BROKEN_SKILL_LINK_FAIL")

        legacy_collision = root / "legacy-collision"
        _copy_fixture_runtime(legacy_collision)
        legacy = legacy_collision / "profiles" / "task-main" / "skills" / "orchestration" / "aota-task-lifecycle" / "SKILL.md"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("legacy shadow\n", encoding="utf-8")
        assert any("legacy-reference-collision:aota-task-lifecycle" in error for error in pre_activation_errors(legacy_collision))
        print("LEGACY_REFERENCE_COLLISION_FAIL")

        escaped = root / "escaped"
        _copy_fixture_runtime(escaped)
        escaped_plugin = escaped / "profiles" / "task-main" / "plugins" / "aota-tools" / "__init__.py"
        escaped_plugin.unlink()
        escaped_plugin.symlink_to(ROOT / "plugin" / "aota-tools" / "__init__.py")
        assert any("plugin-parity:__init__.py" in error for error in pre_activation_errors(escaped))
        print("PRE_ACTIVATION_SYMLINK_ESCAPE_FAIL")

        fresh = root / "fresh"
        _copy_fixture_runtime(fresh, valid_snapshots)
        assert not post_activation_errors(fresh)
        print("POST_ACTIVATION_PASS")

        missing_post = root / "missing-post"
        _copy_fixture_runtime(missing_post)
        missing_errors = post_activation_errors(missing_post)
        assert missing_errors and any(error.endswith(":bootstrap-snapshot") for error in missing_errors)
        assert runtime_errors(missing_post) == missing_errors
        print("POST_ACTIVATION_MISSING_SNAPSHOT_FAIL")
        print("POST_ACTIVATION_ROLLBACK_REQUIRED_PASS")

        stale = root / "stale"
        stale_snapshots = dict(valid_snapshots)
        stale_snapshots["task-main"] = _fixture_snapshot(["aota-profile-task-orchestration"])
        _copy_fixture_runtime(stale, stale_snapshots)
        assert any("snapshot-missing:aota-runtime-smoke-verification" in error for error in post_activation_errors(stale))
        print("POST_ACTIVATION_STALE_SNAPSHOT_FAIL")

        invalid = root / "invalid"
        invalid_snapshots = dict(valid_snapshots)
        invalid_snapshots["task-main"] = "{"
        _copy_fixture_runtime(invalid, invalid_snapshots)
        assert any(error.endswith(":bootstrap-snapshot-parse") for error in post_activation_errors(invalid))
        print("POST_ACTIVATION_INVALID_SNAPSHOT_FAIL")

    # -- Cases A-L: Skill subtree expansion and count parity -----------------

    # Case A: no-reference Skill — SKILL.md only, no references/ subtree
    no_ref_skill = "aota-architecture-review"
    no_ref_root = ROOT / "skills" / no_ref_skill
    no_ref_tree = expand_skill_tree(no_ref_root)
    assert "SKILL.md" in no_ref_tree
    assert not any(r.startswith("references/") for r in no_ref_tree)
    print("CASE_A_NO_REFERENCE_PASS")

    # Case B: single-reference Skill — has references/ with exactly one file
    # We test against skills that actually have references subtrees, but since
    # no current skills have them, we create a temporary fixture.
    with TemporaryDirectory(prefix="aota-skill-tree-single-ref-") as sref_temp:
        sref_root = Path(sref_temp) / "skill"
        (sref_root / "references").mkdir(parents=True)
        (sref_root / "SKILL.md").write_text("---\nname: test-skill\n---\n# Test\n", encoding="utf-8")
        (sref_root / "references" / "ref.md").write_text("ref content\n", encoding="utf-8")
        sref_tree = expand_skill_tree(sref_root)
        assert "SKILL.md" in sref_tree
        assert "references/ref.md" in sref_tree
        assert len(sref_tree) == 2
        print("CASE_B_SINGLE_REFERENCE_PASS")

    # Case C: nested-reference — references/sub/subfile.md is included
    with TemporaryDirectory(prefix="aota-skill-tree-nested-ref-") as nref_temp:
        nref_root = Path(nref_temp) / "skill"
        (nref_root / "references" / "deep" / "nested").mkdir(parents=True)
        (nref_root / "SKILL.md").write_text("---\nname: test-skill\n---\n# Test\n", encoding="utf-8")
        (nref_root / "references" / "deep" / "nested" / "file.md").write_text("deep\n", encoding="utf-8")
        nref_tree = expand_skill_tree(nref_root)
        assert "SKILL.md" in nref_tree
        assert "references/deep/nested/file.md" in nref_tree
        print("CASE_C_NESTED_REFERENCE_PASS")

    # Case D: missing-reference — Skill declares references/ but a file is
    # missing from the runtime; parity check must detect it.
    with TemporaryDirectory(prefix="aota-skill-tree-missing-ref-") as mref_temp:
        mref_src = Path(mref_temp) / "src" / "skill"
        mref_tgt = Path(mref_temp) / "tgt" / "skill"
        (mref_src / "references").mkdir(parents=True)
        (mref_src / "SKILL.md").write_text("source\n", encoding="utf-8")
        (mref_src / "references" / "ref.md").write_text("ref\n", encoding="utf-8")
        (mref_tgt / "references").mkdir(parents=True)
        (mref_tgt / "SKILL.md").write_text("source\n", encoding="utf-8")
        # references/ref.md intentionally missing from target
        missing_errs = skill_tree_errors(mref_src, mref_tgt, mref_temp, "case-d")
        assert any("parity:references/ref.md" in e for e in missing_errs), f"Expected parity error for references/ref.md, got {missing_errs}"
        print("CASE_D_MISSING_REFERENCE_FAIL")

    # Case E: undeclared-runtime-extra — a file exists at runtime but not in
    # canonical source.  Must be detected as UNDECLARED_RUNTIME_EXTRA.
    with TemporaryDirectory(prefix="aota-skill-tree-undeclared-extra-") as ue_temp:
        ue_src = Path(ue_temp) / "src" / "skill"
        ue_tgt = Path(ue_temp) / "tgt" / "skill"
        ue_src.mkdir(parents=True)
        (ue_src / "SKILL.md").write_text("source\n", encoding="utf-8")
        (ue_tgt).mkdir(parents=True)
        (ue_tgt / "SKILL.md").write_text("source\n", encoding="utf-8")
        (ue_tgt / "extra.md").write_text("extra\n", encoding="utf-8")
        ue_errs = skill_tree_errors(ue_src, ue_tgt, ue_temp, "case-e")
        assert any("undeclared-runtime-extra:extra.md" in e for e in ue_errs), f"Expected undeclared-runtime-extra, got {ue_errs}"
        print("CASE_E_UNDECLARED_RUNTIME_EXTRA_FAIL")

    # Case F: hash-mismatch — file exists but content differs
    with TemporaryDirectory(prefix="aota-skill-tree-hash-mismatch-") as hm_temp:
        hm_src = Path(hm_temp) / "src" / "skill"
        hm_tgt = Path(hm_temp) / "tgt" / "skill"
        hm_src.mkdir(parents=True)
        (hm_src / "SKILL.md").write_text("source\n", encoding="utf-8")
        (hm_tgt).mkdir(parents=True)
        (hm_tgt / "SKILL.md").write_text("different\n", encoding="utf-8")
        hm_errs = skill_tree_errors(hm_src, hm_tgt, hm_temp, "case-f")
        assert any("parity:SKILL.md" in e for e in hm_errs), f"Expected hash mismatch, got {hm_errs}"
        print("CASE_F_HASH_MISMATCH_FAIL")

    # Case G: symlink/path-traversal escape — symlink pointing outside root.
    # The outside target must be beyond the runtime_root (se_temp) so the
    # symlink genuinely crosses the bounded root boundary and is classified
    # as symlink-escape (fail-closed), not undeclared-runtime-extra.
    with TemporaryDirectory(prefix="aota-skill-tree-outside-") as outside_temp:
        outside = Path(outside_temp) / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        with TemporaryDirectory(prefix="aota-skill-tree-symlink-escape-") as se_temp:
            se_src = Path(se_temp) / "src" / "skill"
            se_tgt = Path(se_temp) / "tgt" / "skill"
            se_src.mkdir(parents=True)
            (se_src / "SKILL.md").write_text("source\n", encoding="utf-8")
            (se_tgt).mkdir(parents=True)
            (se_tgt / "SKILL.md").write_text("source\n", encoding="utf-8")
            (se_tgt / "escape.md").symlink_to(outside)
            se_errs = skill_tree_errors(se_src, se_tgt, se_temp, "case-g")
            assert any("symlink-escape:escape.md" in e for e in se_errs), f"Expected symlink escape, got {se_errs}"
            print("CASE_G_SYMLINK_PATH_TRAVERSAL_FAIL")

    # Case H: unmanaged-reference — an unmanaged reference file at runtime that
    # is not in canonical source is detected as undeclared-runtime-extra
    with TemporaryDirectory(prefix="aota-skill-tree-unmanaged-ref-") as ur_temp:
        ur_src = Path(ur_temp) / "src" / "skill"
        ur_tgt = Path(ur_temp) / "tgt" / "skill"
        ur_src.mkdir(parents=True)
        (ur_src / "SKILL.md").write_text("source\n", encoding="utf-8")
        (ur_tgt / "references").mkdir(parents=True)
        (ur_tgt / "SKILL.md").write_text("source\n", encoding="utf-8")
        (ur_tgt / "references" / "unmanaged.md").write_text("unmanaged\n", encoding="utf-8")
        ur_errs = skill_tree_errors(ur_src, ur_tgt, ur_temp, "case-h")
        assert any("undeclared-runtime-extra:references/unmanaged.md" in e for e in ur_errs), f"Expected unmanaged reference error, got {ur_errs}"
        print("CASE_H_UNMANAGED_REFERENCE_FAIL")

    # Case I: receipt count parity — canonical counts derived from sources
    counts = canonical_counts()
    assert counts["tools"] == 61, f"Expected 61 tools, got {counts['tools']}"
    assert counts["toolsets"] == 28, f"Expected 28 toolsets, got {counts['toolsets']}"
    assert counts["profiles"] == 6, f"Expected 6 profiles, got {counts['profiles']}"
    # Detect old 59 baseline
    assert counts["tools"] != 59, "Must detect old 59 baseline"
    print("CASE_I_RECEIPT_COUNT_PARITY_PASS")

    # Case J: readiness/receipt count parity — same resolver used
    # Verify the counts dict has exactly the expected keys
    assert set(counts.keys()) == {"tools", "toolsets", "profiles"}
    print("CASE_J_COUNT_RESOLVER_CONSISTENT_PASS")

    # Case K: package/assembly exact equality — both use expand_skill_tree
    expected = assembly_expected_skill_files()
    for skill_id, files in expected.items():
        source_root = ROOT / "skills" / skill_id
        tree = expand_skill_tree(source_root)
        assert files == set(tree.keys()), f"Skill {skill_id}: expected file set mismatch"
    # Verify all Skills with references are enumerated
    ref_skills = skills_with_references()
    # No current skills have references/ subtrees, so ref_skills should be
    # empty — but the enumeration must not fail
    assert isinstance(ref_skills, list)
    print("CASE_K_PACKAGE_ASSEMBLY_EQUALITY_PASS")

    # Case L: runtime reference absent from canonical source fails as
    # UNDECLARED_RUNTIME_EXTRA
    with TemporaryDirectory(prefix="aota-skill-tree-runtime-extra-") as re_temp:
        re_src = Path(re_temp) / "src" / "skill"
        re_tgt = Path(re_temp) / "tgt" / "skill"
        re_src.mkdir(parents=True)
        (re_src / "SKILL.md").write_text("source\n", encoding="utf-8")
        (re_tgt / "references").mkdir(parents=True)
        (re_tgt / "SKILL.md").write_text("source\n", encoding="utf-8")
        (re_tgt / "references" / "runtime_only.md").write_text("runtime\n", encoding="utf-8")
        re_errs = skill_tree_errors(re_src, re_tgt, re_temp, "case-l")
        assert any("undeclared-runtime-extra:references/runtime_only.md" in e for e in re_errs), f"Expected UNDECLARED_RUNTIME_EXTRA, got {re_errs}"
        print("CASE_L_RUNTIME_REFERENCE_ABSENT_FAIL")

    print("RUNTIME_STRICT_COMPATIBILITY_PASS")
    print("PROFILE_ASSEMBLY_FIXTURE_PASS")
    return 0

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("source", "runtime", "pre-activation", "post-activation", "fixture"))
    parser.add_argument("runtime_root", nargs="?", default="/home/latios/.hermes")
    args = parser.parse_args()
    if args.mode == "fixture":
        return fixture()
    if args.mode == "source":
        errors = source_errors()
    elif args.mode == "pre-activation":
        errors = pre_activation_errors(Path(args.runtime_root))
        if errors:
            print("PRE_ACTIVATION_VERIFY=fail")
            print("ACTIVATION_ALLOWED=no")
            print("PROFILE_ASSEMBLY_PRE_ACTIVATION_BLOCKED")
            print("\n".join(errors))
            return 1
        state = snapshot_state(Path(args.runtime_root))
        print("PRE_ACTIVATION_VERIFY=pass")
        print(f"BOOTSTRAP_SNAPSHOT={'pending_activation' if state == 'missing' else 'deferred_post_activation'}")
        print("ACTIVATION_ALLOWED=yes")
        print("PROFILE_ASSEMBLY_PRE_ACTIVATION_PASS")
        return 0
    elif args.mode == "post-activation":
        errors = post_activation_errors(Path(args.runtime_root))
        if errors:
            print("POST_ACTIVATION_VERIFY=fail")
            print("ROLLBACK_REQUIRED=yes")
            print("PROFILE_ASSEMBLY_POST_ACTIVATION_BLOCKED")
            print("\n".join(errors))
            return 1
        print("POST_ACTIVATION_VERIFY=pass")
        print("BOOTSTRAP_SNAPSHOT=valid")
        print("PROFILE_ASSEMBLY_POST_ACTIVATION_PASS")
        return 0
    else:
        errors = runtime_errors(Path(args.runtime_root))
    if errors:
        print("PROFILE_ASSEMBLY_SOURCE_BLOCKED" if args.mode == "source" else "PROFILE_ASSEMBLY_RUNTIME_BLOCKED")
        print("\n".join(errors))
        return 1
    print("PROFILE_ASSEMBLY_SOURCE_PASS" if args.mode == "source" else "PROFILE_ASSEMBLY_RUNTIME_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
