"""aota_operator_consistency_check — run consistency audit on a workspace (P10).

Read-only. Runs 23+ consistency rules against handoff, decision, and task
artifacts. Accepts workspace_id and exactly one of (task_id|handoff_id|decision_id).
Returns status (ok|warning|broken) + checks array.

No file mutations.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from ._operator_common import (
    validate_workspace_id,
    scan_tasks_by_status,
    scan_tasks,
    get_handoff_for_task,
    get_decision_for_handoff,
    get_decision_for_source_task,
    get_approval,
    is_approval_valid,
    TERMINAL_STATUSES,
    MAX_SCAN_TASKS,
    CC_TERMINAL_RECEIPT_MISSING,
    CC_NON_TERMINAL_RECEIPT_CLAIM,
    CC_HANDOFF_MISSING_RECOVERABLE,
    CC_HANDOFF_TASK_BINDING,
    CC_HANDOFF_TERMINAL_MISMATCH,
    CC_ROLE_CARD_DECLARATION,
    CC_CARD_FILENAME,
    CC_ACK_DECISION_MISMATCH,
    CC_DECISION_ACK_PENDING,
    CC_SOURCE_HANDOFF_EXISTS,
    CC_SOURCE_BINDING_CONSISTENCY,
    CC_SINGLE_ACTIVE_DECISION,
    CC_FOLLOWUP_TASK_CREATED,
    CC_FOLLOWUP_TASK_EXISTS,
    CC_FOLLOWUP_SOURCE_DECISION,
    CC_FOLLOWUP_HANDOFF_CONSISTENCY,
    CC_FOLLOWUP_PREDECESSOR,
    CC_TASK_KIND_CONSTRAINT,
    CC_REVIEW_SUBJECT,
    CC_REOPEN_PREDECESSOR,
    CC_APPROVAL_REV_HASH,
    CC_APPROVAL_STALE,
    CC_RESUME_KIND_MISMATCH,
    CC_TIMEOUT_METADATA,
)
from ._handoff_common import (
    find_handoff_path,
    get_handoff_pending_dir,
    get_handoff_ack_dir,
    read_handoff,
    read_role_card_content,
    _ROLE_ARTIFACT_MAP,
    _HANDOFF_ID_RE,
)
from ._orchestration_common import (
    find_decision_path,
    get_decision_dir,
    read_decision,
    _DECISION_DECISIONS,
    _FOLLOWUP_TASK_KINDS,
)
from ._task_spec_common import (
    get_task_dir,
    load_meta,
    STATUS_DRAFT,
    TASK_KINDS,
    read_json,
)
from ._role_contracts import (
    validate_role_contract,
    validate_implementation_semantics,
    validate_diagnosis_semantics,
    validate_architecture_semantics,
)

TOOL_NAME = "aota_operator_consistency_check"
TOOLSET_NAME = "aota_operator"

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Run a consistency audit on a workspace. "
        "Runs 23+ consistency rules against handoff, decision, and task artifacts. "
        "Accepts workspace_id and exactly one of (task_id|handoff_id|decision_id). "
        "Returns status (ok|warning|broken) + detailed checks array with evidence. "
        "Read-only: no file mutations."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {
                "type": "string",
                "description": "Registered workspace identifier",
            },
            "task_id": {
                "type": "string",
                "description": "Check consistency for a specific task (mutually exclusive with handoff_id and decision_id)",
            },
            "handoff_id": {
                "type": "string",
                "description": "Check consistency for a specific handoff (mutually exclusive with task_id and decision_id)",
            },
            "decision_id": {
                "type": "string",
                "description": "Check consistency for a specific decision (mutually exclusive with task_id and handoff_id)",
            },
        },
        "required": ["workspace_id"],
        "additionalProperties": False,
    },
}

# Known card names from _ROLE_ARTIFACT_MAP
_KNOWN_CARD_NAMES = {
    "CARD.json": "coder",
    "DIAGNOSIS_CARD.json": "debugger",
    "REVIEW_CARD.json": "reviewer",
    "ARCHITECT_CARD.json": "architect",
}


def handle(args: dict, **_kwargs) -> str:
    """Handle aota_operator_consistency_check tool invocation."""
    try:
        result = _do_check(args)
        return json.dumps(result, sort_keys=True)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, sort_keys=True)


def _do_check(args: dict) -> dict[str, Any]:
    workspace_id: str = args.get("workspace_id", "")
    task_id: Optional[str] = args.get("task_id")
    handoff_id: Optional[str] = args.get("handoff_id")
    decision_id: Optional[str] = args.get("decision_id")

    # Validate workspace
    err = validate_workspace_id(workspace_id)
    if err:
        return {"status": "error", "error": f"invalid_workspace_id: {err}"}

    # Validate exactly one ID
    ids_specified = sum(1 for x in [task_id, handoff_id, decision_id] if x)
    if ids_specified == 0:
        return {"status": "error", "error": "one of task_id, handoff_id, decision_id is required"}
    if ids_specified > 1:
        return {"status": "error", "error": "task_id, handoff_id, decision_id are mutually exclusive"}

    checks: list[dict[str, Any]] = []

    if task_id is not None:
        _run_task_checks(workspace_id, task_id, checks)
    elif handoff_id is not None:
        _run_handoff_checks(workspace_id, handoff_id, checks)
    elif decision_id is not None:
        _run_decision_checks(workspace_id, decision_id, checks)

    # Determine overall status
    overall = "ok"
    for c in checks:
        if c.get("status") == "broken":
            overall = "broken"
            break
        if c.get("status") == "warning" and overall == "ok":
            overall = "warning"

    return {
        "status": overall,
        "workspace_id": workspace_id,
        "task_id": task_id,
        "handoff_id": handoff_id,
        "decision_id": decision_id,
        "checks": checks,
    }


# ---------------------------------------------------------------------------
# Task-level checks
# ---------------------------------------------------------------------------


def _run_task_checks(
    workspace_id: str, task_id: str, checks: list[dict[str, Any]]
) -> None:
    """Run consistency checks scoped to a specific task."""
    task_dir = get_task_dir(workspace_id, task_id)
    if not task_dir.is_dir():
        checks.append({
            "code": "TASK_NOT_FOUND",
            "status": "broken",
            "evidence": f"Task directory not found for {task_id}",
        })
        return

    try:
        meta = load_meta(task_dir)
    except Exception as e:
        checks.append({
            "code": "TASK_META_UNREADABLE",
            "status": "broken",
            "evidence": f"Cannot read meta.json: {str(e)}",
        })
        return

    status = meta.get("status", "")
    task_kind = meta.get("task_kind", "")
    execution = meta.get("execution", {})
    start_id = execution.get("start_id", "")
    profile = execution.get("profile", meta.get("profile_hint", ""))
    completion_receipt_path = execution.get("completion_receipt_path", "")
    orchestration_ctx = meta.get("orchestration_context", {})
    predecessor_task_id = orchestration_ctx.get("predecessor_task_id") if orchestration_ctx else None
    source_decision_id = orchestration_ctx.get("source_decision_id") if orchestration_ctx else None
    source_handoff_id = orchestration_ctx.get("source_handoff_id") if orchestration_ctx else None

    # P11-K: Schema version, spec fields, and role contract checks
    schema_version = meta.get("schema_version", 1)

    # 1. SCHEMA_VERSION_LEGACY_WARNING
    if schema_version == 1:
        checks.append({
            "code": "SCHEMA_VERSION_LEGACY",
            "status": "warning",
            "evidence": f"Task {task_id} uses legacy schema_version=1 (no role contract)",
        })

    # 2. PROCESS_PATH_INVALID
    spec = meta.get("spec", {})
    process_path = spec.get("process_path")
    if process_path is not None:
        if process_path not in ("fast", "standard", "deep"):
            checks.append({
                "code": "PROCESS_PATH_INVALID",
                "status": "broken",
                "evidence": f"Task {task_id} has invalid process_path: {process_path!r}",
            })

    # 3. VALIDATION_TIER_INVALID
    validation_tier = spec.get("validation_tier")
    if validation_tier is not None:
        if not isinstance(validation_tier, int) or isinstance(validation_tier, bool):
            checks.append({
                "code": "VALIDATION_TIER_INVALID",
                "status": "broken",
                "evidence": f"Task {task_id} has invalid validation_tier type: {type(validation_tier).__name__}",
            })
        elif validation_tier not in (0, 1, 2, 3, 4):
            checks.append({
                "code": "VALIDATION_TIER_INVALID",
                "status": "broken",
                "evidence": f"Task {task_id} has invalid validation_tier: {validation_tier}",
            })

    # 6. HUMAN_CHECKPOINTS_INVALID
    human_checkpoints = spec.get("human_checkpoints", [])
    if human_checkpoints:
        if not isinstance(human_checkpoints, list):
            checks.append({
                "code": "HUMAN_CHECKPOINTS_INVALID",
                "status": "broken",
                "evidence": f"Task {task_id} human_checkpoints is not a list",
            })
        else:
            valid_values = {"deploy", "reload", "restart", "docker", "host_write",
                           "runtime_write", "migration", "destructive_file_operation",
                           "secret_change", "live_worker"}
            for i, cp in enumerate(human_checkpoints):
                if not isinstance(cp, str) or cp not in valid_values:
                    checks.append({
                        "code": "HUMAN_CHECKPOINTS_INVALID",
                        "status": "broken",
                        "evidence": f"Task {task_id} human_checkpoints[{i}] has invalid value: {cp!r}",
                    })
                    break

    # A. Terminal task should have completion receipt
    if status in TERMINAL_STATUSES:
        receipt_exists = False
        if completion_receipt_path:
            receipt_file = task_dir / completion_receipt_path
            receipt_exists = receipt_file.exists()

        if not receipt_exists:
            checks.append({
                "code": CC_TERMINAL_RECEIPT_MISSING,
                "status": "broken",
                "evidence": (
                    f"Task {task_id} has terminal status '{status}' "
                    f"but completion receipt is missing"
                ),
            })

    # A2. Timeout tasks should have timeout metadata
    if status == "timeout":
        timeout_triggered = execution.get("timeout_triggered")
        timeout_seconds = execution.get("timeout_seconds")
        timeout_at = execution.get("timeout_at")
        missing_fields: list[str] = []
        if not timeout_triggered:
            missing_fields.append("timeout_triggered")
        if not timeout_seconds:
            missing_fields.append("timeout_seconds")
        if not timeout_at:
            missing_fields.append("timeout_at")
        if missing_fields:
            checks.append({
                "code": CC_TIMEOUT_METADATA,
                "status": "warning",
                "evidence": (
                    f"Task {task_id} has status 'timeout' but missing "
                    f"timeout metadata fields: {', '.join(missing_fields)}"
                ),
            })

    # B. Non-terminal task should not claim receipt authority
    if status not in TERMINAL_STATUSES and completion_receipt_path:
        checks.append({
            "code": CC_NON_TERMINAL_RECEIPT_CLAIM,
            "status": "warning",
            "evidence": (
                f"Task {task_id} has status '{status}' but has a "
                f"completion_receipt_path '{completion_receipt_path}'"
            ),
        })

    # C. Terminal + receipt should have handoff
    if status in TERMINAL_STATUSES and start_id:
        handoff = get_handoff_for_task(workspace_id, task_id, start_id)
        if handoff is None:
            checks.append({
                "code": CC_HANDOFF_MISSING_RECOVERABLE,
                "status": "warning" if completion_receipt_path else "broken",
                "evidence": (
                    f"Task {task_id} is terminal '{status}' "
                    f"but no handoff found for (task_id={task_id}, start_id={start_id})"
                ),
            })

    # D. handoff.task_id/start_id must match task meta execution
    if start_id:
        handoff = get_handoff_for_task(workspace_id, task_id, start_id)
        if handoff is not None:
            h_task_id = handoff.get("task_id")
            h_start_id = handoff.get("start_id")
            mismatches: list[str] = []
            if h_task_id != task_id:
                mismatches.append(f"handoff.task_id={h_task_id} != {task_id}")
            if h_start_id != start_id:
                mismatches.append(f"handoff.start_id={h_start_id} != {start_id}")
            if mismatches:
                checks.append({
                    "code": CC_HANDOFF_TASK_BINDING,
                    "status": "broken",
                    "evidence": f"Handoff task binding mismatch: {'; '.join(mismatches)}",
                })

    # E. handoff terminal_status must match task status
    if start_id:
        handoff = get_handoff_for_task(workspace_id, task_id, start_id)
        if handoff is not None:
            h_terminal = handoff.get("terminal_status")
            if h_terminal and h_terminal != status:
                checks.append({
                    "code": CC_HANDOFF_TERMINAL_MISMATCH,
                    "status": "broken",
                    "evidence": (
                        f"Handoff terminal_status={h_terminal} "
                        f"does not match task status={status}"
                    ),
                })

    # F. role_artifact.card_exists=true but file missing
    if start_id:
        handoff = get_handoff_for_task(workspace_id, task_id, start_id)
        if handoff is not None:
            role_art = handoff.get("role_artifact", {})
            if role_art.get("card_exists") is True:
                card_name = role_art.get("card_name")
                if card_name:
                    card_path = task_dir / card_name
                    if not card_path.exists() or card_path.is_symlink():
                        checks.append({
                            "code": CC_ROLE_CARD_DECLARATION,
                            "status": "broken",
                            "evidence": (
                                f"Handoff declares card_exists=true for '{card_name}' "
                                f"but file does not exist in {task_dir}"
                            ),
                        })

    # G. Card filename must match profile known mapping
    if profile and profile in _ROLE_ARTIFACT_MAP:
        expected_card = _ROLE_ARTIFACT_MAP[profile].get("card_name")
        if expected_card and start_id:
            handoff = get_handoff_for_task(workspace_id, task_id, start_id)
            if handoff is not None:
                actual_card = handoff.get("role_artifact", {}).get("card_name")
                if actual_card and actual_card != expected_card:
                    checks.append({
                        "code": CC_CARD_FILENAME,
                        "status": "warning",
                        "evidence": (
                            f"Expected card '{expected_card}' for profile '{profile}', "
                            f"but handoff has '{actual_card}'"
                        ),
                    })

    # R. Task kind must match decision constraints
    if source_decision_id:
        dpath = find_decision_path(workspace_id, source_decision_id)
        if dpath:
            try:
                dec = read_decision(dpath)
                followup = dec.get("followup", {})
                expected_kind = followup.get("task_kind")
                if expected_kind and expected_kind != task_kind:
                    checks.append({
                        "code": CC_TASK_KIND_CONSTRAINT,
                        "status": "broken",
                        "evidence": (
                            f"Task kind {task_kind} does not match decision "
                            f"followup_task_kind {expected_kind}"
                        ),
                    })
            except Exception:
                pass

    # S. Review-required follow-up: subject_task_id=source task
    if task_kind == "review" and source_decision_id:
        dpath = find_decision_path(workspace_id, source_decision_id)
        if dpath:
            try:
                dec = read_decision(dpath)
                if dec.get("decision") == "review_required":
                    dec_source_task = dec.get("source_task_id")
                    subject_task_id = meta.get("subject_task_id")
                    if subject_task_id and dec_source_task and subject_task_id != dec_source_task:
                        checks.append({
                            "code": CC_REVIEW_SUBJECT,
                            "status": "broken",
                            "evidence": (
                                f"Review task subject_task_id={subject_task_id} "
                                f"does not match decision source_task_id={dec_source_task}"
                            ),
                        })
            except Exception:
                pass

    # P11-K: Review subject SPEC binding (if schema_version >= 2)
    if task_kind == "review" and schema_version >= 2:
        bound_rev = meta.get("subject_spec_revision")
        bound_hash = meta.get("subject_spec_sha256")
        subject_tid = meta.get("subject_task_id")
        if subject_tid:
            if bound_rev is None or not bound_hash:
                checks.append({
                    "code": "REVIEW_BINDING_MISSING",
                    "status": "warning",
                    "evidence": f"Review task {task_id} (schema v2) missing subject_spec_revision or subject_spec_sha256",
                })
            else:
                # Verify subject hasn't changed
                subject_dir = get_task_dir(workspace_id, subject_tid)
                if subject_dir.is_dir():
                    try:
                        s_meta = load_meta(subject_dir)
                        s_rev = s_meta.get("revision")
                        s_hash = s_meta.get("spec_sha256")
                        if s_rev != bound_rev or s_hash != bound_hash:
                            checks.append({
                                "code": "REVIEW_SUBJECT_STALE",
                                "status": "broken",
                                "evidence": f"Subject SPEC changed since review binding: bound rev={bound_rev}, current={s_rev}",
                            })
                    except Exception:
                        pass

    # Architecture-specific checks
    if task_kind == "architecture":
        # architecture task must not have APPROVAL.json
        approval_path = task_dir / "APPROVAL.json"
        if approval_path.exists():
            checks.append({
                "code": "ARCHITECTURE_HAS_APPROVAL",
                "status": "warning",
                "evidence": f"Architecture task {task_id} has APPROVAL.json but architecture tasks do not require approval",
            })
        # architecture_mode must be valid
        arch_mode = meta.get("architecture_mode", "")
        if arch_mode not in ("design_review", "spec_preflight"):
            checks.append({
                "code": "ARCHITECTURE_MODE_INVALID",
                "status": "broken",
                "evidence": f"Architecture task {task_id} has invalid architecture_mode: {arch_mode!r}",
            })
        # write_scope must be empty
        spec = meta.get("spec", {})
        if spec.get("write_scope", []):
            checks.append({
                "code": "ARCHITECTURE_WRITE_SCOPE_NOT_EMPTY",
                "status": "broken",
                "evidence": f"Architecture task {task_id} has non-empty write_scope",
            })
        # subject_task_id must exist
        if not meta.get("subject_task_id"):
            checks.append({
                "code": "ARCHITECTURE_MISSING_SUBJECT",
                "status": "broken",
                "evidence": f"Architecture task {task_id} missing subject_task_id",
            })

        # P11-J.1-A: spec_preflight must have subject SPEC binding
        if arch_mode == "spec_preflight":
            bound_rev = meta.get("subject_spec_revision")
            bound_hash = meta.get("subject_spec_sha256")
            if bound_rev is None or not bound_hash:
                checks.append({
                    "code": "PREFLIGHT_BINDING_MISSING",
                    "status": "broken",
                    "evidence": f"Architecture spec_preflight task {task_id} missing subject_spec_revision or subject_spec_sha256",
                })
            elif subject_task_id:
                # Verify subject SPEC hasn't changed
                subject_dir = get_task_dir(workspace_id, subject_task_id)
                if subject_dir.is_dir():
                    try:
                        s_meta = load_meta(subject_dir)
                        s_rev = s_meta.get("revision")
                        s_hash = s_meta.get("spec_sha256")
                        if s_rev != bound_rev or s_hash != bound_hash:
                            checks.append({
                                "code": "PREFLIGHT_STALE",
                                "status": "broken",
                                "evidence": f"Subject SPEC changed since preflight binding: bound rev={bound_rev}, current={s_rev}",
                            })
                    except Exception:
                        pass

    # P11-K: Role contract consistency (schema_version >= 2 only)
    if schema_version >= 2:
        role_contract = spec.get("role_contract", {})
        if not isinstance(role_contract, dict):
            checks.append({
                "code": "ROLE_CONTRACT_INVALID_TYPE",
                "status": "broken",
                "evidence": f"Task {task_id} role_contract is not a dict",
            })
        elif task_kind:
            # Import validation
            errors = list(validate_role_contract(task_kind, role_contract, arch_mode))
            if task_kind == "implementation":
                errors.extend(validate_implementation_semantics(role_contract))
            elif task_kind == "diagnosis":
                errors.extend(validate_diagnosis_semantics(role_contract))
            elif task_kind == "architecture":
                errors.extend(validate_architecture_semantics(role_contract, arch_mode))
            if errors:
                error_msgs = []
                for e in errors:
                    error_msgs.append(f"{e.get('field', '')}: {e.get('reason', '')}")
                checks.append({
                    "code": "ROLE_CONTRACT_VIOLATION",
                    "status": "warning",
                    "evidence": f"Task {task_id} role contract violations: {'; '.join(error_msgs[:3])}",
                })

    # T. Reopen_required from reviewer: predecessor_task_id=review subject
    if predecessor_task_id and source_decision_id:
        dpath = find_decision_path(workspace_id, source_decision_id)
        if dpath:
            try:
                dec = read_decision(dpath)
                if dec.get("decision") == "reopen_required":
                    dec_source = dec.get("source_task_id")
                    if dec_source and predecessor_task_id != dec_source:
                        checks.append({
                            "code": CC_REOPEN_PREDECESSOR,
                            "status": "broken",
                            "evidence": (
                                f"Task predecessor_task_id={predecessor_task_id} "
                                f"does not match decision source_task_id={dec_source} "
                                f"for reopen_required"
                            ),
                        })
            except Exception:
                pass

    # U. Implementation approved-ready: APPROVAL revision/hash must match current SPEC
    if task_kind == "implementation" and status == STATUS_DRAFT:
        approval = get_approval(workspace_id, task_id)
        if approval is not None:
            if not is_approval_valid(approval, meta):
                checks.append({
                    "code": CC_APPROVAL_REV_HASH,
                    "status": "warning",
                    "evidence": (
                        f"APPROVAL revision={approval.get('revision')}/hash="
                        f"{approval.get('spec_sha256')} does not match "
                        f"meta revision={meta.get('revision')}/hash="
                        f"{meta.get('spec_sha256')}"
                    ),
                })

    # V. Stale approval
    if task_kind == "implementation" and status == STATUS_DRAFT:
        approval = get_approval(workspace_id, task_id)
        if approval is not None:
            if approval.get("revision") != meta.get("revision"):
                checks.append({
                    "code": CC_APPROVAL_STALE,
                    "status": "warning",
                    "evidence": (
                        f"APPROVAL revision={approval.get('revision')} is stale; "
                        f"current SPEC revision={meta.get('revision')}"
                    ),
                })

    # Cross-reference: source_handoff_id/handoff consistency
    if source_handoff_id:
        hpath = find_handoff_path(workspace_id, source_handoff_id)
        if hpath is None:
            checks.append({
                "code": CC_SOURCE_HANDOFF_EXISTS,
                "status": "broken",
                "evidence": (
                    f"orchestration_context.source_handoff_id={source_handoff_id} "
                    f"not found"
                ),
            })
        else:
            try:
                hdata = read_handoff(hpath)
                if hdata.get("task_id") != task_id:
                    checks.append({
                        "code": CC_SOURCE_BINDING_CONSISTENCY,
                        "status": "warning",
                        "evidence": (
                            f"source_handoff references task {hdata.get('task_id')} "
                            f"but current task is {task_id}"
                        ),
                    })
                if hdata.get("profile") != profile:
                    checks.append({
                        "code": CC_SOURCE_BINDING_CONSISTENCY,
                        "status": "warning",
                        "evidence": (
                            f"source_handoff profile={hdata.get('profile')} "
                            f"does not match task profile={profile}"
                        ),
                    })
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Handoff-level checks
# ---------------------------------------------------------------------------


def _run_handoff_checks(
    workspace_id: str, handoff_id: str, checks: list[dict[str, Any]]
) -> None:
    """Run consistency checks scoped to a specific handoff."""
    hpath = find_handoff_path(workspace_id, handoff_id)
    if hpath is None:
        checks.append({
            "code": "HANDOFF_NOT_FOUND",
            "status": "broken",
            "evidence": f"Handoff artifact not found for {handoff_id}",
        })
        return

    try:
        handoff = read_handoff(hpath)
    except Exception as e:
        checks.append({
            "code": "HANDOFF_UNREADABLE",
            "status": "broken",
            "evidence": f"Cannot read handoff: {str(e)}",
        })
        return

    task_id = handoff.get("task_id", "")
    start_id = handoff.get("start_id", "")
    state = handoff.get("state", "")
    profile = handoff.get("profile", "")
    task_kind = handoff.get("task_kind", "")
    terminal_status = handoff.get("terminal_status", "")
    role_art = handoff.get("role_artifact", {})
    lifecycle = handoff.get("lifecycle", {})
    needs_input_reason = handoff.get("needs_input_reason")

    # D. handoff.task_id/start_id must match task meta execution
    if task_id:
        task_dir = get_task_dir(workspace_id, task_id)
        if task_dir.is_dir():
            try:
                meta = load_meta(task_dir)
                execution = meta.get("execution", {})
                meta_start_id = execution.get("start_id", "")
                meta_status = meta.get("status", "")

                if meta_start_id and meta_start_id != start_id:
                    checks.append({
                        "code": CC_HANDOFF_TASK_BINDING,
                        "status": "broken",
                        "evidence": (
                            f"Handoff start_id={start_id} does not match "
                            f"task meta start_id={meta_start_id}"
                        ),
                    })

                # E. handoff terminal_status must match task status
                if meta_status in TERMINAL_STATUSES and terminal_status != meta_status:
                    checks.append({
                        "code": CC_HANDOFF_TERMINAL_MISMATCH,
                        "status": "broken",
                        "evidence": (
                            f"Handoff terminal_status={terminal_status} "
                            f"does not match task status={meta_status}"
                        ),
                    })
            except Exception:
                pass
        else:
            checks.append({
                "code": CC_HANDOFF_TASK_BINDING,
                "status": "broken",
                "evidence": f"Handoff references task {task_id} but task directory not found",
            })

    # F. role_artifact.card_exists=true but file missing
    if role_art.get("card_exists") is True:
        card_name = role_art.get("card_name")
        if card_name and task_id:
            task_dir = get_task_dir(workspace_id, task_id)
            card_path = task_dir / card_name
            if not card_path.exists() or card_path.is_symlink():
                checks.append({
                    "code": CC_ROLE_CARD_DECLARATION,
                    "status": "broken",
                    "evidence": (
                        f"Handoff declares card_exists=true for '{card_name}' "
                        f"but file missing in task dir {task_id}"
                    ),
                })

    # G. Card filename must match profile known mapping
    if profile and profile in _ROLE_ARTIFACT_MAP:
        expected_card = _ROLE_ARTIFACT_MAP[profile].get("card_name")
        actual_card = role_art.get("card_name")
        if expected_card and actual_card and actual_card != expected_card:
            checks.append({
                "code": CC_CARD_FILENAME,
                "status": "warning",
                "evidence": (
                    f"Expected card '{expected_card}' for profile '{profile}', "
                    f"handoff has '{actual_card}'"
                ),
            })

    # H. Acknowledged handoff: decision should match ack artifact
    # I. Pending handoff with existing decision → DECISION_ACK_PENDING (warning)
    decision = get_decision_for_handoff(workspace_id, handoff_id)
    if decision is not None:
        if state == "pending":
            checks.append({
                "code": CC_DECISION_ACK_PENDING,
                "status": "warning",
                "evidence": (
                    f"Handoff {handoff_id} is pending but an orchestration decision "
                    f"{decision.get('decision_id')} already exists"
                ),
            })

        if state == "acknowledged":
            # Check ack artifact
            from ._handoff_common import read_ack_artifact
            ack_data = read_ack_artifact(workspace_id, handoff_id)
            if ack_data:
                ack_decision = ack_data.get("decision")
                dec_decision = decision.get("decision")
                if ack_decision and dec_decision and ack_decision != dec_decision:
                    checks.append({
                        "code": CC_ACK_DECISION_MISMATCH,
                        "status": "broken",
                        "evidence": (
                            f"Ack decision={ack_decision} does not match "
                            f"orchestration decision={dec_decision}"
                        ),
                    })

    # C. Terminal status should have handoff artifact for recoverable check
    if task_id:
        task_dir = get_task_dir(workspace_id, task_id)
        if task_dir.is_dir():
            try:
                meta = load_meta(task_dir)
                meta_status = meta.get("status", "")
                receipt_path = meta.get("execution", {}).get("completion_receipt_path", "")
                if meta_status in TERMINAL_STATUSES and not lifecycle.get("receipt_exists"):
                    checks.append({
                        "code": CC_TERMINAL_RECEIPT_MISSING,
                        "status": "broken",
                        "evidence": (
                            f"Task {task_id} is terminal '{meta_status}' "
                            f"but handoff lifecycle.receipt_exists is false"
                        ),
                    })
            except Exception:
                pass

    # L. One active decision per handoff
    dec_dir = get_decision_dir(workspace_id)
    matching_decisions: list[str] = []
    if dec_dir.is_dir():
        try:
            for entry in os.listdir(str(dec_dir)):
                if not entry.startswith("DECISION.") or not entry.endswith(".json"):
                    continue
                try:
                    ddata = read_decision(dec_dir / entry)
                except Exception:
                    continue
                if ddata.get("handoff_id") == handoff_id:
                    matching_decisions.append(ddata.get("decision_id", ""))
        except OSError:
            pass
    if len(matching_decisions) > 1:
        checks.append({
            "code": CC_SINGLE_ACTIVE_DECISION,
            "status": "warning",
            "evidence": (
                f"Multiple decisions ({len(matching_decisions)}) found "
                f"for handoff {handoff_id}: {', '.join(matching_decisions)}"
            ),
        })


# ---------------------------------------------------------------------------
# Decision-level checks
# ---------------------------------------------------------------------------


def _run_decision_checks(
    workspace_id: str, decision_id: str, checks: list[dict[str, Any]]
) -> None:
    """Run consistency checks scoped to a specific decision."""
    dpath = find_decision_path(workspace_id, decision_id)
    if dpath is None:
        checks.append({
            "code": "DECISION_NOT_FOUND",
            "status": "broken",
            "evidence": f"Decision artifact not found for {decision_id}",
        })
        return

    try:
        decision = read_decision(dpath)
    except Exception as e:
        checks.append({
            "code": "DECISION_UNREADABLE",
            "status": "broken",
            "evidence": f"Cannot read decision: {str(e)}",
        })
        return

    handoff_id = decision.get("handoff_id", "")
    source_task_id = decision.get("source_task_id", "")
    source_start_id = decision.get("source_start_id", "")
    source_profile = decision.get("source_profile", "")
    source_terminal_status = decision.get("source_terminal_status", "")
    state = decision.get("state", "")
    decision_value = decision.get("decision", "")
    followup = decision.get("followup", {})
    resume = decision.get("resume", {})

    # J. source_handoff_id must exist
    if handoff_id:
        hpath = find_handoff_path(workspace_id, handoff_id)
        if hpath is None:
            checks.append({
                "code": CC_SOURCE_HANDOFF_EXISTS,
                "status": "broken",
                "evidence": (
                    f"Decision references handoff_id={handoff_id} "
                    f"but handoff artifact not found"
                ),
            })
        else:
            # K. source_task_id/start_id/profile/status must match handoff
            try:
                hdata = read_handoff(hpath)
                mismatches: list[str] = []
                if hdata.get("task_id") != source_task_id:
                    mismatches.append(
                        f"source_task_id={source_task_id} != handoff.task_id={hdata.get('task_id')}"
                    )
                if hdata.get("start_id") != source_start_id:
                    mismatches.append(
                        f"source_start_id={source_start_id} != handoff.start_id={hdata.get('start_id')}"
                    )
                if hdata.get("profile") != source_profile:
                    mismatches.append(
                        f"source_profile={source_profile} != handoff.profile={hdata.get('profile')}"
                    )
                if hdata.get("terminal_status") != source_terminal_status:
                    mismatches.append(
                        f"source_terminal_status={source_terminal_status} != "
                        f"handoff.terminal_status={hdata.get('terminal_status')}"
                    )
                if mismatches:
                    checks.append({
                        "code": CC_SOURCE_BINDING_CONSISTENCY,
                        "status": "broken",
                        "evidence": f"Source binding mismatch: {'; '.join(mismatches)}",
                    })
            except Exception:
                pass
    else:
        checks.append({
            "code": CC_SOURCE_HANDOFF_EXISTS,
            "status": "broken",
            "evidence": "Decision has empty handoff_id",
        })

    # L. One active decision per handoff (check from decision side)
    if handoff_id:
        dec_dir = get_decision_dir(workspace_id)
        matching: list[str] = []
        if dec_dir.is_dir():
            try:
                for entry in os.listdir(str(dec_dir)):
                    if not entry.startswith("DECISION.") or not entry.endswith(".json"):
                        continue
                    if entry == dpath.name:
                        continue
                    try:
                        ddata = read_decision(dec_dir / entry)
                    except Exception:
                        continue
                    if ddata.get("handoff_id") == handoff_id:
                        matching.append(ddata.get("decision_id", ""))
            except OSError:
                pass
        if matching:
            checks.append({
                "code": CC_SINGLE_ACTIVE_DECISION,
                "status": "warning",
                "evidence": (
                    f"Another decision(s) exists for same handoff {handoff_id}: "
                    f"{', '.join(matching)}"
                ),
            })

    # K. Source task must exist and match
    if source_task_id:
        task_dir = get_task_dir(workspace_id, source_task_id)
        if not task_dir.is_dir():
            checks.append({
                "code": CC_SOURCE_BINDING_CONSISTENCY,
                "status": "broken",
                "evidence": f"Source task {source_task_id} directory not found",
            })
        else:
            try:
                meta = load_meta(task_dir)
                execution = meta.get("execution", {})
                mismatches = []
                if execution.get("start_id") != source_start_id:
                    mismatches.append(
                        f"source_start_id={source_start_id} != task.start_id={execution.get('start_id')}"
                    )
                if meta.get("status", "") != source_terminal_status:
                    mismatches.append(
                        f"source_terminal_status={source_terminal_status} != task.status="
                        f"{meta.get('status')}"
                    )
                if mismatches:
                    checks.append({
                        "code": CC_SOURCE_BINDING_CONSISTENCY,
                        "status": "warning",
                        "evidence": f"Source task binding mismatch: {'; '.join(mismatches)}",
                    })
            except Exception:
                pass

    # M. state=followup_created must have followup.task_id
    if state == "followup_created":
        followup_task_id = followup.get("task_id")
        if not followup_task_id:
            checks.append({
                "code": CC_FOLLOWUP_TASK_CREATED,
                "status": "broken",
                "evidence": (
                    f"Decision state={state} but followup.task_id is empty"
                ),
            })
        else:
            # N. followup.task_id must exist
            ft_dir = get_task_dir(workspace_id, followup_task_id)
            if not ft_dir.is_dir():
                checks.append({
                    "code": CC_FOLLOWUP_TASK_EXISTS,
                    "status": "broken",
                    "evidence": (
                        f"Decision followup.task_id={followup_task_id} "
                        f"but task directory not found"
                    ),
                })
            else:
                # O. follow-up meta.source_decision_id must equal decision_id
                try:
                    f_meta = load_meta(ft_dir)
                    ctx = f_meta.get("orchestration_context", {}) or {}
                    if ctx.get("source_decision_id") != decision_id:
                        checks.append({
                            "code": CC_FOLLOWUP_SOURCE_DECISION,
                            "status": "broken",
                            "evidence": (
                                f"Follow-up task {followup_task_id} "
                                f"orchestration_context.source_decision_id="
                                f"{ctx.get('source_decision_id')} "
                                f"does not match decision_id={decision_id}"
                            ),
                        })
                    # P. source_handoff_id must be consistent
                    if ctx.get("source_handoff_id") and handoff_id and ctx.get("source_handoff_id") != handoff_id:
                        checks.append({
                            "code": CC_FOLLOWUP_HANDOFF_CONSISTENCY,
                            "status": "broken",
                            "evidence": (
                                f"Follow-up task source_handoff_id={ctx.get('source_handoff_id')} "
                                f"does not match decision handoff_id={handoff_id}"
                            ),
                        })
                    # Q. predecessor_task_id must be consistent
                    if ctx.get("predecessor_task_id") and ctx.get("predecessor_task_id") != source_task_id:
                        checks.append({
                            "code": CC_FOLLOWUP_PREDECESSOR,
                            "status": "warning",
                            "evidence": (
                                f"Follow-up predecessor_task_id={ctx.get('predecessor_task_id')} "
                                f"does not match decision source_task_id={source_task_id}"
                            ),
                        })
                except Exception:
                    pass

    # S. Review-required: subject_task_id must be source task
    if decision_value == "review_required" and followup.get("task_id"):
        ft_dir = get_task_dir(workspace_id, followup["task_id"])
        if ft_dir.is_dir():
            try:
                f_meta = load_meta(ft_dir)
                if f_meta.get("subject_task_id") and f_meta.get("subject_task_id") != source_task_id:
                    checks.append({
                        "code": CC_REVIEW_SUBJECT,
                        "status": "broken",
                        "evidence": (
                            f"Review task subject_task_id={f_meta.get('subject_task_id')} "
                            f"does not match decision source_task_id={source_task_id}"
                        ),
                    })
            except Exception:
                pass

    # T. Reopen_required: predecessor_task_id = source task
    if decision_value == "reopen_required" and followup.get("task_id"):
        ft_dir = get_task_dir(workspace_id, followup["task_id"])
        if ft_dir.is_dir():
            try:
                f_meta = load_meta(ft_dir)
                ctx = f_meta.get("orchestration_context", {}) or {}
                if ctx.get("predecessor_task_id") and ctx["predecessor_task_id"] != source_task_id:
                    checks.append({
                        "code": CC_REOPEN_PREDECESSOR,
                        "status": "broken",
                        "evidence": (
                            f"Reopen task predecessor_task_id={ctx['predecessor_task_id']} "
                            f"does not match decision source_task_id={source_task_id}"
                        ),
                    })
            except Exception:
                pass

    # W. Resumed needs_user_input: resume kind must match follow-up kind
    if resume and resume.get("resumed") and state == "ready_for_followup":
        resume_kind = resume.get("followup_task_kind")
        followup_kind = followup.get("task_kind")
        if resume_kind and followup_kind and resume_kind != followup_kind:
            checks.append({
                "code": CC_RESUME_KIND_MISMATCH,
                "status": "warning",
                "evidence": (
                    f"Resume followup_task_kind={resume_kind} does not match "
                    f"decision followup.task_kind={followup_kind}"
                ),
            })

    # R. Task kind must match decision constraints (from decision side)
    if followup.get("task_id") and followup.get("task_kind"):
        ft_dir = get_task_dir(workspace_id, followup["task_id"])
        if ft_dir.is_dir():
            try:
                f_meta = load_meta(ft_dir)
                if f_meta.get("task_kind") != followup.get("task_kind"):
                    checks.append({
                        "code": CC_TASK_KIND_CONSTRAINT,
                        "status": "broken",
                        "evidence": (
                            f"Decision followup.task_kind={followup.get('task_kind')} "
                            f"but actual task kind={f_meta.get('task_kind')}"
                        ),
                    })
            except Exception:
                pass
