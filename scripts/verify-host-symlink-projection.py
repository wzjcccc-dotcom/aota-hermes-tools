#!/usr/bin/env python3
"""Bounded isolated fixtures for the Host symlink projection adapter."""

from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

import _host_symlink_projection as adapter
import aota_forge_plan_package as package


ROOT = Path(__file__).resolve().parents[1]
ASSEMBLY = yaml.safe_load((ROOT / "deploy/profile-runtime-assembly.yaml").read_text(encoding="utf-8"))
PROJECTIONS = adapter.load_projection_contract(ASSEMBLY)


def make_runtime(root: Path, *, symlinks: bool = True) -> None:
    (root / "plugins/aota-tools").mkdir(parents=True)
    (root / "plugins/aota-tools/tool.py").write_text("canonical\n", encoding="utf-8")
    (root / "profiles").mkdir()
    for profile in ASSEMBLY["profiles"]:
        local = root / "profiles" / profile / "plugins" / "aota-tools"
        local.parent.mkdir(parents=True)
        if symlinks and profile in {item["profile"] for item in PROJECTIONS}:
            local.symlink_to("../../../plugins/aota-tools")
        else:
            local.mkdir()
            (local / "tool.py").write_text("canonical\n", encoding="utf-8")


def expect_invalid(label: str, mutate, reason: str | None = None) -> None:
    with tempfile.TemporaryDirectory(prefix="aota-host-projection-fail-") as temp:
        root = Path(temp) / "runtime"
        make_runtime(root)
        mutate(root)
        try:
            adapter.validate_contract_runtime(root, ASSEMBLY)
        except ValueError as exc:
            if reason and reason not in str(exc):
                raise AssertionError(f"{label}: expected {reason}, got {exc}")
            print(f"FAIL_FIXTURE_PASS {label}")
            return
        raise AssertionError(f"{label}: invalid projection was accepted")


def projection_entries(root: Path) -> list[dict[str, object]]:
    source = root / "source/plugin"
    source.mkdir(parents=True)
    (source / "tool.py").write_text("canonical\n", encoding="utf-8")
    entries: list[dict[str, object]] = [{
        "id": "plugin/tool.py", "group": "plugin", "kind": "copy", "source": source / "tool.py",
        "destination": root / "runtime/plugins/aota-tools/tool.py",
        "root": root / "runtime/plugins/aota-tools", "source_root": source,
        "relative_path": "tool.py", "file_type": "file", "managed": True,
    }]
    for item in PROJECTIONS:
        profile = item["profile"]
        entries.append({
            "id": f"profile_runtime_assembly/{profile}/plugin/tool.py", "group": "profile_runtime_assembly", "kind": "copy",
            "source": source / "tool.py",
            "destination": root / "runtime/profiles" / profile / "plugins/aota-tools/tool.py",
            "root": root / "runtime/profiles" / profile / "plugins/aota-tools",
            "source_root": source, "relative_path": "tool.py", "file_type": "file",
            "managed": True, "profile": profile,
        })
    return entries


def main() -> int:
    result: dict[str, object] = {"schema_version": 1, "pass": [], "fail": []}
    with tempfile.TemporaryDirectory(prefix="aota-host-projection-pass-") as temp:
        root = Path(temp) / "runtime"
        make_runtime(root)
        source_root = Path(temp) / "source/plugin"
        source_root.mkdir(parents=True)
        (source_root / "tool.py").write_text("canonical\n", encoding="utf-8")
        assessments = adapter.validate_contract_runtime(root, ASSEMBLY)
        assert len(assessments) == 5 and all(item["valid"] for item in assessments)
        result["pass"].append("five_declared_symlinks_valid")
        parity = adapter.classify_projection_parity(root, source_root, ASSEMBLY)
        assert all(item["parity_classification"] == "PROJECTION_CONTENT_EXACT_MATCH" for item in parity)
        result["pass"].append("exact_match")
        (root / "plugins/aota-tools/tool.py").write_text("changed\n", encoding="utf-8")
        assert all(item["parity_classification"] == "PROJECTION_CONTENT_MISMATCH" for item in adapter.classify_projection_parity(root, source_root, ASSEMBLY))
        result["pass"].append("content_mismatch")

    with tempfile.TemporaryDirectory(prefix="aota-host-projection-dedup-") as temp:
        root = Path(temp)
        entries = projection_entries(root)
        planned = adapter.apply_projection_plan(entries, {"profiles": ASSEMBLY["profiles"], "projection_contract_schema_version": 1, "plugin_projections": ASSEMBLY["plugin_projections"]}, root / "runtime")
        summary = adapter.projection_plan_summary(planned)
        assert summary["symlink_projection_count"] == 5 and summary["deduplicated_write_count"] == 5
        result["pass"].append("logical_duplicate_deduplication")
        runtime = root / "runtime"
        (runtime / "plugins/aota-tools").mkdir(parents=True)
        (runtime / "plugins/aota-tools/tool.py").write_text("old\n", encoding="utf-8")
        (runtime / "profiles").mkdir()
        for item in PROJECTIONS:
            local = runtime / item["logical_path"]
            local.parent.mkdir(parents=True)
            local.symlink_to("../../../plugins/aota-tools")
        backup = package.backup_package(planned, root / "backups", quiet=True)
        package.copy_preserving(planned[0]["source"], planned[0]["destination"])
        assert len(list((backup / "files").rglob("tool.py"))) == 1
        result["pass"].append("backup_once")
        (runtime / "plugins/aota-tools/tool.py").write_text("new\n", encoding="utf-8")
        package.rollback_package(backup, quiet=True)
        assert (runtime / "plugins/aota-tools/tool.py").read_text(encoding="utf-8") == "old\n"
        assert all((runtime / item["logical_path"]).is_symlink() for item in PROJECTIONS)
        result["pass"].extend(["rollback_once", "projection_after_deploy", "projection_after_rollback", "logical_projection_receipts"])

    def unlisted(root: Path) -> None:
        extra = root / "profiles/project-steward/plugins/aota-tools"
        shutil.rmtree(extra)
        extra.symlink_to("../../../plugins/aota-tools")

    expect_invalid("undeclared_symlink", unlisted, "undeclared")
    expect_invalid("dangling_symlink", lambda root: (root / "profiles/task-main/plugins/aota-tools").unlink() or (root / "profiles/task-main/plugins/aota-tools").symlink_to("../../../missing"), "dangling")

    def chain(root: Path) -> None:
        target = root / "plugins/aota-tools-link"
        target.symlink_to("aota-tools")
        logical = root / "profiles/task-main/plugins/aota-tools"
        logical.unlink()
        logical.symlink_to("../../../plugins/aota-tools-link")

    expect_invalid("symlink_chain", chain, "symlink_chain")

    def circular(root: Path) -> None:
        first = root / "plugins/aota-tools-link"
        second = root / "plugins/aota-tools-loop"
        first.symlink_to("aota-tools-loop")
        second.symlink_to("aota-tools-link")
        logical = root / "profiles/task-main/plugins/aota-tools"
        logical.unlink()
        logical.symlink_to("../../../plugins/aota-tools-link")

    expect_invalid("circular_symlink", circular, "circular")

    def outside(root: Path) -> None:
        logical = root / "profiles/task-main/plugins/aota-tools"
        logical.unlink()
        logical.symlink_to("/tmp")

    expect_invalid("outside_runtime", outside, "outside_runtime_root")

    def source_workspace(root: Path) -> None:
        logical = root / "profiles/task-main/plugins/aota-tools"
        logical.unlink()
        logical.symlink_to(ROOT)

    expect_invalid("source_workspace_target", source_workspace, "outside_runtime_root")

    def profile_target(root: Path) -> None:
        logical = root / "profiles/task-main/plugins/aota-tools"
        logical.unlink()
        logical.symlink_to("../../../profiles/coder/plugins/aota-tools")

    expect_invalid("profile_to_profile", profile_target, "symlink_chain")

    def parent_target(root: Path) -> None:
        logical = root / "profiles/task-main/plugins/aota-tools"
        logical.unlink()
        logical.symlink_to("../../../plugins")

    expect_invalid("parent_directory", parent_target, "target_mismatch")

    def file_target(root: Path) -> None:
        (root / "plugins/file").write_text("file\n", encoding="utf-8")
        logical = root / "profiles/task-main/plugins/aota-tools"
        logical.unlink()
        logical.symlink_to("../../../plugins/file")

    expect_invalid("file_target", file_target, "target_is_not_directory")

    def global_root_symlink(root: Path) -> None:
        real = root / "plugins/aota-tools-real"
        real.mkdir()
        (real / "tool.py").write_text("canonical\n", encoding="utf-8")
        (root / "plugins/aota-tools").rename(root / "plugins/aota-tools-old")
        (root / "plugins/aota-tools").symlink_to("aota-tools-old")

    expect_invalid("global_root_symlink", global_root_symlink, "symlink_chain")

    def runtime_root_symlink(root: Path) -> None:
        real = root.with_name("runtime-real")
        root.rename(real)
        root.symlink_to(real)

    expect_invalid("runtime_root_symlink", runtime_root_symlink, "runtime_root_is_symlink")

    def dotdot_escape(root: Path) -> None:
        outside = root.parent / "outside"
        outside.mkdir()
        logical = root / "profiles/task-main/plugins/aota-tools"
        logical.unlink()
        logical.symlink_to("../../../../outside")

    expect_invalid("dotdot_escape", dotdot_escape, "outside_runtime_root")

    duplicate = copy.deepcopy(ASSEMBLY)
    duplicate["plugin_projections"].append(copy.deepcopy(duplicate["plugin_projections"][0]))
    try:
        adapter.load_projection_contract(duplicate)
    except ValueError:
        result["fail"].append("manifest_duplicate_projection")
    else:
        raise AssertionError("duplicate manifest accepted")
    unknown = copy.deepcopy(ASSEMBLY)
    unknown["plugin_projections"][0]["profile"] = "unknown"
    try:
        adapter.load_projection_contract(unknown)
    except ValueError:
        result["fail"].append("manifest_unknown_profile")
    else:
        raise AssertionError("unknown profile accepted")
    mismatch = copy.deepcopy(ASSEMBLY)
    mismatch["plugin_projections"][0]["target_path"] = "plugins/other"
    try:
        adapter.load_projection_contract(mismatch)
    except ValueError:
        result["fail"].append("manifest_target_mismatch")
    else:
        raise AssertionError("target mismatch accepted")

    with tempfile.TemporaryDirectory(prefix="aota-host-projection-security-") as temp:
        root = Path(temp)
        outside_file = root / "outside"
        outside_file.write_text("outside\n", encoding="utf-8")
        bad_root = root / "backups"
        bad_root.symlink_to(root)
        try:
            package.backup_package([], bad_root, quiet=True)
        except ValueError:
            result["fail"].append("backup_path_symlink_escape")
        else:
            raise AssertionError("backup symlink root accepted")

    with tempfile.TemporaryDirectory(prefix="aota-collision-") as temp:
        collision_root = Path(temp)
        source_a = collision_root / "a"
        source_b = collision_root / "b"
        source_a.write_text("a\n", encoding="utf-8")
        source_b.write_text("b\n", encoding="utf-8")
        collision = [{"id": "a", "kind": "copy", "source": source_a, "destination": collision_root / "collision", "root": collision_root, "source_root": collision_root}, {"id": "b", "kind": "copy", "source": source_b, "destination": collision_root / "collision", "root": collision_root, "source_root": collision_root}]
        try:
            adapter.apply_projection_plan(collision, {"profiles": {}, "projection_contract_schema_version": 1, "plugin_projections": []}, collision_root)
        except ValueError:
            result["fail"].append("physical_target_collision")
        else:
            raise AssertionError("physical collision accepted")

    ambiguous = copy.deepcopy(ASSEMBLY)
    ambiguous["plugin_projections"][0]["target_path"] = "plugins\\aota-tools"
    try:
        adapter.load_projection_contract(ambiguous)
    except ValueError:
        result["fail"].append("unicode_case_separator_ambiguity")
    else:
        raise AssertionError("ambiguous separator accepted")
    with tempfile.TemporaryDirectory(prefix="aota-rollback-escape-") as temp:
        root = Path(temp)
        outside = root / "outside"
        outside.mkdir()
        runtime = root / "runtime"
        runtime.mkdir()
        (runtime / "profiles").symlink_to(outside, target_is_directory=True)
        try:
            package.safe_target(runtime / "profiles/task-main/plugins/aota-tools/file", runtime)
        except ValueError:
            result["fail"].append("rollback_path_symlink_escape")
        else:
            raise AssertionError("rollback symlink path accepted")
    print(json.dumps({"schema_version": 1, "pass_count": len(result["pass"]), "fail_count": len(result["fail"]), "pass": result["pass"], "fail": result["fail"]}, indent=2, sort_keys=True))
    print("HOST_SYMLINK_PROJECTION_FIXTURE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
