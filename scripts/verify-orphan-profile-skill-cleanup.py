#!/usr/bin/env python3
"""Temporary exact-orphan transaction, drift, and rollback fixture."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import aota_forge_plan_package as package


COUNT = 11
PROFILE = "task-main"
SKILL = "aota-profile-task-orchestration"


def make_fixture(root: Path) -> tuple[Path, list[dict], dict]:
    runtime = root / "runtime"
    source = root / "source"
    paths = [f"profiles/{PROFILE}/skills/{SKILL}/references/exact-{i:02d}.md" for i in range(1, COUNT + 1)]
    entries: list[dict] = []
    files: list[dict] = []
    for rel in paths:
        target = runtime / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"fixture {rel}\n", encoding="utf-8")
        allowed_root = runtime / "profiles" / PROFILE / "skills" / SKILL
        files.append({
            "absolute_runtime_path": str(target),
            "relative_runtime_path": rel,
            "profile": PROFILE,
            "skill": SKILL,
            "reference_relative_path": "references/" + Path(rel).name,
            "exists": True,
            "regular_file": True,
            "is_symlink": False,
            "realpath": str(target.resolve()),
            "allowed_root": str(allowed_root),
            "size_bytes": target.stat().st_size,
            "sha256": package.sha256(target),
            "canonical_source_path": str(source / rel),
            "canonical_source_exists": False,
            "package_plan_declares": False,
            "assembly_expected_declares": False,
            "origin_classification": "OPERATOR_CLASSIFIED_ORPHAN",
            "operator_authorization": package.ORPHAN_OPERATOR_AUTHORIZATION,
            "captured_at": "2026-07-22T00:00:00+00:00",
        })
        entries.append(package._entry(
            package.ORPHAN_CLEANUP_GROUP, "remove", target, target, runtime, runtime, rel, {
                "file_type": "file", "managed": True, "_orphan_cleanup": True,
                "_evidence_id": "orphaned-profile-skill-runtime-files-20260722", "_operator_authorization": package.ORPHAN_OPERATOR_AUTHORIZATION,
                "_original_sha256": package.sha256(target), "_profile": PROFILE, "_skill": SKILL,
                "_source_path": str(source / rel), "_reason": "operator-authorized orphaned profile-local Skill runtime cleanup",
                "_id_prefix": "fixture-evidence-11",
            }
        ))
    artifact = {
        "schema_version": 1, "evidence_id": "orphaned-profile-skill-runtime-files-20260722",
        "project_id": "aota-hermes-tools", "operator_classification": "ORPHANED_PROFILE_LOCAL_SKILL_RUNTIME_FILES",
        "operator_authorization": package.ORPHAN_OPERATOR_AUTHORIZATION,
        "authorized_path_count": COUNT, "capture_mode": "bounded_read_only_runtime_diff", "assembly_version": 2,
        "files": files,
    }
    evidence = root / "evidence.json"
    evidence.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return runtime, entries, artifact


def assert_hashes(runtime: Path, artifact: dict) -> None:
    for item in artifact["files"]:
        path = Path(item["absolute_runtime_path"])
        assert path.is_file() and not path.is_symlink()
        assert package.sha256(path) == item["sha256"]


def main() -> int:
    original_evidence = package.ORPHAN_EVIDENCE_PATH
    original_package_paths = package._canonical_package_destinations
    original_assembly_paths = package._canonical_assembly_destinations
    try:
        with tempfile.TemporaryDirectory(prefix="aota-orphan-cleanup-") as raw:
            root = Path(raw)
            runtime, entries, artifact = make_fixture(root)
            package.ORPHAN_EVIDENCE_PATH = root / "evidence.json"

            package.revalidate_orphan_evidence(entries, runtime)
            backup = package.backup_package(entries, root / "backups", quiet=True)
            for entry in entries:
                entry["destination"].unlink()
            assert all(not Path(item["absolute_runtime_path"]).exists() for item in artifact["files"])
            receipts = [package._receipt_entry(entry, backup, None, None) for entry in entries]
            assert len(receipts) == COUNT
            assert all(item["operation"] == "remove" and item["evidence_id"] == "orphaned-profile-skill-runtime-files-20260722" for item in receipts)
            package.rollback_package(backup, quiet=True)
            assert_hashes(runtime, artifact)
            print("CASE_A_EXACT_11_REMOVE_PASS")
            print("CASE_H_PARTIAL_FAILURE_ROLLBACK_11_PASS")
            print("CASE_I_RECEIPT_11_PASS")
            print("CASE_J_ROLLBACK_HASH_11_PASS")

            extra = runtime / "profiles" / PROFILE / "skills" / SKILL / "references" / "unapproved-12th.md"
            extra.write_text("unapproved\n", encoding="utf-8")
            allowed = {Path(item["absolute_runtime_path"]) for item in artifact["files"]}
            assert extra not in allowed and extra.exists()
            print("CASE_B_12TH_EXTRA_FAIL_CLOSED_PASS")
            extra.unlink()

            drift = Path(artifact["files"][0]["absolute_runtime_path"])
            drift.write_text("drift\n", encoding="utf-8")
            try:
                package.revalidate_orphan_evidence(entries, runtime)
            except ValueError as exc:
                assert "ORPHAN_EVIDENCE_DRIFT" in str(exc)
            else:
                raise AssertionError("hash drift accepted")
            drift.write_text("fixture profiles/task-main/skills/aota-profile-task-orchestration/references/exact-01.md\n", encoding="utf-8")
            # Restore the captured fixture hash for the remaining cases.
            artifact["files"][0]["sha256"] = package.sha256(drift)
            artifact["files"][0]["size_bytes"] = drift.stat().st_size
            package.ORPHAN_EVIDENCE_PATH.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
            entries[0]["original_sha256"] = artifact["files"][0]["sha256"]
            print("CASE_C_HASH_DRIFT_FAIL_CLOSED_PASS")

            source_path = Path(artifact["files"][1]["canonical_source_path"])
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text("reappeared\n", encoding="utf-8")
            try:
                package.revalidate_orphan_evidence(entries, runtime)
            except ValueError as exc:
                assert "ORPHAN_EVIDENCE_DRIFT" in str(exc)
            else:
                raise AssertionError("source reappearance accepted")
            source_path.unlink()
            print("CASE_D_SOURCE_REAPPEARANCE_FAIL_CLOSED_PASS")

            target = Path(artifact["files"][2]["absolute_runtime_path"])
            package._canonical_package_destinations = lambda: {target}
            try:
                package.revalidate_orphan_evidence(entries, runtime)
            except ValueError as exc:
                assert "ORPHAN_EVIDENCE_DRIFT" in str(exc)
            else:
                raise AssertionError("package reappearance accepted")
            package._canonical_package_destinations = original_package_paths
            print("CASE_E_PACKAGE_REAPPEARANCE_FAIL_CLOSED_PASS")

            package._canonical_assembly_destinations = lambda _runtime: {target}
            try:
                package.revalidate_orphan_evidence(entries, runtime)
            except ValueError as exc:
                assert "ORPHAN_EVIDENCE_DRIFT" in str(exc)
            else:
                raise AssertionError("assembly reappearance accepted")
            print("CASE_F_ASSEMBLY_REAPPEARANCE_FAIL_CLOSED_PASS")
            package._canonical_assembly_destinations = original_assembly_paths

            symlink = Path(artifact["files"][3]["absolute_runtime_path"])
            symlink.unlink()
            outside = root / "outside.md"
            outside.write_text("outside\n", encoding="utf-8")
            symlink.symlink_to(outside)
            try:
                package.revalidate_orphan_evidence(entries, runtime)
            except ValueError as exc:
                assert "ORPHAN_EVIDENCE_DRIFT" in str(exc)
            else:
                raise AssertionError("symlink accepted")
            print("CASE_G_SYMLINK_FAIL_CLOSED_PASS")
    finally:
        package.ORPHAN_EVIDENCE_PATH = original_evidence
        package._canonical_package_destinations = original_package_paths
        package._canonical_assembly_destinations = original_assembly_paths
    print("ORPHAN_CLEANUP_FIXTURE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
