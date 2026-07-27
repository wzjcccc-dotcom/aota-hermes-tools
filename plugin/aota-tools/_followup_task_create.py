"""aota_followup_task_create — create a bounded follow-up task based on an orchestration decision (P8-E).

Creates a draft task SPEC based on a previously recorded orchestration decision.
Does NOT auto-start the task, accept model-specified source_handoff_id/
predecessor_task_id/subject_task_id.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from ._handoff_common import find_handoff_path, read_handoff, validate_handoff_id
from ._orchestration_common import (
    _FOLLOWUP_TASK_KINDS,
    acquire_decision_lock,
    atomic_write_json,
    find_decision_path,
    get_decision_dir,
    get_decision_filename,
    read_decision,
    release_decision_lock,
    validate_decision_id,
    validate_workspace_id,
)
from ._task_spec_common import (
    PROFILE_TASK_ROOT,
    TASK_KINDS,
    TASK_KIND_PROFILE_HINT,
    RISK_LEVELS,
    STATUS_DRAFT,
    HUMAN_CHECKPOINT_POLICY_MAP,
    SOURCE_MUTATION_POLICY_MAP,
    FIELD_LIMITS,
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
    get_task_dir,
    load_meta,
    validate_task_reference,
    create_exclusive_task_dir,
    WorkspaceError,
    SPEC_SCHEMA_VERSION_CURRENT,
    validate_process_path,
    validate_validation_tier,
    validate_human_checkpoints,
    validate_role_contract_for_task,
    role_contract_errors_to_message,
    apply_defaults,
)
from ._role_contracts import FORBIDDEN_OPERATIONS

TOOL_NAME = "aota_followup_task_create"
TOOLSET_NAME = "aota_orchestration"

_FORBIDDEN_OPS_LIST = sorted(FORBIDDEN_OPERATIONS)
_FOLLOWUP_KINDS_LIST = sorted(_FOLLOWUP_TASK_KINDS)

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Create a bounded follow-up task based on a previously recorded "
        "orchestration decision. Derives source_handoff_id, predecessor_task_id, "
        "and subject_task_id from the decision artifact. "
        "Does NOT auto-start the task.\n\n"
        "FOLLOW-UP SEMANTICS:\n"
        "Follow-up tasks are created FROM an existing orchestration decision, "
        "NOT from scratch. The decision artifact carries the handoff lineage "
        "(source_handoff_id → predecessor_task_id → subject_task_id). "
        "Workers must NOT create their own follow-up mutation tasks; only "
        "task-main can create follow-up tasks via orchestration decisions.\n\n"
        "WHEN TO USE FOLLOW-UP vs NEW SPEC:\n"
        "- Use followup_task_create when: an orchestration decision explicitly "
        "requires a follow-up (decision=review_required/needs_followup/reopen_required). "
        "The decision provides the handoff context.\n"
        "- Use task_spec_create when: starting a brand new work item with no "
        "prior decision lineage. No handoff context is available.\n\n"
        "DECISION VALUE RULES:\n"
        "- review_required → task_kind defaults to 'review' (or must be 'review' if explicit)\n"
        "- needs_followup → task_kind must be explicitly provided\n"
        "- reopen_required → task_kind must be explicitly provided\n"
        "- accepted/no_action → follow-up not permitted\n\n"
        "FORBIDDEN OPERATIONS (for implementation role_contract):\n"
        "  " + ", ".join(_FORBIDDEN_OPS_LIST) + "\n\n"
        "VALID EXAMPLE (reviewer follow-up from review_required decision):\n"
        "  {workspace_id: my-ws, decision_id: od_..., "
        "title: Review implementation, goal: Review the coder changes, "
        "risk_level: low, read_scope: [plugin/**], "
        "acceptance_criteria: [spec compliance verified], "
        "stop_conditions: [all evidence reviewed], "
        "evidence_required: [SPEC.md, git diff]}\n\n"
        "VALID EXAMPLE (implementation correction from reopen_required):\n"
        "  {workspace_id: my-ws, decision_id: od_..., "
        "task_kind: implementation, "
        "title: Fix review findings, goal: Address reviewer feedback, "
        "risk_level: medium, read_scope: [plugin/**], "
        "write_scope: [plugin/**], "
        "acceptance_criteria: [all findings resolved], "
        "stop_conditions: [all changes applied], "
        "evidence_required: [git diff]}\n\n"
        "Role-specific contract (role_contract object):\n"
        "- implementation: required_changes (list[str], required), change_budget (dict with max_changed_files, allow_create, allow_delete, allow_move, allow_dependency_change), behavioral_invariants, allowed_validation_targets, forbidden_operations (accepted: " + ", ".join(_FORBIDDEN_OPS_LIST) + "), checkpoint_conditions, compatibility_requirements\n"
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
            "decision_id": {
                "type": "string",
                "description": "Orchestration decision ID to base this follow-up on",
            },
            "task_kind": {
                "type": "string",
                "enum": list(_FOLLOWUP_TASK_KINDS),
                "description": (
                    "Task kind for the follow-up. Accepted values: " + ", ".join(_FOLLOWUP_KINDS_LIST) + ". "
                    "Constraints depend on the "
                    "decision value. "
                    "review_required -> must be 'review' (default if omitted). "
                    "needs_followup/reopen_required -> must be explicitly provided. "
                    "Not required for decisions that do not permit follow-up."
                ),
            },
            "title": {
                "type": "string",
                "description": f"Short human-readable title (max {FIELD_LIMITS['title']} characters)",
            },
            "goal": {
                "type": "string",
                "description": f"Detailed goal description (max {FIELD_LIMITS['goal']} characters)",
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
            "architecture_mode": {
                "type": "string",
                "enum": ["design_review", "spec_preflight"],
                "description": "Architecture review mode (required when task_kind=architecture)",
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
                "description": "Role-specific contract fields. Required fields and allowed fields depend on task_kind. See tool description for per-kind schema. For implementation: forbidden_operations accepted values are " + ", ".join(_FORBIDDEN_OPS_LIST) + ". Forbidden fields will be rejected.",
                "default": {},
            },
        },
        "required": [
            "workspace_id",
            "decision_id",
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


def handle(args: dict, **_kwargs) -> str:
    """Handle aota_followup_task_create tool invocation."""
    try:
        result = _do_create(args)
        return json.dumps(result, sort_keys=True)
    except WorkspaceError as e:
        return json.dumps(
            {"status": "rejected", "error": str(e)}, sort_keys=True
        )
    except Exception as e:
        return json.dumps(
            {"status": "error", "error": str(e)}, sort_keys=True
        )


def _do_create(args: dict) -> dict[str, Any]:
    # ------------------------------------------------------------------
    # Extract parameters
    # ------------------------------------------------------------------
    workspace_id: str = args.get("workspace_id", "")
    decision_id: str = args.get("decision_id", "")
    task_kind: Optional[str] = args.get("task_kind")
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
    architecture_mode: str | None = args.get("architecture_mode")
    process_path: str | None = args.get("process_path")
    validation_tier: int | None = args.get("validation_tier")
    human_checkpoints: list[str] = args.get("human_checkpoints") or []
    role_contract: dict = args.get("role_contract") or {}

    # ------------------------------------------------------------------
    # 1. Validate workspace_id, decision_id
    # ------------------------------------------------------------------
    err = validate_workspace_id(workspace_id)
    if err:
        return {"status": "error", "error": f"invalid_workspace_id: {err}"}

    err = validate_decision_id(decision_id)
    if err:
        return {"status": "error", "error": f"invalid_decision_id: {err}"}

    # ------------------------------------------------------------------
    # 2. Find and read decision artifact
    # ------------------------------------------------------------------
    decision_path = find_decision_path(workspace_id, decision_id)
    if decision_path is None:
        return {
            "status": "error",
            "error": f"decision not found: {decision_id}",
        }

    try:
        decision = read_decision(decision_path)
    except (OSError, json.JSONDecodeError) as e:
        return {
            "status": "error",
            "error": f"failed to read decision artifact: {str(e)}",
        }

    # ------------------------------------------------------------------
    # 3. Verify decision.workspace_id == workspace_id
    # ------------------------------------------------------------------
    if decision.get("workspace_id") != workspace_id:
        return {
            "status": "error",
            "error": (
                f"decision workspace_id mismatch: "
                f"expected {workspace_id!r}, "
                f"got {decision.get('workspace_id')!r}"
            ),
        }

    # ------------------------------------------------------------------
    # 4. Verify decision.handoff_id is valid and find handoff
    # ------------------------------------------------------------------
    handoff_id = decision.get("handoff_id", "")
    err = validate_handoff_id(handoff_id)
    if err:
        return {
            "status": "error",
            "error": f"invalid handoff_id in decision: {err}",
        }

    handoff_path = find_handoff_path(workspace_id, handoff_id)
    if handoff_path is None:
        return {
            "status": "error",
            "error": f"handoff not found: {handoff_id}",
        }

    # ------------------------------------------------------------------
    # 5. Read handoff to get source_task_id
    # ------------------------------------------------------------------
    try:
        handoff_data = read_handoff(handoff_path)
    except (OSError, json.JSONDecodeError) as e:
        return {
            "status": "error",
            "error": f"failed to read handoff: {str(e)}",
        }

    source_handoff_task_id: str = handoff_data.get("task_id", "")

    # ------------------------------------------------------------------
    # 6. Check decision.state — must be "recorded"
    # ------------------------------------------------------------------
    decision_state = decision.get("state", "")
    decision_value = decision.get("decision", "")

    if decision_state == "closed":
        return {
            "status": "rejected",
            "error": (
                f"decision '{decision_id}' is closed "
                f"(decision={decision_value}), no follow-up allowed"
            ),
        }
    if decision_state == "awaiting_user":
        return {
            "status": "rejected",
            "error": (
                f"decision '{decision_id}' is awaiting user input, "
                f"needs resume first before follow-up task can be created"
            ),
        }

    # NEW P9: allow ready_for_followup if resume exists and resumed=true
    if decision_state == "ready_for_followup":
        resume = decision.get("resume")
        if resume is None or not resume.get("resumed"):
            return {
                "status": "rejected",
                "error": (
                    f"decision '{decision_id}' is in state ready_for_followup "
                    f"but resume metadata is missing or not marked as resumed"
                ),
            }
        # Validate task_kind matches resume.followup_task_kind
        resume_task_kind = resume.get("followup_task_kind")
        if task_kind is not None and task_kind != resume_task_kind:
            return {
                "status": "error",
                "error": (
                    f"task_kind mismatch: decision '{decision_id}' resume "
                    f"requires followup_task_kind={resume_task_kind!r}, "
                    f"but got {task_kind!r}"
                ),
            }
        if task_kind is None and resume_task_kind:
            task_kind = resume_task_kind
        # Fall through to the rest of the validation/create logic
        # which will set decision_state to followup_created on success

    # ------------------------------------------------------------------
    # 7. Check if decision already has followup.task_id set
    # ------------------------------------------------------------------
    followup = decision.get("followup", {})
    existing_task_id = followup.get("task_id")
    if existing_task_id is not None:
        # Idempotent: return existing task_id
        return {
            "status": "ok",
            "task_id": existing_task_id,
            "decision_id": decision_id,
            "workspace_id": workspace_id,
            "task_kind": followup.get("task_kind"),
            "idempotent": True,
        }

    # ------------------------------------------------------------------
    # 8. Validate task_kind
    # ------------------------------------------------------------------
    if task_kind is not None and task_kind not in _FOLLOWUP_TASK_KINDS:
        return {
            "status": "error",
            "error": (
                f"invalid task_kind: {task_kind!r}. "
                f"Accepted values: " + ", ".join(_FOLLOWUP_KINDS_LIST) + ". "
                f"Choose the correct task_kind for the follow-up."
            ),
        }

    if decision_value == "review_required":
        if task_kind is not None and task_kind != "review":
            return {
                "status": "error",
                "error": (
                    f"review_required decision requires task_kind "
                    f"to be 'review', got {task_kind!r}. "
                    f"review_required follow-ups must be review tasks."
                ),
            }
        resolved_task_kind = task_kind or "review"
    elif decision_value == "needs_followup":
        if not task_kind:
            return {
                "status": "error",
                "error": (
                    f"needs_followup decision requires an explicit "
                    f"task_kind. Accepted values: " + ", ".join(_FOLLOWUP_KINDS_LIST) + "."
                ),
            }
        resolved_task_kind = task_kind
    elif decision_value == "reopen_required":
        if not task_kind:
            return {
                "status": "error",
                "error": (
                    f"reopen_required decision requires an explicit "
                    f"task_kind. Accepted values: " + ", ".join(_FOLLOWUP_KINDS_LIST) + "."
                ),
            }
        resolved_task_kind = task_kind
    elif decision_value == "needs_user_input" and decision_state == "ready_for_followup":
        # P9: resumed needs_user_input decision — allow follow-up
        # task_kind already validated against resume.followup_task_kind above
        resolved_task_kind = task_kind
    else:
        # accepted, no_action, needs_user_input without resume — should have been caught earlier
        return {
            "status": "error",
            "error": (
                f"decision '{decision_value}' does not permit "
                f"follow-up task creation. Only review_required, needs_followup, "
                f"reopen_required decisions can create follow-up tasks."
            ),
        }

    # ------------------------------------------------------------------
    # 9. Derive lineage from handoff/decision
    # ------------------------------------------------------------------
    # predecessor_task_id = the task that the follow-up builds upon
    predecessor_task_id: str = source_handoff_task_id
    # For reopen_required from a reviewer handoff, predecessor should point
    # to the subject task (original implementation), not the review task
    handoff_subject = handoff_data.get("subject_task_id")
    if handoff_subject:
        predecessor_task_id = handoff_subject

    # subject_task_id (for review tasks) = handoff's task_id (the source task)
    subject_task_id: Optional[str] = None
    if resolved_task_kind == "review":
        subject_task_id = source_handoff_task_id
    if resolved_task_kind == "architecture":
        subject_task_id = source_handoff_task_id

    # P11-J.1-A: For spec_preflight, read subject SPEC revision/hash
    subject_spec_revision: int | None = None
    subject_spec_sha256: str | None = None
    if resolved_task_kind == "architecture" and architecture_mode == "spec_preflight" and subject_task_id:
        subject_dir = get_task_dir(workspace_id, subject_task_id)
        subject_meta = load_meta(subject_dir)
        subject_spec_revision = subject_meta.get("revision")
        subject_spec_sha256 = subject_meta.get("spec_sha256")
        if subject_spec_revision is None or not subject_spec_sha256:
            raise WorkspaceError(
                f"subject_spec_unreadable: cannot read revision/hash from subject task '{subject_task_id}'"
            )

    # P11-K: Review tasks also bind subject SPEC revision/hash
    if resolved_task_kind == "review" and subject_task_id:
        subject_dir = get_task_dir(workspace_id, subject_task_id)
        subject_meta = load_meta(subject_dir)
        subject_spec_revision = subject_meta.get("revision")
        subject_spec_sha256 = subject_meta.get("spec_sha256")
        if subject_spec_revision is None or not subject_spec_sha256:
            raise WorkspaceError(
                f"subject_spec_unreadable: cannot read revision/hash from subject task '{subject_task_id}'"
            )

    # ------------------------------------------------------------------
    # 10. Acquire decision lock (prevent duplicate follow-up creation)
    # ------------------------------------------------------------------
    lock_fd: Optional[int] = None
    try:
        lock_fd = acquire_decision_lock(workspace_id, decision_id, timeout=5.0)
    except TimeoutError:
        return {
            "status": "error",
            "error": f"could not acquire lock for decision: {decision_id}",
        }
    except RuntimeError as e:
        return {"status": "error", "error": f"lock error: {str(e)}"}

    try:
        # ------------------------------------------------------------------
        # 11. Under lock: re-check if followup.task_id already set (race)
        # ------------------------------------------------------------------
        try:
            fresh_decision = read_decision(decision_path)
        except (OSError, json.JSONDecodeError) as e:
            return {
                "status": "error",
                "error": f"failed to re-read decision: {str(e)}",
            }

        fresh_followup = fresh_decision.get("followup", {})
        fresh_task_id = fresh_followup.get("task_id")
        if fresh_task_id is not None:
            return {
                "status": "ok",
                "task_id": fresh_task_id,
                "decision_id": decision_id,
                "workspace_id": workspace_id,
                "task_kind": fresh_followup.get("task_kind"),
                "idempotent": True,
            }

        # ------------------------------------------------------------------
        # 12. Create the draft task (same logic as _task_spec_create._do_create)
        # ------------------------------------------------------------------

        # 12a. Validate scope expressions
        all_scope_lists = {
            "read_scope": read_scope,
            "write_scope": write_scope,
            "forbidden_scope": forbidden_scope,
        }
        for key, items in all_scope_lists.items():
            err = validate_scope_expressions(items)
            if err:
                raise WorkspaceError(f"{key}: {err}")

        # 12b. Validate semantic rules
        err = validate_semantic_rules(resolved_task_kind, write_scope, subject_task_id, architecture_mode)
        if err:
            raise WorkspaceError(err)

        # 12c. Scope overlap check
        err = check_scope_overlap(write_scope, forbidden_scope)
        if err:
            raise WorkspaceError(err)

        # 12c1. P11-K: Validate shared new fields
        err = validate_process_path(process_path)
        if err:
            raise WorkspaceError(err)
        err = validate_validation_tier(validation_tier)
        if err:
            raise WorkspaceError(err)
        err = validate_human_checkpoints(human_checkpoints)
        if err:
            raise WorkspaceError(err)

        # 12c2. P11-K: Validate role contract
        role_errors = validate_role_contract_for_task(resolved_task_kind, role_contract, architecture_mode)
        if role_errors:
            msg = role_contract_errors_to_message(role_errors)
            raise WorkspaceError(f"role_contract_validation_failed: {msg}")

        # Apply defaults to role_contract
        if role_contract is None:
            role_contract = {}
        role_contract = apply_defaults(resolved_task_kind, role_contract, architecture_mode)

        # 12d. Resolve workspace
        from ._workspace import resolve_workspace

        workspace_root = resolve_workspace(workspace_id)

        # 12e. Validate field limits
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

        # 12f. Validate reference tasks
        if subject_task_id:
            validate_task_reference(
                workspace_id, subject_task_id, "subject_task_id"
            )
        if predecessor_task_id:
            validate_task_reference(
                workspace_id, predecessor_task_id, "predecessor_task_id"
            )

        # 12g. Generate task_id + exclusive mkdir
        task_id, task_dir = create_exclusive_task_dir(workspace_id)

        profile_hint = TASK_KIND_PROFILE_HINT[resolved_task_kind]
        revision = 1
        status = STATUS_DRAFT
        now = utc_now_iso()

        # 12h. Render SPEC.md
        spec_md = render_spec_md(
            task_id=task_id,
            workspace_id=workspace_id,
            task_kind=resolved_task_kind,
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
            parent_task_id=predecessor_task_id,
            architecture_mode=architecture_mode,
            subject_spec_revision=subject_spec_revision,
            subject_spec_sha256=subject_spec_sha256,
            process_path=process_path,
            validation_tier=validation_tier,
            human_checkpoints=human_checkpoints,
            role_contract=role_contract,
            spec_schema_version=SPEC_SCHEMA_VERSION_CURRENT,
        )

        spec_md_bytes = len(spec_md.encode("utf-8"))
        max_bytes = 65536
        if spec_md_bytes > max_bytes:
            _cleanup_dir(task_dir)
            raise WorkspaceError(
                f"SPEC.md exceeds {max_bytes} bytes (got {spec_md_bytes})"
            )

        # 12i. Compute SHA-256
        spec_sha256 = compute_sha256(spec_md)

        # 12j. Build meta.json
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
        )

        human_checkpoint_policy = HUMAN_CHECKPOINT_POLICY_MAP[resolved_task_kind]
        source_mutation_policy = SOURCE_MUTATION_POLICY_MAP[resolved_task_kind]

        meta = build_meta(
            task_id=task_id,
            workspace_id=workspace_id,
            workspace_root_at_creation=str(workspace_root),
            task_kind=resolved_task_kind,
            profile_hint=profile_hint,
            risk_level=risk_level,
            revision=revision,
            created_at=now,
            updated_at=now,
            subject_task_id=subject_task_id,
            parent_task_id=predecessor_task_id,
            human_checkpoint_policy=human_checkpoint_policy,
            source_mutation_policy=source_mutation_policy,
            spec_sha256=spec_sha256,
            spec=spec_dict,
            architecture_mode=architecture_mode,
            subject_spec_revision=subject_spec_revision,
            subject_spec_sha256=subject_spec_sha256,
            spec_schema_version=SPEC_SCHEMA_VERSION_CURRENT,
        )

        # ADD orchestration_context to meta
        meta["orchestration_context"] = {
            "source_handoff_id": handoff_id,
            "source_decision_id": decision_id,
            "predecessor_task_id": predecessor_task_id,
        }

        # 12k. Atomic write both files
        try:
            spec_path = task_dir / "SPEC.md"
            meta_path = task_dir / "meta.json"
            atomic_write(spec_path, spec_md)
            write_json(meta_path, meta)
        except Exception:
            _cleanup_dir(task_dir)
            raise

        # ------------------------------------------------------------------
        # 13. Update decision artifact: set followup.task_id, created_at, state
        # ------------------------------------------------------------------
        updated_followup = dict(fresh_decision.get("followup", {}))
        updated_followup["task_kind"] = resolved_task_kind
        updated_followup["task_id"] = task_id
        updated_followup["created_at"] = now
        fresh_decision["followup"] = updated_followup
        fresh_decision["state"] = "followup_created"

        atomic_write_json(decision_path, fresh_decision)

    finally:
        if lock_fd is not None:
            release_decision_lock(lock_fd)

    # ------------------------------------------------------------------
    # 14. Return compact result
    # ------------------------------------------------------------------
    return {
        "status": "ok",
        "task_id": task_id,
        "decision_id": decision_id,
        "workspace_id": workspace_id,
        "task_kind": resolved_task_kind,
        "profile_hint": profile_hint,
        "risk_level": risk_level,
        "subject_task_id": subject_task_id,
        "predecessor_task_id": predecessor_task_id,
        "source_handoff_id": handoff_id,
        "spec_schema_version": SPEC_SCHEMA_VERSION_CURRENT,
        "idempotent": False,
    }


def _cleanup_dir(task_dir: Path) -> None:
    """Remove the task directory (and its contents) on failure."""
    import shutil

    try:
        if task_dir.exists():
            shutil.rmtree(str(task_dir))
    except Exception:
        pass
