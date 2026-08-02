"""aota_handoff_open — open exact handoff and return compact role card content (P8-D).

Reads handoff metadata + compact role card (CARD.json / DIAGNOSIS_CARD.json /
REVIEW_CARD.json / ARCHITECT_CARD.json / STEWARD_CARD.json). Does NOT auto-read full artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from ._handoff_common import (
    PROFILE_TASK_ROOT,
    find_handoff_path,
    get_handoff_filename,
    read_handoff,
    read_role_card_projection,
    validate_task_id,
    validate_handoff_id,
    validate_workspace_id,
)
from ._completion_subject_resolver import (
    CompletionSubjectError,
    _result_error,
    resolve_current_completion_subject,
    resolved_context,
)
from ._next_tool_contract import attach_next_tool_option

TOOL_NAME = "aota_handoff_open"
TOOLSET_NAME = "aota_handoff"

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Open the current completion handoff and return its metadata plus compact "
        "role card content (including STEWARD_CARD.json). "
        "The canonical current-completion response also includes a compact completion "
        "facade with semantic CARD/RESULT references, worker outcome, and authoritative "
        "receipt projection; no handoff/task identifier is required. "
        "Does NOT auto-open full report artifacts (RESULT.md / DIAGNOSIS.md / "
        "REVIEW.md / ARCHITECT_REVIEW.md / STEWARD_RESULT.md). If the role card is missing, returns card_missing=true and "
        "the handoff stays pending."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "handoff_ref": {
                "type": "string",
                "description": "Semantic selector; omit for the current completion handoff.",
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}


def handle(args: dict, **_kwargs) -> str:
    """Handle aota_handoff_open tool invocation."""
    try:
        # Canonical completion path: all identity is resolved from trusted
        # delivery/session/artifact bindings.  The old explicit handler path
        # remains available to internal compatibility callers only.
        if "handoff_id" not in args and "workspace_id" not in args:
            subject = resolve_current_completion_subject(
                args, _kwargs, ref=args.get("handoff_ref", "current_completion")
            )
            result = _do_open_resolved(subject)
            result.update(
                {
                    "operation_result": "handoff_opened",
                    "resolved_context": resolved_context(
                        subject, selector=args.get("handoff_ref", "current_completion")
                    ),
                    "next_action": (
                        "review_card_and_record_decision"
                        if not result.get("needs_full_report_review")
                        else "open_current_full_report"
                    ),
                    "retryable": False,
                    "human_action_required": False,
                }
            )
            if result.get("status") == "opened" and not result.get("needs_full_report_review"):
                from ._orchestration_decision_record import SCHEMA as decision_schema

                attach_next_tool_option(
                    result,
                    decision_schema,
                    include=("subject_ref", "followup_task_kind"),
                    reason="The compact card is sufficient; record one semantic orchestration decision before acknowledgment.",
                )
                result["flow_disposition"] = "continue"
            elif result.get("status") == "opened":
                result["flow_disposition"] = "continue"
            else:
                result["flow_disposition"] = "stop"
            return json.dumps(result, sort_keys=True)
        result = _do_open(args)
        return json.dumps(result, sort_keys=True)
    except CompletionSubjectError as exc:
        return json.dumps(
            _result_error(exc, operation="handoff_open"), sort_keys=True
        )
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, sort_keys=True)


def _do_open(args: dict) -> dict[str, Any]:
    """Core open logic: validate, locate, read, return."""
    workspace_id: str = args.get("workspace_id", "")
    handoff_id: str = args.get("handoff_id", "")

    # Validate inputs
    err = validate_workspace_id(workspace_id)
    if err:
        return {"status": "error", "error": f"invalid_workspace_id: {err}"}

    err = validate_handoff_id(handoff_id)
    if err:
        return {"status": "error", "error": f"invalid_handoff_id: {err}"}

    # Find handoff file
    handoff_path = find_handoff_path(workspace_id, handoff_id)
    if handoff_path is None:
        return {
            "status": "error",
            "error": f"handoff not found: {handoff_id}",
        }

    # Read handoff data
    handoff_data = read_handoff(handoff_path)

    # Verify workspace_id matches
    if handoff_data.get("workspace_id") != workspace_id:
        return {
            "status": "error",
            "error": "workspace_id mismatch in handoff data",
        }

    # Read role card from task directory
    task_id = handoff_data.get("task_id", "")
    role_artifact = handoff_data.get("role_artifact", {})
    if not isinstance(role_artifact, dict):
        role_artifact = {}
    card_name: Optional[str] = role_artifact.get("card_name")

    # Security: derive task_dir using known task_id (not from handoff content for path)
    task_dir = (
        PROFILE_TASK_ROOT / workspace_id / task_id
        if isinstance(task_id, str) and validate_task_id(task_id) is None
        else PROFILE_TASK_ROOT / workspace_id
    )

    card_content: Optional[str] = None
    card_missing: bool = True
    card_read_reason = "unrecognized_card"

    if isinstance(task_id, str) and validate_task_id(task_id) is None:
        card_content, card_missing, card_read_reason = read_role_card_projection(
            task_dir, handoff_data.get("profile"), card_name
        )

    # Build result (handoff metadata + card content)
    result: dict[str, Any] = {
        "status": "ok",
        "handoff_id": handoff_data.get("handoff_id"),
        "workspace_id": handoff_data.get("workspace_id"),
        "task_id": handoff_data.get("task_id"),
        "start_id": handoff_data.get("start_id"),
        "profile": handoff_data.get("profile"),
        "task_kind": handoff_data.get("task_kind"),
        "terminal_status": handoff_data.get("terminal_status"),
        "created_at": handoff_data.get("created_at"),
        "state": handoff_data.get("state"),
        "role_artifact": role_artifact,
        "lifecycle": handoff_data.get("lifecycle"),
        "subject_task_id": handoff_data.get("subject_task_id"),
        "needs_input_reason": handoff_data.get("needs_input_reason"),
        "card_missing": card_missing,
        "card_content": card_content,
        "card_read_reason": card_read_reason,
    }

    # P9: add decision metadata if a decision exists for this handoff
    try:
        from ._orchestration_common import get_decision_dir, read_decision
        import os as _os

        dec_dir = get_decision_dir(workspace_id)
        if dec_dir.is_dir():
            for _entry in _os.listdir(str(dec_dir)):
                if not _entry.startswith("DECISION.") or not _entry.endswith(".json"):
                    continue
                try:
                    _dec = read_decision(dec_dir / _entry)
                except (OSError, json.JSONDecodeError):
                    continue
                if _dec.get("handoff_id") == handoff_id:
                    result["decision_state"] = _dec.get("state")
                    result["awaiting_user"] = _dec.get("state") == "awaiting_user"
                    result["followup_task_id"] = _dec.get("followup", {}).get("task_id")
                    break
    except Exception:
        pass

    return result


def _do_open_resolved(subject: dict[str, Any]) -> dict[str, Any]:
    """Open a resolver-owned subject without asking the model for its ID."""
    handoff_data = subject["handoff"]
    task_dir = subject["task_dir"]
    role_artifact = handoff_data.get("role_artifact", {})
    card_name = subject.get("card_name")
    card_content: Optional[str] = None
    card_missing = True
    card_read_reason = "card_missing"
    if isinstance(card_name, str):
        card_path = task_dir / card_name
        if card_path.is_file() and not card_path.is_symlink():
            try:
                card_content = card_path.read_text(encoding="utf-8")
                json.loads(card_content)
                card_missing = False
                card_read_reason = "ok"
            except (OSError, UnicodeError, json.JSONDecodeError):
                card_read_reason = "card_unreadable"
    if card_missing:
        return {
            "status": "rejected",
            "operation_result": "handoff_card_unavailable",
            "error": "card_missing",
            "card_available": False,
            "retryable": False,
            "human_action_required": False,
            "next_action": "stop_and_report_completion_subject_failure",
        }
    completion = _completion_facade(subject, handoff_data, card_content)
    return {
        "status": "opened",
        "handoff_id": handoff_data.get("handoff_id"),
        "workspace_id": handoff_data.get("workspace_id"),
        "task_id": handoff_data.get("task_id"),
        "start_id": handoff_data.get("start_id"),
        "profile": handoff_data.get("profile"),
        "task_kind": handoff_data.get("task_kind"),
        "terminal_status": handoff_data.get("terminal_status"),
        "created_at": handoff_data.get("created_at"),
        "state": handoff_data.get("state"),
        "role_artifact": role_artifact,
        "lifecycle": handoff_data.get("lifecycle"),
        "card_available": True,
        "card_missing": False,
        "card_content": card_content,
        "card_read_reason": card_read_reason,
        "completion": completion,
        "needs_full_report_review": bool(
            json.loads(card_content).get("needs_full_report_review", False)
            if card_content
            else False
        ),
    }


def _completion_facade(
    subject: dict[str, Any], handoff_data: dict[str, Any], card_content: str
) -> dict[str, Any]:
    """Project one model-facing completion envelope from trusted authorities.

    The resolver has already validated task/start/spec/profile/receipt/handoff
    bindings.  This projection deliberately exposes only semantic references and
    bounded outcome/receipt facts; durable IDs, hashes and filesystem paths stay
    in the compatibility fields used by internal callers.
    """
    role_artifact = handoff_data.get("role_artifact")
    if not isinstance(role_artifact, dict):
        role_artifact = {}
    card = subject.get("card") if isinstance(subject.get("card"), dict) else {}
    receipt = subject.get("receipt") if isinstance(subject.get("receipt"), dict) else {}
    outcome_artifact = subject.get("outcome") if isinstance(subject.get("outcome"), dict) else {}
    meta = subject.get("meta") if isinstance(subject.get("meta"), dict) else {}
    execution = meta.get("execution") if isinstance(meta.get("execution"), dict) else {}

    terminal_status = handoff_data.get("terminal_status") or receipt.get("status") or meta.get("status")
    worker_outcome = (
        card.get("outcome")
        or handoff_data.get("outcome")
        or outcome_artifact.get("outcome")
        or receipt.get("outcome")
    )
    full_name = role_artifact.get("full_name") or handoff_data.get("full_report_ref")
    full_available = bool(role_artifact.get("full_exists"))
    if not full_available and isinstance(full_name, str) and full_name:
        task_dir = subject.get("task_dir")
        if isinstance(task_dir, Path):
            full_path = task_dir / full_name
            full_available = full_path.is_file() and not full_path.is_symlink()

    receipt_projection = {
        "ref": "current_completion_receipt",
        "available": bool(receipt),
        "authoritative": True,
        "status": receipt.get("status") or terminal_status,
        "exit_code": receipt.get("exit_code"),
        "scope_compliance": receipt.get("scope_compliance") or handoff_data.get("scope_compliance"),
        "reconciliation_state": execution.get("reconciliation_state"),
        "completed_at": receipt.get("completed_at") or subject.get("completed_at"),
    }
    return {
        "schema_version": 1,
        "status": "ready" if receipt else "degraded",
        "terminal_status": terminal_status,
        "worker_outcome": worker_outcome,
        "verdict": card.get("verdict") or handoff_data.get("verdict"),
        "summary": card.get("summary") or handoff_data.get("summary"),
        "card": {
            "ref": "current_role_card",
            "name": role_artifact.get("card_name") or handoff_data.get("artifact_card_ref"),
            "available": True,
            "content": card_content,
            "needs_full_report_review": bool(
                card.get("needs_full_report_review")
                or handoff_data.get("needs_full_report_review")
            ),
        },
        "result": {
            "ref": "current_role_result",
            "name": full_name,
            "available": full_available,
        },
        "receipt": receipt_projection,
        "recommended_next_action": card.get("recommended_next_action") or handoff_data.get("recommended_next_action"),
    }
