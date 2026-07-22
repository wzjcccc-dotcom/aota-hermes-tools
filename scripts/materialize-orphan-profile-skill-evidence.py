#!/usr/bin/env python3
"""Materialize the authorized exact orphan evidence from the canonical assembly.

This is intentionally a bounded resolver-based capture.  It never walks an
unrelated runtime tree and it never mutates runtime files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import profile_runtime_assembly as assembly  # noqa: E402
import aota_forge_plan_package as package  # noqa: E402


EVIDENCE_ID = "orphaned-profile-skill-runtime-files-20260722"
OUTPUT = ROOT / "deploy" / "evidence" / f"{EVIDENCE_ID}.json"
PROJECT_ID = "aota-hermes-tools"
OPERATOR_CLASSIFICATION = "ORPHANED_PROFILE_LOCAL_SKILL_RUNTIME_FILES"
OPERATOR_AUTHORIZATION = "GRANTED_EXACT_11_PATHS"
AUTHORIZED_COUNT = 11


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def package_expected(runtime_root: Path) -> set[Path]:
    entries = package.build_entries(package.load_manifest())
    return {
        entry["destination"]
        for entry in entries
        if entry["kind"] in {"copy", "remove"} and entry["destination"].is_relative_to(runtime_root)
    }


def capture(runtime_root: Path) -> dict:
    runtime_root = runtime_root.resolve(strict=True)
    assembly_data = assembly.load_assembly()
    package_paths = package_expected(runtime_root)
    files: list[dict] = []
    seen: set[str] = set()
    for profile, spec in sorted(assembly_data["profiles"].items()):
        for skill in sorted({str(x) for x in spec.get("active_skills", []) + spec.get("reference_skills", [])}):
            allowed_root = runtime_root / "profiles" / profile / "skills" / skill
            actual = assembly.expand_skill_tree(allowed_root, include_escapes=True)
            source_root = ROOT / "skills" / skill
            expected = assembly.expand_skill_tree(source_root)
            for relative, target in sorted(actual.items()):
                if relative in expected or not relative.startswith("references/") or not relative.endswith(".md"):
                    continue
                absolute = target.absolute()
                relative_runtime = absolute.relative_to(runtime_root).as_posix()
                if relative_runtime in seen:
                    raise RuntimeError(f"duplicate exact runtime path: {relative_runtime}")
                seen.add(relative_runtime)
                source_path = source_root / relative
                files.append({
                    "absolute_runtime_path": str(absolute),
                    "relative_runtime_path": relative_runtime,
                    "profile": profile,
                    "skill": skill,
                    "reference_relative_path": relative,
                    "exists": target.exists() or target.is_symlink(),
                    "regular_file": target.is_file() and not target.is_symlink(),
                    "is_symlink": target.is_symlink(),
                    "realpath": str(target.resolve(strict=False)),
                    "allowed_root": str(allowed_root),
                    "size_bytes": target.stat().st_size if target.is_file() and not target.is_symlink() else None,
                    "sha256": digest(target) if target.is_file() and not target.is_symlink() else None,
                    "canonical_source_path": str(source_path),
                    "canonical_source_exists": source_path.is_file() and not source_path.is_symlink(),
                    "package_plan_declares": absolute in package_paths,
                    "assembly_expected_declares": relative in expected,
                    "origin_classification": "OPERATOR_CLASSIFIED_ORPHAN",
                    "operator_authorization": OPERATOR_AUTHORIZATION,
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                })
    files.sort(key=lambda item: item["relative_runtime_path"])
    artifact = {
        "schema_version": 1,
        "evidence_id": EVIDENCE_ID,
        "project_id": PROJECT_ID,
        "operator_classification": OPERATOR_CLASSIFICATION,
        "operator_authorization": OPERATOR_AUTHORIZATION,
        "authorized_path_count": AUTHORIZED_COUNT,
        "capture_mode": "bounded_read_only_runtime_diff",
        "assembly_version": 2,
        "capture_command": "scripts/materialize-orphan-profile-skill-evidence.py --runtime-root ${AOTA_HERMES_HOME_HOST}",
        "files": files,
    }
    return artifact


def self_check(artifact: dict, runtime_root: Path) -> None:
    files = artifact.get("files", [])
    assert len(files) == AUTHORIZED_COUNT, f"REFERENCE_EXTRA_COUNT={len(files)}"
    paths = [item["absolute_runtime_path"] for item in files]
    assert len(paths) == len(set(paths)), "DUPLICATE_PATHS>0"
    assert all(item["exists"] for item in files), "ALL_EXIST=no"
    assert all(item["regular_file"] for item in files), "ALL_REGULAR=no"
    assert all(not item["is_symlink"] for item in files), "ALL_SYMLINK=yes"
    assert all(Path(item["realpath"]).is_relative_to(Path(item["allowed_root"]).resolve()) for item in files), "ALL_ALLOWED_ROOT=no"
    assert all(not item["canonical_source_exists"] for item in files), "ALL_CANONICAL_SOURCE_ABSENT=no"
    assert all(not item["package_plan_declares"] for item in files), "ALL_PACKAGE_PLAN_ABSENT=no"
    assert all(not item["assembly_expected_declares"] for item in files), "ALL_ASSEMBLY_EXPECTED_ABSENT=no"
    assert all(isinstance(item["sha256"], str) and len(item["sha256"]) == 64 for item in files), "ALL_SHA256_PRESENT=no"
    for item in files:
        path = Path(item["absolute_runtime_path"])
        assert digest(path) == item["sha256"], f"hash mismatch: {path}"
    print("EVIDENCE_ARTIFACT_SELF_CHECK=PASS")
    print("RUNTIME_EVIDENCE_REVALIDATION=PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", default=package.expand("${AOTA_HERMES_HOME_HOST}"))
    parser.add_argument("--output", default=str(OUTPUT))
    args = parser.parse_args()
    artifact = capture(Path(args.runtime_root))
    self_check(artifact, Path(args.runtime_root))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"EVIDENCE_ARTIFACT_PATH={output}")
    print(f"EVIDENCE_FILE_COUNT={len(artifact['files'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
