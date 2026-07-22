"""Read-only, bounded access to the bound subject task's public artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ._active_task_context import (
    ActiveTaskError,
    MAX_ARTIFACT_BYTES,
    assert_artifact_target,
    load_active_task_context,
    read_bounded_text,
)


TOOL_NAME = "aota_subject_task_artifact_open"
TOOLSET_NAME = "aota_active_task_context"
_SUBJECT_ID = re.compile(r"^pt_[0-9]{8}T[0-9]{6}_[0-9a-f]{8}$")
_ARTIFACTS = ("SPEC.md", "scope.json", "meta.json")
_PROFILES = {"architect", "reviewer", "task-main"}

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Read one bounded, allowlisted artifact from the trusted subject task "
        "bound to the active Architect, Reviewer, or task-main task. No path, "
        "glob, workspace override, or root override is accepted."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "subject_task_id": {"type": "string", "pattern": _SUBJECT_ID.pattern, "maxLength": 32},
            "artifact_name": {"type": "string", "enum": list(_ARTIFACTS)},
            "max_bytes": {"type": "integer", "minimum": 1, "maximum": MAX_ARTIFACT_BYTES},
        },
        "required": ["subject_task_id", "artifact_name"],
        "additionalProperties": False,
    },
}


def _error(code: str) -> str:
    return json.dumps({"status": "rejected", "error_code": code}, sort_keys=True)


def _subject_dir(ctx: Any, subject_task_id: str) -> Path:
    if ctx.profile not in _PROFILES:
        raise ActiveTaskError("subject_artifact_not_allowed")
    if not isinstance(subject_task_id, str) or not _SUBJECT_ID.fullmatch(subject_task_id):
        raise ActiveTaskError("subject_task_id_invalid")
    if ctx.meta.get("subject_task_id") != subject_task_id or subject_task_id == ctx.task_id:
        raise ActiveTaskError("subject_binding_required")
    subject_dir = ctx.root / ctx.workspace_id / subject_task_id
    if subject_dir.is_symlink():
        raise ActiveTaskError("active_task_binding_invalid")
    try:
        root = ctx.root.resolve(strict=True)
        canonical = subject_dir.resolve(strict=True)
        canonical.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ActiveTaskError("active_task_binding_invalid") from exc
    if canonical != subject_dir or not subject_dir.is_dir():
        raise ActiveTaskError("active_task_binding_invalid")
    return subject_dir


def _open(args: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(args, dict) or set(args) - {"subject_task_id", "artifact_name", "max_bytes"}:
        raise ActiveTaskError("subject_artifact_not_allowed")
    if args.get("artifact_name") not in _ARTIFACTS:
        raise ActiveTaskError("subject_artifact_not_allowed")
    requested = args.get("max_bytes", MAX_ARTIFACT_BYTES)
    if not isinstance(requested, int) or isinstance(requested, bool) or not 1 <= requested <= MAX_ARTIFACT_BYTES:
        raise ActiveTaskError("subject_artifact_too_large")
    ctx = load_active_task_context()
    subject_dir = _subject_dir(ctx, args.get("subject_task_id"))
    target = assert_artifact_target(subject_dir, args["artifact_name"], allowed=set(_ARTIFACTS))
    text, digest, size, lines = read_bounded_text(target)
    if size > requested:
        raise ActiveTaskError("subject_artifact_too_large")
    content: Any = text
    if args["artifact_name"] in {"scope.json", "meta.json"}:
        try:
            content = json.loads(text)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ActiveTaskError("subject_artifact_invalid") from exc
        if not isinstance(content, dict):
            raise ActiveTaskError("subject_artifact_invalid")
    return {
        "status": "ok",
        "subject_task_id": args["subject_task_id"],
        "artifact_name": args["artifact_name"],
        "content": content,
        "sha256": digest,
        "bytes": size,
        "lines": lines,
    }


def handle(args: dict[str, Any], **_kwargs: Any) -> str:
    try:
        return json.dumps(_open(args), ensure_ascii=False, sort_keys=True)
    except ActiveTaskError as exc:
        return _error(exc.code)
    except Exception:
        return _error("subject_artifact_invalid")
