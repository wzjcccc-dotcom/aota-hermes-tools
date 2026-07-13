"""Shared utilities for AOTA Durable Handoff (P8-D).

Provides: handoff ID generation, path resolution, atomic write/move,
lock helpers, role artifact mapping, and validation.

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
AOTA_PROFILE_TASK_ROOT = os.environ.get(
    "AOTA_PROFILE_TASK_ROOT", "/aota-runtime/profile-tasks"
)

HANDOFF_ROOT = Path(AOTA_RUNTIME_ROOT) / "handoffs"
PROFILE_TASK_ROOT = Path(AOTA_PROFILE_TASK_ROOT)

_HANDOFF_ID_RE = re.compile(r"^ho_\d{8}T\d{6}_[a-f0-9]{8}$")
_HANDOFF_ID_MAX_LENGTH = 64
_FORBIDDEN_CHARS = ("\x00", "/", "\\", " ")
_TERMINAL_STATUSES = frozenset(
    {"done", "failed", "needs_input", "cancelled", "timeout", "scope_violation"}
)

# Role artifact mapping for handoff schema
_ROLE_ARTIFACT_MAP: dict[str, dict[str, Optional[str]]] = {
    "coder": {
        "kind": "coder",
        "card_name": "CARD.json",
        "full_name": "RESULT.md",
    },
    "debugger": {
        "kind": "diagnosis",
        "card_name": "DIAGNOSIS_CARD.json",
        "full_name": "DIAGNOSIS.md",
    },
    "reviewer": {
        "kind": "review",
        "card_name": "REVIEW_CARD.json",
        "full_name": "REVIEW.md",
    },
    "architect": {
        "kind": "architecture",
        "card_name": "ARCHITECT_CARD.json",
        "full_name": "ARCHITECT_REVIEW.md",
    },
}


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def validate_handoff_id(handoff_id: str) -> Optional[str]:
    """Validate handoff_id format. Returns None if valid, error string otherwise."""
    if not handoff_id:
        return "handoff_id is empty"
    if len(handoff_id) > _HANDOFF_ID_MAX_LENGTH:
        return f"handoff_id exceeds {_HANDOFF_ID_MAX_LENGTH} characters"
    for ch in _FORBIDDEN_CHARS:
        if ch in handoff_id:
            return f"handoff_id contains forbidden character: {ch!r}"
    if ".." in handoff_id:
        return "handoff_id contains '..' segment"
    if not _HANDOFF_ID_RE.match(handoff_id):
        return (
            f"invalid handoff_id format: expected ho_<timestamp>_<random>, "
            f"got {handoff_id!r}"
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


def validate_task_id(task_id: str) -> Optional[str]:
    """Validate task_id format (pt_<timestamp>_<8hex>)."""
    import re as _re

    _TASK_ID_RE = _re.compile(r"^pt_\d{8}T\d{6}_[a-f0-9]{8}$")
    if not task_id:
        return "task_id is empty"
    if len(task_id) > _HANDOFF_ID_MAX_LENGTH:
        return f"task_id exceeds {_HANDOFF_ID_MAX_LENGTH} characters"
    for ch in _FORBIDDEN_CHARS:
        if ch in task_id:
            return f"task_id contains forbidden character: {ch!r}"
    if ".." in task_id:
        return "task_id contains '..' segment"
    if not _TASK_ID_RE.match(task_id):
        return (
            f"invalid task_id format: expected pt_<timestamp>_<random>, "
            f"got {task_id!r}"
        )
    return None


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------


def generate_handoff_id() -> str:
    """Generate a unique handoff ID: ho_<UTC_TIMESTAMP>_<8HEX>."""
    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    rand = secrets.token_hex(4)  # 8 hex chars
    return f"ho_{ts}_{rand}"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def get_handoff_pending_dir(workspace_id: str) -> Path:
    """Return the pending handoff directory for a workspace."""
    return HANDOFF_ROOT / workspace_id / "pending"


def get_handoff_ack_dir(workspace_id: str) -> Path:
    """Return the acknowledged handoff directory for a workspace."""
    return HANDOFF_ROOT / workspace_id / "acknowledged"


def get_handoff_lock_path(workspace_id: str, handoff_id: str) -> Path:
    """Return the filesystem path for the flock file of a handoff."""
    return HANDOFF_ROOT / workspace_id / f".{handoff_id}.lock"


def get_handoff_filename(handoff_id: str) -> str:
    """Return the filename for a handoff artifact."""
    return f"handoff.{handoff_id}.json"


def get_ack_filename(handoff_id: str) -> str:
    """Return the filename for a handoff ack artifact."""
    return f"handoff.{handoff_id}.ack.json"


def find_handoff_path(workspace_id: str, handoff_id: str) -> Optional[Path]:
    """Find a handoff by handoff_id, checking pending then acknowledged dirs.

    Returns the Path if found, None otherwise.
    """
    pending_dir = get_handoff_pending_dir(workspace_id)
    pending_path = pending_dir / get_handoff_filename(handoff_id)
    if pending_path.exists():
        return pending_path

    ack_dir = get_handoff_ack_dir(workspace_id)
    ack_path = ack_dir / get_handoff_filename(handoff_id)
    if ack_path.exists():
        return ack_path

    return None


# ---------------------------------------------------------------------------
# Find existing handoff by (workspace_id, task_id, start_id) binding
# ---------------------------------------------------------------------------


def find_existing_handoff(
    workspace_id: str, task_id: str, start_id: str
) -> Optional[str]:
    """Scan pending handoffs for an exact (workspace_id, task_id, start_id) binding.

    Returns handoff_id if found, None otherwise.
    """
    pending_dir = get_handoff_pending_dir(workspace_id)
    if not pending_dir.is_dir():
        return None

    try:
        for entry in os.listdir(str(pending_dir)):
            if not entry.startswith("handoff.") or not entry.endswith(".json"):
                continue
            # Strip prefix/suffix to get handoff_id
            # handoff.<handoff_id>.json
            handoff_id = entry[len("handoff.") : -len(".json")]
            if not _HANDOFF_ID_RE.match(handoff_id):
                continue
            try:
                data = read_handoff(pending_dir / entry)
            except (OSError, json.JSONDecodeError):
                continue
            if (
                data.get("task_id") == task_id
                and data.get("start_id") == start_id
                and data.get("workspace_id") == workspace_id
            ):
                return handoff_id
    except OSError:
        pass

    # Also scan acknowledged directory
    ack_dir = get_handoff_ack_dir(workspace_id)
    if ack_dir.is_dir():
        try:
            for entry in os.listdir(str(ack_dir)):
                if not entry.startswith("handoff.") or not entry.endswith(".json"):
                    continue
                handoff_id = entry[len("handoff.") : -len(".json")]
                if not _HANDOFF_ID_RE.match(handoff_id):
                    continue
                try:
                    data = read_handoff(ack_dir / entry)
                except (OSError, json.JSONDecodeError):
                    continue
                if (
                    data.get("task_id") == task_id
                    and data.get("start_id") == start_id
                    and data.get("workspace_id") == workspace_id
                ):
                    return handoff_id
        except OSError:
            pass

    return None


# ---------------------------------------------------------------------------
# Handoff data builder
# ---------------------------------------------------------------------------


def build_handoff_data(
    workspace_id: str,
    task_id: str,
    start_id: str,
    profile: str,
    task_kind: str,
    terminal_status: str,
    created_at: str,
    subject_task_id: Optional[str] = None,
    needs_input_reason: Optional[str] = None,
    task_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Build a handoff data dict per the P8-D handoff schema V1.

    Args:
        workspace_id: Workspace identifier.
        task_id: Task ID (pt_...).
        start_id: Start ID (pt_...).
        profile: Profile name (coder|debugger|reviewer).
        task_kind: Task kind (implementation|diagnosis|review).
        terminal_status: Terminal status (done|failed|needs_input|cancelled).
        created_at: ISO8601 timestamp of creation.
        subject_task_id: Subject task ID for reviewer tasks.
        needs_input_reason: Reason for needs_input status.
        task_dir: Task directory for checking artifact existence.

    Returns:
        Handoff dict per schema V1.
    """
    handoff_id = generate_handoff_id()

    # Resolve role artifact info
    role_info = _ROLE_ARTIFACT_MAP.get(profile, {})
    card_name: Optional[str] = role_info.get("card_name")
    full_name: Optional[str] = role_info.get("full_name")
    kind: Optional[str] = role_info.get("kind")

    card_exists = False
    full_exists = False
    if task_dir is not None and card_name:
        card_path = task_dir / card_name
        card_exists = card_path.is_file() and not card_path.is_symlink()
    if task_dir is not None and full_name:
        full_path = task_dir / full_name
        full_exists = full_path.is_file() and not full_path.is_symlink()

    # Check lifecycle artifacts
    outcome_artifact_exists = False
    receipt_exists = False
    if task_dir is not None:
        outcome_path = task_dir / f"worker-outcome.{start_id}.json"
        outcome_artifact_exists = outcome_path.is_file()
        receipt_path = task_dir / f"completion.{start_id}.json"
        receipt_exists = receipt_path.is_file()

    handoff: dict[str, Any] = {
        "handoff_id": handoff_id,
        "workspace_id": workspace_id,
        "task_id": task_id,
        "start_id": start_id,
        "profile": profile,
        "task_kind": task_kind,
        "terminal_status": terminal_status,
        "created_at": created_at,
        "role_artifact": {
            "kind": kind,
            "card_name": card_name,
            "full_name": full_name,
            "card_exists": card_exists,
            "full_exists": full_exists,
        },
        "lifecycle": {
            "outcome_artifact_exists": outcome_artifact_exists,
            "receipt_exists": receipt_exists,
        },
        "subject_task_id": subject_task_id,
        "needs_input_reason": needs_input_reason,
        "source": "profile_task_finalizer",
        "state": "pending",
    }

    return handoff


# ---------------------------------------------------------------------------
# Atomic I/O for handoff artifacts
# ---------------------------------------------------------------------------


def write_handoff_atomic(workspace_id: str, handoff_data: dict[str, Any]) -> Path:
    """Write a pending handoff atomically via temp+replace.

    Creates parent directories as needed.
    Returns the Path of the written handoff file.
    """
    handoff_id = handoff_data.get("handoff_id", "")
    pending_dir = get_handoff_pending_dir(workspace_id)
    pending_dir.mkdir(parents=True, exist_ok=True)

    filename = get_handoff_filename(handoff_id)
    file_path = pending_dir / filename
    if file_path.is_symlink():
        raise OSError(f"target exists as symlink, refusing to overwrite: {file_path}")
    content = json.dumps(handoff_data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    fd, tmp_path_str = tempfile.mkstemp(
        dir=str(pending_dir), prefix=f".{filename}.tmp_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path_str, str(file_path))
    except Exception:
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise

    return file_path


def read_handoff(path: Path) -> dict[str, Any]:
    """Read and return a handoff dict from a JSON file."""
    if path.is_symlink():
        raise OSError(f"handoff file is a symlink, rejecting: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


def write_ack_artifact(
    workspace_id: str,
    handoff_id: str,
    decision: str,
    note: Optional[str] = None,
    acknowledged_at: str = "",
) -> Path:
    """Write an ack artifact alongside the (already moved) handoff.

    The ack artifact is written to the same directory as the acknowledged handoff.
    Returns the Path of the written ack file.
    """
    ack_dir = get_handoff_ack_dir(workspace_id)

    ack_data: dict[str, Any] = {
        "handoff_id": handoff_id,
        "workspace_id": workspace_id,
        "acknowledged_at": acknowledged_at,
        "decision": decision,
        "source": "explicit_tool_invocation",
    }
    if note:
        ack_data["note"] = note

    filename = get_ack_filename(handoff_id)
    file_path = ack_dir / filename
    if file_path.is_symlink():
        raise OSError(f"target exists as symlink, refusing to overwrite: {file_path}")
    content = json.dumps(ack_data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    fd, tmp_path_str = tempfile.mkstemp(
        dir=str(ack_dir), prefix=f".{filename}.tmp_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path_str, str(file_path))
    except Exception:
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise

    return file_path


def move_handoff_to_ack(workspace_id: str, handoff_id: str) -> Optional[Path]:
    """Move a handoff from pending/ to acknowledged/ via os.replace.

    Returns the new Path in acknowledged/ on success, None if pending file
    doesn't exist.
    """
    pending_dir = get_handoff_pending_dir(workspace_id)
    ack_dir = get_handoff_ack_dir(workspace_id)
    ack_dir.mkdir(parents=True, exist_ok=True)

    pending_path = pending_dir / get_handoff_filename(handoff_id)
    if not pending_path.exists() or pending_path.is_symlink():
        return None

    ack_path = ack_dir / get_handoff_filename(handoff_id)
    os.replace(str(pending_path), str(ack_path))

    return ack_path


# ---------------------------------------------------------------------------
# Read ack artifact
# ---------------------------------------------------------------------------


def read_ack_artifact(workspace_id: str, handoff_id: str) -> Optional[dict[str, Any]]:
    """Read the ack artifact for a handoff, if it exists."""
    ack_dir = get_handoff_ack_dir(workspace_id)
    ack_path = ack_dir / get_ack_filename(handoff_id)
    if not ack_path.exists():
        return None
    return read_handoff(ack_path)


# ---------------------------------------------------------------------------
# Timestamp helper
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    """Return current UTC time in ISO 8601 format (Z suffix)."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# List pending handoffs with filters
# ---------------------------------------------------------------------------


def list_pending_handoffs(
    workspace_id: str,
    limit: int = 10,
    terminal_status: Optional[str] = None,
    profile: Optional[str] = None,
) -> list[dict[str, Any]]:
    """List pending handoffs sorted oldest-first, with optional filters.

    Args:
        workspace_id: Workspace to scan.
        limit: Max results (default 10, max 50).
        terminal_status: Optional filter by terminal_status.
        profile: Optional filter by profile.

    Returns:
        List of compact handoff dicts sorted by created_at (oldest first).
    """
    pending_dir = get_handoff_pending_dir(workspace_id)
    if not pending_dir.is_dir():
        return []

    handoffs: list[dict[str, Any]] = []
    try:
        for entry in os.listdir(str(pending_dir)):
            if not entry.startswith("handoff.") or not entry.endswith(".json"):
                continue
            handoff_id = entry[len("handoff.") : -len(".json")]
            if not _HANDOFF_ID_RE.match(handoff_id):
                continue
            try:
                data = read_handoff(pending_dir / entry)
            except (OSError, json.JSONDecodeError):
                continue

            # Apply filters
            if terminal_status and data.get("terminal_status") != terminal_status:
                continue
            if profile and data.get("profile") != profile:
                continue

            # Build compact entry
            role_art = data.get("role_artifact", {})
            compact: dict[str, Any] = {
                "handoff_id": data.get("handoff_id", ""),
                "task_id": data.get("task_id", ""),
                "profile": data.get("profile", ""),
                "task_kind": data.get("task_kind", ""),
                "terminal_status": data.get("terminal_status", ""),
                "created_at": data.get("created_at", ""),
                "card_kind": role_art.get("kind"),
                "card_exists": role_art.get("card_exists", False),
                "needs_input_reason": data.get("needs_input_reason"),
                "subject_task_id": data.get("subject_task_id"),
                "decision_exists": False,
                "decision": None,
                "decision_state": None,
            }
            # P8-E: check for orchestration decision
            try:
                from ._orchestration_common import get_decision_dir, read_decision
                dec_dir = get_decision_dir(workspace_id)
                if dec_dir.is_dir():
                    for dec_entry in os.listdir(str(dec_dir)):
                        if not dec_entry.startswith("DECISION.") or not dec_entry.endswith(".json"):
                            continue
                        try:
                            dec_data = read_decision(dec_dir / dec_entry)
                        except (OSError, json.JSONDecodeError):
                            continue
                        if dec_data.get("handoff_id") == data.get("handoff_id", ""):
                            compact["decision_exists"] = True
                            compact["decision"] = dec_data.get("decision")
                            compact["decision_state"] = dec_data.get("state")
                            break
            except Exception:
                pass
            handoffs.append(compact)
    except OSError:
        return []

    # Sort by created_at oldest-first (deterministic)
    handoffs.sort(key=lambda h: h.get("created_at", ""))

    # Apply limit
    effective_limit = max(1, min(limit, 50))
    return handoffs[:effective_limit]


# ---------------------------------------------------------------------------
# Lock helpers  (fcntl.flock, per-handoff locking)
# ---------------------------------------------------------------------------


def acquire_handoff_lock(
    workspace_id: str, handoff_id: str, timeout: float = 5.0
) -> int:
    """Acquire exclusive flock on a handoff with bounded timeout.

    Returns an open file descriptor the caller **must** release via
    :func:`release_handoff_lock`.

    Raises TimeoutError if lock cannot be acquired within *timeout* seconds.
    """
    lock_path = get_handoff_lock_path(workspace_id, handoff_id)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as e:
        raise RuntimeError(f"failed to open handoff lock file: {e}") from e

    start = time.monotonic()
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() - start >= timeout:
                os.close(fd)
                raise TimeoutError(
                    f"could not acquire handoff lock for {handoff_id} "
                    f"within {timeout}s"
                )
            time.sleep(0.1)
        except OSError as e:
            os.close(fd)
            raise RuntimeError(f"handoff flock error: {e}") from e


def release_handoff_lock(fd: int) -> None:
    """Release an acquired handoff flock and close the file descriptor."""
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except (OSError, ValueError):
        pass
    try:
        os.close(fd)
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------------------
# Read card content from task directory
# ---------------------------------------------------------------------------


def read_role_card_content(
    task_dir: Path, card_name: Optional[str]
) -> tuple[Optional[str], bool]:
    """Read a role card file from the task directory.

    Args:
        task_dir: Task directory Path.
        card_name: Card filename (CARD.json, DIAGNOSIS_CARD.json, etc.).

    Returns:
        Tuple of (card_content_string, card_missing_bool).
        - If card exists and is readable: (content, False)
        - If card missing: (None, True)
        - If card exists but can't be read: (None, True)
    """
    if not card_name:
        return None, True

    # Security: derive the allowlist from the single trusted role mapping.
    known_cards = {info["card_name"] for info in _ROLE_ARTIFACT_MAP.values()}
    if card_name not in known_cards:
        return None, True

    card_path = task_dir / card_name
    if card_path.is_symlink():
        return None, True
    if not card_path.exists() or not card_path.is_file():
        return None, True

    try:
        content = card_path.read_text("utf-8")
        return content, False
    except (OSError, UnicodeDecodeError):
        return None, True


def read_role_card_projection(
    task_dir: Path,
    profile: Optional[str],
    declared_card_name: Optional[str],
) -> tuple[Optional[str], bool, str]:
    """Read the card authorized for *profile* with a bounded reason."""
    role_info = _ROLE_ARTIFACT_MAP.get(profile or "")
    if not role_info:
        return None, True, "unrecognized_profile"
    expected_name = role_info["card_name"]
    if declared_card_name != expected_name:
        return None, True, "unrecognized_card"

    card_path = task_dir / expected_name
    if card_path.is_symlink():
        return None, True, "card_non_regular"
    if not card_path.exists():
        return None, True, "card_missing"
    if not card_path.is_file():
        return None, True, "card_non_regular"
    try:
        content = card_path.read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        return None, True, "card_unreadable"
    try:
        json.loads(content)
    except json.JSONDecodeError:
        return None, True, "card_parse_failed"
    return content, False, "ok"
