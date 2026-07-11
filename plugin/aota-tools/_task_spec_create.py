"""aota_task_spec_create — create a bounded AOTA task specification artifact.

This tool only creates a draft SPEC; it does not approve or start execution.
Use it before any profile execution task.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ._task_spec_common import (
    PROFILE_TASK_ROOT,
    TASK_KINDS,
    TASK_KIND_PROFILE_HINT,
    RISK_LEVELS,
    STATUS_DRAFT,
    HUMAN_CHECKPOINT_POLICY_MAP,
    SOURCE_MUTATION_POLICY_MAP,
    generate_task_id,
    utc_now_iso,
    compute_sha256,
    validate_scope_expressions,
    validate_semantic_rules,
    check_scope_overlap,
    validate_field_limits,
    render_spec_md,
    build_spec_dict,
    build_meta,
    atomic_write,
    write_json,
    validate_task_reference,
    create_exclusive_task_dir,
    WorkspaceError,
)

TOOL_NAME = "aota_task_spec_create"
TOOLSET_NAME = "aota_task_spec"

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Use to create a bounded AOTA task specification artifact. "
        "This tool only creates a draft SPEC; it does not approve or start "
        "execution. Use it before any profile execution task."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {
                "type": "string",
                "description": "Registered workspace identifier (e.g. 'aota-runtime')",
            },
            "task_kind": {
                "type": "string",
                "enum": list(TASK_KINDS),
                "description": "Task kind: implementation, diagnosis, or review",
            },
            "title": {
                "type": "string",
                "description": "Short human-readable title (max 200 characters)",
            },
            "goal": {
                "type": "string",
                "description": "Detailed goal description (max 8000 characters)",
            },
            "risk_level": {
                "type": "string",
                "enum": list(RISK_LEVELS),
                "description": "Assessed risk level: low, medium, or high",
            },
            "known_inputs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Known inputs / context references",
                "default": [],
            },
            "read_scope": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Glob patterns defining read-accessible paths",
            },
            "write_scope": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Glob patterns defining write-accessible paths",
                "default": [],
            },
            "forbidden_scope": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Glob patterns explicitly forbidden",
                "default": [],
            },
            "acceptance_criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Measurable acceptance criteria",
            },
            "validation_policy": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Validation / review policy items",
                "default": [],
            },
            "stop_conditions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Conditions that stop task execution",
            },
            "evidence_required": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Required evidence items for completion",
            },
            "subject_task_id": {
                "type": "string",
                "description": "Subject task ID (required for review tasks)",
            },
            "parent_task_id": {
                "type": "string",
                "description": "Parent task ID for task hierarchy",
            },
        },
        "required": [
            "workspace_id",
            "task_kind",
            "title",
            "goal",
            "risk_level",
            "read_scope",
            "acceptance_criteria",
            "stop_conditions",
            "evidence_required",
        ],
        "additionalProperties": False,
    },
}


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handle(args: dict, **_kwargs) -> str:
    try:
        return _do_create(args)
    except WorkspaceError as e:
        return json.dumps(
            {"status": "rejected", "error": str(e)}, sort_keys=True
        )
    except Exception as e:
        return json.dumps(
            {"status": "failed", "error": str(e)}, sort_keys=True
        )


def _do_create(args: dict) -> str:
    # ------------------------------------------------------------------
    # Extract parameters
    # ------------------------------------------------------------------
    workspace_id: str = args.get("workspace_id", "")
    task_kind: str = args.get("task_kind", "")
    title: str = args.get("title", "")
    goal: str = args.get("goal", "")
    risk_level: str = args.get("risk_level", "")
    known_inputs: list[str] = args.get("known_inputs") or []
    read_scope: list[str] = args.get("read_scope", [])
    write_scope: list[str] = args.get("write_scope") or []
    forbidden_scope: list[str] = args.get("forbidden_scope") or []
    acceptance_criteria: list[str] = args.get("acceptance_criteria", [])
    validation_policy: list[str] = args.get("validation_policy") or []
    stop_conditions: list[str] = args.get("stop_conditions", [])
    evidence_required: list[str] = args.get("evidence_required", [])
    subject_task_id: str | None = args.get("subject_task_id")
    parent_task_id: str | None = args.get("parent_task_id")

    # ------------------------------------------------------------------
    # 1. Validate scope expressions
    # ------------------------------------------------------------------
    all_scope_lists = {
        "read_scope": read_scope,
        "write_scope": write_scope,
        "forbidden_scope": forbidden_scope,
    }
    for key, items in all_scope_lists.items():
        err = validate_scope_expressions(items)
        if err:
            raise WorkspaceError(f"{key}: {err}")

    # ------------------------------------------------------------------
    # 2. Validate semantic rules per task_kind
    # ------------------------------------------------------------------
    err = validate_semantic_rules(task_kind, write_scope, subject_task_id)
    if err:
        raise WorkspaceError(err)

    # ------------------------------------------------------------------
    # 2b. Scope overlap check
    # ------------------------------------------------------------------
    err = check_scope_overlap(write_scope, forbidden_scope)
    if err:
        raise WorkspaceError(err)

    # ------------------------------------------------------------------
    # 3. Resolve workspace
    # ------------------------------------------------------------------
    from ._workspace import resolve_workspace

    workspace_root = resolve_workspace(workspace_id)

    # ------------------------------------------------------------------
    # 4. Validate field limits
    # ------------------------------------------------------------------
    err = validate_field_limits(
        title,
        goal,
        known_inputs,
        read_scope,
        write_scope,
        forbidden_scope,
        acceptance_criteria,
        validation_policy,
        stop_conditions,
        evidence_required,
    )
    if err:
        raise WorkspaceError(err)

    # ------------------------------------------------------------------
    # 5. Validate reference tasks
    # ------------------------------------------------------------------
    if subject_task_id:
        validate_task_reference(
            workspace_id, subject_task_id, "subject_task_id"
        )
    if parent_task_id:
        validate_task_reference(
            workspace_id, parent_task_id, "parent_task_id"
        )

    # ------------------------------------------------------------------
    # 6. Generate task_id + exclusive mkdir
    # ------------------------------------------------------------------
    task_id, task_dir = create_exclusive_task_dir(workspace_id)

    profile_hint = TASK_KIND_PROFILE_HINT[task_kind]
    revision = 1
    status = STATUS_DRAFT
    now = utc_now_iso()

    # ------------------------------------------------------------------
    # 7. Render SPEC.md
    # ------------------------------------------------------------------
    spec_md = render_spec_md(
        task_id=task_id,
        workspace_id=workspace_id,
        task_kind=task_kind,
        profile_hint=profile_hint,
        risk_level=risk_level,
        revision=revision,
        status=status,
        goal=goal,
        known_inputs=known_inputs,
        read_scope=read_scope,
        write_scope=write_scope,
        forbidden_scope=forbidden_scope,
        acceptance_criteria=acceptance_criteria,
        validation_policy=validation_policy,
        stop_conditions=stop_conditions,
        evidence_required=evidence_required,
        subject_task_id=subject_task_id,
        parent_task_id=parent_task_id,
    )

    spec_md_bytes = len(spec_md.encode("utf-8"))
    max_bytes = 65536
    if spec_md_bytes > max_bytes:
        # Clean up directory
        _cleanup_dir(task_dir)
        raise WorkspaceError(
            f"SPEC.md exceeds {max_bytes} bytes (got {spec_md_bytes})"
        )

    # ------------------------------------------------------------------
    # 8. Compute SHA-256
    # ------------------------------------------------------------------
    spec_sha256 = compute_sha256(spec_md)

    # ------------------------------------------------------------------
    # 9. Build meta.json
    # ------------------------------------------------------------------
    spec_dict = build_spec_dict(
        title=title,
        goal=goal,
        known_inputs=known_inputs,
        read_scope=read_scope,
        write_scope=write_scope,
        forbidden_scope=forbidden_scope,
        acceptance_criteria=acceptance_criteria,
        validation_policy=validation_policy,
        stop_conditions=stop_conditions,
        evidence_required=evidence_required,
    )

    human_checkpoint_policy = HUMAN_CHECKPOINT_POLICY_MAP[task_kind]
    source_mutation_policy = SOURCE_MUTATION_POLICY_MAP[task_kind]

    meta = build_meta(
        task_id=task_id,
        workspace_id=workspace_id,
        workspace_root_at_creation=str(workspace_root),
        task_kind=task_kind,
        profile_hint=profile_hint,
        risk_level=risk_level,
        revision=revision,
        created_at=now,
        updated_at=now,
        subject_task_id=subject_task_id,
        parent_task_id=parent_task_id,
        human_checkpoint_policy=human_checkpoint_policy,
        source_mutation_policy=source_mutation_policy,
        spec_sha256=spec_sha256,
        spec=spec_dict,
    )

    # ------------------------------------------------------------------
    # 10. Atomic write both files
    # ------------------------------------------------------------------
    try:
        spec_path = task_dir / "SPEC.md"
        meta_path = task_dir / "meta.json"
        atomic_write(spec_path, spec_md)
        write_json(meta_path, meta)
    except Exception:
        _cleanup_dir(task_dir)
        raise

    # ------------------------------------------------------------------
    # 11. Return compact output
    # ------------------------------------------------------------------
    return json.dumps(
        {
            "status": "created",
            "task_id": task_id,
            "workspace_id": workspace_id,
            "task_kind": task_kind,
            "profile_hint": profile_hint,
            "risk_level": risk_level,
            "revision": revision,
            "spec_path": str(spec_path),
            "meta_path": str(meta_path),
            "spec_sha256": spec_sha256,
            "human_checkpoint_policy": human_checkpoint_policy,
            "error": None,
        },
        sort_keys=True,
    )


def _cleanup_dir(task_dir: Path) -> None:
    """Remove the task directory (and its contents) on failure."""
    import shutil

    try:
        if task_dir.exists():
            shutil.rmtree(str(task_dir))
    except Exception:
        pass
