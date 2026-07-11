"""aota_file_copy — direct byte-preserving file/directory copy inside an allowlisted workspace.

Uses shutil for actual copy operations; no shell cp/rsync.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from ._workspace import resolve_relative_path, WorkspaceError

TOOL_NAME = "aota_file_copy"
TOOLSET_NAME = "aota_fs_copy"

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Use for direct byte-preserving file or directory copying inside an "
        "allowlisted workspace. Prefer over delegate_task + terminal cp for "
        "simple copy operations. Do not use for editing or transforming file content."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {
                "type": "string",
                "description": "Registered workspace identifier (e.g. 'aota-runtime')",
            },
            "source": {
                "type": "string",
                "description": "Workspace-relative source path (file or directory)",
            },
            "destination": {
                "type": "string",
                "description": "Workspace-relative destination path",
            },
            "overwrite": {
                "type": "boolean",
                "description": "Allow overwriting existing destination (default: false)",
                "default": False,
            },
        },
        "required": ["workspace_id", "source", "destination"],
        "additionalProperties": False,
    },
}

# Hard limits
MAX_FILE_COUNT = 10000
MAX_TOTAL_BYTES = 10737418240  # 10 GiB


# ---------------------------------------------------------------------------
# Preflight helpers
# ---------------------------------------------------------------------------

def _classify_source(path: Path) -> tuple[str, int, int, int]:
    """Classify source path and count bytes/files.

    Returns (source_type, total_bytes, file_count, directory_count).
    Raises WorkspaceError for unsupported types.
    """
    if not path.exists():
        raise WorkspaceError("source path does not exist")

    # Resolve symlinks for classification
    try:
        real = path.resolve()
    except (OSError, RuntimeError):
        real = path

    # Check for symlink (we resolve first, then check if original was symlink)
    if path.is_symlink():
        raise WorkspaceError("source is a symlink; use aota_path_info to resolve first")

    if path.is_file():
        st = path.stat()
        return ("file", st.st_size, 1, 0)

    if path.is_dir():
        total_bytes = 0
        file_count = 0
        directory_count = 0
        for root_str, dirs, files in os.walk(str(path)):
            root = Path(root_str)
            directory_count += 1 if root != path else 0  # don't count root itself
            for fname in files:
                fp = root / fname
                try:
                    if fp.is_symlink():
                        # Count symlinks as 0 bytes for preflight
                        continue
                    st = fp.stat()
                    total_bytes += st.st_size
                    file_count += 1
                except OSError:
                    continue
            for dname in dirs:
                dp = root / dname
                if not dp.is_symlink():
                    directory_count += 1

        return ("directory", total_bytes, file_count, directory_count)

    # Check for special files
    mode = path.stat().st_mode
    import stat
    if stat.S_ISSOCK(mode):
        raise WorkspaceError("source is a socket; not copyable")
    if stat.S_ISFIFO(mode):
        raise WorkspaceError("source is a FIFO; not copyable")
    if stat.S_ISBLK(mode) or stat.S_ISCHR(mode):
        raise WorkspaceError("source is a device file; not copyable")

    raise WorkspaceError("source is not a regular file or directory")


def _check_free_space(dest_root: Path, needed_bytes: int) -> None:
    """Check that destination filesystem has enough free space."""
    try:
        usage = shutil.disk_usage(str(dest_root))
        free = usage.free
        if needed_bytes > free * 0.8:
            raise WorkspaceError(
                f"insufficient free space on destination filesystem: "
                f"need {needed_bytes} bytes, only {free} bytes free"
            )
    except OSError as e:
        raise WorkspaceError(f"failed to check disk space: {e}") from e


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def handle(args: dict, **_kwargs) -> str:
    workspace_id: str = args.get("workspace_id", "")
    source: str = args.get("source", "")
    destination: str = args.get("destination", "")
    overwrite: bool = args.get("overwrite", False)

    try:
        return _do_copy(workspace_id, source, destination, overwrite)
    except WorkspaceError as e:
        return json.dumps(
            {
                "workspace_id": workspace_id,
                "source": source,
                "destination": destination,
                "source_type": None,
                "bytes_copied": 0,
                "file_count": 0,
                "overwritten": False,
                "status": "rejected",
                "error": str(e),
                "partial_copy": False,
            },
            sort_keys=True,
        )
    except Exception as e:
        return json.dumps(
            {
                "workspace_id": workspace_id,
                "source": source,
                "destination": destination,
                "source_type": None,
                "bytes_copied": 0,
                "file_count": 0,
                "overwritten": False,
                "status": "failed",
                "error": str(e),
                "partial_copy": False,
            },
            sort_keys=True,
        )


def _do_copy(
    workspace_id: str,
    source: str,
    destination: str,
    overwrite: bool,
) -> str:
    # ------------------------------------------------------------------
    # Resolve source (must exist)
    # ------------------------------------------------------------------
    try:
        source_root, source_path = resolve_relative_path(workspace_id, source, must_exist=True)
    except WorkspaceError as e:
        raise WorkspaceError(f"source: {e}") from e

    # ------------------------------------------------------------------
    # Resolve destination (may not exist)
    # ------------------------------------------------------------------
    try:
        dest_root, dest_path = resolve_relative_path(workspace_id, destination, must_exist=False)
    except WorkspaceError as e:
        raise WorkspaceError(f"destination: {e}") from e

    # Source and destination must be in same workspace
    if source_root != dest_root:
        raise WorkspaceError("source and destination must be in the same workspace")

    # ------------------------------------------------------------------
    # Source classification
    # ------------------------------------------------------------------
    source_type, total_bytes, file_count, directory_count = _classify_source(source_path)

    # ------------------------------------------------------------------
    # Self-copy check
    # ------------------------------------------------------------------
    if source_path.resolve() == dest_path.resolve():
        raise WorkspaceError("source and destination are the same path; self-copy rejected")

    # ------------------------------------------------------------------
    # Subtree check (directory copy into own subtree)
    # ------------------------------------------------------------------
    if source_type == "directory":
        try:
            dest_path.resolve().relative_to(source_path.resolve())
            raise WorkspaceError(
                "destination is inside the source directory subtree; "
                "recursive self-copy rejected"
            )
        except ValueError:
            # Not inside — good
            pass

    # ------------------------------------------------------------------
    # Destination pre-checks
    # ------------------------------------------------------------------
    dest_exists = dest_path.exists()
    dest_is_dir = dest_path.is_dir() if dest_exists else False

    if dest_exists:
        if not overwrite:
            raise WorkspaceError(
                f"destination already exists: {destination} "
                "(set overwrite=true to overwrite)"
            )
        # overwrite=true: only files can be overwritten
        if dest_is_dir:
            raise WorkspaceError(
                f"destination is an existing directory: {destination}; "
                "overwrite not supported for directories"
            )
        # File overwrite is OK
    else:
        # Create parent directories if needed
        dest_path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Resource preflight
    # ------------------------------------------------------------------
    if file_count > MAX_FILE_COUNT:
        raise WorkspaceError(
            f"source has {file_count} files; maximum allowed is {MAX_FILE_COUNT}"
        )
    if total_bytes > MAX_TOTAL_BYTES:
        raise WorkspaceError(
            f"source total size {total_bytes} bytes exceeds maximum {MAX_TOTAL_BYTES}"
        )

    # Check free space on destination filesystem
    _check_free_space(dest_root, total_bytes)

    # ------------------------------------------------------------------
    # Perform copy
    # ------------------------------------------------------------------
    overwritten = dest_exists

    if source_type == "file":
        # Atomic file copy: copy to temp sibling, then atomic rename
        dest_dir = dest_path.parent
        fd, tmp_path_str = tempfile.mkstemp(dir=str(dest_dir), prefix=".aota_copy_")
        os.close(fd)
        tmp_path = Path(tmp_path_str)
        try:
            shutil.copy2(str(source_path), str(tmp_path))
            os.replace(tmp_path_str, str(dest_path))
        except (OSError, shutil.Error) as e:
            # Clean up temp file on failure
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise WorkspaceError(f"file copy failed: {e}") from e

        bytes_copied = total_bytes
        copied_file_count = 1

    else:
        # Directory copy
        dest_parent = dest_path.parent
        dest_name = dest_path.name
        try:
            shutil.copytree(
                str(source_path),
                str(dest_path),
                symlinks=True,
                dirs_exist_ok=False,  # Never overwrite existing directory
            )
        except FileExistsError:
            raise WorkspaceError(
                f"destination directory already exists: {destination}"
            ) from None
        except shutil.Error as e:
            raise WorkspaceError(f"directory copy failed: {e}") from e

        bytes_copied = total_bytes
        copied_file_count = file_count

    return json.dumps(
        {
            "workspace_id": workspace_id,
            "source": source,
            "destination": destination,
            "source_type": source_type,
            "bytes_copied": bytes_copied,
            "file_count": copied_file_count,
            "overwritten": overwritten,
            "status": "copied",
            "error": None,
            "partial_copy": False,
        },
        sort_keys=True,
    )
