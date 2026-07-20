"""aota_reviewer_report_submit — submit review report via trusted context.

Reviewer-only tool: writes REVIEW_CARD.json + REVIEW.md for the currently
running review profile task. Task identity from trusted env vars.
Subject task ID is read from meta.json (not from model input).
"""

from __future__ import annotations

import datetime
import json
import os
import tempfile
from pathlib import Path
from ._active_task_context import ActiveTaskError, assert_artifact_target, load_active_task_context, validate_tool_args
from ._spec_contract import apply_common_card

TOOL_NAME = "aota_reviewer_report_submit"
TOOLSET_NAME = "aota_reviewer_artifact"
ALLOWED_PROFILE = "reviewer"

VALID_VERDICTS = {"pass", "fail", "inconclusive"}

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Submit a review report for the currently running reviewer task. "
        "Writes REVIEW_CARD.json (compact) and REVIEW.md (full report). "
        "Subject task ID is read from task meta.json, not from input. "
        "Only available to reviewer profile workers."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["pass", "fail", "inconclusive"],
                "description": "Review verdict",
            },
            "summary": {
                "type": "string",
                "maxLength": 2000,
                "description": "Short review summary",
            },
            "key_evidence": {
                "type": "array",
                "items": {"type": "string", "maxLength": 500},
                "maxItems": 20,
                "description": "Key evidence supporting the verdict",
            },
            "blocking_findings": {
                "type": "array",
                "items": {"type": "string", "maxLength": 500},
                "maxItems": 20,
                "description": "Blocking findings (if verdict is fail/inconclusive)",
            },
            "missing_evidence": {
                "type": "array",
                "items": {"type": "string", "maxLength": 500},
                "maxItems": 20,
                "description": "Missing or unavailable evidence",
            },
            "scope_findings": {
                "type": "array",
                "items": {"type": "string", "maxLength": 500},
                "maxItems": 10,
                "description": "Scope compliance findings",
            },
            "recommendation": {
                "type": "string",
                "maxLength": 500,
                "description": "Recommendation for task-main",
            },
            "full_report": {
                "type": "string",
                "maxLength": 10000,
                "description": "Optional full review body (REVIEW.md content)",
            },
        },
        "required": ["verdict"],
        "additionalProperties": False,
    },
}


# ---------------------------------------------------------------------------
# Trusted context
# ---------------------------------------------------------------------------


def _trusted_context() -> tuple[str, str, str, str]:
    """Read task identity from trusted env vars."""
    try:
        ctx = load_active_task_context()
    except ActiveTaskError as exc:
        raise PermissionError(exc.code) from exc
    return ctx.workspace_id, ctx.task_id, ctx.start_id, ctx.profile


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
    """Handle aota_reviewer_report_submit call."""
    try:
        validate_tool_args(args, SCHEMA)
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
    verdict: str = args.get("verdict", "")
    summary: str | None = args.get("summary")
    key_evidence: list[str] = args.get("key_evidence", [])
    blocking_findings: list[str] = args.get("blocking_findings", [])
    missing_evidence: list[str] = args.get("missing_evidence", [])
    scope_findings: list[str] = args.get("scope_findings", [])
    recommendation: str | None = args.get("recommendation")
    full_report: str | None = args.get("full_report")

    if verdict not in VALID_VERDICTS:
        raise ValueError(f"invalid verdict: {verdict!r}")

    # 3. Resolve task directory
    profile_task_root = os.environ.get(
        "AOTA_PROFILE_TASK_ROOT", "/aota-runtime/profile-tasks"
    )
    task_dir = Path(profile_task_root) / workspace_id / task_id

    if not task_dir.is_dir():
        raise RuntimeError(f"task directory not found: {task_dir}")

    # 4. Verify task is running and read subject_task_id from meta
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

    # Subject binding from meta, not from model input
    subject_task_id: str | None = meta.get("subject_task_id")

    # 5. Build REVIEW_CARD.json
    now = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    card = {
        "review_task_id": task_id,
        "subject_task_id": subject_task_id,
        "start_id": start_id,
        "profile": profile,
        "verdict": verdict,
        "summary": summary,
        "key_evidence": key_evidence,
        "blocking_findings": blocking_findings,
        "missing_evidence": missing_evidence,
        "scope_findings": scope_findings,
        "recommendation": recommendation,
        "created_at": now,
        "source": "reviewer_report_tool",
    }

    # 6. Build REVIEW.md body
    md_lines = [f"# Review Report: {task_id}", ""]
    md_lines.append(f"**Profile:** {profile}")
    md_lines.append(f"**Verdict:** {verdict}")
    if subject_task_id:
        md_lines.append(f"**Subject Task:** {subject_task_id}")
    md_lines.append(f"**Created:** {now}")
    md_lines.append("")
    if summary:
        md_lines.append("## Summary")
        md_lines.append(summary)
        md_lines.append("")
    if key_evidence:
        md_lines.append("## Key Evidence")
        for item in key_evidence:
            md_lines.append(f"- {item}")
        md_lines.append("")
    if blocking_findings:
        md_lines.append("## Blocking Findings")
        for item in blocking_findings:
            md_lines.append(f"- {item}")
        md_lines.append("")
    if missing_evidence:
        md_lines.append("## Missing Evidence")
        for item in missing_evidence:
            md_lines.append(f"- {item}")
        md_lines.append("")
    if scope_findings:
        md_lines.append("## Scope Findings")
        for item in scope_findings:
            md_lines.append(f"- {item}")
        md_lines.append("")
    if recommendation:
        md_lines.append("## Recommendation")
        md_lines.append(recommendation)
        md_lines.append("")
    if full_report:
        md_lines.append("## Full Report")
        md_lines.append("")
        md_lines.append(full_report)
        md_lines.append("")

    # The role-local report vocabulary predates the shared WI-09C card
    # envelope.  Preserve the detailed ``fail`` report verdict in REVIEW.md,
    # but expose its canonical task-main action as ``needs_fix`` on the Card.
    # Without this mapping, a failed review is silently downgraded to
    # ``not_applicable`` by the shared envelope validator.
    if verdict == "fail":
        card["verdict"] = "needs_fix"
    card = apply_common_card(meta, "reviewer", card, "REVIEW.md")
    # Write REVIEW_CARD.json
    card_path = assert_artifact_target(task_dir, "REVIEW_CARD.json", allowed={"REVIEW_CARD.json", "REVIEW.md"})
    md_path = assert_artifact_target(task_dir, "REVIEW.md", allowed={"REVIEW_CARD.json", "REVIEW.md"})
    _atomic_write_json(card_path, card)

    # Write REVIEW.md
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
