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

from ._active_task_context import ActiveTaskError, assert_artifact_target, load_active_task_context, validate_tool_args

TOOL_NAME = "aota_worker_outcome_submit"
TOOLSET_NAME = "aota_worker_outcome"

VALID_OUTCOMES = {"completed", "partial", "failed", "needs_input"}

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
                "enum": ["completed", "partial", "failed", "needs_input"],
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
    try:
        ctx = load_active_task_context()
    except ActiveTaskError as exc:
        raise PermissionError(exc.code) from exc
    return ctx.workspace_id, ctx.task_id, ctx.start_id, ctx.profile


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
        validate_tool_args(args, SCHEMA)
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

    if profile == "project-steward":
        if meta.get("task_kind") != "stewardship":
            raise RuntimeError("steward profile requires stewardship task kind")
        if outcome in {"completed", "partial"}:
            card_path = task_dir / "STEWARD_CARD.json"
            report_path = task_dir / "STEWARD_RESULT.md"
            if not card_path.is_file() or card_path.is_symlink() or not report_path.is_file() or report_path.is_symlink():
                raise RuntimeError("steward_report_artifacts_missing")
            try:
                card = json.loads(card_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("steward_card_invalid") from exc
            expected_card_hash = meta.get("spec_hash") if meta.get("contract_version") == 1 else meta.get("spec_sha256")
            if (card.get("role") != "project-steward" or card.get("task_id") != task_id or card.get("spec_id") != task_id or card.get("spec_revision") != meta.get("revision") or card.get("spec_hash") != expected_card_hash or card.get("outcome") != outcome):
                raise RuntimeError("steward_card_binding_mismatch")

    # 5. Build outcome artifact path
    outcome_filename = f"worker-outcome.{start_id}.json"
    outcome_path = assert_artifact_target(task_dir, outcome_filename, allowed={outcome_filename})

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
