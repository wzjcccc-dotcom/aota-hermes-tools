"""Shared, pure control-plane reference resolver for AOTA Forge.

This module resolves bounded *semantic* references (``active_frozen_spec``,
``active_profile_task``, ``active_work_item``, ``current_plan``,
``current_workspace_selection``, ``explicit_trusted_reference``) into a
validated binding envelope.  It only reads canonical control-plane artifacts
and reuses the existing workspace, SPEC, Plan, decision, and Profile Task
helpers.  It never creates durable truth, never guesses, and fails closed on
ambiguity, staleness, or identity mismatch.

Resolution priority is fixed, deterministic, and testable:

    1. explicit_trusted_reference
    2. active_profile_task binding
    3. trusted current-session active frozen SPEC binding
    4. frozen_spec binding
    5. active_work_item / linked plan
    6. current_workspace_selection decision
    7. ambiguous / missing error

The resolver returns a bounded envelope.  Workers must not use it to change a
frozen project binding; the envelope is a read-only observation of existing
canonical authorities.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from ._workspace import WorkspaceError, _load_registry, resolve_workspace
from ._task_spec_common import PROFILE_TASK_ROOT, get_task_dir, load_meta
from ._spec_contract import canonical_hash, validate_spec
from ._session_active_spec_binding import (
    SessionBindingError,
    TRUSTED_PRINCIPALS,
    read_session_active_spec,
    trusted_session_context,
)
from ._session_state_authority import (
    POINTER_KIND_ACTIVE_TASK,
    POINTER_KIND_ACTIVE_FROZEN_SPEC,
    POINTER_KIND_CURRENT_DRAFT_SPEC,
    SessionStateError,
    project_partition,
    read_pointer_for_context,
)

# Bounded semantic reference vocabulary.  Each name has a fixed artifact type.
# No free text is accepted as a reference.
SUPPORTED_REFS: frozenset[str] = frozenset({
    "explicit_trusted_reference",
    "active_profile_task",
    "frozen_spec",
    "active_work_item",
    "current_plan",
    "current_workspace_selection",
    # Convenience aliases used by the high-frequency start path.  They map to
    # the same artifact kinds above and are resolved by the same authority.
    "active_frozen_spec",
    "active_task",
    "current_task",
    "subject_task",
    "parent",
    "current",
    "active",
    "subject",
})

# Map every supported alias to its canonical resolution source.  The canonical
# sources are the six authorities listed in the priority order.  Aliases are
# not a parallel system; they only name the same authority with a shorter
# label and are constrained to a fixed artifact type.
_ALIAS_SOURCE: dict[str, str] = {
    "active_frozen_spec": "frozen_spec",
    "active_task": "active_profile_task",
    "current_task": "active_profile_task",
    "subject_task": "active_profile_task",
    "parent": "active_profile_task",
    "current": "frozen_spec",
    "active": "active_profile_task",
    "subject": "active_profile_task",
}

CANONICAL_SOURCES: tuple[str, ...] = (
    "explicit_trusted_reference",
    "active_profile_task",
    "frozen_spec",
    "active_work_item",
    "current_plan",
    "current_workspace_selection",
)

# Deterministic, bounded ambiguity / missing codes for task-main consumption.
ERROR_CODES: frozenset[str] = frozenset({
    "reference_missing",
    "reference_ambiguous",
    "reference_invalid",
    "binding_mismatch",
    "spec_stale",
    "canonical_hash_mismatch",
    "raw_sha_mismatch",
    "workspace_binding_mismatch",
    "worker_binding_change_forbidden",
    "reference_stale",
})


class ReferenceError(Exception):
    """Bounded resolver error carrying a deterministic code."""

    def __init__(self, code: str, *, detail: str = "", choices: list[dict[str, Any]] | None = None) -> None:
        super().__init__(code if not detail else f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.choices = choices or []


def _is_worker_context() -> bool:
    """A worker Profile Task context is present when its trusted env is set."""
    return bool(os.environ.get("AOTA_PROFILE_TASK_ID")) and bool(
        os.environ.get("AOTA_PROFILE_TASK_START_ID")
    )


def _meta_is_frozen(meta: Mapping[str, Any]) -> bool:
    if meta.get("contract_version") == 1:
        return meta.get("status") == "frozen"
    return meta.get("status") == "draft" and meta.get("frozen_revision") == meta.get("revision")


def _spec_binding(meta: Mapping[str, Any]) -> dict[str, Any]:
    """Return the canonical SPEC binding fields from a frozen task meta."""
    spec = meta.get("spec") if isinstance(meta.get("spec"), dict) else {}
    kind = meta.get("spec_kind") or spec.get("spec_kind")
    context = spec.get("workspace_context") if isinstance(spec.get("workspace_context"), dict) else {}
    return {
        "spec_id": meta.get("spec_id") or spec.get("spec_id"),
        "revision": meta.get("revision") or spec.get("revision"),
        "spec_hash": meta.get("spec_hash") or spec.get("spec_hash"),
        "spec_sha256": meta.get("spec_sha256"),
        "spec_kind": meta.get("spec_kind") or spec.get("spec_kind"),
        "resolved_profile": meta.get("resolved_profile") or spec.get("resolved_profile"),
        "work_item_id": meta.get("work_item_id") or spec.get("work_item_id"),
        "project_id": meta.get("project_id") or spec.get("project_id"),
        "subject_task_id": meta.get("subject_task_id") or spec.get("subject_task_id"),
        "architecture_mode": meta.get("architecture_mode") or spec.get("architecture_mode"),
        "workspace_id": meta.get("workspace_id") or context.get("workspace_id"),
        "workspace_registry_digest": context.get("workspace_registry_digest") or ("standalone" if str(meta.get("project_id", "")).startswith("standalone:") else None),
        "project_manifest_digest": context.get("project_manifest_digest") or ("standalone" if str(meta.get("project_id", "")).startswith("standalone:") else None),
        "project_registry_revision": context.get("project_registry_revision") or (0 if str(meta.get("project_id", "")).startswith("standalone:") else None),
        "frozen_at": meta.get("frozen_at") or spec.get("frozen_at"),
        # This is the immutable approval policy captured by the SPEC kind. The
        # start handler still performs the live APPROVAL.json gate.
        "approval_status": "required" if kind == "implementation" else "not_required",
    }


def _workspace_binding(meta: Mapping[str, Any]) -> dict[str, Any] | None:
    spec = meta.get("spec") if isinstance(meta.get("spec"), dict) else {}
    context = spec.get("workspace_context") if isinstance(spec, dict) else None
    if not isinstance(context, dict):
        return None
    return {
        "workspace_id": context.get("workspace_id"),
        "project_id": context.get("project_id"),
        "decision_id": context.get("decision_id"),
        "workspace_registry_digest": context.get("workspace_registry_digest"),
        "project_manifest_digest": context.get("project_manifest_digest"),
        "project_registry_revision": context.get("project_registry_revision"),
    }


def _full_workspace_context(meta: Mapping[str, Any]) -> dict[str, Any]:
    spec = meta.get("spec") if isinstance(meta.get("spec"), dict) else {}
    context = spec.get("workspace_context")
    if not isinstance(context, dict):
        if str(spec.get("project_id", meta.get("project_id", ""))).startswith("standalone:"):
            workspace_id = meta.get("workspace_id")
            if not isinstance(workspace_id, str) or not workspace_id:
                raise ReferenceError("workspace_binding_mismatch", detail="standalone_workspace_missing")
            return {
                "schema_version": 1,
                "workspace_id": workspace_id,
                "canonical_root": "standalone",
                "workspace_registry_digest": "standalone",
                "project_id": spec.get("project_id"),
                "project_root": "standalone",
                "manifest_path": "standalone",
                "project_manifest_digest": "standalone",
                "project_registry_revision": 0,
                "relationship": "standalone",
                "source_type": "control_plane_generated",
                "recommendation_id": None,
                "decision_id": None,
                "frozen_at": meta.get("frozen_at") or spec.get("frozen_at"),
                "frozen_by": "task-main",
            }
        raise ReferenceError("workspace_binding_mismatch", detail="workspace_context_missing")
    workspace_id = context.get("workspace_id")
    project_id = context.get("project_id") or spec.get("project_id")
    if workspace_id is None or project_id is None:
        raise ReferenceError("workspace_binding_mismatch", detail="workspace_context_incomplete")
    if workspace_id != meta.get("workspace_id") or project_id != spec.get("project_id"):
        raise ReferenceError("workspace_binding_mismatch", detail="workspace_project_binding_mismatch")
    try:
        from ._workspace_context import validate_workspace_context
        validate_workspace_context(context)
    except Exception as exc:
        code = "workspace_binding_mismatch"
        if "digest" in str(exc) or "revision" in str(exc):
            code = "workspace_binding_mismatch"
        raise ReferenceError(code, detail=str(exc)) from exc
    return dict(context)


def _validated_workspace_context(workspace_id: str, project_id: str) -> dict[str, Any]:
    """Revalidate the workspace/project binding against the live registry."""
    from ._workspace_context import resolve_workspace_context
    return resolve_workspace_context(workspace_id, project_id)


def _list_task_dirs(workspace_id: str) -> list[Path]:
    """List bounded task directories for a workspace, sorted deterministically."""
    from . import _task_spec_common
    root = _task_spec_common.PROFILE_TASK_ROOT / workspace_id
    if not root.is_dir():
        return []
    out: list[Path] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.is_symlink():
            continue
        if (entry / "meta.json").is_file():
            out.append(entry)
    return out


def _read_meta_safe(task_dir: Path) -> dict[str, Any] | None:
    meta_path = task_dir / "meta.json"
    if meta_path.is_symlink() or not meta_path.is_file():
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _verify_frozen_canonical(meta: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a frozen canonical SPEC and return its binding fields.

    Fails closed on stale revision, canonical hash mismatch, or raw SHA
    mismatch between stored meta and the on-disk SPEC.md.
    """
    if meta.get("contract_version") != 1:
        raise ReferenceError("reference_invalid", detail="not_a_canonical_spec")
    spec = meta.get("spec")
    if not isinstance(spec, dict):
        raise ReferenceError("reference_invalid", detail="spec_payload_missing")
    if meta.get("status") != "frozen":
        raise ReferenceError("spec_stale", detail="status_not_frozen")
    try:
        validate_spec(spec, frozen=True)
    except Exception as exc:  # ContractError
        raise ReferenceError("reference_invalid", detail=f"spec_validation:{exc}") from exc
    for key in ("spec_id", "spec_kind", "resolved_profile", "project_id", "work_item_id", "subject_task_id"):
        if meta.get(key) != spec.get(key):
            raise ReferenceError("binding_mismatch", detail=f"meta_spec:{key}")
    if meta.get("revision") != spec.get("revision"):
        raise ReferenceError("spec_stale", detail="revision_binding_mismatch")
    digest = canonical_hash(spec)
    if not meta.get("spec_hash") or meta["spec_hash"] != digest:
        raise ReferenceError("canonical_hash_mismatch")
    # Raw file-content SHA-256 is a separate frozen binding and must remain
    # intact.  The on-disk SPEC.md must match meta.spec_sha256 when present.
    stored_raw = meta.get("spec_sha256")
    if not isinstance(stored_raw, str) or not stored_raw:
        raise ReferenceError("raw_sha_mismatch", detail="raw_sha_binding_missing")
    task_dir = Path(str(meta.get("_task_dir", "")))
    if not task_dir.is_dir():
        raise ReferenceError("raw_sha_mismatch", detail="spec_artifact_missing")
    try:
        from ._task_spec_common import load_spec_md, compute_sha256
        actual_raw = compute_sha256(load_spec_md(task_dir))
    except Exception as exc:
        raise ReferenceError("raw_sha_mismatch", detail="spec_artifact_unreadable") from exc
    if actual_raw != stored_raw:
        raise ReferenceError("raw_sha_mismatch")
    _full_workspace_context(meta)
    return _spec_binding(meta)


def _session_active_spec_binding(
    workspace_id: str,
    session_id: str | None,
    principal: str | None,
) -> dict[str, Any] | None:
    """Resolve and revalidate one trusted session's active frozen SPEC.

    A present but stale binding is an error. It is deliberately never treated
    as a missing binding, because doing so could switch to another session's
    frozen SPEC through the workspace fallback.
    """
    if not session_id or principal not in TRUSTED_PRINCIPALS:
        return None
    # Canonical authority: one exact session-state pointer, then the durable
    # frozen artifact it names.  A stale canonical pointer never falls through
    # to another session or a workspace-wide candidate.
    canonical_context = SimpleNamespace(
        session_id=session_id, principal=principal, profile=principal,
        workspace_id=workspace_id, worker_context=False,
    )
    try:
        canonical = read_pointer_for_context(POINTER_KIND_ACTIVE_FROZEN_SPEC, canonical_context)
    except SessionStateError as exc:
        raise ReferenceError(exc.code, detail=exc.detail) from exc
    if canonical is not None:
        if canonical.get("state") in {"consumed", "closed"}:
            raise ReferenceError("reference_consumed", detail="active_frozen_spec_consumed")
        if canonical.get("state") != "active":
            raise ReferenceError("reference_stale", detail="active_frozen_spec_state_invalid")
        task_id = canonical.get("task_id") or canonical.get("spec_id")
        task_dir = get_task_dir(workspace_id, task_id) if isinstance(task_id, str) else Path("/")
        meta = _read_meta_safe(task_dir)
        if meta is None or meta.get("workspace_id") != workspace_id:
            raise ReferenceError("reference_stale", detail="active_frozen_spec_artifact_missing")
        meta_copy = dict(meta)
        meta_copy["_task_dir"] = str(task_dir)
        try:
            actual = _verify_frozen_canonical(meta_copy)
        except ReferenceError as exc:
            raise ReferenceError("reference_stale", detail=f"active_frozen_pointer:{exc.code}") from exc
        actual["task_id"] = meta.get("task_id") or task_id
        actual["_task_dir"] = str(task_dir)
        actual["_meta"] = dict(meta)
        for key in ("task_id", "spec_id", "revision", "spec_hash", "spec_sha256"):
            if canonical.get(key) != actual.get(key):
                raise ReferenceError("reference_stale", detail=f"active_frozen_pointer:{key}")
        return actual
    try:
        binding = read_session_active_spec(workspace_id, session_id)
    except SessionBindingError as exc:
        raise ReferenceError(exc.code, detail=exc.detail) from exc
    if binding is None:
        return None
    if binding.get("session_id") != session_id:
        raise ReferenceError("reference_stale", detail="session_binding_identity_mismatch")
    if binding.get("workspace_id") != workspace_id:
        raise ReferenceError("workspace_binding_mismatch", detail="session_binding_workspace_mismatch")
    if binding.get("binding_source") != "task_spec_freeze":
        raise ReferenceError("reference_stale", detail="session_binding_source_invalid")

    task_id = binding.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise ReferenceError("reference_stale", detail="session_binding_task_missing")
    task_dir = get_task_dir(workspace_id, task_id)
    meta = _read_meta_safe(task_dir)
    if meta is None:
        raise ReferenceError("reference_stale", detail="session_binding_task_not_found")
    if meta.get("status") != "frozen":
        raise ReferenceError("reference_stale", detail="active_spec_not_frozen")
    meta_copy = dict(meta)
    meta_copy["_task_dir"] = str(task_dir)
    try:
        actual = _verify_frozen_canonical(meta_copy)
    except ReferenceError as exc:
        raise ReferenceError("reference_stale", detail=f"active_spec_binding:{exc.code}") from exc
    actual["task_id"] = meta.get("task_id") or task_id
    actual["_task_dir"] = str(task_dir)
    actual["_meta"] = dict(meta)

    # Every field in the session artifact is checked against the live task
    # projection. Hashes remain distinct and workspace context is checked by
    # _verify_frozen_canonical against the current registry/manifest.
    required_equal = (
        "task_id", "spec_id", "revision", "spec_hash", "spec_sha256",
        "project_id", "resolved_profile", "frozen_at", "approval_status",
        "workspace_registry_digest", "project_manifest_digest",
        "project_registry_revision",
    )
    for key in required_equal:
        if binding.get(key) != actual.get(key):
            raise ReferenceError("reference_stale", detail=f"active_spec_binding_mismatch:{key}")
    return actual


def _active_profile_task_binding(workspace_id: str) -> dict[str, Any] | None:
    """Resolve the unique running Profile Task in a workspace, if exactly one."""
    running: list[tuple[Path, dict[str, Any]]] = []
    for task_dir in _list_task_dirs(workspace_id):
        meta = _read_meta_safe(task_dir)
        if not meta:
            continue
        if meta.get("status") == "running":
            running.append((task_dir, meta))
    if len(running) == 0:
        return None
    if len(running) > 1:
        raise ReferenceError(
            "reference_ambiguous",
            detail="multiple_running_profile_tasks",
        )
    task_dir, meta = running[0]
    verified_meta = dict(meta)
    verified_meta["_task_dir"] = str(task_dir)
    spec = verified_meta.get("spec") if isinstance(verified_meta.get("spec"), dict) else {}
    try:
        validate_spec(spec, frozen=True)
        if verified_meta.get("revision") != spec.get("revision"):
            raise ReferenceError("spec_stale", detail="running_revision_mismatch")
        if verified_meta.get("spec_hash") != canonical_hash(spec):
            raise ReferenceError("canonical_hash_mismatch")
        stored_raw = verified_meta.get("spec_sha256")
        from ._task_spec_common import load_spec_md, compute_sha256
        if not stored_raw or compute_sha256(load_spec_md(task_dir)) != stored_raw:
            raise ReferenceError("raw_sha_mismatch")
        _full_workspace_context(verified_meta)
    except ReferenceError:
        raise
    except Exception as exc:
        raise ReferenceError("reference_invalid", detail=f"active_task_validation:{exc}") from exc
    binding = _spec_binding(meta)
    binding["task_id"] = meta.get("task_id") or task_dir.name
    binding["_task_dir"] = str(task_dir)
    binding["_meta"] = dict(meta)
    return binding


def _session_active_task_binding(
    workspace_id: str, session_id: str | None, principal: str | None,
) -> dict[str, Any] | None:
    """Resolve active_task from one trusted session pointer only."""
    if not session_id or principal not in TRUSTED_PRINCIPALS:
        return None
    context = SimpleNamespace(
        session_id=session_id, principal=principal, profile=principal,
        workspace_id=workspace_id, worker_context=False,
    )
    try:
        pointer = read_pointer_for_context(POINTER_KIND_ACTIVE_TASK, context)
    except SessionStateError as exc:
        raise ReferenceError(exc.code, detail=exc.detail) from exc
    if pointer is None:
        return None
    if pointer.get("state") not in {"active", "terminal"}:
        raise ReferenceError("reference_consumed", detail="active_task_consumed")
    task_id = pointer.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise ReferenceError("reference_stale", detail="active_task_id_missing")
    task_dir = get_task_dir(workspace_id, task_id)
    meta = _read_meta_safe(task_dir)
    if meta is None or meta.get("workspace_id") != workspace_id:
        raise ReferenceError("reference_stale", detail="active_task_artifact_missing")
    execution = meta.get("execution") if isinstance(meta.get("execution"), dict) else {}
    expected_status = "running" if pointer.get("state") == "active" else "terminal"
    if expected_status == "running" and meta.get("status") != "running":
        raise ReferenceError("reference_stale", detail="active_task_status")
    if expected_status == "terminal" and meta.get("status") not in {"done", "failed", "cancelled", "timeout", "needs_input", "scope_violation"}:
        raise ReferenceError("reference_stale", detail="active_task_terminal_status")
    binding = _spec_binding(meta)
    binding.update({
        "task_id": task_id,
        "start_id": execution.get("start_id"),
        "terminal_status": meta.get("status"),
        "completed_at": execution.get("completed_at"),
        "_task_dir": str(task_dir),
        "_meta": dict(meta),
    })
    for key, value in {
        "task_id": task_id,
        "start_id": execution.get("start_id"),
        "revision": meta.get("revision") or execution.get("spec_revision"),
        "spec_hash": meta.get("spec_hash") or execution.get("spec_hash"),
        "spec_sha256": meta.get("spec_sha256") or execution.get("spec_sha256"),
    }.items():
        if pointer.get(key) != value:
            raise ReferenceError("reference_stale", detail=f"active_task_binding:{key}")
    return binding


def _frozen_spec_binding(workspace_id: str) -> dict[str, Any] | None:
    """Resolve the unique frozen canonical SPEC in a workspace, if exactly one."""
    frozen: list[tuple[Path, dict[str, Any]]] = []
    invalid_codes: list[str] = []
    for task_dir in _list_task_dirs(workspace_id):
        meta = _read_meta_safe(task_dir)
        if not meta:
            continue
        if not _meta_is_frozen(meta):
            continue
        if meta.get("contract_version") != 1:
            continue
        # Validate before considering it a candidate.
        try:
            meta_copy = dict(meta)
            meta_copy["_task_dir"] = str(task_dir)
            _verify_frozen_canonical(meta_copy)
        except ReferenceError as exc:
            invalid_codes.append(exc.code)
            continue
        frozen.append((task_dir, meta))
    if len(frozen) == 0:
        if len(invalid_codes) == 1:
            raise ReferenceError(invalid_codes[0], detail="frozen_candidate_invalid")
        return None
    if len(frozen) > 1:
        raise ReferenceError(
            "reference_ambiguous",
            detail="multiple_frozen_specs",
        )
    task_dir, meta = frozen[0]
    binding = _spec_binding(meta)
    binding["task_id"] = meta.get("task_id") or task_dir.name
    binding["_task_dir"] = str(task_dir)
    binding["_meta"] = dict(meta)
    return binding


def _current_workspace_selection(workspace_id: str) -> dict[str, Any] | None:
    """Resolve the unique validated task-main workspace_selection decision."""
    from ._orchestration_common import get_decision_dir, read_decision
    decision_dir = get_decision_dir(workspace_id)
    if not decision_dir.is_dir():
        return None
    candidates: list[dict[str, Any]] = []
    for entry in sorted(decision_dir.iterdir()):
        if not entry.is_file() or entry.is_symlink():
            continue
        if not entry.name.startswith("DECISION.") or not entry.name.endswith(".json"):
            continue
        try:
            record = read_decision(entry)
        except Exception:
            continue
        if record.get("decision_type") != "workspace_selection":
            continue
        if record.get("decided_by") != "task-main":
            continue
        context = record.get("workspace_context")
        if not isinstance(context, dict):
            raise ReferenceError("workspace_binding_mismatch", detail="selection_context_missing")
        try:
            from ._workspace_context import validate_workspace_context
            validate_workspace_context(context)
        except Exception as exc:
            raise ReferenceError("workspace_binding_mismatch", detail=str(exc)) from exc
        if context.get("workspace_id") != workspace_id:
            raise ReferenceError("workspace_binding_mismatch", detail="selection_workspace_mismatch")
        if context.get("decision_id") not in {None, record.get("decision_id")}:
            raise ReferenceError("workspace_binding_mismatch", detail="selection_decision_mismatch")
        candidates.append(record)
    if len(candidates) == 0:
        return None
    if len(candidates) > 1:
        raise ReferenceError("reference_ambiguous", detail="multiple_workspace_selections")
    record = candidates[0]
    context = record.get("workspace_context")
    result = dict(context)
    result["decision_id"] = record.get("decision_id")
    return result


def _active_work_item(workspace_id: str) -> dict[str, Any] | None:
    """Resolve the active Work Item / Plan in a workspace, if exactly one plan exists.

    Reads the canonical Plan under ``.aota/forge/plans``.  Returns the active
    milestone/work item plus the plan identity.  Multiple plans with active
    work items yield bounded ambiguity.
    """
    from ._plan_open import _resolve_plan_file, _read_plan_json, _verify_and_validate, _PlanOpenError
    try:
        root = resolve_workspace(workspace_id)
    except WorkspaceError:
        return None
    plans_root = root / ".aota" / "forge" / "plans"
    if not plans_root.is_dir():
        return None
    active: list[dict[str, Any]] = []
    for entry in sorted(plans_root.iterdir()):
        if not entry.is_dir() or entry.is_symlink():
            continue
        plan_file = entry / "plan.json"
        if not plan_file.is_file():
            continue
        try:
            plan = _verify_and_validate(_read_plan_json(plan_file), entry.name)
        except _PlanOpenError:
            continue
        if not plan.get("active_work_item_id"):
            continue
        active.append({
            "plan_id": plan.get("plan_id"),
            "plan_revision": plan.get("revision"),
            "plan_sha256": plan.get("plan_sha256"),
            "active_milestone_id": plan.get("active_milestone_id"),
            "active_work_item_id": plan.get("active_work_item_id"),
        })
    if len(active) == 0:
        return None
    if len(active) > 1:
        raise ReferenceError("reference_ambiguous", detail="multiple_active_work_items")
    return active[0]


def _plan_candidates(workspace_id: str) -> list[dict[str, Any]]:
    """Read valid canonical Plans without selecting one implicitly."""
    from ._plan_open import _read_plan_json, _verify_and_validate, _PlanOpenError
    try:
        root = resolve_workspace(workspace_id)
    except WorkspaceError:
        return []
    plans_root = root / ".aota" / "forge" / "plans"
    if not plans_root.is_dir():
        return []
    candidates: list[dict[str, Any]] = []
    for entry in sorted(plans_root.iterdir()):
        if not entry.is_dir() or entry.is_symlink() or not (entry / "plan.json").is_file():
            continue
        try:
            plan = _verify_and_validate(_read_plan_json(entry / "plan.json"), entry.name)
        except _PlanOpenError:
            continue
        candidates.append(plan)
    return candidates


def resolve_current_plan(workspace_id: str, *, require_active_work_item: bool = False) -> dict[str, Any]:
    """Resolve one current Plan, exposing IDs only to trusted handlers."""
    candidates = _plan_candidates(workspace_id)
    if require_active_work_item:
        candidates = [item for item in candidates if item.get("active_work_item_id")]
    if not candidates:
        raise ReferenceError("reference_missing", detail="current_plan_missing" if not require_active_work_item else "active_work_item_missing")
    if len(candidates) > 1:
        choices = [
            {"selector": f"choice:{index}", "title": item.get("title"),
             "status": item.get("status"), "has_active_work_item": bool(item.get("active_work_item_id"))}
            for index, item in enumerate(candidates, 1)
        ]
        raise ReferenceError("reference_ambiguous", detail="multiple_current_plans", choices=choices)
    return candidates[0]


def resolve_current_work_item(workspace_id: str, ref: Any = "current") -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve the active Work Item or one bounded semantic choice."""
    plan = resolve_current_plan(workspace_id, require_active_work_item=False)
    items = [item for item in plan.get("work_items", []) if item.get("status") not in {"closed", "cancelled", "superseded"}]
    active_id = plan.get("active_work_item_id")
    if ref in (None, "current", "active", "current_work_item"):
        if not active_id:
            if len(items) == 1:
                return plan, items[0]
            if not items:
                raise ReferenceError("reference_missing", detail="active_work_item_missing")
            raise ReferenceError("reference_ambiguous", detail="multiple_active_work_items", choices=[
                {"selector": f"choice:{index}", "title": item.get("title"), "status": item.get("status")}
                for index, item in enumerate(items, 1)
            ])
        match = next((item for item in items if item.get("work_item_id") == active_id), None)
        if match is None:
            raise ReferenceError("reference_stale", detail="active_work_item_not_found")
        return plan, match
    if isinstance(ref, str) and ref.startswith("choice:"):
        try:
            index = int(ref.split(":", 1)[1]) - 1
        except ValueError:
            raise ReferenceError("reference_invalid", detail="work_item_selector_invalid")
        if index < 0 or index >= len(items):
            raise ReferenceError("reference_invalid", detail="work_item_selector_out_of_bounds")
        return plan, items[index]
    raise ReferenceError("reference_invalid", detail="work_item_ref_must_be_semantic")


def resolve_current_draft_spec(workspace_id: str, context: Any | None = None) -> dict[str, Any]:
    """Resolve the current draft from session-state; legacy scan is bounded."""
    if context is not None and getattr(context, "usable_for_active_spec", False):
        try:
            pointer = read_pointer_for_context(POINTER_KIND_CURRENT_DRAFT_SPEC, context)
        except SessionStateError as exc:
            raise ReferenceError(exc.code, detail=exc.detail) from exc
        if pointer is None:
            raise ReferenceError("reference_missing", detail="current_draft_spec_missing")
        if pointer.get("state") == "consumed":
            raise ReferenceError("reference_consumed", detail="current_draft_spec_consumed")
        if pointer.get("state") != "active":
            raise ReferenceError("reference_stale", detail="current_draft_spec_state_invalid")
        task_id = pointer.get("task_id") or pointer.get("spec_id")
        if not isinstance(task_id, str) or not task_id:
            raise ReferenceError("reference_stale", detail="current_draft_spec_task_missing")
        task_dir = get_task_dir(workspace_id, task_id)
        meta = _read_meta_safe(task_dir)
        if not meta:
            raise ReferenceError("reference_stale", detail="current_draft_spec_artifact_missing")
        spec = meta.get("spec") if isinstance(meta.get("spec"), dict) else {}
        if meta.get("workspace_id") != workspace_id or meta.get("status") != "draft":
            raise ReferenceError("reference_stale", detail="current_draft_spec_artifact_binding")
        expected = {
            "task_id": meta.get("task_id") or task_id,
            "spec_id": meta.get("spec_id") or spec.get("spec_id") or task_id,
            "revision": meta.get("revision") or spec.get("revision"),
            "spec_sha256": meta.get("spec_sha256"),
        }
        for key, value in expected.items():
            if pointer.get(key) != value:
                raise ReferenceError("reference_stale", detail=f"current_draft_spec_binding:{key}")
        project = meta.get("project_id") or spec.get("project_id")
        if project_partition(pointer.get("project_id")) != project_partition(project):
            raise ReferenceError("workspace_binding_mismatch", detail="current_draft_spec_project")
        return {
            **expected,
            "spec_kind": meta.get("spec_kind") or spec.get("spec_kind"),
            "resolution_source": "session_state_pointer",
        }

    # Compatibility only: canonical model-visible paths always pass trusted
    # context and therefore never reach this historical fallback.
    candidates: list[dict[str, Any]] = []
    for task_dir in _list_task_dirs(workspace_id):
        meta = _read_meta_safe(task_dir)
        if not meta or meta.get("contract_version") != 1 or meta.get("status") != "draft":
            continue
        revision = meta.get("revision")
        if isinstance(revision, int) and revision >= 1:
            candidates.append({"spec_id": meta.get("spec_id") or meta.get("task_id") or task_dir.name, "revision": revision, "spec_kind": meta.get("spec_kind") or (meta.get("spec") or {}).get("spec_kind")})
    if not candidates:
        raise ReferenceError("reference_missing", detail="current_draft_spec_missing")
    if len(candidates) > 1:
        raise ReferenceError("reference_ambiguous", detail="current_draft_spec_ambiguous", choices=[
            {"selector": f"choice:{index}", "spec_kind": item.get("spec_kind")} for index, item in enumerate(candidates, 1)
        ])
    return candidates[0]


# ---------------------------------------------------------------------------
# Public resolver
# ---------------------------------------------------------------------------


def normalize_reference(ref: Any) -> str:
    """Normalize and validate a semantic reference name."""
    if not isinstance(ref, str) or not ref:
        raise ReferenceError("reference_missing", detail="reference_not_supplied")
    if ref not in SUPPORTED_REFS:
        raise ReferenceError("reference_invalid", detail=f"unknown_reference:{ref}")
    return _ALIAS_SOURCE.get(ref, ref)


def _registered_workspace_ids() -> list[str]:
    registry = _load_registry()
    return sorted(key for key in registry if isinstance(key, str) and key)


def _resolve_workspace_for_start(
    ref: str,
    workspace_id: str | None,
    *,
    trusted_session_id: str | None = None,
    trusted_principal: str | None = None,
) -> str:
    """Find one registry-backed workspace for semantic start resolution."""
    trusted = workspace_id or os.environ.get("AOTA_TRUSTED_WORKSPACE_ID", "").strip()
    if trusted:
        return trusted
    candidates: list[str] = []
    errors: list[ReferenceError] = []
    for candidate in _registered_workspace_ids():
        try:
            resolve(
                candidate,
                ref,
                trusted_session_id=trusted_session_id,
                trusted_principal=trusted_principal,
            )
        except ReferenceError as exc:
            if exc.code not in {"reference_missing", "workspace_binding_mismatch"}:
                errors.append(exc)
            continue
        candidates.append(candidate)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise ReferenceError("reference_ambiguous", detail="multiple_workspace_candidates")
    if errors:
        # Preserve the first deterministic validation failure instead of
        # converting a stale or mismatched canonical artifact to "missing".
        raise errors[0]
    raise ReferenceError("reference_missing", detail="no_workspace_candidate")


def resolve(
    workspace_id: str,
    ref: Any,
    *,
    trusted_reference: Mapping[str, Any] | None = None,
    trusted_session_id: str | None = None,
    trusted_principal: str | None = None,
) -> dict[str, Any]:
    """Resolve *ref* for *workspace_id* into a bounded binding envelope.

    Returns a dict with ``status``, ``reference_kind``, ``resolution_source``,
    and the validated binding fields.  Raises :class:`ReferenceError` on any
    ambiguity, staleness, or mismatch.

    The resolver never accepts a ``project_id`` argument; project identity is
    resolved from canonical bindings, not from caller text.
    """
    if not isinstance(workspace_id, str) or not workspace_id:
        raise ReferenceError("workspace_binding_mismatch", detail="workspace_id_required")
    requested_ref = ref
    source = normalize_reference(ref)

    if requested_ref == "active_frozen_spec" and _is_worker_context():
        raise ReferenceError("worker_binding_change_forbidden")

    # Handler kwargs/runtime context are trusted metadata.  The model-facing
    # semantic reference remains only ``task_ref``.
    if requested_ref in {"active_frozen_spec", "active_task", "current_task"} and trusted_session_id is None:
        context = trusted_session_context()
        trusted_session_id = context.session_id
        trusted_principal = context.principal

    envelope: dict[str, Any] = {
        "status": "resolved",
        "reference_kind": source,
        "workspace_id": workspace_id,
        "resolution_source": source,
    }

    # 1. explicit trusted reference — caller supplied exact ids; validated.
    if source == "explicit_trusted_reference":
        if not isinstance(trusted_reference, Mapping):
            raise ReferenceError("reference_missing", detail="trusted_reference_required")
        task_id = trusted_reference.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise ReferenceError("reference_missing", detail="trusted_task_id_required")
        task_dir = get_task_dir(workspace_id, task_id)
        meta = _read_meta_safe(task_dir)
        if not meta:
            raise ReferenceError("reference_missing", detail="trusted_task_not_found")
        if meta.get("status") == "running":
            binding = _active_profile_task_binding(workspace_id)
        else:
            meta_copy = dict(meta)
            meta_copy["_task_dir"] = str(task_dir)
            _verify_frozen_canonical(meta_copy)
            binding = _spec_binding(meta)
        if not binding or binding.get("task_id", meta.get("task_id")) != task_id:
            raise ReferenceError("binding_mismatch", detail="trusted_task_binding_mismatch")
        checks = {
            "task_id": task_id,
            "revision": trusted_reference.get("revision"),
            "spec_hash": trusted_reference.get("spec_hash"),
            "spec_sha256": trusted_reference.get("spec_sha256"),
        }
        for key, expected in checks.items():
            if expected is not None and binding.get(key) != expected:
                raise ReferenceError("binding_mismatch", detail=f"trusted_reference:{key}")
        envelope["task_ref"] = {"task_id": task_id, "spec_revision": binding.get("revision"), "spec_hash": binding.get("spec_hash"), "spec_sha256": binding.get("spec_sha256")}
        envelope["spec_ref"] = {k: binding.get(k) for k in ("spec_id", "revision", "spec_hash", "spec_sha256", "spec_kind", "resolved_profile")}
        envelope["work_item_ref"] = {"work_item_id": binding.get("work_item_id")}
        envelope["workspace_context"] = _full_workspace_context(meta)
        return envelope

    # 2. active Profile Task binding
    if source == "active_profile_task":
        binding = _session_active_task_binding(workspace_id, trusted_session_id, trusted_principal)
        if binding is None and not trusted_session_id:
            binding = _active_profile_task_binding(workspace_id)
        if binding is None:
            raise ReferenceError("reference_missing", detail="no_active_profile_task")
        envelope["task_ref"] = {"task_id": binding.get("task_id")}
        envelope["spec_ref"] = {k: binding.get(k) for k in ("spec_id", "revision", "spec_hash", "spec_sha256", "spec_kind", "resolved_profile")}
        envelope["task_ref"]["spec_revision"] = binding.get("revision")
        envelope["task_ref"]["spec_hash"] = binding.get("spec_hash")
        envelope["task_ref"]["spec_sha256"] = binding.get("spec_sha256")
        envelope["work_item_ref"] = {"work_item_id": binding.get("work_item_id")}
        wb = _full_workspace_context(binding.get("_meta", {}))
        if wb:
            envelope["workspace_context"] = wb
        return envelope

    # 3. frozen SPEC binding
    if source == "frozen_spec":
        binding = None
        if requested_ref == "active_frozen_spec":
            binding = _session_active_spec_binding(
                workspace_id, trusted_session_id, trusted_principal
            )
        # Workspace-wide uniqueness is explicitly a fallback.  A present
        # session binding that failed validation has already raised above and
        # must not reach this branch.
        if binding is None:
            if requested_ref == "active_frozen_spec" and trusted_session_id and trusted_principal in TRUSTED_PRINCIPALS:
                raise ReferenceError("reference_missing", detail="active_frozen_spec_missing")
            binding = _frozen_spec_binding(workspace_id)
        if binding is None:
            raise ReferenceError("reference_missing", detail="no_frozen_spec")
        envelope["spec_ref"] = {k: binding.get(k) for k in ("spec_id", "revision", "spec_hash", "spec_sha256", "spec_kind", "resolved_profile", "work_item_id", "project_id", "subject_task_id", "architecture_mode")}
        envelope["task_ref"] = {"task_id": binding.get("task_id"), "spec_revision": binding.get("revision"), "spec_hash": binding.get("spec_hash"), "spec_sha256": binding.get("spec_sha256")}
        envelope["work_item_ref"] = {"work_item_id": binding.get("work_item_id")}
        envelope["workspace_context"] = _full_workspace_context(binding.get("_meta", {}))
        return envelope

    # 4. active Work Item / linked Plan
    if source == "active_work_item" or source == "current_plan":
        binding = _active_work_item(workspace_id)
        if binding is None:
            raise ReferenceError("reference_missing", detail="no_active_work_item")
        envelope["plan_ref"] = {k: binding.get(k) for k in ("plan_id", "plan_revision", "plan_sha256")}
        envelope["work_item_ref"] = {k: binding.get(k) for k in ("active_milestone_id", "active_work_item_id")}
        return envelope

    # 5. current workspace selection decision
    if source == "current_workspace_selection":
        binding = _current_workspace_selection(workspace_id)
        if binding is None:
            raise ReferenceError("reference_missing", detail="no_workspace_selection")
        envelope["workspace_context"] = binding
        return envelope

    raise ReferenceError("reference_invalid", detail=f"unhandled_source:{source}")


def resolve_for_start(
    workspace_id: str | None,
    ref: Any,
    *,
    trusted_session_id: str | None = None,
    trusted_principal: str | None = None,
) -> dict[str, Any]:
    """Resolve a semantic reference for the canonical Profile Task start path.

    Returns the exact fields the start handler needs: ``task_id``,
    ``expected_revision``, ``expected_spec_hash`` (canonical), and
    ``expected_spec_sha256`` (raw file).  The two hashes are kept distinct and
    never aliased.  Workers cannot use this to change a frozen binding.
    """
    if _is_worker_context():
        raise ReferenceError("worker_binding_change_forbidden")
    if trusted_session_id is None:
        context = trusted_session_context()
        trusted_session_id = context.session_id
        trusted_principal = context.principal
    resolved_workspace_id = _resolve_workspace_for_start(
        ref,
        workspace_id,
        trusted_session_id=trusted_session_id,
        trusted_principal=trusted_principal,
    )
    envelope = resolve(
        resolved_workspace_id,
        ref,
        trusted_session_id=trusted_session_id,
        trusted_principal=trusted_principal,
    )
    spec_ref = envelope.get("spec_ref") or {}
    task_ref = envelope.get("task_ref") or {}
    task_id = task_ref.get("task_id")
    revision = task_ref.get("spec_revision") or spec_ref.get("revision")
    spec_hash = task_ref.get("spec_hash") or spec_ref.get("spec_hash")
    spec_sha256 = task_ref.get("spec_sha256") or spec_ref.get("spec_sha256")
    if not task_id or not revision or not spec_hash:
        raise ReferenceError("reference_missing", detail="start_binding_incomplete")
    if not spec_sha256:
        # Frozen canonical SPECs always store the raw SHA; missing means stale.
        raise ReferenceError("spec_stale", detail="raw_sha_binding_missing")
    return {
        "workspace_id": resolved_workspace_id,
        "task_id": task_id,
        "expected_revision": revision,
        "expected_spec_hash": spec_hash,
        "expected_spec_sha256": spec_sha256,
        "resolution_source": envelope.get("resolution_source"),
    }


def run_isolated_smoke() -> dict[str, str]:
    """Bounded stdlib-only smoke for the resolver contract.

    Exercises: supported-ref normalization, unknown-referreject, missing
    reference, and the worker-binding-change guard.
    """
    # Unknown reference is rejected.
    try:
        normalize_reference("latest")
        raise AssertionError("unknown reference accepted")
    except ReferenceError as exc:
        assert exc.code == "reference_invalid"
    # Empty reference is rejected.
    try:
        normalize_reference("")
        raise AssertionError("empty reference accepted")
    except ReferenceError as exc:
        assert exc.code == "reference_missing"
    # Aliases normalize to canonical sources.
    assert normalize_reference("active_frozen_spec") == "frozen_spec"
    assert normalize_reference("subject_task") == "active_profile_task"
    assert normalize_reference("current") == "frozen_spec"
    # Missing workspace raises a bounded error.
    try:
        resolve("", "frozen_spec")
        raise AssertionError("empty workspace accepted")
    except ReferenceError as exc:
        assert exc.code == "workspace_binding_mismatch"
    # Worker-context start resolution is forbidden.
    prior = os.environ.get("AOTA_PROFILE_TASK_ID")
    os.environ["AOTA_PROFILE_TASK_ID"] = "pt_smoke"
    os.environ["AOTA_PROFILE_TASK_START_ID"] = "pt_smoke"
    try:
        try:
            resolve_for_start("fixture", "frozen_spec")
            raise AssertionError("worker start resolution allowed")
        except ReferenceError as exc:
            assert exc.code == "worker_binding_change_forbidden"
    finally:
        if prior is None:
            os.environ.pop("AOTA_PROFILE_TASK_ID", None)
            os.environ.pop("AOTA_PROFILE_TASK_START_ID", None)
        else:
            os.environ["AOTA_PROFILE_TASK_ID"] = prior
    return {"status": "PASS", "scope": "reference_resolver_contract"}
