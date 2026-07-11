"""aota_debugger_report_submit — submit diagnosis report via trusted context.

Debugger-only tool: submits DIAGNOSIS_CARD.json + DIAGNOSIS.md for the
currently running profile task. Task identity from trusted env vars,
NOT from model input. Profile gate ensures only debugger workers can
use this tool.
"""

from __future__ import annotations

import datetime
import json
import os
import tempfile
from pathlib import Path

TOOL_NAME = "aota_debugger_report_submit"
TOOLSET_NAME = "aota_debugger_artifact"
ALLOWED_PROFILE = "debugger"

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Submit a diagnosis report for the currently running debugger task. "
        "Writes DIAGNOSIS_CARD.json (compact) and DIAGNOSIS.md (full report). "
        "Only available to debugger profile workers. "
        "Task identity is obtained from trusted execution context, not from input."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "maxLength": 2000,
                "description": "Short diagnosis summary",
            },
            "root_cause": {
                "type": "string",
                "maxLength": 2000,
                "description": "Identified root cause",
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "Confidence in diagnosis (0.0–1.0)",
            },
            "key_evidence": {
                "type": "array",
                "items": {"type": "string", "maxLength": 500},
                "maxItems": 20,
                "description": "Key evidence supporting the diagnosis",
            },
            "open_questions": {
                "type": "array",
                "items": {"type": "string", "maxLength": 500},
                "maxItems": 10,
                "description": "Open/unresolved questions",
            },
            "missing_inputs": {
                "type": "array",
                "items": {"type": "string", "maxLength": 200},
                "maxItems": 10,
                "description": "Missing inputs required for diagnosis",
            },
            "recommended_next_action": {
                "type": "string",
                "maxLength": 500,
                "description": "Recommended next action (for task-main)",
            },
            "full_report": {
                "type": "string",
                "maxLength": 10000,
                "description": "Optional full diagnosis report (DIAGNOSIS.md body)",
            },
        },
        "required": ["summary", "confidence"],
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
    """Handle aota_debugger_report_submit call."""
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
    root_cause: str | None = args.get("root_cause")
    confidence: float = args.get("confidence", 0.0)
    key_evidence: list[str] = args.get("key_evidence", [])
    open_questions: list[str] = args.get("open_questions", [])
    missing_inputs: list[str] = args.get("missing_inputs", [])
    recommended_next_action: str | None = args.get("recommended_next_action")
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

    # 4. Verify task is running (same checks as outcome tool)
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

    # 5. Build DIAGNOSIS_CARD.json
    now = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    card = {
        "task_id": task_id,
        "start_id": start_id,
        "profile": profile,
        "summary": summary,
        "root_cause": root_cause,
        "confidence": confidence,
        "key_evidence": key_evidence,
        "open_questions": open_questions,
        "missing_inputs": missing_inputs,
        "recommended_next_action": recommended_next_action,
        "created_at": now,
        "source": "debugger_report_tool",
    }

    # 6. Build DIAGNOSIS.md body
    md_lines = [f"# Diagnosis Report: {task_id}", ""]
    md_lines.append(f"**Profile:** {profile}")
    md_lines.append(f"**Created:** {now}")
    md_lines.append("")
    md_lines.append("## Summary")
    md_lines.append(summary)
    md_lines.append("")
    if root_cause:
        md_lines.append("## Root Cause")
        md_lines.append(root_cause)
        md_lines.append("")
    md_lines.append(f"**Confidence:** {confidence}")
    md_lines.append("")
    if key_evidence:
        md_lines.append("## Key Evidence")
        for item in key_evidence:
            md_lines.append(f"- {item}")
        md_lines.append("")
    if open_questions:
        md_lines.append("## Open Questions")
        for item in open_questions:
            md_lines.append(f"- {item}")
        md_lines.append("")
    if missing_inputs:
        md_lines.append("## Missing Inputs")
        for item in missing_inputs:
            md_lines.append(f"- {item}")
        md_lines.append("")
    if recommended_next_action:
        md_lines.append("## Recommended Next Action")
        md_lines.append(recommended_next_action)
        md_lines.append("")
    if full_report:
        md_lines.append("## Full Report")
        md_lines.append("")
        md_lines.append(full_report)
        md_lines.append("")

    # Write DIAGNOSIS_CARD.json
    card_path = task_dir / "DIAGNOSIS_CARD.json"
    _atomic_write_json(card_path, card)

    # Write DIAGNOSIS.md (full artifact)
    md_path = task_dir / "DIAGNOSIS.md"
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
