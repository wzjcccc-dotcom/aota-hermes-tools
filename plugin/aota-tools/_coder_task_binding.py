"""Trusted frozen-SPEC binding shared by Coder-only bounded tools."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ._project_common import load_project
from ._project_registry import load_search_records
from ._spec_contract import ContractError, canonical_hash, validate_spec
from ._workspace import resolve_workspace


class CoderBindingError(ValueError):
    """Raised when a request is outside the trusted Coder task boundary."""


def load_coder_binding(task_id: object, spec_id: object) -> tuple[Path, dict[str, Any]]:
    """Return registered project root and immutable implementation SPEC.

    Identity is always taken from trusted worker markers.  Caller values only
    prove that the request refers to the active task; they never select a task
    or project.
    """
    if os.environ.get("AOTA_PROFILE_TASK_PROFILE") != "coder":
        raise CoderBindingError("profile_not_allowed")
    active_task = os.environ.get("AOTA_PROFILE_TASK_ID", "")
    start_id = os.environ.get("AOTA_PROFILE_TASK_START_ID", "")
    workspace_id = os.environ.get("AOTA_PROFILE_TASK_WORKSPACE_ID", "")
    if not active_task or not start_id or not workspace_id:
        raise CoderBindingError("trusted_task_context_missing")
    if task_id != active_task:
        raise CoderBindingError("task_binding_mismatch")
    task_root = Path(os.environ.get("AOTA_PROFILE_TASK_ROOT", "/aota-runtime/profile-tasks"))
    try:
        meta = json.loads((task_root / workspace_id / active_task / "meta.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CoderBindingError("task_meta_unavailable") from exc
    spec = meta.get("spec")
    if not isinstance(spec, dict):
        raise CoderBindingError("frozen_spec_missing")
    try:
        validate_spec(spec, frozen=True)
    except ContractError as exc:
        raise CoderBindingError(f"invalid_frozen_spec:{exc}") from exc
    if (
        spec_id != spec.get("spec_id")
        or meta.get("status") != "running"
        or meta.get("task_id") != active_task
        or meta.get("spec_kind") != "implementation"
        or meta.get("resolved_profile") != "coder"
        or meta.get("spec_hash") != spec.get("spec_hash")
        or canonical_hash(spec) != spec.get("spec_hash")
        or meta.get("execution", {}).get("start_id") != start_id
    ):
        raise CoderBindingError("frozen_spec_binding_mismatch")
    root = resolve_workspace(workspace_id)
    project_id = spec.get("project_id")
    records, _ = load_search_records(workspace_id, root)
    record = next((item for item in records if item.get("project_id") == project_id), None)
    if record is None:
        raise CoderBindingError("unregistered_project")
    project_root = (root / record["root"]).resolve(strict=False)
    try:
        project_root.relative_to(root.resolve())
        _, manifest = load_project(project_root / ".aota" / "project.yaml")
    except (ValueError, OSError) as exc:
        raise CoderBindingError("project_path_escape") from exc
    if manifest.get("project", {}).get("id") != project_id:
        raise CoderBindingError("project_binding_mismatch")
    return project_root, spec
