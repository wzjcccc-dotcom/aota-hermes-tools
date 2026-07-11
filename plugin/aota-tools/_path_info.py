"""aota_path_info — exact local path existence/type/size check.

Replaces: stat, test -e/-d, file, readlink.
Prefer this tool over delegate_task + terminal stat/test/file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ._workspace import resolve_relative_path, WorkspaceError

TOOL_NAME = "aota_path_info"
TOOLSET_NAME = "aota_fs_readonly"

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Use for exact local path existence/type/size checks within a registered AOTA workspace. "
        "Prefer this tool over delegate_task + terminal stat/test/file/readlink. "
        "Returns compact metadata: exists, type (file/directory/symlink/other/missing), "
        "size_bytes, mtime, mode, is_symlink, symlink_target. "
        "Does not list directory contents, read file content, hash, or modify anything."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {
                "type": "string",
                "description": "Registered workspace identifier (e.g. 'aota-runtime')",
            },
            "path": {
                "type": "string",
                "description": "Workspace-relative path to inspect",
            },
        },
        "required": ["workspace_id", "path"],
        "additionalProperties": False,
    },
}


def _classify(path: Path) -> str:
    """Classify path type into fixed enum."""
    if path.is_symlink():
        return "symlink"
    if path.is_file():
        return "file"
    if path.is_dir():
        return "directory"
    return "other"


def handle(args: dict, **_kwargs) -> str:
    workspace_id = args.get("workspace_id", "")
    path_str = args.get("path", "")

    try:
        root, resolved = resolve_relative_path(workspace_id, path_str, must_exist=False)
    except WorkspaceError as e:
        return json.dumps({"error": str(e)}, sort_keys=True)

    root_real = root.resolve()

    result = {
        "workspace_id": workspace_id,
        "path": path_str,
        "exists": False,
        "type": "missing",
        "size_bytes": None,
        "mtime": None,
        "mode": None,
        "is_symlink": False,
        "symlink_target": None,
    }

    if not resolved.exists():
        # Check if it's a broken symlink
        raw = root_real / path_str
        if raw.is_symlink():
            result["exists"] = False
            result["type"] = "symlink"
            result["is_symlink"] = True
            try:
                result["symlink_target"] = os.readlink(str(raw))
            except OSError:
                pass
        return json.dumps(result, sort_keys=True)

    result["exists"] = True
    result["is_symlink"] = resolved.is_symlink() or (root_real / path_str).is_symlink()

    # For symlinks, get the link target text
    raw = root_real / path_str
    if raw.is_symlink():
        result["type"] = "symlink"
        try:
            link_text = os.readlink(str(raw))
            result["symlink_target"] = link_text
        except OSError:
            pass
        # Check if the resolved target is within workspace
        try:
            resolved.relative_to(root_real)
            result["target_within_workspace"] = True
        except ValueError:
            result["target_within_workspace"] = False
            # Don't read target content; just report escape
    else:
        result["type"] = _classify(resolved)

    # Get stat info from the resolved path
    try:
        st = resolved.stat()
        result["size_bytes"] = st.st_size
        result["mtime"] = int(st.st_mtime)
        result["mode"] = oct(st.st_mode & 0o777)
    except OSError:
        pass

    return json.dumps(result, sort_keys=True)
