"""Postflight scope verification for AOTA Profile Tasks (P8-A).

Provides verify_scope() to check git diff against write_scope globs.
stdlib-only. No third-party dependencies.
"""

from __future__ import annotations

import fnmatch
import json
import subprocess
from pathlib import Path
from typing import Any


def verify_scope(task_dir: Path, workspace_root: Path) -> dict[str, Any]:
    """Verify scope compliance by scanning git diff in workspace_root.

    Reads scope.json from task_dir, runs ``git diff --name-only`` in
    *workspace_root*, and checks each changed file against the
    write_scope glob patterns.

    Returns:
        {"scope_compliance": "unknown|passed|violated", "violated_paths": [...]}
    """
    # 1. Read scope.json from task_dir
    scope_json_path = task_dir / "scope.json"
    if not scope_json_path.exists():
        return {"scope_compliance": "unknown", "violated_paths": []}

    try:
        scope_data = json.loads(scope_json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return {"scope_compliance": "unknown", "violated_paths": []}

    write_scope: list[str] = scope_data.get("write_scope", [])

    # 2. Check if git is available
    try:
        import shutil

        if shutil.which("git") is None:
            return {"scope_compliance": "unknown", "violated_paths": []}
    except Exception:
        return {"scope_compliance": "unknown", "violated_paths": []}

    # 3. Check if workspace is a git repo
    git_dir = workspace_root / ".git"
    if not git_dir.is_dir():
        return {"scope_compliance": "unknown", "violated_paths": []}

    # 4. Run git diff --name-only (modified + deleted tracked files)
    #    Also run git ls-files --others (untracked new files)
    changed_files: list[str] = []
    try:
        diff_result = subprocess.run(
            ["git", "-C", str(workspace_root), "diff", "--name-only"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError, PermissionError):
        return {"scope_compliance": "unknown", "violated_paths": []}

    if diff_result.returncode != 0:
        return {"scope_compliance": "unknown", "violated_paths": []}

    changed_files.extend(
        f.strip() for f in diff_result.stdout.strip().splitlines() if f.strip()
    )

    # Also collect untracked files (new files not in git yet)
    try:
        untracked_result = subprocess.run(
            [
                "git", "-C", str(workspace_root),
                "ls-files", "--others", "--exclude-standard",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if untracked_result.returncode == 0:
            changed_files.extend(
                f.strip()
                for f in untracked_result.stdout.strip().splitlines()
                if f.strip()
            )
    except (subprocess.TimeoutExpired, OSError, PermissionError):
        pass  # Best-effort: only diff results are used

    # 5. No changes → passed
    if not changed_files:
        return {"scope_compliance": "passed", "violated_paths": []}

    # 6. Check each changed file against write_scope glob patterns
    violated_paths: list[str] = []
    for changed_file in changed_files:
        allowed = False
        for pattern in write_scope:
            if fnmatch.fnmatch(changed_file, pattern):
                allowed = True
                break
        if not allowed:
            violated_paths.append(changed_file)

    # 7. Return result
    if violated_paths:
        return {"scope_compliance": "violated", "violated_paths": violated_paths}
    else:
        return {"scope_compliance": "passed", "violated_paths": []}
