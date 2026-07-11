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
    validate_task_reference,
    create_exclusive_task_dir,
    WorkspaceError,
)

TOOL_NAME = "aota_followup_task_create"
TOOLSET_NAME = "aota_orchestration"

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Create a bounded follow-up task based on a previously recorded "
        "orchestration decision. Derives source_handoff_id, predecessor_task_id, "
        "and subject_task_id from the decision artifact. "
        "Does NOT auto-start the task."
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
                    "Task kind for the follow-up. Constraints depend on the "
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
                f"Must be one of: {', '.join(_FOLLOWUP_TASK_KINDS)}"
            ),
        }

    if decision_value == "review_required":
        if task_kind is not None and task_kind != "review":
            return {
                "status": "error",
                "error": (
                    f"review_required decision requires task_kind "
                    f"to be 'review', got {task_kind!r}"
                ),
            }
        resolved_task_kind = task_kind or "review"
    elif decision_value == "needs_followup":
        if not task_kind:
            return {
                "status": "error",
                "error": (
                    f"needs_followup decision requires an explicit "
                    f"task_kind"
                ),
            }
        resolved_task_kind = task_kind
    elif decision_value == "reopen_required":
        if not task_kind:
            return {
                "status": "error",
                "error": (
                    f"reopen_required decision requires an explicit "
                    f"task_kind"
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
                f"follow-up task creation"
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
        err = validate_semantic_rules(resolved_task_kind, write_scope, subject_task_id)
        if err:
            raise WorkspaceError(err)

        # 12c. Scope overlap check
        err = check_scope_overlap(write_scope, forbidden_scope)
        if err:
            raise WorkspaceError(err)

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
