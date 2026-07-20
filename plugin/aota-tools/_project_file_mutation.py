"""Coder-only, frozen-SPEC-bound text file tools; never a generic file API."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from ._coder_task_binding import CoderBindingError, load_coder_binding
from ._active_task_context import load_active_task_context
from ._profile_task_scope import has_current_task_prior_write, record_scope_event
from ._task_spec_scope import CanonicalScopeError, extract_canonical_scope

TOOLSET_NAME = "aota_coder_file_mutation"
READ_TOOL_NAME = "aota_project_file_read"
WRITE_TOOL_NAME = "aota_project_file_write"
PATCH_TOOL_NAME = "aota_project_file_patch"
_MAX_BYTES = 262_144

_BASE = {
    "task_id": {"type": "string", "maxLength": 128},
    "spec_id": {"type": "string", "maxLength": 128},
    "path": {"type": "string", "maxLength": 512},
}
READ_SCHEMA = {"name": READ_TOOL_NAME, "description": "Read one text file only when the active frozen Coder SPEC permits its relative path.", "parameters": {"type": "object", "properties": _BASE, "required": ["task_id", "spec_id", "path"], "additionalProperties": False}}
WRITE_SCHEMA = {"name": WRITE_TOOL_NAME, "description": "Atomically write one bounded text file only within active frozen Coder SPEC write_scope.", "parameters": {"type": "object", "properties": {**_BASE, "content": {"type": "string", "maxLength": _MAX_BYTES}, "expected_sha256": {"type": "string", "maxLength": 64}}, "required": ["task_id", "spec_id", "path", "content"], "additionalProperties": False}}
PATCH_SCHEMA = {"name": PATCH_TOOL_NAME, "description": "Atomically replace one exact bounded text fragment only within active frozen Coder SPEC write_scope.", "parameters": {"type": "object", "properties": {**_BASE, "find": {"type": "string", "maxLength": _MAX_BYTES}, "replace": {"type": "string", "maxLength": _MAX_BYTES}, "expected_sha256": {"type": "string", "maxLength": 64}}, "required": ["task_id", "spec_id", "path", "find", "replace", "expected_sha256"], "additionalProperties": False}}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _relative_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise CoderBindingError("invalid_relative_path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path == PurePosixPath("."):
        raise CoderBindingError("invalid_relative_path")
    return path


def _scope_allows(path: PurePosixPath, scopes: object) -> bool:
    if not isinstance(scopes, list):
        return False
    rendered = path.as_posix()
    for raw in scopes:
        if not isinstance(raw, str):
            continue
        candidate = raw.rstrip("/")
        prefix = candidate[:-3].rstrip("/") if candidate.endswith("/**") else candidate
        if rendered == prefix or rendered.startswith(prefix + "/") or path.match(candidate):
            return True
    return False


def _target(root: Path, path: PurePosixPath, *, must_exist: bool) -> Path:
    target = root.joinpath(*path.parts)
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise CoderBindingError("symlink_not_allowed")
    if must_exist and (not target.is_file() or target.is_symlink()):
        raise CoderBindingError("regular_file_required")
    try:
        target.resolve(strict=False).relative_to(root.resolve())
    except ValueError as exc:
        raise CoderBindingError("path_escape") from exc
    return target


def _check_path(spec: dict[str, Any], path: PurePosixPath, *, write: bool) -> None:
    try:
        scope = extract_canonical_scope(spec)
    except CanonicalScopeError as exc:
        raise CoderBindingError(f"invalid_frozen_scope:{exc}") from exc
    if _scope_allows(path, scope["forbidden_scope"]):
        raise CoderBindingError("forbidden_scope_denied")
    if write:
        if not spec.get("capability_contract", {}).get("source_write") or not _scope_allows(path, scope["write_scope"]):
            raise CoderBindingError("write_scope_denied")
    elif not _scope_allows(path, scope["read_scope"]):
        raise CoderBindingError("read_scope_denied")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    except Exception:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def _reply(fn, args: dict) -> str:
    try:
        return json.dumps(fn(args), sort_keys=True)
    except (CoderBindingError, UnicodeError, OSError, ValueError) as exc:
        return json.dumps({"status": "rejected", "error": str(exc)}, sort_keys=True)


def _record_project_event(path: PurePosixPath, operation: str, source: str, **kwargs: Any) -> None:
    try:
        record_scope_event(load_active_task_context().task_dir, path=path.as_posix(), operation=operation, source=source, **kwargs)
    except Exception:
        pass


def handle_read(args: dict, **_kwargs: Any) -> str:
    def work(value: dict) -> dict[str, Any]:
        root, spec = load_coder_binding(value.get("task_id"), value.get("spec_id"))
        relative = _relative_path(value.get("path"))
        scope = extract_canonical_scope(spec)
        authority = "read_scope"
        allowed = _scope_allows(relative, scope["read_scope"]) and not _scope_allows(relative, scope["forbidden_scope"])
        reason = "scope_allowed" if allowed else ("forbidden_scope_denied" if _scope_allows(relative, scope["forbidden_scope"]) else "read_scope_denied")
        if not allowed and reason == "read_scope_denied":
            ctx = load_active_task_context()
            if has_current_task_prior_write(ctx.task_dir, path=relative.as_posix(), workspace_root=root, scope=scope):
                allowed = True
                authority = "current_task_prior_write"
                reason = "post_write_verification_allowed"
        _record_project_event(relative, "read", READ_TOOL_NAME, allowed=allowed, decision_reason=reason, attribution="post_write_verification" if authority == "current_task_prior_write" else "worker_action", authority=authority if allowed else None)
        if not allowed:
            raise CoderBindingError(reason)
        content = _target(root, relative, must_exist=True).read_text(encoding="utf-8")
        if len(content.encode("utf-8")) > _MAX_BYTES: raise CoderBindingError("file_too_large")
        return {"status": "ok", "path": relative.as_posix(), "content": content, "sha256": _digest(content)}
    return _reply(work, args)


def handle_write(args: dict, **_kwargs: Any) -> str:
    def work(value: dict) -> dict[str, Any]:
        content = value.get("content")
        if not isinstance(content, str) or len(content.encode("utf-8")) > _MAX_BYTES or "\x00" in content: raise CoderBindingError("invalid_text_content")
        root, spec = load_coder_binding(value.get("task_id"), value.get("spec_id"))
        relative = _relative_path(value.get("path")); _check_path(spec, relative, write=True)
        target = _target(root, relative, must_exist=False)
        mutation_kind = "write" if target.exists() else "create"
        if target.exists():
            previous = target.read_text(encoding="utf-8")
            expected = value.get("expected_sha256")
            if expected is not None and expected != _digest(previous): raise CoderBindingError("expected_sha256_mismatch")
        _atomic_write(target, content)
        _record_project_event(relative, mutation_kind, WRITE_TOOL_NAME, allowed=True, decision_reason="scope_allowed", attribution="worker_action", authority="write_scope", success=True)
        return {"status": "written", "path": relative.as_posix(), "sha256": _digest(content), "atomic": True}
    return _reply(work, args)


def handle_patch(args: dict, **_kwargs: Any) -> str:
    def work(value: dict) -> dict[str, Any]:
        find, replace = value.get("find"), value.get("replace")
        if not isinstance(find, str) or not find or not isinstance(replace, str) or "\x00" in find + replace: raise CoderBindingError("invalid_patch")
        root, spec = load_coder_binding(value.get("task_id"), value.get("spec_id"))
        relative = _relative_path(value.get("path")); _check_path(spec, relative, write=True)
        target = _target(root, relative, must_exist=True); before = target.read_text(encoding="utf-8")
        if value.get("expected_sha256") != _digest(before): raise CoderBindingError("expected_sha256_mismatch")
        if before.count(find) != 1: raise CoderBindingError("patch_requires_exactly_one_match")
        after = before.replace(find, replace, 1)
        if len(after.encode("utf-8")) > _MAX_BYTES: raise CoderBindingError("file_too_large")
        _atomic_write(target, after)
        _record_project_event(relative, "patch", PATCH_TOOL_NAME, allowed=True, decision_reason="scope_allowed", attribution="worker_action", authority="write_scope", success=True)
        return {"status": "patched", "path": relative.as_posix(), "sha256": _digest(after), "atomic": True}
    return _reply(work, args)
