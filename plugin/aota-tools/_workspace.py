"""Workspace registry resolver and path safety helper for AOTA read-only tools.

All P2 tools must go through ``resolve_workspace`` / ``resolve_relative_path``
to translate ``workspace_id`` + relative path into a safe filesystem path.
No model-supplied absolute paths are ever accepted.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Tuple

_PLUGIN_DIR = Path(__file__).resolve().parent
_REGISTRY_PATH = _PLUGIN_DIR / "workspaces.json"


class WorkspaceError(Exception):
    """Compact, model-facing error for workspace/path safety violations."""


def _load_registry() -> dict:
    """Load workspaces.json from plugin directory.

    Returns empty dict on missing/malformed file (fail-closed: nothing resolves).
    """
    try:
        with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except (OSError, json.JSONDecodeError):
        return {}


def resolve_workspace(workspace_id: str) -> Path:
    """Resolve a workspace_id to its verified filesystem root.

    Resolver rules:
      1. Only look at registry candidates for this workspace_id
      2. For existing candidates, use realpath
      3. 0 exist  → error
         1 exists  → use it
         >1 exist and realpath differs → error
      4. Never guess, never search for alternative paths
      5. Never fall back to cwd, /, or HOME

    Returns the resolved Path (realpath-confirmed, exists, is directory).
    """
    if not workspace_id:
        raise WorkspaceError("workspace_id is required")

    registry = _load_registry()
    entry = registry.get(workspace_id)
    if entry is None:
        raise WorkspaceError(f"unknown workspace_id: {workspace_id}")

    candidates = entry.get("candidates") if isinstance(entry, dict) else None
    if not candidates or not isinstance(candidates, list):
        raise WorkspaceError(f"no candidates for workspace_id: {workspace_id}")

    # Resolve each candidate to realpath, collect existing ones
    resolved: list[Path] = []
    for c in candidates:
        if not isinstance(c, str) or not c:
            continue
        p = Path(c)
        if p.exists():
            resolved.append(p.resolve())

    if len(resolved) == 0:
        raise WorkspaceError(f"workspace_id '{workspace_id}': no candidate path exists in this runtime")

    if len(resolved) > 1:
        # Check if all resolved paths are the same (same realpath)
        unique = set(str(r) for r in resolved)
        if len(unique) > 1:
            raise WorkspaceError(
                f"workspace_id '{workspace_id}': multiple distinct candidate paths exist, ambiguous"
            )

    root = resolved[0]
    if not root.is_dir():
        raise WorkspaceError(f"workspace_id '{workspace_id}': resolved root is not a directory: {root}")

    return root


def resolve_relative_path(
    workspace_id: str,
    relative_path: str,
    must_exist: bool = True,
) -> Tuple[Path, Path]:
    """Resolve a workspace-relative path to its safe filesystem path.

    Core rules:
      1. path must be a relative path
      2. reject absolute path
      3. reject NUL byte
      4. reject empty workspace_id
      5. reject unknown workspace_id
      6. ``..`` must not escape workspace root
      7. use realpath to check actual target
      8. symlink target escaping root → reject
      9. never use string prefix check

    Returns (root, resolved_path).
    If must_exist=True and path doesn't exist, raises WorkspaceError.

    For paths that DO exist, resolved_path is the realpath-confirmed target.
    For paths that DON'T exist (must_exist=False), we validate the parent chain.
    """
    root = resolve_workspace(workspace_id)

    if not relative_path:
        raise WorkspaceError("path is required")

    if "\x00" in relative_path:
        raise WorkspaceError("path contains NUL byte")

    # Reject absolute paths
    p = Path(relative_path)
    if p.is_absolute():
        raise WorkspaceError("absolute paths are not allowed; use workspace-relative path")

    # Join with root, then resolve
    joined = root / p

    if must_exist:
        if not joined.exists():
            raise WorkspaceError(f"path does not exist: {relative_path}")
        real = joined.resolve()
    else:
        # For non-existing paths, walk up to the first existing ancestor and resolve that
        # Then re-join the remaining non-existing components
        if joined.exists():
            real = joined.resolve()
        else:
            # Find first existing ancestor
            ancestor = joined
            non_existing_parts: list[str] = []
            while not ancestor.exists():
                non_existing_parts.insert(0, ancestor.name)
                ancestor = ancestor.parent
                if ancestor == root:
                    break
            real_ancestor = ancestor.resolve()
            real = real_ancestor
            for part in non_existing_parts:
                real = real / part

    # Reliable path containment: check that real path is within root's resolved path
    root_real = root.resolve()
    try:
        real.relative_to(root_real)
    except ValueError:
        raise WorkspaceError(f"path escapes workspace root: {relative_path}")

    return root, real
