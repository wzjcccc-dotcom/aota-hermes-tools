"""aota_search_files — bounded local filename/content search.

Replaces: grep, rg, find, wc.
Prefer this tool over delegate_task + terminal grep/rg/find.
Use CodeGraph for symbol/call relationships.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ._workspace import resolve_workspace, resolve_relative_path, WorkspaceError

TOOL_NAME = "aota_search_files"
TOOLSET_NAME = "aota_fs_readonly"

DEFAULT_MAX_RESULTS = 50
HARD_MAX_RESULTS = 200
DEFAULT_MAX_FILE_BYTES = 2097152

SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".codegraph",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    "dist",
    "build",
    ".eggs",
    ".rtk",
}

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Use for bounded local filename/content search within a registered AOTA workspace. "
        "Prefer this tool over delegate_task + terminal grep/rg/find/wc. "
        "Use CodeGraph for symbol/call relationships. "
        "V1 uses literal substring search (no regex). "
        "Modes: content (matches with preview), files_only (unique file paths), count."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {
                "type": "string",
                "description": "Registered workspace identifier",
            },
            "query": {
                "type": "string",
                "description": "Search query (literal substring, not regex)",
            },
            "path": {
                "type": "string",
                "description": "Workspace-relative file or directory to search in (default: workspace root)",
            },
            "mode": {
                "type": "string",
                "enum": ["content", "files_only", "count"],
                "description": "Search mode (default: content)",
            },
            "case_sensitive": {
                "type": "boolean",
                "description": "Case-sensitive search (default: false)",
            },
            "file_glob": {
                "type": "string",
                "description": "Optional glob pattern to filter files (e.g. '*.py')",
            },
            "max_results": {
                "type": "integer",
                "description": f"Maximum results (default {DEFAULT_MAX_RESULTS}, hard max {HARD_MAX_RESULTS})",
            },
            "max_file_bytes": {
                "type": "integer",
                "description": f"Skip files larger than this (default {DEFAULT_MAX_FILE_BYTES})",
            },
        },
        "required": ["workspace_id", "query"],
        "additionalProperties": False,
    },
}


def _is_binary(data: bytes, sample_size: int = 8192) -> bool:
    sample = data[:sample_size]
    if b"\x00" in sample:
        return True
    if sample:
        text_chars = bytes(range(32, 127)) + b"\n\r\t\f\b"
        non_text = sum(1 for b in sample if b not in text_chars)
        if non_text / len(sample) > 0.30:
            return True
    return False


def _matches_glob(filename: str, glob_pattern: str) -> bool:
    """Simple glob match using fnmatch."""
    import fnmatch
    return fnmatch.fnmatch(filename, glob_pattern)


def _walk_bounded(root: Path, max_file_bytes: int):
    """Yield (path, size) for text files under root, skipping SKIP_DIRS and binary/large files."""
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skip dirs in-place
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            fp = os.path.join(dirpath, fname)
            try:
                st = os.stat(fp)
                if st.st_size > max_file_bytes:
                    continue
                yield Path(fp), st.st_size
            except OSError:
                continue


def _iter_search_files(search_root: Path, max_file_bytes: int):
    """Yield one exact file or a bounded directory walk."""
    if search_root.is_file():
        try:
            size = search_root.stat().st_size
        except OSError:
            return
        if size <= max_file_bytes:
            yield search_root, size
        return
    yield from _walk_bounded(search_root, max_file_bytes)


def handle(args: dict, **_kwargs) -> str:
    workspace_id = args.get("workspace_id", "")
    query = args.get("query", "")
    path_str = args.get("path", ".")
    mode = args.get("mode", "content")
    case_sensitive = args.get("case_sensitive", False)
    file_glob = args.get("file_glob")
    max_results = args.get("max_results", DEFAULT_MAX_RESULTS)
    max_file_bytes = args.get("max_file_bytes", DEFAULT_MAX_FILE_BYTES)

    if not isinstance(max_results, int) or max_results < 1:
        max_results = DEFAULT_MAX_RESULTS
    max_results = min(max_results, HARD_MAX_RESULTS)
    if not isinstance(max_file_bytes, int) or max_file_bytes < 1:
        max_file_bytes = DEFAULT_MAX_FILE_BYTES

    if mode not in ("content", "files_only", "count"):
        mode = "content"

    if not query:
        return json.dumps({"error": "query is required"}, sort_keys=True)

    try:
        root, search_root = resolve_relative_path(workspace_id, path_str, must_exist=True)
    except WorkspaceError as e:
        return json.dumps({"error": str(e)}, sort_keys=True)

    if not search_root.is_dir() and not search_root.is_file():
        return json.dumps({"error": f"search path is not a regular file or directory: {path_str}"}, sort_keys=True)

    search_query = query if case_sensitive else query.lower()

    results = []
    result_count = 0
    truncated = False
    skipped_large_files = 0
    matched_file_count = 0
    match_count = 0

    for fp, fsize in _iter_search_files(search_root, max_file_bytes):
        fname = fp.name

        # Apply glob filter
        if file_glob and not _matches_glob(fname, file_glob):
            continue

        # For files_only and count modes, check filename match
        if mode == "files_only":
            check_text = fname if case_sensitive else fname.lower()
            if search_query in check_text:
                results.append(str(fp.relative_to(root)))
                result_count += 1
                matched_file_count += 1
                if result_count >= max_results:
                    truncated = True
                    break
            continue

        # For content and count modes, read file content
        try:
            raw = fp.read_bytes()
        except OSError:
            continue

        if _is_binary(raw):
            continue

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue

        text_to_search = text if case_sensitive else text.lower()
        file_had_match = False

        for line_no, line in enumerate(text.split("\n"), 1):
            line_to_search = line if case_sensitive else line.lower()
            if search_query in line_to_search:
                file_had_match = True
                match_count += 1
                if mode == "content" and result_count < max_results:
                    # Build compact preview (max 120 chars)
                    preview = line.strip()
                    if len(preview) > 120:
                        # Try to center around the match
                        idx = line_to_search.index(search_query)
                        start = max(0, idx - 40)
                        end = min(len(line), idx + len(search_query) + 40)
                        preview = line[start:end].strip()
                        if len(preview) > 120:
                            preview = preview[:120]
                    results.append({
                        "path": str(fp.relative_to(root)),
                        "line": line_no,
                        "preview": preview,
                    })
                    result_count += 1
                elif mode == "content" and result_count >= max_results:
                    truncated = True

        if file_had_match:
            matched_file_count += 1

        if mode == "content" and result_count >= max_results and truncated:
            break

    result = {
        "workspace_id": workspace_id,
        "query": query,
        "mode": mode,
        "results": results,
        "result_count": result_count,
        "truncated": truncated,
        "matched_file_count": matched_file_count,
    }
    if mode == "count":
        result["match_count"] = match_count
        # Clear results for count mode
        result["results"] = []
        result["result_count"] = matched_file_count

    return json.dumps(result, sort_keys=True)
