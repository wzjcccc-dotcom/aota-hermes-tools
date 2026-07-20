"""Read-only bounded access to the current worker's active task artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ._task_spec_scope import CanonicalScopeError, compute_scope_digest, extract_canonical_scope

from ._active_task_context import (
    ActiveTaskContext,
    ActiveTaskError,
    MAX_ARTIFACT_BYTES,
    assert_artifact_target,
    binding_summary,
    load_active_task_context,
    read_bounded_text,
    role_artifacts,
)


TOOL_NAME = "aota_active_task_artifact_open"
TOOLSET_NAME = "aota_active_task_context"
_ARTIFACTS = ("SPEC", "SCOPE", "BINDING", "INPUT_MANIFEST", "SUBJECT_CARD", "SUBJECT_RESULT")
_MAX_OUTPUT_BYTES = MAX_ARTIFACT_BYTES
_SCOPE_CURRENT_REQUIRED = {
    "schema_version", "workspace_id", "project_id", "task_id", "start_id",
    "spec_id", "spec_revision", "spec_hash", "read_scope", "write_scope",
    "forbidden_scope", "scope_source", "scope_digest", "created_at",
}
_SCOPE_ALLOWED = _SCOPE_CURRENT_REQUIRED | {
    "scope_schema_version", "process_session_id", "spec_sha256", "revision",
}
_SCOPE_LEGACY_ALLOWED = {
    "workspace_id", "task_id", "read_scope", "write_scope", "forbidden_scope",
    "created_at", "spec_sha256", "revision",
}

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Read one allowlisted artifact from the currently executing worker task. "
        "Task identity and the task directory come only from trusted active-task context; "
        "this tool accepts no task ID, workspace ID, path, or filename."
    ),
    "parameters": {
        "type": "object",
        "properties": {"artifact": {"type": "string", "enum": list(_ARTIFACTS)}},
        "required": ["artifact"],
        "additionalProperties": False,
    },
}


def _error(code: str) -> str:
    return json.dumps({"status": "rejected", "error_code": code}, sort_keys=True)


def _safe_json(path: Path, *, allowed: set[str]) -> tuple[dict[str, Any], str, int, int]:
    text, digest, size, lines = read_bounded_text(path)
    try:
        parsed = json.loads(text)
    except (TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise ActiveTaskError("active_task_artifact_invalid") from exc
    if not isinstance(parsed, dict) or set(parsed) - allowed:
        raise ActiveTaskError("active_task_artifact_invalid")
    return parsed, digest, size, lines


def _subject_context(ctx: ActiveTaskContext) -> tuple[Path, dict[str, Any]]:
    if ctx.profile not in {"reviewer", "debugger", "architect"}:
        raise ActiveTaskError("subject_binding_required")
    subject_id = ctx.meta.get("subject_task_id")
    if not isinstance(subject_id, str) or not subject_id or subject_id == ctx.task_id:
        raise ActiveTaskError("subject_binding_required")
    # The subject is selected by frozen task metadata, never by model input.
    subject_dir = ctx.root / ctx.workspace_id / subject_id
    if subject_dir.is_symlink():
        raise ActiveTaskError("active_task_binding_invalid")
    try:
        subject_dir.resolve(strict=True).relative_to(ctx.root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ActiveTaskError("active_task_binding_invalid") from exc
    subject_meta_path = subject_dir / "meta.json"
    if subject_meta_path.is_symlink() or not subject_meta_path.is_file():
        raise ActiveTaskError("subject_binding_required")
    try:
        if subject_meta_path.stat().st_size > 256 * 1024:
            raise ActiveTaskError("active_task_artifact_too_large")
        subject_meta = json.loads(subject_meta_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ActiveTaskError("subject_binding_required") from exc
    if not isinstance(subject_meta, dict):
        raise ActiveTaskError("subject_binding_required")
    try:
        if subject_dir.resolve(strict=True) != subject_dir:
            raise ActiveTaskError("active_task_binding_invalid")
    except OSError as exc:
        raise ActiveTaskError("active_task_binding_invalid") from exc
    bound_revision = ctx.meta.get("subject_spec_revision")
    bound_sha = ctx.meta.get("subject_spec_sha256")
    if bound_revision is not None and subject_meta.get("revision") != bound_revision:
        raise ActiveTaskError("subject_binding_required")
    if bound_sha and subject_meta.get("spec_sha256") != bound_sha:
        raise ActiveTaskError("subject_binding_required")
    return subject_dir, subject_meta


def _scope_content(ctx: ActiveTaskContext, target: Path) -> tuple[dict[str, Any], str, int, int]:
    scope, digest, size, lines = _safe_json(target, allowed=_SCOPE_ALLOWED | _SCOPE_LEGACY_ALLOWED)
    if _SCOPE_CURRENT_REQUIRED.issubset(scope):
        if scope.get("schema_version") != 1 or scope.get("scope_schema_version", 1) != 1:
            raise ActiveTaskError("active_task_artifact_invalid")
        if scope.get("workspace_id") != ctx.workspace_id or scope.get("task_id") != ctx.task_id or scope.get("start_id") != ctx.start_id:
            raise ActiveTaskError("active_task_binding_invalid")
        meta = ctx.meta
        execution = meta.get("execution") if isinstance(meta.get("execution"), dict) else {}
        expected = {
            "project_id": meta.get("project_id", ""),
            "spec_id": meta.get("spec_id", ctx.task_id),
            "spec_revision": meta.get("revision", execution.get("spec_revision")),
            "spec_hash": meta.get("spec_hash", execution.get("spec_hash")),
        }
        for key, value in expected.items():
            if value is not None and scope.get(key) != value:
                raise ActiveTaskError("active_task_binding_invalid")
        if "spec_sha256" in scope and meta.get("spec_sha256") and scope.get("spec_sha256") != meta.get("spec_sha256"):
            raise ActiveTaskError("active_task_binding_invalid")
        if "revision" in scope and scope.get("revision") != scope.get("spec_revision"):
            raise ActiveTaskError("active_task_binding_invalid")
        expected_process = execution.get("scope_process_session_id")
        if expected_process and scope.get("process_session_id") != expected_process:
            raise ActiveTaskError("active_task_binding_invalid")
        if scope.get("scope_source") not in {"payload", "legacy_top_level"}:
            raise ActiveTaskError("active_task_artifact_invalid")
        try:
            canonical = extract_canonical_scope({"payload": {field: scope[field] for field in ("read_scope", "write_scope", "forbidden_scope")}})
        except CanonicalScopeError as exc:
            raise ActiveTaskError("active_task_artifact_invalid") from exc
        canonical["scope_source"] = scope["scope_source"]
        canonical["scope_schema_version"] = scope.get("scope_schema_version", 1)
        if compute_scope_digest(canonical) != scope.get("scope_digest"):
            raise ActiveTaskError("scope_digest_mismatch")
        if execution.get("scope_digest") and execution.get("scope_digest") != scope.get("scope_digest"):
            raise ActiveTaskError("scope_digest_mismatch")
        content = {key: scope[key] for key in _SCOPE_ALLOWED if key in scope}
        content["artifact_digest"] = digest
        content["legacy_schema"] = False
        return content, digest, size, lines

    # Explicit compatibility branch for pre-canonical task artifacts.  It is
    # intentionally narrower than the current schema and never bypasses the
    # current binding/digest path above.
    if not {"workspace_id", "task_id", "read_scope", "write_scope", "forbidden_scope"}.issubset(scope):
        raise ActiveTaskError("active_task_artifact_invalid")
    if scope.get("workspace_id") != ctx.workspace_id or scope.get("task_id") != ctx.task_id:
        raise ActiveTaskError("active_task_binding_invalid")
    for field in ("read_scope", "write_scope", "forbidden_scope"):
        if not isinstance(scope[field], list) or not all(isinstance(item, str) for item in scope[field]):
            raise ActiveTaskError("active_task_artifact_invalid")
    content = {key: scope[key] for key in _SCOPE_LEGACY_ALLOWED if key in scope}
    content["artifact_digest"] = digest
    content["legacy_schema"] = True
    return content, digest, size, lines


def _open(args: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(args, dict) or set(args) != {"artifact"} or args.get("artifact") not in _ARTIFACTS:
        return {"status": "rejected", "error_code": "active_task_artifact_not_allowed"}
    ctx = load_active_task_context()
    artifact = args["artifact"]
    if artifact == "BINDING":
        content = binding_summary(ctx)
        rendered = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return {"status": "ok", "artifact": artifact, "content": content,
                "sha256": __import__("hashlib").sha256(rendered.encode("utf-8")).hexdigest(),
                "bytes": len(rendered.encode("utf-8")), "lines": 1}

    if artifact == "SPEC":
        target = assert_artifact_target(ctx.task_dir, "SPEC.md", allowed={"SPEC.md"})
        text, digest, size, lines = read_bounded_text(target)
        return {"status": "ok", "artifact": artifact, "content": text, "sha256": digest, "bytes": size, "lines": lines}

    if artifact == "SCOPE":
        target = assert_artifact_target(ctx.task_dir, "scope.json", allowed={"scope.json"})
        scope, digest, size, lines = _scope_content(ctx, target)
        return {"status": "ok", "artifact": artifact, "content": scope, "sha256": digest, "bytes": size, "lines": lines}

    if artifact == "INPUT_MANIFEST":
        target = assert_artifact_target(ctx.task_dir, "input-manifest.json", allowed={"input-manifest.json"})
        parsed, digest, size, lines = _safe_json(target, allowed={"schema_version", "artifacts"})
        return {"status": "ok", "artifact": artifact, "content": parsed, "sha256": digest, "bytes": size, "lines": lines}

    subject_dir, subject_meta = _subject_context(ctx)
    subject_profile = subject_meta.get("resolved_profile") or subject_meta.get("profile_hint")
    if subject_profile not in {"coder", "debugger", "reviewer", "architect", "project-steward"}:
        raise ActiveTaskError("subject_artifact_not_allowed")
    card_name, result_name = role_artifacts(subject_profile)
    name = card_name if artifact == "SUBJECT_CARD" else result_name
    target = assert_artifact_target(subject_dir, name, allowed={name})
    text, digest, size, lines = read_bounded_text(target)
    if len(text.encode("utf-8")) > _MAX_OUTPUT_BYTES:
        raise ActiveTaskError("active_task_artifact_too_large")
    return {"status": "ok", "artifact": artifact, "content": text, "sha256": digest, "bytes": size, "lines": lines,
            "subject_task_id": ctx.meta.get("subject_task_id")}


def handle(args: dict[str, Any], **_kwargs: Any) -> str:
    try:
        return json.dumps(_open(args), ensure_ascii=False, sort_keys=True)
    except ActiveTaskError as exc:
        return _error(exc.code)
    except Exception:
        return _error("active_task_artifact_invalid")
