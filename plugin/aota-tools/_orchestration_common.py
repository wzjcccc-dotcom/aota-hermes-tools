"""Shared utilities for AOTA Durable Orchestration Decision artifacts (P8-E).

Provides: decision ID generation, path resolution, atomic write/move,
lock helpers, validation, and constants.

stdlib only. No third-party dependencies.
"""

from __future__ import annotations

import datetime
import fcntl
import json
import os
import re
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AOTA_RUNTIME_ROOT = os.environ.get("AOTA_RUNTIME_ROOT", "/aota-runtime")

DECISION_ROOT = Path(AOTA_RUNTIME_ROOT) / "decisions"

_DECISION_ID_RE = re.compile(r"^od_\d{8}T\d{6}_[a-f0-9]{8}$")
_DECISION_ID_MAX_LENGTH = 64
_FORBIDDEN_CHARS = ("\x00", "/", "\\", " ")

_DECISION_DECISIONS = frozenset(
    {
        "accepted",
        "needs_followup",
        "needs_user_input",
        "review_required",
        "reopen_required",
        "no_action",
    }
)

_FOLLOWUP_TASK_KINDS = ("implementation", "diagnosis", "review")
_REASON_MAX_LENGTH = 4000
_USER_INPUT_SUMMARY_MAX_LENGTH = 4000


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def validate_decision_id(decision_id: str) -> Optional[str]:
    """Validate decision_id format. Returns None if valid, error string otherwise."""
    if not decision_id:
        return "decision_id is empty"
    if len(decision_id) > _DECISION_ID_MAX_LENGTH:
        return f"decision_id exceeds {_DECISION_ID_MAX_LENGTH} characters"
    for ch in _FORBIDDEN_CHARS:
        if ch in decision_id:
            return f"decision_id contains forbidden character: {ch!r}"
    if ".." in decision_id:
        return "decision_id contains '..' segment"
    if not _DECISION_ID_RE.match(decision_id):
        return (
            f"invalid decision_id format: expected od_<timestamp>_<random>, "
            f"got {decision_id!r}"
        )
    return None


def validate_workspace_id(workspace_id: str) -> Optional[str]:
    """Validate workspace_id (no path traversal, no NUL)."""
    if not workspace_id:
        return "workspace_id is empty"
    if len(workspace_id) > 128:
        return "workspace_id exceeds 128 characters"
    for ch in _FORBIDDEN_CHARS:
        if ch in workspace_id:
            return f"workspace_id contains forbidden character: {ch!r}"
    if ".." in workspace_id:
        return "workspace_id contains '..' segment"
    return None


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------


def generate_decision_id() -> str:
    """Generate a unique decision ID: od_<UTC_TIMESTAMP>_<8HEX>."""
    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    rand = secrets.token_hex(4)  # 8 hex chars
    return f"od_{ts}_{rand}"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def get_decision_dir(workspace_id: str) -> Path:
    """Return the decision directory for a workspace."""
    return DECISION_ROOT / workspace_id


def get_decision_filename(decision_id: str) -> str:
    """Return the filename for a decision artifact."""
    return f"DECISION.{decision_id}.json"


def get_decision_lock_path(workspace_id: str, decision_id: str) -> Path:
    """Return the filesystem path for the flock file of a decision."""
    return DECISION_ROOT / workspace_id / f".{decision_id}.lock"


def find_decision_path(workspace_id: str, decision_id: str) -> Optional[Path]:
    """Find a decision artifact by decision_id.

    Returns the Path if found, None otherwise.
    """
    decision_dir = get_decision_dir(workspace_id)
    decision_path = decision_dir / get_decision_filename(decision_id)
    if decision_path.exists():
        return decision_path
    return None


# ---------------------------------------------------------------------------
# Timestamp helper
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    """Return current UTC time in ISO 8601 format (Z suffix)."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Atomic I/O for decision artifacts
# ---------------------------------------------------------------------------


def atomic_write_json(path: Path, data: dict) -> Path:
    """Write a JSON dict to *path* atomically via temp+replace.

    Creates parent directories as needed.
    Returns the Path of the written file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise OSError(f"target exists as symlink, refusing to overwrite: {path}")
    content = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    fd, tmp_path_str = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.tmp_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path_str, str(path))
    except Exception:
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise

    return path


def read_decision(path: Path) -> dict:
    """Read and return a decision dict from a JSON file."""
    if path.is_symlink():
        raise OSError(f"decision file is a symlink, rejecting: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Lock helpers  (fcntl.flock, per-decision locking)
# ---------------------------------------------------------------------------


def acquire_decision_lock(
    workspace_id: str, decision_id: str, timeout: float = 5.0
) -> int:
    """Acquire exclusive flock on a decision with bounded timeout.

    Returns an open file descriptor the caller **must** release via
    :func:`release_decision_lock`.

    Raises TimeoutError if lock cannot be acquired within *timeout* seconds.
    """
    lock_path = get_decision_lock_path(workspace_id, decision_id)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as e:
        raise RuntimeError(f"failed to open decision lock file: {e}") from e

    start = time.monotonic()
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() - start >= timeout:
                os.close(fd)
                raise TimeoutError(
                    f"could not acquire decision lock for {decision_id} "
                    f"within {timeout}s"
                )
            time.sleep(0.1)
        except OSError as e:
            os.close(fd)
            raise RuntimeError(f"decision flock error: {e}") from e


def release_decision_lock(fd: int) -> None:
    """Release an acquired decision flock and close the file descriptor."""
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except (OSError, ValueError):
        pass
    try:
        os.close(fd)
    except (OSError, ValueError):
        pass
