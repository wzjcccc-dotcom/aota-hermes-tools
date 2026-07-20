"""aota_architect_report_submit — submit architecture review report via trusted context.

Architect-only tool: writes ARCHITECT_CARD.json + ARCHITECT_REVIEW.md for the
currently running architecture profile task. Task identity from trusted env vars.
Subject task ID and architecture mode are read from meta.json (not from model input).
"""

from __future__ import annotations

import datetime
import json
import os
import tempfile
from pathlib import Path
from ._active_task_context import ActiveTaskError, assert_artifact_target, load_active_task_context, validate_tool_args
from ._spec_contract import apply_common_card

TOOL_NAME = "aota_architect_report_submit"
TOOLSET_NAME = "aota_architect_artifact"
ALLOWED_PROFILE = "architect"

VALID_VERDICTS = {"approve", "approve_with_changes", "block", "inconclusive"}
VALID_MODES = {"design_review", "spec_preflight"}

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Submit an architecture review report for the currently running architect task. "
        "Writes ARCHITECT_CARD.json (compact) and ARCHITECT_REVIEW.md (full report). "
        "Subject task ID and architecture mode are read from task meta.json, not from input. "
        "Only available to architect profile workers."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["design_review", "spec_preflight"],
                "description": "Architecture review mode",
            },
            "verdict": {
                "type": "string",
                "enum": ["approve", "approve_with_changes", "block", "inconclusive"],
                "description": "Architecture review verdict",
            },
            "summary": {
                "type": "string",
                "maxLength": 2000,
                "description": "Short architecture review summary",
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
                "description": "Blocking findings (if verdict is block or approve_with_changes)",
            },
            "required_changes": {
                "type": "array",
                "items": {"type": "string", "maxLength": 500},
                "maxItems": 20,
                "description": "Required changes before proceeding",
            },
            "optional_improvements": {
                "type": "array",
                "items": {"type": "string", "maxLength": 500},
                "maxItems": 20,
                "description": "Optional improvements or suggestions",
            },
            "missing_inputs": {
                "type": "array",
                "items": {"type": "string", "maxLength": 500},
                "maxItems": 20,
                "description": "Missing or unavailable inputs needed for review",
            },
            "risk_findings": {
                "type": "array",
                "items": {"type": "string", "maxLength": 500},
                "maxItems": 20,
                "description": "Risk-related findings (blast radius, rollback, compatibility)",
            },
            "validation_findings": {
                "type": "array",
                "items": {"type": "string", "maxLength": 500},
                "maxItems": 20,
                "description": "Validation or spec-quality findings",
            },
            "residual_risks": {
                "type": "array",
                "items": {"type": "string", "maxLength": 500},
                "maxItems": 20,
                "description": "Residual risks that remain after the review",
            },
            "recommendation": {
                "type": "string",
                "maxLength": 500,
                "description": "Overall recommendation for the orchestrator",
            },
            "full_report": {
                "type": "string",
                "maxLength": 10000,
                "description": "Optional full architecture review body (ARCHITECT_REVIEW.md content)",
            },
        },
        "required": ["mode", "verdict"],
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
    """Handle aota_architect_report_submit call."""
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
    mode: str = args.get("mode", "")
    verdict: str = args.get("verdict", "")
    summary: str | None = args.get("summary")
    key_evidence: list[str] = args.get("key_evidence", [])
    blocking_findings: list[str] = args.get("blocking_findings", [])
    required_changes: list[str] = args.get("required_changes", [])
    optional_improvements: list[str] = args.get("optional_improvements", [])
    missing_inputs: list[str] = args.get("missing_inputs", [])
    risk_findings: list[str] = args.get("risk_findings", [])
    validation_findings: list[str] = args.get("validation_findings", [])
    residual_risks: list[str] = args.get("residual_risks", [])
    recommendation: str | None = args.get("recommendation")
    full_report: str | None = args.get("full_report")

    if mode not in VALID_MODES:
        raise ValueError(
            f"invalid mode: {mode!r}, expected one of {sorted(VALID_MODES)}"
        )
    if verdict not in VALID_VERDICTS:
        raise ValueError(
            f"invalid verdict: {verdict!r}, expected one of {sorted(VALID_VERDICTS)}"
        )

    # 3. Resolve task directory
    profile_task_root = os.environ.get(
        "AOTA_PROFILE_TASK_ROOT", "/aota-runtime/profile-tasks"
    )
    task_dir = Path(profile_task_root) / workspace_id / task_id

    if not task_dir.is_dir():
        raise RuntimeError(f"task directory not found: {task_dir}")

    # 4. Verify task is running and read subject_task_id / architecture_mode from meta
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

    # Subject binding and architecture mode from meta, not from model input
    subject_task_id: str | None = meta.get("subject_task_id")
    architecture_mode: str | None = meta.get("architecture_mode")

    # P11-J.1-A: For spec_preflight, verify subject SPEC still matches binding
    stale_binding = False
    if architecture_mode == "spec_preflight":
        bound_revision = meta.get("subject_spec_revision")
        bound_sha256 = meta.get("subject_spec_sha256")
        if bound_revision is not None and bound_sha256:
            subj_id = meta.get("subject_task_id")
            if subj_id:
                subject_dir = Path(profile_task_root) / workspace_id / subj_id
                subject_meta_path = subject_dir / "meta.json"
                if subject_meta_path.exists():
                    with open(subject_meta_path, "r", encoding="utf-8") as f:
                        subject_meta = json.load(f)
                    current_revision = subject_meta.get("revision")
                    current_sha256 = subject_meta.get("spec_sha256")
                    if current_revision != bound_revision or current_sha256 != bound_sha256:
                        # Subject SPEC changed during review — still submit but mark stale
                        stale_binding = True

    # 5. Build ARCHITECT_CARD.json
    now = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    card = {
        "architect_task_id": task_id,
        "subject_task_id": subject_task_id,
        "architecture_mode": architecture_mode,
        "subject_spec_revision": meta.get("subject_spec_revision"),
        "subject_spec_sha256": meta.get("subject_spec_sha256"),
        "stale_binding": stale_binding if stale_binding else None,
        "start_id": start_id,
        "profile": profile,
        "mode": mode,
        "verdict": verdict,
        "summary": summary,
        "key_evidence": key_evidence,
        "blocking_findings": blocking_findings,
        "required_changes": required_changes,
        "optional_improvements": optional_improvements,
        "missing_inputs": missing_inputs,
        "risk_findings": risk_findings,
        "validation_findings": validation_findings,
        "residual_risks": residual_risks,
        "recommendation": recommendation,
        "created_at": now,
        "source": "architect_report_tool",
    }

    # 6. Build ARCHITECT_REVIEW.md body
    md_lines = [f"# Architecture Review Report: {task_id}", ""]
    md_lines.append(f"**Profile:** {profile}")
    md_lines.append(f"**Mode:** {mode}")
    md_lines.append(f"**Verdict:** {verdict}")
    if subject_task_id:
        md_lines.append(f"**Subject Task:** {subject_task_id}")
    if architecture_mode:
        md_lines.append(f"**Architecture Mode:** {architecture_mode}")
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
    if required_changes:
        md_lines.append("## Required Changes")
        for item in required_changes:
            md_lines.append(f"- {item}")
        md_lines.append("")
    if optional_improvements:
        md_lines.append("## Optional Improvements")
        for item in optional_improvements:
            md_lines.append(f"- {item}")
        md_lines.append("")
    if missing_inputs:
        md_lines.append("## Missing Inputs")
        for item in missing_inputs:
            md_lines.append(f"- {item}")
        md_lines.append("")
    if risk_findings:
        md_lines.append("## Risk Findings")
        for item in risk_findings:
            md_lines.append(f"- {item}")
        md_lines.append("")
    if validation_findings:
        md_lines.append("## Validation Findings")
        for item in validation_findings:
            md_lines.append(f"- {item}")
        md_lines.append("")
    if residual_risks:
        md_lines.append("## Residual Risks")
        for item in residual_risks:
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

    card = apply_common_card(meta, "architect", card, "ARCHITECT_REVIEW.md")
    # Write ARCHITECT_CARD.json
    card_path = assert_artifact_target(task_dir, "ARCHITECT_CARD.json", allowed={"ARCHITECT_CARD.json", "ARCHITECT_REVIEW.md"})
    md_path = assert_artifact_target(task_dir, "ARCHITECT_REVIEW.md", allowed={"ARCHITECT_CARD.json", "ARCHITECT_REVIEW.md"})
    _atomic_write_json(card_path, card)

    # Write ARCHITECT_REVIEW.md
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
