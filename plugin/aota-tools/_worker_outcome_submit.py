"""aota_worker_outcome_submit — submit terminal outcome via trusted context.

Profile Task worker completion contract tool for read-only workers.
The tool reads identity from trusted env vars (AOTA_PROFILE_TASK_*),
NOT from model input.
"""

from __future__ import annotations

import datetime
import json
import os
import tempfile
from pathlib import Path

TOOL_NAME = "aota_worker_outcome_submit"
TOOLSET_NAME = "aota_worker_outcome"

VALID_OUTCOMES = {"completed", "failed", "needs_input"}

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Submit terminal outcome for the currently running Profile Task. "
        "Task identity is obtained from trusted execution context, not from input. "
        "Only allowed for running tasks. Idempotent for same outcome. "
        "Conflicting second outcome is rejected."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "outcome": {
                "type": "string",
                "enum": ["completed", "failed", "needs_input"],
                "description": "Terminal outcome",
            },
            "reason": {
                "type": "string",
                "maxLength": 200,
                "description": "Optional reason (required if outcome=needs_input)",
            },
        },
        "required": ["outcome"],
        "additionalProperties": False,
    },
}


# ---------------------------------------------------------------------------
# Trusted execution context
# ---------------------------------------------------------------------------


def _trusted_context() -> tuple[str, str, str, str]:
    """Read task identity from trusted env vars set by _profile_task_start.py.

    Returns (workspace_id, task_id, start_id, profile).
    Raises PermissionError if any var is missing/empty.
    """
    workspace_id = os.environ.get("AOTA_PROFILE_TASK_WORKSPACE_ID", "")
    task_id = os.environ.get("AOTA_PROFILE_TASK_ID", "")
    start_id = os.environ.get("AOTA_PROFILE_TASK_START_ID", "")
    profile = os.environ.get("AOTA_PROFILE_TASK_PROFILE", "")
    if not all([workspace_id, task_id, start_id, profile]):
        raise PermissionError(
            "missing trusted execution context (AOTA_PROFILE_TASK_* env vars)"
        )
    return workspace_id, task_id, start_id, profile


# ---------------------------------------------------------------------------
# Atomic file I/O
# ---------------------------------------------------------------------------


def _atomic_write_json(path: Path, data: dict) -> None:
    """Atomically write a JSON dict to *path* via temp sibling + os.replace."""
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


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def handle(args: dict, **_kwargs) -> str:
    """Handle aota_worker_outcome_submit call."""
    try:
        return _do_submit(args)
    except (PermissionError, ValueError, RuntimeError) as e:
        return json.dumps({"status": "rejected", "error": str(e)}, sort_keys=True)
    except Exception as e:
        return json.dumps({"status": "failed", "error": str(e)}, sort_keys=True)


def _do_submit(args: dict) -> str:
    # 1. Validate model input
    outcome: str = args.get("outcome", "")
    reason: str | None = args.get("reason")

    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"invalid outcome: {outcome!r}")
    if outcome == "needs_input" and not reason:
        raise ValueError("reason is required when outcome=needs_input")
    if reason and len(reason) > 200:
        raise ValueError(f"reason exceeds 200 characters")

    # 2. Read trusted context (model CANNOT supply these)
    workspace_id, task_id, start_id, profile = _trusted_context()

    # 3. Resolve task directory
    profile_task_root = os.environ.get(
        "AOTA_PROFILE_TASK_ROOT", "/aota-runtime/profile-tasks"
    )
    task_dir = Path(profile_task_root) / workspace_id / task_id

    if not task_dir.is_dir():
        raise RuntimeError(f"task directory not found: {task_dir}")

    # 4. Verify meta.json exists and task is running
    meta_path = task_dir / "meta.json"
    if not meta_path.exists():
        raise RuntimeError("meta.json not found in task directory")

    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    if meta.get("status") != "running":
        raise RuntimeError(
            f"task status is '{meta.get('status')}', expected 'running'"
        )

    execution = meta.get("execution", {})
    if execution.get("start_id") != start_id:
        raise RuntimeError("start_id mismatch with execution context")
    if execution.get("profile") != profile:
        raise RuntimeError("profile mismatch with execution context")

    # 5. Build outcome artifact path
    outcome_filename = f"worker-outcome.{start_id}.json"
    outcome_path = task_dir / outcome_filename

    # 6. Prepare payload
    now = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    payload = {
        "workspace_id": workspace_id,
        "task_id": task_id,
        "start_id": start_id,
        "profile": profile,
        "outcome": outcome,
        "reason": reason,
        "submitted_at": now,
        "source": "worker_outcome_tool",
    }

    # 7. Idempotency / conflict check
    if outcome_path.exists():
        existing = json.loads(outcome_path.read_text("utf-8"))
        if existing.get("outcome") == outcome:
            return json.dumps(
                {"status": "idempotent", "outcome": outcome, "reason": reason},
                sort_keys=True,
            )
        else:
            raise RuntimeError(
                f"conflicting outcome: existing='{existing.get('outcome')}', "
                f"submitted='{outcome}'"
            )

    # 8. Atomic write
    _atomic_write_json(outcome_path, payload)

    return json.dumps(
        {"status": "submitted", "outcome": outcome, "reason": reason},
        sort_keys=True,
    )
