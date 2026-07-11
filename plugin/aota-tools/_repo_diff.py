"""aota_repo_diff_readonly — bounded read-only Git diff evidence.

Replaces: git diff, git diff --stat, git diff --name-status.
Prefer this tool over delegate_task + terminal git diff.
No model-supplied git args, revisions, or pathspecs accepted.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ._workspace import resolve_workspace, WorkspaceError

TOOL_NAME = "aota_repo_diff_readonly"
TOOLSET_NAME = "aota_repo_readonly"

DEFAULT_MAX_BYTES = 65536
HARD_MAX_BYTES = 262144
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
        "Use for bounded read-only Git diff evidence within a registered AOTA workspace. "
        "Prefer this tool over delegate_task + terminal git diff. "
        "Modes: summary (compact stat), files (name-status), patch (unified diff). "
        "Scope: unstaged or staged. No arbitrary revisions, commits, or git options accepted."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {
                "type": "string",
                "description": "Registered workspace identifier (must be a Git repo)",
            },
            "mode": {
                "type": "string",
                "enum": ["summary", "files", "patch"],
                "description": "Diff output mode (default: summary)",
            },
            "scope": {
                "type": "string",
                "enum": ["unstaged", "staged"],
                "description": "Diff scope (default: unstaged)",
            },
            "max_bytes": {
                "type": "integer",
                "description": f"Max output bytes (default {DEFAULT_MAX_BYTES}, hard max {HARD_MAX_BYTES})",
            },
        },
        "required": ["workspace_id"],
        "additionalProperties": False,
    },
}


def _find_git_root(start: Path) -> Path | None:
    p = start
    while p.is_dir():
        if (p / ".git").exists():
            return p
        if p.parent == p:
            return None
        p = p.parent
    return None


def _run_git(cwd: Path, args: list[str], timeout: int = GIT_TIMEOUT) -> tuple[str, str, int]:
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
    mode = args.get("mode", "summary")
    scope = args.get("scope", "unstaged")
    max_bytes = args.get("max_bytes", DEFAULT_MAX_BYTES)

    if mode not in ("summary", "files", "patch"):
        mode = "summary"
    if scope not in ("unstaged", "staged"):
        scope = "unstaged"
    if not isinstance(max_bytes, int) or max_bytes < 1:
        max_bytes = DEFAULT_MAX_BYTES
    max_bytes = min(max_bytes, HARD_MAX_BYTES)

    try:
        root = resolve_workspace(workspace_id)
    except WorkspaceError as e:
        return json.dumps({"error": str(e)}, sort_keys=True)

    git_root = _find_git_root(root)
    if git_root is None:
        return json.dumps({"error": f"not a Git repository: {workspace_id}"}, sort_keys=True)

    # Build fixed git command — no model-supplied args
    git_args = ["git", "diff", "--no-ext-diff", "--no-textconv"]

    if scope == "staged":
        git_args.append("--cached")

    if mode == "summary":
        git_args.append("--stat")
    elif mode == "files":
        git_args.append("--name-status")
    elif mode == "patch":
        pass  # default unified diff

    stdout, stderr, rc = _run_git(git_root, git_args)
    if rc != 0 and not stdout:
        return json.dumps({"error": f"git diff failed: {stderr[:200]}"}, sort_keys=True)

    output = stdout
    truncated = False

    if mode == "summary":
        # Parse compact stat
        result = {
            "workspace_id": workspace_id,
            "mode": mode,
            "scope": scope,
            "raw_stat": output[:max_bytes],
            "truncated": len(output) > max_bytes,
        }
        # Try to extract counts
        lines = output.strip().split("\n")
        changed_files = 0
        insertions = 0
        deletions = 0
        for line in lines:
            if " file" in line or "files" in line:
                # Last line like "3 files changed, 10 insertions(+), 2 deletions(-)"
                parts = line.split(",")
                for part in parts:
                    part = part.strip()
                    if "file" in part:
                        try:
                            changed_files = int(part.split()[0])
                        except (ValueError, IndexError):
                            pass
                    if "insertion" in part:
                        try:
                            insertions = int(part.split()[0])
                        except (ValueError, IndexError):
                            pass
                    if "deletion" in part:
                        try:
                            deletions = int(part.split()[0])
                        except (ValueError, IndexError):
                            pass
        result["changed_file_count"] = changed_files
        result["insertions"] = insertions
        result["deletions"] = deletions
        return json.dumps(result, sort_keys=True)

    elif mode == "files":
        # Parse name-status
        files_list = []
        binary_changes = []
        for line in output.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t", 1)
            status = parts[0] if len(parts) > 0 else "?"
            path = parts[1] if len(parts) > 1 else ""
            if "Binary files" in line:
                binary_changes.append(path)
                continue
            files_list.append({"status": status, "path": path})
            if len(files_list) >= max_bytes // 100:  # rough bound on entries
                truncated = True
                break

        result = {
            "workspace_id": workspace_id,
            "mode": mode,
            "scope": scope,
            "files": files_list,
            "binary_changes": binary_changes,
            "truncated": truncated,
        }
        return json.dumps(result, sort_keys=True)

    else:  # patch
        patch_output = output
        if len(patch_output.encode("utf-8")) > max_bytes:
            patch_bytes = patch_output.encode("utf-8")[:max_bytes]
            try:
                patch_output = patch_bytes.decode("utf-8")
            except UnicodeDecodeError:
                for i in range(len(patch_bytes) - 1, 0, -1):
                    try:
                        patch_output = patch_bytes[:i].decode("utf-8")
                        break
                    except UnicodeDecodeError:
                        continue
            truncated = True

        # Check for binary diff markers
        binary_markers = []
        for line in output.split("\n"):
            if line.startswith("Binary files") or "Binary file" in line:
                binary_markers.append(line)

        result = {
            "workspace_id": workspace_id,
            "mode": mode,
            "scope": scope,
            "patch": patch_output,
            "truncated": truncated,
            "binary_changes": binary_markers,
        }
        return json.dumps(result, sort_keys=True)
