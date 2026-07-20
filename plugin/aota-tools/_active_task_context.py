"""Fail-closed active Profile Task context and artifact boundary helpers.

The worker receives its task identity through the launcher environment.  This
module is intentionally small and shared by the read-only active-task reader
and the role-specific artifact writers; it is not a generic filesystem API.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_PROFILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_PROFILES = {"coder", "debugger", "reviewer", "architect", "project-steward"}
_MAX_META_BYTES = 256 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024
MAX_ARTIFACT_LINES = 2000
MAX_LINE_LENGTH = 2000


class ActiveTaskError(PermissionError):
    """Stable machine-readable active-task boundary error."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ActiveTaskContext:
    root: Path
    task_dir: Path
    workspace_id: str
    task_id: str
    start_id: str
    profile: str
    meta: dict[str, Any]


def _safe_id(value: object, *, code: str = "active_task_binding_invalid") -> str:
    if not isinstance(value, str) or not value or not _ID.fullmatch(value) or ".." in value:
        raise ActiveTaskError(code)
    return value


def _safe_profile(value: object) -> str:
    if not isinstance(value, str) or not _PROFILE.fullmatch(value) or value not in _PROFILES:
        raise ActiveTaskError("active_task_binding_invalid")
    return value


def _regular_component(path: Path, *, missing_code: str) -> None:
    """Reject symlink traversal for every existing path component."""
    try:
        if path.is_symlink():
            raise ActiveTaskError("active_task_binding_invalid")
        if not path.exists():
            raise ActiveTaskError(missing_code)
        if not path.is_dir() and path.name not in {"meta.json", "SPEC.md", "scope.json"}:
            return
    except OSError as exc:
        raise ActiveTaskError(missing_code) from exc


def _safe_task_dir(root: Path, workspace_id: str, task_id: str) -> Path:
    if root.is_symlink():
        raise ActiveTaskError("active_task_binding_invalid")
    workspace_dir = root / workspace_id
    task_dir = workspace_dir / task_id
    for component in (workspace_dir, task_dir):
        if component.is_symlink():
            raise ActiveTaskError("active_task_binding_invalid")
    try:
        canonical_root = root.resolve(strict=True)
        canonical_task = task_dir.resolve(strict=True)
        canonical_task.relative_to(canonical_root)
    except (OSError, ValueError) as exc:
        raise ActiveTaskError("active_task_binding_invalid") from exc
    if canonical_task != task_dir:
        raise ActiveTaskError("active_task_binding_invalid")
    if not task_dir.is_dir():
        raise ActiveTaskError("active_task_context_missing")
    return task_dir


def load_active_task_context(*, expected_profile: str | None = None) -> ActiveTaskContext:
    """Load and cross-check only the task selected by trusted worker env."""
    names = (
        "AOTA_PROFILE_TASK_ROOT",
        "AOTA_PROFILE_TASK_WORKSPACE_ID",
        "AOTA_PROFILE_TASK_ID",
        "AOTA_PROFILE_TASK_START_ID",
        "AOTA_PROFILE_TASK_PROFILE",
    )
    values = {name: os.environ.get(name, "") for name in names}
    if any(not value for value in values.values()):
        raise ActiveTaskError("active_task_context_missing")
    workspace_id = _safe_id(values["AOTA_PROFILE_TASK_WORKSPACE_ID"])
    task_id = _safe_id(values["AOTA_PROFILE_TASK_ID"])
    start_id = _safe_id(values["AOTA_PROFILE_TASK_START_ID"])
    profile = _safe_profile(values["AOTA_PROFILE_TASK_PROFILE"])
    if expected_profile is not None and profile != expected_profile:
        raise ActiveTaskError("active_task_profile_denied")
    raw_root = Path(values["AOTA_PROFILE_TASK_ROOT"])
    if not raw_root.is_absolute() or ".." in raw_root.parts:
        raise ActiveTaskError("active_task_binding_invalid")
    root = raw_root.resolve(strict=False)
    task_dir = _safe_task_dir(root, workspace_id, task_id)
    meta_path = task_dir / "meta.json"
    if meta_path.is_symlink() or not meta_path.is_file():
        raise ActiveTaskError("active_task_binding_invalid")
    try:
        if meta_path.stat().st_size > _MAX_META_BYTES:
            raise ActiveTaskError("active_task_artifact_too_large")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except ActiveTaskError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ActiveTaskError("active_task_binding_invalid") from exc
    if not isinstance(meta, dict):
        raise ActiveTaskError("active_task_binding_invalid")
    execution = meta.get("execution")
    if (
        meta.get("workspace_id") != workspace_id
        or meta.get("task_id") != task_id
        or not isinstance(execution, dict)
        or execution.get("start_id") != start_id
        or execution.get("profile") != profile
        or meta.get("status") != "running"
    ):
        raise ActiveTaskError("active_task_binding_invalid")
    if expected_profile is not None and meta.get("resolved_profile", meta.get("profile_hint")) not in {None, expected_profile}:
        raise ActiveTaskError("active_task_binding_invalid")
    return ActiveTaskContext(root, task_dir, workspace_id, task_id, start_id, profile, meta)


def active_task_directory(*, expected_profile: str | None = None) -> Path:
    return load_active_task_context(expected_profile=expected_profile).task_dir


def assert_artifact_target(task_dir: Path, filename: str, *, allowed: Iterable[str]) -> Path:
    """Return one closed task-local target and reject symlink escapes."""
    if filename not in set(allowed) or not filename or "/" in filename or "\\" in filename or ".." in filename:
        raise ActiveTaskError("active_task_artifact_not_allowed")
    target = task_dir / filename
    if target.is_symlink():
        raise ActiveTaskError("active_task_artifact_invalid")
    try:
        if target.resolve(strict=False).parent != task_dir.resolve(strict=True):
            raise ActiveTaskError("active_task_artifact_invalid")
    except (OSError, ValueError) as exc:
        raise ActiveTaskError("active_task_artifact_invalid") from exc
    return target


def _redact(text: str) -> str:
    patterns = (
        (re.compile(r"(?im)^(\s*(?:export\s+)?[A-Z][A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|AUTH|CREDENTIAL)[A-Z0-9_]*\s*[:=])\s*.*$"), r"\1 [REDACTED]"),
        (re.compile(r"(?i)(\"?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|authorization|credential)\"?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^,}\s]+)"), r"\1\"[REDACTED]\""),
    )
    for pattern, replacement in patterns:
        text = pattern.sub(replacement, text)
    return text


def read_bounded_text(path: Path) -> tuple[str, str, int, int]:
    if path.is_symlink() or not path.is_file():
        raise ActiveTaskError("active_task_artifact_missing")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ActiveTaskError("active_task_artifact_missing") from exc
    if len(raw) > MAX_ARTIFACT_BYTES or b"\x00" in raw:
        raise ActiveTaskError("active_task_artifact_too_large" if len(raw) > MAX_ARTIFACT_BYTES else "active_task_artifact_invalid")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ActiveTaskError("active_task_artifact_invalid") from exc
    lines = text.splitlines()
    if len(lines) > MAX_ARTIFACT_LINES or any(len(line) > MAX_LINE_LENGTH for line in lines):
        raise ActiveTaskError("active_task_artifact_too_large")
    redacted = _redact(text)
    return redacted, hashlib.sha256(raw).hexdigest(), len(raw), len(lines)


def binding_summary(ctx: ActiveTaskContext) -> dict[str, Any]:
    meta = ctx.meta
    spec = meta.get("spec") if isinstance(meta.get("spec"), dict) else {}
    execution = meta.get("execution") if isinstance(meta.get("execution"), dict) else {}
    spec_id = meta.get("spec_id") or spec.get("spec_id") or ctx.task_id
    spec_revision = meta.get("revision", execution.get("spec_revision"))
    spec_hash = meta.get("spec_hash") or spec.get("spec_hash") or execution.get("spec_hash")
    spec_sha256 = meta.get("spec_sha256") or execution.get("spec_sha256")
    return {
        "workspace_id": ctx.workspace_id,
        "project_id": meta.get("project_id") or spec.get("project_id"),
        "work_item_id": meta.get("work_item_id") or spec.get("work_item_id"),
        "task_id": ctx.task_id,
        "start_id": ctx.start_id,
        "spec_id": spec_id,
        "spec_revision": spec_revision,
        "spec_hash": spec_hash,
        "spec_sha256": spec_sha256,
        "resolved_profile": meta.get("resolved_profile") or meta.get("profile_hint") or ctx.profile,
        "subject_task_id": meta.get("subject_task_id"),
        "architecture_mode": meta.get("architecture_mode"),
        "task_kind": meta.get("task_kind") or meta.get("spec_kind"),
        "spec_kind": meta.get("spec_kind") or meta.get("task_kind"),
        "status": meta.get("status"),
    }


def role_artifacts(profile: str) -> tuple[str, str]:
    return {
        "coder": ("CARD.json", "RESULT.md"),
        "debugger": ("DIAGNOSIS_CARD.json", "DIAGNOSIS.md"),
        "reviewer": ("REVIEW_CARD.json", "REVIEW.md"),
        "architect": ("ARCHITECT_CARD.json", "ARCHITECT_REVIEW.md"),
        "project-steward": ("STEWARD_CARD.json", "STEWARD_RESULT.md"),
    }[profile]


def validate_tool_args(args: Any, schema: dict[str, Any]) -> None:
    """Apply the bounded subset of JSON Schema used by role writers."""
    if not isinstance(args, dict):
        raise ValueError("invalid_tool_arguments")
    params = schema.get("parameters", {})
    properties = params.get("properties", {})
    if params.get("additionalProperties") is False and set(args) - set(properties):
        raise ValueError("unknown_report_field")
    for name in params.get("required", []):
        if name not in args:
            raise ValueError(f"missing_{name}")

    def check(value: Any, spec: dict[str, Any]) -> None:
        kind = spec.get("type")
        if kind == "string":
            if not isinstance(value, str) or "\x00" in value:
                raise ValueError("invalid_string_field")
            if "maxLength" in spec and len(value) > spec["maxLength"]:
                raise ValueError("bounded_string_exceeded")
        elif kind == "array":
            if not isinstance(value, list) or len(value) > spec.get("maxItems", len(value)):
                raise ValueError("bounded_list_exceeded")
            item_spec = spec.get("items", {})
            for item in value:
                check(item, item_spec)
        elif kind == "object" and not isinstance(value, dict):
            raise ValueError("invalid_object_field")
        elif kind == "boolean" and not isinstance(value, bool):
            raise ValueError("invalid_boolean_field")
        elif kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            raise ValueError("invalid_integer_field")
        elif kind == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            raise ValueError("invalid_number_field")
        if "enum" in spec and value not in spec["enum"]:
            raise ValueError("invalid_enum_field")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in spec and value < spec["minimum"]:
                raise ValueError("number_below_minimum")
            if "maximum" in spec and value > spec["maximum"]:
                raise ValueError("number_above_maximum")

    for name, value in args.items():
        check(value, properties[name])
