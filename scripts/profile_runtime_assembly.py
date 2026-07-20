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
    return sorted(errors)


def path_escapes(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return True
    return False


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
            source = ROOT / "skills" / str(skill) / "SKILL.md"
            target = profile_root / "skills" / str(skill) / "SKILL.md"
            if not target.is_file() or path_escapes(target, runtime_root) or sha256(source) != sha256(target):
                errors.append(f"runtime:{profile}:skill-parity:{skill}")
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
        for skill in spec.get("active_skills", []):
            target = profile_root / "skills" / str(skill) / "SKILL.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / "skills" / str(skill) / "SKILL.md", target)
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
        assert any("skill-parity:aota-profile-task-orchestration" in error for error in pre_activation_errors(broken_skill))
        print("PRE_ACTIVATION_BROKEN_SKILL_LINK_FAIL")

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
