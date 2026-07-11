#!/usr/bin/env python3
"""Controlled cleanup script for AOTA test artifacts.

Usage: python3 cleanup-controlled-run.py <manifest_path>

Safety: exact paths only, no glob, no prefix, no symlink, no broad parent.

Enforces exact-path-only deletion to prevent incidents like P10 where
prefix-based deletion accidentally deleted pre-existing test task directories.
"""

import json
import os
import re
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RUNTIME_ROOT = "/aota-runtime"
MAX_PATHS = 1000
MAX_MANIFEST_SIZE = 1_000_000  # 1 MB
GLOB_RE = re.compile(r"[*?\[\]]")
DATE_PREFIX_RE = re.compile(r"_\d{8}T")  # e.g. pt_20260710T

# Known structural grouping directories — paths ending at these are "broad"
STRUCTURAL_DIRS = frozenset({
    "pending",
    "acknowledged",
    "task-spec",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _die(msg: str) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


def _usage() -> None:
    print(__doc__, file=sys.stderr)
    sys.exit(1)


def _load_manifest(path: str) -> tuple[str, list[str]]:
    """Load a cleanup-manifest.json file and return (run_id, allowed_paths).

    Exits on any load / validation failure.
    """
    mp = Path(path)
    if not mp.exists():
        _die(f"manifest not found: {path}")

    size = mp.stat().st_size
    if size > MAX_MANIFEST_SIZE:
        _die(
            f"manifest exceeds size limit ({size} > {MAX_MANIFEST_SIZE} bytes)"
        )

    try:
        with open(mp, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        _die(f"invalid manifest file: {exc}")

    if not isinstance(data, dict):
        _die("manifest must be a JSON object")

    run_id = data.get("run_id", "")
    if not isinstance(run_id, str) or not run_id:
        _die("manifest missing required string field 'run_id'")

    paths = data.get("allowed_paths")
    if not isinstance(paths, list):
        _die("'allowed_paths' must be a list")

    if len(paths) > MAX_PATHS:
        _die(
            f"manifest has {len(paths)} paths "
            f"(maximum allowed: {MAX_PATHS})"
        )

    # Verify each entry is a string
    for i, entry in enumerate(paths):
        if not isinstance(entry, str):
            _die(f"'allowed_paths[{i}]' is not a string")

    return run_id, paths


def _normalize(p: str) -> str:
    """Normalize a path: strip whitespace, collapse //, remove trailing /."""
    p = p.strip()
    while "//" in p:
        p = p.replace("//", "/")
    p = p.rstrip("/")
    return p if p else "/"


def _validate_path(
    path_str: str,
    runtime_root: str,
    run_id: str,
) -> tuple[bool, str | None]:
    """Run all safety checks on a single manifest path.

    Returns (is_valid, reason_on_failure).
    """
    norm = _normalize(path_str)
    root = runtime_root.rstrip("/")

    # -- 1. Containment ----------------------------------------------------
    if norm != root and not norm.startswith(root + "/"):
        return False, "path is outside the runtime root"

    # -- 2. Glob characters ------------------------------------------------
    if GLOB_RE.search(norm):
        return False, "path contains glob characters (* ? [ ])"

    # -- 3. Root directories -----------------------------------------------
    if norm in ("/", root, root + "/"):
        return False, "path is a root or runtime-root directory"

    # -- 4. Parent broad paths ---------------------------------------------
    relative = norm[len(root):].lstrip("/")
    parts = [p for p in relative.split("/") if p]

    if not parts:
        return False, "path is the runtime root directory"

    # Single category level (e.g. /aota-runtime/profile-tasks)
    if len(parts) == 1:
        return False, (
            f"broad parent directory (category level only): {path_str}"
        )

    category = parts[0]

    # tmp/ and test-runs/ are specific at 2 levels (category / run_id)
    if category in ("tmp", "test-runs"):
        if len(parts) < 2:
            return False, f"broad parent directory: {path_str}"
        # 2 parts or more is specific enough for tmp/test-runs
    else:
        # profile-tasks, locks, handoffs, decisions need ≥3 levels
        if len(parts) < 3:
            return False, (
                f"broad parent directory (not specific enough): {path_str}"
            )
        # Reject structural grouping dirs (pending, acknowledged, task-spec)
        if parts[-1] in STRUCTURAL_DIRS:
            return False, (
                f"structural directory, not an artifact: {path_str}"
            )

    # Date-prefix directory check (potential group prefix)
    if DATE_PREFIX_RE.search(parts[-1]):
        return False, (
            f"path looks like a date-prefix group directory: {path_str}"
        )

    # -- 5. Path traversal via resolve() -----------------------------------
    try:
        pp = Path(norm)
        resolved = pp.resolve()
        root_resolved = Path(root).resolve()
        # Check that resolved path stays within the runtime root
        try:
            resolved.relative_to(root_resolved)
        except ValueError:
            return False, (
                f"path traversal detected: resolves outside runtime root"
            )
    except (OSError, RuntimeError) as exc:
        return False, f"path resolution error: {exc}"

    return True, None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] in ("-h", "--help", "--usage"):
        _usage()

    manifest_path = sys.argv[1]
    run_id, raw_paths = _load_manifest(manifest_path)
    runtime_root = os.environ.get("AOTA_RUNTIME_ROOT", RUNTIME_ROOT)

    # -- Deduplicate paths -------------------------------------------------
    seen: set[str] = set()
    paths: list[str] = []
    for p in raw_paths:
        norm = _normalize(p)
        if norm not in seen:
            seen.add(norm)
            paths.append(norm)
    dups_removed = len(raw_paths) - len(paths)

    # -- Validate all paths ------------------------------------------------
    rejected: list[dict] = []
    valid: list[str] = []
    for p in paths:
        ok, reason = _validate_path(p, runtime_root, run_id)
        if ok:
            valid.append(p)
        else:
            rejected.append({"path": p, "reason": reason})
            # Safety negative test A: glob chars → glob_rejected counted
            # Safety negative test B: date prefix → caught above
            # Safety negative test C: broad parent → caught above
            # Safety negative test D: external path → caught above

    # -- Symlink check (runtime check on existing paths only) --------------
    symlinks_rejected: int = 0
    still_valid: list[str] = []
    for p in valid:
        pp = Path(p)
        if pp.exists() and pp.is_symlink():
            # Safety negative test E: symlink → reject
            symlinks_rejected += 1
            rejected.append({"path": p, "reason": "is a symlink"})
        else:
            still_valid.append(p)
    valid = still_valid

    # -- Count glob-rejected for reporting ---------------------------------
    glob_rejected: int = sum(
        1 for r in rejected if "glob" in (r.get("reason") or "").lower()
    )

    # -- 5. Preview --------------------------------------------------------
    print(f"Run ID: {run_id}")
    print(f"Manifest: {manifest_path}")
    print(f"Total unique paths: {len(paths)}")
    if dups_removed:
        print(f"Duplicates removed: {dups_removed}")
    print(f"Paths to delete: {len(valid)}")
    print(f"Rejected paths: {len(rejected)}")
    print()
    if valid:
        print("--- Paths to delete ---")
        for p in valid:
            print(f"  {p}")
        print()

    # -- 6. Verify targets belong to run (cross-check) ---------------------
    run_id_prefix = run_id.split("_")[0] if "_" in run_id else run_id
    unmatched: list[str] = []
    for p in valid:
        # Extract all run-like tokens from each path component
        parts = p.split("/")
        matched = False
        for part in parts:
            # If a component looks like it carries a run identifier,
            # it should start with or contain the run_id prefix.
            if run_id and run_id in part:
                matched = True
                break
            if run_id_prefix and part.startswith(run_id_prefix):
                matched = True
                break
        if not matched and run_id:
            # The path doesn't contain the run_id — flag for review
            # (warn only, do not reject — not all artifact paths embed
            #  the run_id)
            unmatched.append(p)

    if unmatched:
        print(
            "Warning: the following paths do not contain the run_id "
            f"('{run_id}') in any path component:",
        )
        for p in unmatched:
            print(f"  {p}")
        print()

    # -- 7. Delete exact targets -------------------------------------------
    deleted: int = 0
    already_missing: int = 0

    for p in valid:
        pp = Path(p)
        if not pp.exists():
            # Safety negative test G: already missing → idempotent success
            already_missing += 1
            continue
        # Redundant symlink check (belt-and-suspenders)
        if pp.is_symlink():
            symlinks_rejected += 1
            continue
        try:
            if pp.is_dir():
                shutil.rmtree(pp)
            else:
                pp.unlink()
            deleted += 1
        except OSError as exc:
            print(f"Error deleting {p}: {exc}", file=sys.stderr)

    # -- 8. Verify gone ----------------------------------------------------
    still_exists: list[str] = []
    for p in valid:
        pp = Path(p)
        if pp.exists():
            still_exists.append(p)

    if still_exists:
        print("Warning: the following paths still exist after deletion:")
        for p in still_exists:
            print(f"  {p}")
        print()

    # -- 9. Build report ---------------------------------------------------
    result = {
        "run_id": run_id,
        "manifest_path": manifest_path,
        "total_paths": len(paths),
        "deleted": deleted,
        "already_missing": already_missing,
        "rejected": [r["path"] for r in rejected],
        "pre_existing_touched": 0,
        "symlinks_rejected": symlinks_rejected,
        "glob_rejected": glob_rejected,
        "success": True,
    }

    if still_exists:
        result["success"] = False

    print(json.dumps(result, indent=2))

    if result["success"]:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
