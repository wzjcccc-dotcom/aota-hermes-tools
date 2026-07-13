"""aota_plan_open — exact, read-only access to a canonical Plan v1 artifact.

Plans are project-owned artifacts under the fixed, trusted workspace-relative
root ``.aota/forge/plans/<plan_id>/plan.json``.  No caller-controlled paths,
filesystem discovery, artifact writes, or disk PLAN.md reads are permitted.
"""
from __future__ import annotations

import hmac
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Mapping

from ._handoff_common import validate_workspace_id
from ._plan_common import (
    PlanError,
    compute_plan_sha256,
    create_plan,
    render_plan_markdown,
    validate_plan,
    validate_plan_id,
)
from ._workspace import WorkspaceError, resolve_workspace

TOOL_NAME = "aota_plan_open"
TOOLSET_NAME = "aota_plan_read"
PLAN_ROOT_PARTS = (".aota", "forge", "plans")
MAX_PLAN_BYTES = 1024 * 1024
MAX_FULL_RESPONSE_BYTES = 1024 * 1024
MAX_COMPACT_RESPONSE_BYTES = 64 * 1024
MAX_COMPACT_TEXT = 1000
MAX_RECENT_DECISIONS = 5
MAX_BLOCKERS = 10

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Open exactly one canonical AOTA Plan from a trusted workspace. "
        "Reads only plan.json under the fixed Plan root, validates schema and SHA, "
        "and returns a bounded compact or full canonical projection. "
        "Does not accept a path, scan plans, trust PLAN.md, or modify artifacts."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {
                "type": "string",
                "description": "Registered workspace identifier",
            },
            "plan_id": {
                "type": "string",
                "description": "Exact canonical Plan ID (plan_<identifier>)",
            },
            "view": {
                "type": "string",
                "enum": ["compact", "full"],
                "default": "compact",
                "description": "compact current-work summary or full canonical projection",
            },
        },
        "required": ["workspace_id", "plan_id"],
        "additionalProperties": False,
    },
}


class _PlanOpenError(Exception):
    def __init__(self, code: str, summary: str = "") -> None:
        self.code = code
        self.summary = summary
        super().__init__(code)


def _error(code: str, workspace_id: str | None = None, plan_id: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "error", "error_code": code}
    if isinstance(workspace_id, str) and workspace_id:
        result["workspace_id"] = workspace_id
    if isinstance(plan_id, str) and plan_id:
        result["plan_id"] = plan_id
    return result


def _bounded(value: str, maximum: int = MAX_COMPACT_TEXT) -> tuple[str, bool]:
    if len(value) <= maximum:
        return value, False
    return value[:maximum], True


def _plan_base(root: Path) -> Path:
    current = root
    for part in PLAN_ROOT_PARTS:
        current = current / part
        if current.is_symlink():
            raise _PlanOpenError("PLAN_SYMLINK_REJECTED")
        if not current.exists():
            raise _PlanOpenError("PLAN_NOT_FOUND")
        if not current.is_dir():
            raise _PlanOpenError("PLAN_ROOT_UNSAFE")
    try:
        current.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise _PlanOpenError("PLAN_PATH_ESCAPE") from exc
    return current


def _resolve_plan_file(root: Path, plan_id: str) -> Path:
    base = _plan_base(root)
    plan_dir = base / plan_id
    if plan_dir.is_symlink():
        raise _PlanOpenError("PLAN_SYMLINK_REJECTED")
    if not plan_dir.exists():
        raise _PlanOpenError("PLAN_NOT_FOUND")
    if not plan_dir.is_dir():
        raise _PlanOpenError("PLAN_ROOT_UNSAFE")
    plan_file = plan_dir / "plan.json"
    if plan_file.is_symlink():
        raise _PlanOpenError("PLAN_SYMLINK_REJECTED")
    if not plan_file.exists():
        raise _PlanOpenError("PLAN_NOT_FOUND")
    if not plan_file.is_file():
        raise _PlanOpenError("PLAN_FILE_INVALID_TYPE")
    try:
        plan_file.resolve().relative_to(base.resolve())
    except ValueError as exc:
        raise _PlanOpenError("PLAN_PATH_ESCAPE") from exc
    return plan_file


def _read_plan_json(plan_file: Path) -> Mapping[str, Any]:
    try:
        size = plan_file.stat().st_size
    except OSError as exc:
        raise _PlanOpenError("PLAN_READ_FAILED") from exc
    if size > MAX_PLAN_BYTES:
        raise _PlanOpenError("PLAN_TOO_LARGE")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(plan_file, flags)
        with os.fdopen(fd, "rb") as handle:
            file_stat = os.fstat(handle.fileno())
            if not stat.S_ISREG(file_stat.st_mode):
                raise _PlanOpenError("PLAN_FILE_INVALID_TYPE")
            raw = handle.read(MAX_PLAN_BYTES + 1)
    except _PlanOpenError:
        raise
    except OSError as exc:
        raise _PlanOpenError("PLAN_READ_FAILED") from exc
    if len(raw) > MAX_PLAN_BYTES:
        raise _PlanOpenError("PLAN_TOO_LARGE")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _PlanOpenError("PLAN_INVALID_UTF8") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _PlanOpenError("PLAN_INVALID_JSON") from exc
    if not isinstance(payload, dict):
        raise _PlanOpenError("PLAN_NOT_OBJECT")
    return payload


def _verify_and_validate(payload: Mapping[str, Any], requested_plan_id: str) -> dict[str, Any]:
    stored_id = payload.get("plan_id")
    if isinstance(stored_id, str) and stored_id != requested_plan_id:
        raise _PlanOpenError("PLAN_ID_MISMATCH")
    stored_sha = payload.get("plan_sha256")
    if isinstance(stored_sha, str) and len(stored_sha) == 64:
        try:
            computed_sha = compute_plan_sha256(payload)
        except (TypeError, ValueError) as exc:
            raise _PlanOpenError("PLAN_SCHEMA_INVALID") from exc
        if not hmac.compare_digest(stored_sha, computed_sha):
            raise _PlanOpenError("PLAN_SHA_MISMATCH")
    try:
        return validate_plan(payload)
    except PlanError as exc:
        raise _PlanOpenError("PLAN_SCHEMA_INVALID") from exc


def _active(entries: list[dict[str, Any]], key: str, active_id: str | None) -> dict[str, Any] | None:
    if active_id is None:
        return None
    return next((entry for entry in entries if entry[key] == active_id), None)


def _compact(plan: Mapping[str, Any]) -> dict[str, Any]:
    active_milestone = _active(plan["milestones"], "milestone_id", plan["active_milestone_id"])
    active_work_item = _active(plan["work_items"], "work_item_id", plan["active_work_item_id"])
    current_state, state_truncated = _bounded(plan["current_state"])
    next_action = plan["next_action"]
    next_action, action_truncated = _bounded(next_action) if next_action else (None, False)
    truncated = state_truncated or action_truncated

    milestone_projection = None
    if active_milestone is not None:
        objective, was_truncated = _bounded(active_milestone["objective"])
        truncated = truncated or was_truncated
        milestone_projection = {
            "milestone_id": active_milestone["milestone_id"],
            "title": active_milestone["title"],
            "status": active_milestone["status"],
            "objective": objective,
        }
    work_item_projection = None
    if active_work_item is not None:
        item_action = active_work_item["next_action"]
        item_action, was_truncated = _bounded(item_action) if item_action else (None, False)
        truncated = truncated or was_truncated
        work_item_projection = {
            "work_item_id": active_work_item["work_item_id"],
            "title": active_work_item["title"],
            "status": active_work_item["status"],
            "risk_level": active_work_item["risk_level"],
            "architect_gate": active_work_item["architect_gate"],
            "spec_preflight": active_work_item["spec_preflight"],
            "next_action": item_action,
        }

    blockers: list[dict[str, str]] = []
    if active_milestone is not None and active_milestone["status"] == "blocked":
        blockers.append({"kind": "milestone", "id": active_milestone["milestone_id"], "title": active_milestone["title"], "status": "blocked"})
    if active_work_item is not None:
        if active_work_item["status"] == "blocked":
            blockers.append({"kind": "work_item", "id": active_work_item["work_item_id"], "title": active_work_item["title"], "status": "blocked"})
        by_id = {item["work_item_id"]: item for item in plan["work_items"]}
        for dependency_id in active_work_item["dependencies"]:
            dependency = by_id[dependency_id]
            if dependency["status"] == "blocked":
                blockers.append({"kind": "work_item", "id": dependency_id, "title": dependency["title"], "status": "blocked"})
    if len(blockers) > MAX_BLOCKERS:
        blockers = blockers[:MAX_BLOCKERS]
        truncated = True

    recent = sorted(plan["decisions"], key=lambda entry: entry["created_at"], reverse=True)
    if len(recent) > MAX_RECENT_DECISIONS:
        recent = recent[:MAX_RECENT_DECISIONS]
        truncated = True
    decisions = [{key: entry[key] for key in ("decision_id", "summary", "status", "source", "created_at")} for entry in recent]
    terminal = {"closed", "cancelled", "superseded"}
    return {
        "status": "ok",
        "workspace_id": None,  # set by caller after trusted resolution
        "view": "compact",
        "plan_id": plan["plan_id"],
        "title": plan["title"],
        "status_value": plan["status"],
        "planning_depth": plan["planning_depth"],
        "architect_gate": plan["architect_gate"],
        "delivery_path": plan["delivery_path"],
        "revision": plan["revision"],
        "updated_at": plan["updated_at"],
        "current_state": current_state,
        "active_milestone": milestone_projection,
        "active_work_item": work_item_projection,
        "blockers": blockers,
        "recent_decisions": decisions,
        "next_action": next_action,
        "counts": {
            "milestones": len(plan["milestones"]),
            "work_items": len(plan["work_items"]),
            "open_work_items": sum(item["status"] not in terminal for item in plan["work_items"]),
            "blocked_work_items": sum(item["status"] == "blocked" for item in plan["work_items"]),
        },
        "truncated": truncated,
    }


def _full(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": "ok",
        "view": "full",
        "plan_id": plan["plan_id"],
        "plan": plan,
        "markdown_projection": render_plan_markdown(plan),
        "validation": {"schema_valid": True, "sha_valid": True},
    }


def _bounded_response(result: dict[str, Any], maximum: int) -> dict[str, Any]:
    if len(json.dumps(result, ensure_ascii=False, sort_keys=True).encode("utf-8")) > maximum:
        return _error("PLAN_RESPONSE_TOO_LARGE", result.get("workspace_id"), result.get("plan_id"))
    return result


def _do_open(args: dict) -> dict[str, Any]:
    workspace_id = args.get("workspace_id", "")
    requested_plan_id = args.get("plan_id", "")
    view = args.get("view", "compact")
    workspace_error = validate_workspace_id(workspace_id)
    if workspace_error:
        return _error("WORKSPACE_NOT_FOUND")
    try:
        plan_id = validate_plan_id(requested_plan_id)
    except PlanError:
        return _error("PLAN_ID_INVALID", workspace_id)
    if view not in {"compact", "full"}:
        return _error("PLAN_VIEW_INVALID", workspace_id, plan_id)
    try:
        root = resolve_workspace(workspace_id)
    except WorkspaceError:
        return _error("WORKSPACE_NOT_FOUND", workspace_id, plan_id)
    try:
        plan_file = _resolve_plan_file(root, plan_id)
        plan = _verify_and_validate(_read_plan_json(plan_file), plan_id)
        result = _compact(plan) if view == "compact" else _full(plan)
        result["workspace_id"] = workspace_id
        return _bounded_response(result, MAX_COMPACT_RESPONSE_BYTES if view == "compact" else MAX_FULL_RESPONSE_BYTES)
    except _PlanOpenError as exc:
        return _error(exc.code, workspace_id, plan_id)
    except Exception:
        return _error("PLAN_READ_FAILED", workspace_id, plan_id)


def handle(args: dict, **_kwargs: Any) -> str:
    """Return a bounded, sanitized JSON response for one exact Plan artifact."""
    return json.dumps(_do_open(args), ensure_ascii=False, sort_keys=True)


def run_isolated_smoke() -> dict[str, str]:
    """Source-level fixture smoke: no runtime artifact, plugin deployment, or writes outside tempdir."""
    from . import _workspace

    timestamp = "2026-07-13T00:00:00Z"
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw) / "workspace"
        root.mkdir()
        registry = Path(raw) / "workspaces.json"
        registry.write_text(json.dumps({"fixture": {"candidates": [str(root)]}}), encoding="utf-8")
        previous_registry = _workspace._REGISTRY_PATH
        _workspace._REGISTRY_PATH = registry
        try:
            plan_dir = root / ".aota" / "forge" / "plans" / "plan_test"
            plan_dir.mkdir(parents=True)
            plan = create_plan(title="Test", goal="Plan read smoke", plan_id="plan_test", timestamp=timestamp)
            milestone = {"milestone_id": "ms_one", "title": "M", "status": "ready", "objective": "Objective", "dependencies": [], "acceptance_criteria": [], "evidence": [], "architect_review_id": None, "created_at": timestamp, "updated_at": timestamp}
            item = {"work_item_id": "wi_one", "milestone_id": "ms_one", "title": "W", "goal": "Goal", "status": "ready", "risk_level": "medium", "architect_gate": "A1", "architect_review_id": None, "spec_preflight": "optional", "dependencies": [], "linked_task_ids": [], "evidence": [], "next_action": "Proceed", "created_at": timestamp, "updated_at": timestamp}
            plan["milestones"], plan["work_items"] = [milestone], [item]
            plan["active_milestone_id"], plan["active_work_item_id"] = "ms_one", "wi_one"
            plan["next_action"] = "Proceed"
            plan["plan_sha256"] = compute_plan_sha256(plan)
            plan = validate_plan(plan)
            source = plan_dir / "plan.json"
            source.write_text(json.dumps(plan), encoding="utf-8")
            (plan_dir / "PLAN.md").write_text("TAMPERED", encoding="utf-8")
            before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (source, plan_dir / "PLAN.md")}
            compact = _do_open({"workspace_id": "fixture", "plan_id": "plan_test"})
            full = _do_open({"workspace_id": "fixture", "plan_id": "plan_test", "view": "full"})
            assert compact["status"] == "ok" and compact["active_work_item"]["work_item_id"] == "wi_one"
            assert full["status"] == "ok" and full["plan"] == plan and "TAMPERED" not in full["markdown_projection"]
            for path, (contents, mtime) in before.items():
                assert path.read_bytes() == contents and path.stat().st_mtime_ns == mtime
            assert _do_open({"workspace_id": "fixture", "plan_id": "plan_missing"})["error_code"] == "PLAN_NOT_FOUND"
            assert _do_open({"workspace_id": "missing", "plan_id": "plan_test"})["error_code"] == "WORKSPACE_NOT_FOUND"
            for invalid in ("../x", "a/b", "a\\b", "%2e%2e", "plan_\x01"):
                assert _do_open({"workspace_id": "fixture", "plan_id": invalid})["error_code"] == "PLAN_ID_INVALID"
            source.write_bytes(b"\xff")
            assert _do_open({"workspace_id": "fixture", "plan_id": "plan_test"})["error_code"] == "PLAN_INVALID_UTF8"
            source.write_text("[]", encoding="utf-8")
            assert _do_open({"workspace_id": "fixture", "plan_id": "plan_test"})["error_code"] == "PLAN_NOT_OBJECT"
            source.write_text("{", encoding="utf-8")
            assert _do_open({"workspace_id": "fixture", "plan_id": "plan_test"})["error_code"] == "PLAN_INVALID_JSON"
            source.write_text(json.dumps({**plan, "title": "changed"}), encoding="utf-8")
            assert _do_open({"workspace_id": "fixture", "plan_id": "plan_test"})["error_code"] == "PLAN_SHA_MISMATCH"
            source.write_text(json.dumps({**plan, "plan_id": "plan_other", "plan_sha256": compute_plan_sha256({**plan, "plan_id": "plan_other"})}), encoding="utf-8")
            assert _do_open({"workspace_id": "fixture", "plan_id": "plan_test"})["error_code"] == "PLAN_ID_MISMATCH"
            source.write_text(json.dumps(plan), encoding="utf-8")
            os.unlink(plan_dir / "PLAN.md")
            external = Path(raw) / "external"
            external.mkdir()
            (plan_dir / "PLAN.md").symlink_to(external)
            assert _do_open({"workspace_id": "fixture", "plan_id": "plan_test"})["status"] == "ok"
            source.unlink()
            source.symlink_to(external / "plan.json")
            assert _do_open({"workspace_id": "fixture", "plan_id": "plan_test"})["error_code"] == "PLAN_SYMLINK_REJECTED"
            source.unlink()
            source.write_text(json.dumps(plan), encoding="utf-8")
            plan_dir.rename(plan_dir.with_name("plan_real"))
            plan_dir.symlink_to(plan_dir.with_name("plan_real"), target_is_directory=True)
            assert _do_open({"workspace_id": "fixture", "plan_id": "plan_test"})["error_code"] == "PLAN_SYMLINK_REJECTED"
            plan_dir.unlink()
            plan_dir.with_name("plan_real").rename(plan_dir)
            source = plan_dir / "plan.json"
            source.write_bytes(b"x" * (MAX_PLAN_BYTES + 1))
            assert _do_open({"workspace_id": "fixture", "plan_id": "plan_test"})["error_code"] == "PLAN_TOO_LARGE"
            source.write_text(json.dumps(plan), encoding="utf-8")
            invalid_schema = {**plan, "status": "unknown"}
            invalid_schema["plan_sha256"] = compute_plan_sha256(invalid_schema)
            source.write_text(json.dumps(invalid_schema), encoding="utf-8")
            assert _do_open({"workspace_id": "fixture", "plan_id": "plan_test"})["error_code"] == "PLAN_SCHEMA_INVALID"
            bounded = json.loads(json.dumps(plan))
            bounded["decisions"] = [
                {"decision_id": f"pd_d{i}", "summary": "Decision", "rationale": "Rationale", "status": "accepted", "source": "user", "related_milestone_ids": [], "related_work_item_ids": [], "evidence": [], "created_at": timestamp}
                for i in range(MAX_RECENT_DECISIONS + 1)
            ]
            bounded["plan_sha256"] = compute_plan_sha256(bounded)
            source.write_text(json.dumps(validate_plan(bounded)), encoding="utf-8")
            bounded_compact = _do_open({"workspace_id": "fixture", "plan_id": "plan_test"})
            assert bounded_compact["truncated"] and len(bounded_compact["recent_decisions"]) == MAX_RECENT_DECISIONS
            original_limit = MAX_FULL_RESPONSE_BYTES
            try:
                globals()["MAX_FULL_RESPONSE_BYTES"] = 1
                assert _do_open({"workspace_id": "fixture", "plan_id": "plan_test", "view": "full"})["error_code"] == "PLAN_RESPONSE_TOO_LARGE"
            finally:
                globals()["MAX_FULL_RESPONSE_BYTES"] = original_limit
            source.unlink()
            source.mkdir()
            assert _do_open({"workspace_id": "fixture", "plan_id": "plan_test"})["error_code"] == "PLAN_FILE_INVALID_TYPE"
            source.rmdir()
            source.write_text(json.dumps(plan), encoding="utf-8")
        finally:
            _workspace._REGISTRY_PATH = previous_registry
    return {"status": "PASS", "read_only": "READ_ONLY_CONFIRMED=yes", "plan_root": ".aota/forge/plans"}
