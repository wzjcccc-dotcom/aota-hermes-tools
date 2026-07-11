"""aota_repo_status_readonly — read-only Git working-tree status evidence.

Replaces: git status, git branch --show-current, git rev-parse HEAD.
Do not delegate terminal merely to run git status.
No model-supplied git args allowed.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ._workspace import resolve_workspace, WorkspaceError

TOOL_NAME = "aota_repo_status_readonly"
TOOLSET_NAME = "aota_repo_readonly"

DEFAULT_MAX_ENTRIES = 100
HARD_MAX_ENTRIES = 500
GIT_TIMEOUT = 10

_GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_PAGER": "cat",
    "PAGER": "cat",
    "HOME": os.environ.get("HOME", "/tmp"),
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
}

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Use for read-only Git working-tree status evidence within a registered AOTA workspace. "
        "Returns branch, head_commit, is_dirty, and bounded staged/unstaged/untracked entries. "
        "Do not delegate terminal merely to run git status. "
        "No model-supplied git options are accepted; uses fixed read-only git commands only."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {
                "type": "string",
                "description": "Registered workspace identifier (must be a Git repo root or inside one)",
            },
            "max_entries": {
                "type": "integer",
                "description": f"Max entries per category (default {DEFAULT_MAX_ENTRIES}, hard max {HARD_MAX_ENTRIES})",
            },
        },
        "required": ["workspace_id"],
        "additionalProperties": False,
    },
}


def _find_git_root(start: Path) -> Path | None:
    """Find the enclosing .git directory by walking up, but never above the workspace root."""
    p = start
    while p.is_dir():
        if (p / ".git").exists():
            return p
        if p.parent == p:
            return None
        p = p.parent
    return None


def _run_git(cwd: Path, args: list[str], timeout: int = GIT_TIMEOUT) -> tuple[str, str, int]:
    """Run git with fixed argv list, no shell, bounded env."""
    env = dict(_GIT_ENV)
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired:
        return "", "git command timed out", -1
    except Exception as e:
        return "", str(e), -1


def handle(args: dict, **_kwargs) -> str:
    workspace_id = args.get("workspace_id", "")
    max_entries = args.get("max_entries", DEFAULT_MAX_ENTRIES)

    if not isinstance(max_entries, int) or max_entries < 1:
        max_entries = DEFAULT_MAX_ENTRIES
    max_entries = min(max_entries, HARD_MAX_ENTRIES)

    try:
        root = resolve_workspace(workspace_id)
    except WorkspaceError as e:
        return json.dumps({"error": str(e)}, sort_keys=True)

    git_root = _find_git_root(root)
    if git_root is None:
        return json.dumps({"error": f"not a Git repository: {workspace_id}"}, sort_keys=True)

    # Branch
    branch, _, rc = _run_git(git_root, ["git", "rev-parse", "--abbrev-ref", "HEAD"])
    branch = branch.strip() if rc == 0 else "unknown"

    # Head commit
    head, _, rc = _run_git(git_root, ["git", "rev-parse", "HEAD"])
    head = head.strip() if rc == 0 else "unknown"

    # Porcelain status: --porcelain=v1 gives staged and unstaged in one pass
    porcelain, _, rc = _run_git(git_root, ["git", "status", "--porcelain=v1", "--untracked-files=normal"])
    if rc != 0:
        return json.dumps({"error": "git status failed"}, sort_keys=True)

    staged = []
    unstaged = []
    untracked = []

    for line in porcelain.split("\n"):
        if not line:
            continue
        # Porcelain v1 format: XY <path>  (X=staged, Y=unstaged)
        x = line[0] if len(line) > 0 else " "
        y = line[1] if len(line) > 1 else " "
        # Path starts at column 3
        path_part = line[3:] if len(line) > 3 else ""
        # Handle rename format: "R  old -> new"
        if " -> " in path_part and x in ("R", "C"):
            parts = path_part.split(" -> ", 1)
            path_part = parts[1] if len(parts) == 2 else path_part

        if x == "?" and y == "?":
            untracked.append(path_part)
        else:
            if x != " " and x != "?":
                staged.append(path_part)
            if y != " " and y != "?":
                unstaged.append(path_part)

    # Truncate
    staged_truncated = len(staged) > max_entries
    unstaged_truncated = len(unstaged) > max_entries
    untracked_truncated = len(untracked) > max_entries

    staged = staged[:max_entries]
    unstaged = unstaged[:max_entries]
    untracked = untracked[:max_entries]

    is_dirty = bool(staged or unstaged or untracked)

    result = {
        "workspace_id": workspace_id,
        "repo_root": str(git_root.relative_to(root)) if git_root != root else ".",
        "branch": branch,
        "head_commit": head[:12] if head != "unknown" else head,
        "is_dirty": is_dirty,
        "staged": {"count": len(staged), "paths": staged, "truncated": staged_truncated},
        "unstaged": {"count": len(unstaged), "paths": unstaged, "truncated": unstaged_truncated},
        "untracked": {"count": len(untracked), "paths": untracked, "truncated": untracked_truncated},
    }

    return json.dumps(result, sort_keys=True)
