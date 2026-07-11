"""aota_coder_report_submit — submit coder report via trusted context.

Coder-only tool: writes CARD.json (compact) + RESULT.md (full report) for
the currently running profile task. Task identity from trusted env vars,
NOT from model input. Profile gate ensures only coder workers can
use this tool.
"""

from __future__ import annotations

import datetime
import json
import os
import tempfile
from pathlib import Path

TOOL_NAME = "aota_coder_report_submit"
TOOLSET_NAME = "aota_coder_artifact"
ALLOWED_PROFILE = "coder"

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Submit a coder report for the currently running coder task. "
        "Writes CARD.json (compact) and RESULT.md (full report). "
        "Only available to coder profile workers. "
        "Task identity is obtained from trusted execution context, not from input."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "maxLength": 2000,
                "description": "Short summary of the coder task result",
            },
            "changed_paths": {
                "type": "array",
                "items": {"type": "string", "maxLength": 500},
                "maxItems": 50,
                "description": "List of files changed by the coder task",
            },
            "validation": {
                "type": "array",
                "items": {"type": "string", "maxLength": 500},
                "maxItems": 20,
                "description": "Validation steps performed and their results",
            },
            "risks": {
                "type": "array",
                "items": {"type": "string", "maxLength": 500},
                "maxItems": 10,
                "description": "Known risks or concerns with the changes",
            },
            "follow_up": {
                "type": "string",
                "maxLength": 500,
                "description": "Suggested next step or follow-up action",
            },
            "full_report": {
                "type": "string",
                "maxLength": 10000,
                "description": "Optional full report body (RESULT.md content)",
            },
        },
        "required": ["summary"],
        "additionalProperties": False,
    },
}


# ---------------------------------------------------------------------------
# Trusted context
# ---------------------------------------------------------------------------


def _trusted_context() -> tuple[str, str, str, str]:
    """Read task identity from trusted env vars."""
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


def _atomic_write(path: Path, content: str) -> None:
    """Atomically write text content to *path*."""
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
    """Handle aota_coder_report_submit call."""
    try:
        return _do_submit(args)
    except (PermissionError, ValueError, RuntimeError) as e:
        return json.dumps({"status": "rejected", "error": str(e)}, sort_keys=True)
    except Exception as e:
        return json.dumps({"status": "failed", "error": str(e)}, sort_keys=True)


def _do_submit(args: dict) -> str:
    # 1. Profile gate
    workspace_id, task_id, start_id, profile = _trusted_context()
    if profile != ALLOWED_PROFILE:
        raise PermissionError(
            f"profile_not_allowed: this tool is for '{ALLOWED_PROFILE}' profile, "
            f"current profile is '{profile}'"
        )

    # 2. Validate inputs
    summary: str = args.get("summary", "")
    changed_paths: list[str] = args.get("changed_paths", [])
    validation: list[str] = args.get("validation", [])
    risks: list[str] = args.get("risks", [])
    follow_up: str | None = args.get("follow_up")
    full_report: str | None = args.get("full_report")

    if not summary:
        raise ValueError("summary is required")

    # 3. Resolve task directory
    profile_task_root = os.environ.get(
        "AOTA_PROFILE_TASK_ROOT", "/aota-runtime/profile-tasks"
    )
    task_dir = Path(profile_task_root) / workspace_id / task_id

    if not task_dir.is_dir():
        raise RuntimeError(f"task directory not found: {task_dir}")

    # 4. Verify task is running
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
        raise RuntimeError("start_id mismatch")
    if execution.get("profile") != profile:
        raise RuntimeError("profile mismatch")

    # 5. Build CARD.json
    now = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    card = {
        "task_id": task_id,
        "start_id": start_id,
        "profile": profile,
        "summary": summary,
        "changed_paths": changed_paths,
        "validation": validation,
        "risks": risks,
        "follow_up": follow_up,
        "created_at": now,
        "source": "coder_report_tool",
    }

    # 6. Build RESULT.md body
    md_lines = [f"# Coder Report: {task_id}", ""]
    md_lines.append(f"**Profile:** {profile}")
    md_lines.append(f"**Created:** {now}")
    md_lines.append("")
    md_lines.append("## Summary")
    md_lines.append(summary)
    md_lines.append("")
    if changed_paths:
        md_lines.append("## Changed Paths")
        for item in changed_paths:
            md_lines.append(f"- {item}")
        md_lines.append("")
    if validation:
        md_lines.append("## Validation")
        for item in validation:
            md_lines.append(f"- {item}")
        md_lines.append("")
    if risks:
        md_lines.append("## Risks")
        for item in risks:
            md_lines.append(f"- {item}")
        md_lines.append("")
    if follow_up:
        md_lines.append("## Follow-Up")
        md_lines.append(follow_up)
        md_lines.append("")
    if full_report:
        md_lines.append("## Full Report")
        md_lines.append("")
        md_lines.append(full_report)
        md_lines.append("")

    # Write CARD.json
    card_path = task_dir / "CARD.json"
    _atomic_write_json(card_path, card)

    # Write RESULT.md (full artifact)
    md_path = task_dir / "RESULT.md"
    _atomic_write(md_path, "\n".join(md_lines))

    return json.dumps(
        {
            "status": "submitted",
            "card": str(card_path.name),
            "artifact": str(md_path.name),
        },
        sort_keys=True,
    )


def _atomic_write_json(path: Path, data: dict) -> None:
    """Atomically write a JSON dict."""
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
