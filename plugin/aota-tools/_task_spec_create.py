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
    get_task_dir,
    load_meta,
    WorkspaceError,
    SPEC_SCHEMA_VERSION_CURRENT,
    validate_process_path,
    validate_validation_tier,
    validate_human_checkpoints,
    validate_role_contract_for_task,
    role_contract_errors_to_message,
    apply_defaults,
)
from ._spec_traceability import build_trusted_snapshot, validate_traceability_input

TOOL_NAME = "aota_task_spec_create"
TOOLSET_NAME = "aota_task_spec"

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Use to create a bounded AOTA task specification artifact. "
        "This tool only creates a draft SPEC; it does not approve or start "
        "execution. Use it before any profile execution task.\n\n"
        "Role-specific contract (role_contract object):\n"
        "- implementation: required_changes (list[str], required), change_budget (dict with max_changed_files, allow_create, allow_delete, allow_move, allow_dependency_change), behavioral_invariants, allowed_validation_targets, forbidden_operations, checkpoint_conditions, compatibility_requirements\n"
        "- diagnosis: observed_symptoms (list[str], required), diagnostic_questions (list[str], required), reproduction_context, suspected_components, initial_hypotheses, evidence_plan, mutation_policy (readonly|isolated_reproduction_only), confidence_expectation (exploratory|probable|confirmed_required)\n"
        "- review: artifacts_under_review (list[str], required), review_dimensions (list[str], required), acceptance_mapping_required (bool), verdict_rules, inconclusive_conditions, independence_requirements\n"
        "- architecture: review_questions (list[str], required), gate_criteria (list[str], required), constraints, risk_focus + design_review: problem_statement (required), proposed_design (required), alternatives_considered, blast_radius, rollback_strategy, compatibility_strategy, unresolved_decisions, validation_strategy + spec_preflight: preflight_dimensions (required)\n"
        "Forbidden fields from other task kinds will be rejected."
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
            "architecture_mode": {
                "type": "string",
                "enum": ["design_review", "spec_preflight"],
                "description": "Architecture review mode (required for architecture task kind)",
            },
            "process_path": {
                "type": "string",
                "enum": ["fast", "standard", "deep"],
                "description": "Process path: fast (low-risk, local, reversible), standard (multi-file, medium risk), deep (control-plane, security, data, cross-service, high-risk). Default: standard",
            },
            "validation_tier": {
                "type": "integer",
                "enum": [0, 1, 2, 3, 4],
                "description": "Validation tier: 0 (Static), 1 (Local Smoke), 2 (Integration), 3 (Runtime), 4 (Live E2E). Default: 0",
            },
            "human_checkpoints": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Human checkpoint triggers (e.g. deploy, reload, restart, docker, host_write, runtime_write, migration, destructive_file_operation, secret_change, live_worker)",
                "default": [],
            },
            "role_contract": {
                "type": "object",
                "description": "Role-specific contract fields. Required fields and allowed fields depend on task_kind. See tool description for per-kind schema. Forbidden fields from other task kinds will be rejected.",
                "default": {},
            },
            "traceability": {
                "type": "object",
                "description": "Optional strict source reference: {mode: standalone} or {mode: plan_linked, plan_id, milestone_id, work_item_id, architect_review_id?}. Trusted revision/SHA fields are never accepted.",
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
    architecture_mode: str | None = args.get("architecture_mode")
    process_path: str | None = args.get("process_path")
    validation_tier: int | None = args.get("validation_tier")
    human_checkpoints: list[str] = args.get("human_checkpoints") or []
    role_contract: dict | None = args.get("role_contract") or {}
    traceability_input: dict | None = args.get("traceability")

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
    err = validate_semantic_rules(task_kind, write_scope, subject_task_id, architecture_mode)
    if err:
        raise WorkspaceError(err)

    # ------------------------------------------------------------------
    # 2b. Scope overlap check
    # ------------------------------------------------------------------
    err = check_scope_overlap(write_scope, forbidden_scope)
    if err:
        raise WorkspaceError(err)

    # ------------------------------------------------------------------
    # 2c. P11-K: Validate shared new fields
    # ------------------------------------------------------------------
    err = validate_process_path(process_path)
    if err:
        raise WorkspaceError(err)
    err = validate_validation_tier(validation_tier)
    if err:
        raise WorkspaceError(err)
    err = validate_human_checkpoints(human_checkpoints)
    if err:
        raise WorkspaceError(err)

    # ------------------------------------------------------------------
    # 2d. P11-K: Validate role contract
    # ------------------------------------------------------------------
    role_errors = validate_role_contract_for_task(task_kind, role_contract, architecture_mode)
    if role_errors:
        msg = role_contract_errors_to_message(role_errors)
        raise WorkspaceError(f"role_contract_validation_failed: {msg}")

    # Apply defaults to role_contract
    if role_contract is None:
        role_contract = {}
    role_contract = apply_defaults(task_kind, role_contract, architecture_mode)

    # ------------------------------------------------------------------
    # 3. Resolve workspace
    # ------------------------------------------------------------------
    from ._workspace import resolve_workspace

    workspace_root = resolve_workspace(workspace_id)

    # ------------------------------------------------------------------
    # 3a. Validate the optional source reference against the trusted Plan.
    # ------------------------------------------------------------------
    source_traceability = None
    parsed_traceability = validate_traceability_input(traceability_input)
    if parsed_traceability is not None:
        source_traceability = build_trusted_snapshot(workspace_id, parsed_traceability)

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

    # P11-J.1-A: For spec_preflight, read subject SPEC revision/hash
    subject_spec_revision: int | None = None
    subject_spec_sha256: str | None = None
    if task_kind == "architecture" and architecture_mode == "spec_preflight" and subject_task_id:
        subject_dir = get_task_dir(workspace_id, subject_task_id)
        subject_meta = load_meta(subject_dir)
        subject_spec_revision = subject_meta.get("revision")
        subject_spec_sha256 = subject_meta.get("spec_sha256")
        if subject_spec_revision is None or not subject_spec_sha256:
            raise WorkspaceError(
                f"subject_spec_unreadable: cannot read revision/hash from subject task '{subject_task_id}'"
            )

    # P11-K: Review tasks also bind subject SPEC revision/hash
    if task_kind == "review" and subject_task_id:
        subject_dir = get_task_dir(workspace_id, subject_task_id)
        subject_meta = load_meta(subject_dir)
        subject_spec_revision = subject_meta.get("revision")
        subject_spec_sha256 = subject_meta.get("spec_sha256")
        if subject_spec_revision is None or not subject_spec_sha256:
            raise WorkspaceError(
                f"subject_spec_unreadable: cannot read revision/hash from subject task '{subject_task_id}'"
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
        architecture_mode=architecture_mode,
        subject_spec_revision=subject_spec_revision,
        subject_spec_sha256=subject_spec_sha256,
        process_path=process_path,
        validation_tier=validation_tier,
        human_checkpoints=human_checkpoints,
        role_contract=role_contract,
        source_traceability=source_traceability,
        spec_schema_version=SPEC_SCHEMA_VERSION_CURRENT,
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
        process_path=process_path,
        validation_tier=validation_tier,
        human_checkpoints=human_checkpoints,
        role_contract=role_contract,
        source_traceability=source_traceability,
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
        architecture_mode=architecture_mode,
        subject_spec_revision=subject_spec_revision,
        subject_spec_sha256=subject_spec_sha256,
        spec_schema_version=SPEC_SCHEMA_VERSION_CURRENT,
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
            "spec_schema_version": SPEC_SCHEMA_VERSION_CURRENT,
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
