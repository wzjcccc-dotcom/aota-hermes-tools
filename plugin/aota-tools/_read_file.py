"""aota_read_file — bounded local text read.

Replaces: cat, sed -n, head, tail.
Prefer this tool over delegate_task + terminal cat/sed/head/tail.
Use CodeGraph instead when semantic code relationships are needed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ._workspace import resolve_relative_path, WorkspaceError

TOOL_NAME = "aota_read_file"
TOOLSET_NAME = "aota_fs_readonly"

DEFAULT_MAX_BYTES = 65536
HARD_MAX_BYTES = 262144

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Use for bounded local text reads within a registered AOTA workspace. "
        "Prefer this tool over delegate_task + terminal cat/sed/head/tail. "
        "Use CodeGraph instead when semantic code relationships are needed. "
        "Reads regular text files only; binary files are rejected. "
        "Supports line range and byte-limit truncation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {
                "type": "string",
                "description": "Registered workspace identifier",
            },
            "path": {
                "type": "string",
                "description": "Workspace-relative file path to read",
            },
            "start_line": {
                "type": "integer",
                "description": "Line number to start reading from (1-indexed, default 1)",
            },
            "end_line": {
                "type": "integer",
                "description": "Line number to stop reading at (inclusive)",
            },
            "max_bytes": {
                "type": "integer",
                "description": f"Maximum bytes to return (default {DEFAULT_MAX_BYTES}, hard max {HARD_MAX_BYTES})",
            },
        },
        "required": ["workspace_id", "path"],
        "additionalProperties": False,
    },
}


def _is_binary(data: bytes, sample_size: int = 8192) -> bool:
    """Detect binary content by checking for NUL bytes in a sample."""
    sample = data[:sample_size]
    if b"\x00" in sample:
        return True
    # Check for high ratio of non-text bytes
    if sample:
        text_chars = bytes(range(32, 127)) + b"\n\r\t\f\b"
        non_text = sum(1 for b in sample if b not in text_chars)
        if non_text / len(sample) > 0.30:
            return True
    return False


def handle(args: dict, **_kwargs) -> str:
    workspace_id = args.get("workspace_id", "")
    path_str = args.get("path", "")
    start_line = args.get("start_line", 1)
    end_line = args.get("end_line")
    max_bytes = args.get("max_bytes", DEFAULT_MAX_BYTES)

    if not isinstance(start_line, int) or start_line < 1:
        start_line = 1
    if end_line is not None and (not isinstance(end_line, int) or end_line < start_line):
        end_line = None
    if not isinstance(max_bytes, int) or max_bytes < 1:
        max_bytes = DEFAULT_MAX_BYTES
    max_bytes = min(max_bytes, HARD_MAX_BYTES)

    try:
        root, resolved = resolve_relative_path(workspace_id, path_str, must_exist=True)
    except WorkspaceError as e:
        return json.dumps({"error": str(e)}, sort_keys=True)

    if not resolved.is_file():
        return json.dumps({"error": f"not a regular file: {path_str}"}, sort_keys=True)

    try:
        raw = resolved.read_bytes()
    except OSError as e:
        return json.dumps({"error": f"read failed: {e}"}, sort_keys=True)

    if _is_binary(raw):
        return json.dumps({"error": f"binary file rejected: {path_str}"}, sort_keys=True)

    # Decode UTF-8, fail on decode error (no silent replacement)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        return json.dumps({"error": f"UTF-8 decode failure: {e}"}, sort_keys=True)

    lines = text.split("\n")
    # Adjust for trailing newline producing empty last element
    if lines and lines[-1] == "" and text.endswith("\n"):
        lines = lines[:-1]

    total_lines = len(lines)
    end = end_line if end_line is not None else total_lines
    end = min(end, total_lines)

    selected_lines = lines[start_line - 1 : end]
    content = "\n".join(selected_lines)
    if text.endswith("\n"):
        content += "\n"

    truncated = False
    if len(content.encode("utf-8")) > max_bytes:
        # Truncate at byte boundary, then decode safely
        content_bytes = content.encode("utf-8")[:max_bytes]
        # Try to cut at a valid UTF-8 boundary
        try:
            content = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            # Trim back to last valid boundary
            for i in range(len(content_bytes) - 1, 0, -1):
                try:
                    content = content_bytes[:i].decode("utf-8")
                    break
                except UnicodeDecodeError:
                    continue
        truncated = True

    result = {
        "workspace_id": workspace_id,
        "path": path_str,
        "size_bytes": len(raw),
        "start_line": start_line,
        "end_line_returned": start_line + len(selected_lines) - 1 if selected_lines else start_line,
        "total_lines": total_lines,
        "content": content,
        "truncated": truncated,
    }

    return json.dumps(result, sort_keys=True)
