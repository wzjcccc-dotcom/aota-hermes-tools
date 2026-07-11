"""aota_operator_inbox_open — open an exact operator inbox item with evidence projection (P10).

Read-only. Resolves item_id deterministically to the exact item.
Returns compact metadata + relevant evidence projection based on item_type.
No file mutations.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from ._operator_common import (
    validate_workspace_id,
    validate_item_id,
    parse_item_id,
    get_handoff_for_task,
    get_decision_for_handoff,
    get_decision_for_source_task,
    get_approval,
    is_approval_valid,
    build_handoff_item_id,
    build_decision_item_id,
    build_task_item_id,
    build_approval_item_id,
    ITEM_TYPE_PRIORITY,
    TERMINAL_STATUSES,
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
)
from ._task_spec_common import (
    get_task_dir,
    load_meta,
    STATUS_DRAFT,
    read_json,
)

TOOL_NAME = "aota_operator_inbox_open"
TOOLSET_NAME = "aota_operator"

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Open an exact operator inbox item by item_id. "
        "Resolves item_id deterministically to find the exact handoff, decision, "
        "task, or approval artifact. Returns compact metadata plus relevant "
        "evidence projection (different for each item_type). "
        "Accepts no path/task_id/handoff_id/decision_id overrides. "
        "Read-only: no file mutations."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {
                "type": "string",
                "description": "Registered workspace identifier",
            },
            "item_id": {
                "type": "string",
                "description": "Exact operator inbox item ID (oi_handoff_<hid>, oi_decision_<did>, oi_task_<tid>_<reason>, oi_approval_<tid>)",
            },
        },
        "required": ["workspace_id", "item_id"],
        "additionalProperties": False,
    },
}


def handle(args: dict, **_kwargs) -> str:
    """Handle aota_operator_inbox_open tool invocation."""
    try:
        result = _do_open(args)
        return json.dumps(result, sort_keys=True)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, sort_keys=True)


def _do_open(args: dict) -> dict[str, Any]:
    workspace_id: str = args.get("workspace_id", "")
    item_id: str = args.get("item_id", "")

    # 1. Validate inputs
    err = validate_workspace_id(workspace_id)
    if err:
        return {"status": "error", "error": f"invalid_workspace_id: {err}"}

    err = validate_item_id(item_id)
    if err:
        return {"status": "error", "error": f"invalid_item_id: {err}"}

    parsed = parse_item_id(item_id)
    if parsed is None:
        return {"status": "error", "error": f"cannot parse item_id: {item_id}"}

    item_type = parsed.get("type", "")
    inner_id = parsed.get("id", "")
    suffix = parsed.get("suffix", "")

    # 2. Resolve by type
    if item_type == "handoff":
        return _open_handoff_item(workspace_id, inner_id)
    elif item_type == "decision":
        return _open_decision_item(workspace_id, inner_id)
    elif item_type == "task":
        return _open_task_item(workspace_id, inner_id, suffix)
    elif item_type == "approval":
        return _open_approval_item(workspace_id, inner_id)
    else:
        return {"status": "error", "error": f"unknown item_type: {item_type}"}


# ---------------------------------------------------------------------------
# Handoff item opener
# ---------------------------------------------------------------------------


def _open_handoff_item(workspace_id: str, handoff_id: str) -> dict[str, Any]:
    """Open a handoff inbox item."""
    hpath = find_handoff_path(workspace_id, handoff_id)
    if hpath is None:
        return {"status": "error", "error": f"handoff not found: {handoff_id}"}

    try:
        handoff = read_handoff(hpath)
    except (OSError, json.JSONDecodeError) as e:
        return {"status": "error", "error": f"failed to read handoff: {str(e)}"}

    # Build compact evidence
    task_id = handoff.get("task_id", "")
    role_art = handoff.get("role_artifact", {})
    lifecycle = handoff.get("lifecycle", {})

    # Read task meta if available
    task_meta: Optional[dict[str, Any]] = None
    if task_id:
        task_dir = get_task_dir(workspace_id, task_id)
        if task_dir.is_dir():
            try:
                task_meta = load_meta(task_dir)
            except Exception:
                pass

    # Read role card content (compact)
    card_content: Optional[str] = None
    card_missing = True
    if task_id and role_art.get("card_name"):
        task_dir = get_task_dir(workspace_id, task_id)
        card_content, card_missing = read_role_card_content(
            task_dir, role_art.get("card_name")
        )

    # Read associated decision if any
    decision: Optional[dict[str, Any]] = get_decision_for_handoff(workspace_id, handoff_id)

    # Determine state (pending or acknowledged)
    pending_dir = get_handoff_pending_dir(workspace_id)
    ack_dir = get_handoff_ack_dir(workspace_id)
    in_pending = (pending_dir / f"handoff.{handoff_id}.json").exists()
    in_ack = (ack_dir / f"handoff.{handoff_id}.json").exists()
    state = "acknowledged" if in_ack else ("pending" if in_pending else "unknown")

    result: dict[str, Any] = {
        "status": "ok",
        "item_id": build_handoff_item_id(handoff_id),
        "item_type": "pending_handoff",
        "handoff": {
            "handoff_id": handoff_id,
            "workspace_id": handoff.get("workspace_id"),
            "task_id": task_id,
            "start_id": handoff.get("start_id"),
            "profile": handoff.get("profile"),
            "task_kind": handoff.get("task_kind"),
            "terminal_status": handoff.get("terminal_status"),
            "state": state,
            "created_at": handoff.get("created_at"),
            "subject_task_id": handoff.get("subject_task_id"),
            "needs_input_reason": handoff.get("needs_input_reason"),
        },
        "role_artifact": role_art,
        "lifecycle": lifecycle,
        "card_content": card_content,
        "card_missing": card_missing,
        "task_meta": task_meta,
        "decision": decision,
        "recommended_action": {
            "tool": "aota_handoff_open" if state == "pending" else "aota_orchestration_decision_open",
            "hint": "Open full handoff details via aota_handoff_open",
        },
    }

    return result


# ---------------------------------------------------------------------------
# Decision item opener
# ---------------------------------------------------------------------------


def _open_decision_item(workspace_id: str, decision_id: str) -> dict[str, Any]:
    """Open a decision inbox item."""
    dpath = find_decision_path(workspace_id, decision_id)
    if dpath is None:
        return {"status": "error", "error": f"decision not found: {decision_id}"}

    try:
        decision = read_decision(dpath)
    except (OSError, json.JSONDecodeError) as e:
        return {"status": "error", "error": f"failed to read decision: {str(e)}"}

    state = decision.get("state", "")
    decision_value = decision.get("decision", "")
    handoff_id = decision.get("handoff_id", "")
    source_task_id = decision.get("source_task_id", "")
    followup = decision.get("followup", {})
    resume = decision.get("resume", {})

    # Determine item_type from state
    if state == "awaiting_user":
        item_type = "awaiting_user"
    elif state == "ready_for_followup" and followup.get("task_id") is None:
        item_type = "ready_for_followup"
    else:
        item_type = "decision"

    # Source handoff evidence
    handoff_evidence: Optional[dict[str, Any]] = None
    if handoff_id:
        hpath = find_handoff_path(workspace_id, handoff_id)
        if hpath:
            try:
                hdata = read_handoff(hpath)
                handoff_evidence = {
                    "handoff_id": handoff_id,
                    "state": hdata.get("state"),
                    "profile": hdata.get("profile"),
                    "task_kind": hdata.get("task_kind"),
                    "terminal_status": hdata.get("terminal_status"),
                    "created_at": hdata.get("created_at"),
                }
            except Exception:
                pass
        else:
            handoff_evidence = {"missing": True, "error": "handoff artifact not found"}

    # Source task evidence
    task_evidence: Optional[dict[str, Any]] = None
    if source_task_id:
        task_dir = get_task_dir(workspace_id, source_task_id)
        if task_dir.is_dir():
            try:
                meta = load_meta(task_dir)
                task_evidence = {
                    "task_id": meta.get("task_id"),
                    "status": meta.get("status"),
                    "task_kind": meta.get("task_kind"),
                    "profile_hint": meta.get("profile_hint"),
                }
            except Exception:
                task_evidence = {"missing": True, "error": "task meta unreadable"}
        else:
            task_evidence = {"missing": True, "error": "task directory not found"}

    # Follow-up followup_task_id evidence if present
    followup_task_evidence: Optional[dict[str, Any]] = None
    followup_task_id = followup.get("task_id")
    if followup_task_id:
        ft_dir = get_task_dir(workspace_id, followup_task_id)
        if ft_dir.is_dir():
            try:
                f_meta = load_meta(ft_dir)
                followup_task_evidence = {
                    "task_id": f_meta.get("task_id"),
                    "status": f_meta.get("status"),
                    "task_kind": f_meta.get("task_kind"),
                }
            except Exception:
                pass

    result: dict[str, Any] = {
        "status": "ok",
        "item_id": build_decision_item_id(decision_id),
        "item_type": item_type,
        "decision": {
            "decision_id": decision_id,
            "workspace_id": decision.get("workspace_id"),
            "handoff_id": handoff_id,
            "source_task_id": source_task_id,
            "source_start_id": decision.get("source_start_id"),
            "source_profile": decision.get("source_profile"),
            "source_terminal_status": decision.get("source_terminal_status"),
            "decision": decision_value,
            "state": state,
            "reason": decision.get("reason"),
            "created_at": decision.get("created_at"),
            "followup": followup,
            "resume": resume,
            "human_checkpoint": decision.get("human_checkpoint"),
        },
        "handoff_evidence": handoff_evidence,
        "task_evidence": task_evidence,
        "followup_task_evidence": followup_task_evidence,
        "recommended_action": {
            "tool": "aota_orchestration_decision_open",
            "hint": f"Open full decision details via aota_orchestration_decision_open",
        },
    }

    return result


# ---------------------------------------------------------------------------
# Task item opener
# ---------------------------------------------------------------------------


def _open_task_item(workspace_id: str, task_id: str, suffix: str) -> dict[str, Any]:
    """Open a task inbox item."""
    task_dir = get_task_dir(workspace_id, task_id)
    if not task_dir.is_dir():
        return {"status": "error", "error": f"task not found: {task_id}"}

    try:
        meta = load_meta(task_dir)
    except Exception as e:
        return {"status": "error", "error": f"failed to read task meta: {str(e)}"}

    status = meta.get("status", "")
    task_kind = meta.get("task_kind", "")
    execution = meta.get("execution", {})
    start_id = execution.get("start_id", "")
    profile = execution.get("profile", meta.get("profile_hint", ""))
    created_at = meta.get("created_at", "")

    # Determine item_type based on status + suffix
    if suffix == "needs_approval":
        item_type = "draft_requires_approval"
    elif suffix == "approved_ready":
        item_type = "approved_ready_to_start"
    elif suffix == "ready_to_start":
        item_type = "draft_ready_to_start"
    elif suffix == "needs_input_no_dec":
        item_type = "needs_input_task_without_decision"
    elif suffix == "failed_unconsumed":
        item_type = "failed_task_unconsumed"
    elif suffix == "cancelled_unconsumed":
        item_type = "cancelled_task_unconsumed"
    elif suffix == "running":
        item_type = "running_task"
    elif suffix == "receipt_missing":
        item_type = "artifact_gap"
    elif suffix == "card_gap":
        item_type = "artifact_gap"
    else:
        item_type = "task"

    # Handoff evidence
    handoff_evidence: Optional[dict[str, Any]] = None
    handoff = get_handoff_for_task(workspace_id, task_id, start_id) if start_id else None
    if handoff:
        handoff_evidence = {
            "handoff_id": handoff.get("handoff_id"),
            "state": handoff.get("state"),
            "profile": handoff.get("profile"),
            "terminal_status": handoff.get("terminal_status"),
            "created_at": handoff.get("created_at"),
            "role_artifact": handoff.get("role_artifact"),
        }

    # Decision evidence
    decision_evidence: Optional[dict[str, Any]] = None
    if handoff:
        hid = handoff.get("handoff_id", "")
        if hid:
            dec = get_decision_for_handoff(workspace_id, hid)
            if dec:
                decision_evidence = {
                    "decision_id": dec.get("decision_id"),
                    "decision": dec.get("decision"),
                    "state": dec.get("state"),
                    "followup": dec.get("followup"),
                }

    # Approval evidence
    approval_evidence: Optional[dict[str, Any]] = None
    approval = get_approval(workspace_id, task_id)
    if approval:
        approval_evidence = {
            "approval_id": approval.get("approval_id"),
            "revision": approval.get("revision"),
            "spec_sha256": approval.get("spec_sha256"),
            "approved_at": approval.get("approved_at"),
            "valid": is_approval_valid(approval, meta) if approval else False,
        }

    # Artifact manifest
    artifacts: dict[str, dict[str, bool]] = {}
    if start_id:
        for name in ("SPEC.md", "meta.json", "CARD.json", "DIAGNOSIS_CARD.json", "REVIEW_CARD.json",
                      "RESULT.md", "DIAGNOSIS.md", "REVIEW.md"):
            artifacts[name] = {"exists": (task_dir / name).is_file()}
        for dynamic_name in (
            f"completion.{start_id}.json",
            f"worker-outcome.{start_id}.json",
        ):
            artifacts[dynamic_name] = {"exists": (task_dir / dynamic_name).is_file()}
        artifacts["APPROVAL.json"] = {"exists": (task_dir / "APPROVAL.json").is_file()}

    result: dict[str, Any] = {
        "status": "ok",
        "item_id": build_task_item_id(task_id, suffix) if suffix else build_task_item_id(task_id),
        "item_type": item_type,
        "task": {
            "task_id": task_id,
            "workspace_id": meta.get("workspace_id"),
            "task_kind": task_kind,
            "status": status,
            "revision": meta.get("revision"),
            "spec_sha256": meta.get("spec_sha256"),
            "profile": profile,
            "start_id": start_id,
            "created_at": created_at,
            "updated_at": meta.get("updated_at"),
            "completed_at": execution.get("completed_at"),
            "exit_code": execution.get("exit_code"),
            "subject_task_id": meta.get("subject_task_id"),
            "predecessor_task_id": meta.get("predecessor_task_id"),
            "orchestration_context": meta.get("orchestration_context"),
        },
        "handoff_evidence": handoff_evidence,
        "decision_evidence": decision_evidence,
        "approval_evidence": approval_evidence,
        "artifacts": artifacts,
        "recommended_action": {
            "tool": "aota_profile_task_status",
            "hint": "Query full task status via aota_profile_task_status",
        },
    }

    return result


# ---------------------------------------------------------------------------
# Approval item opener
# ---------------------------------------------------------------------------


def _open_approval_item(workspace_id: str, task_id: str) -> dict[str, Any]:
    """Open an approval inbox item."""
    task_dir = get_task_dir(workspace_id, task_id)
    if not task_dir.is_dir():
        return {"status": "error", "error": f"task not found: {task_id}"}

    try:
        meta = load_meta(task_dir)
        approval = get_approval(workspace_id, task_id)
    except Exception as e:
        return {"status": "error", "error": f"failed to read task data: {str(e)}"}

    if approval is None:
        return {"status": "error", "error": f"no APPROVAL.json found for task {task_id}"}

    valid = is_approval_valid(approval, meta)
    task_kind = meta.get("task_kind", "")
    status = meta.get("status", "")

    result: dict[str, Any] = {
        "status": "ok",
        "item_id": build_approval_item_id(task_id),
        "item_type": "draft_requires_approval" if not valid else "approved_ready_to_start",
        "approval": {
            "approval_id": approval.get("approval_id"),
            "task_id": task_id,
            "workspace_id": approval.get("workspace_id"),
            "revision": approval.get("revision"),
            "spec_sha256": approval.get("spec_sha256"),
            "approved_at": approval.get("approved_at"),
            "task_kind": approval.get("task_kind"),
            "valid": valid,
        },
        "task_status": status,
        "task_kind": task_kind,
        "meta_revision": meta.get("revision"),
        "meta_spec_sha256": meta.get("spec_sha256"),
        "recommended_action": {
            "tool": "aota_profile_task_start" if valid and status == "draft" else "aota_profile_task_approve",
            "hint": "Start task if approved and draft",
        },
    }

    return result
