"""Trusted resolver for the current Profile Task completion subject.

The resolver is intentionally control-plane only.  Model arguments may select
one bounded semantic choice, but never identify a task, handoff, receipt, or
decision.  All other identity comes from trusted handler metadata and the
durable task/handoff/receipt artifacts.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from ._completion_observation import (
    COMPLETION_TRANSPORT_LEGACY_DURABLE,
    COMPLETION_TRANSPORT_TERMINAL_BACKGROUND,
    completion_observation_context,
)
from ._handoff_common import (
    PROFILE_TASK_ROOT,
    find_handoff_path,
    get_handoff_ack_dir,
    get_handoff_pending_dir,
    read_ack_artifact,
    read_handoff,
    validate_handoff_id,
    validate_task_id,
    validate_workspace_id,
)
from ._orchestration_common import get_decision_dir, get_decision_filename, read_decision
from ._session_active_spec_binding import trusted_session_context
from ._session_state_authority import (
    POINTER_KIND_CURRENT_COMPLETION,
    POINTER_KIND_CURRENT_COMPLETED_TASK,
    POINTER_KIND_CURRENT_DECISION,
    SESSION_STATE_ROOT,
    SessionStateError,
    read_pointer_for_context,
)
from ._task_spec_common import get_task_dir, load_meta


class CompletionSubjectError(ValueError):
    """Stable, bounded resolver failure."""

    def __init__(
        self,
        code: str,
        *,
        detail: str = "",
        choices: list[dict[str, Any]] | None = None,
        next_action: str = "stop_and_report_completion_subject_failure",
    ) -> None:
        super().__init__(code if not detail else f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.choices = choices or []
        self.next_action = next_action


_SEMANTIC_REFS = frozenset({
    "current_completion",
    "current_decided_handoff",
    "current_completed_task",
    "current_relevant_item",
    "current_decided_decision",
    "current_awaiting_user_decision",
})


def _result_error(exc: CompletionSubjectError, *, operation: str) -> dict[str, Any]:
    return {
        "status": "rejected",
        "operation_result": operation,
        "error": exc.code,
        "retryable": False,
        "same_call_retryable": False,
        "flow_disposition": "await_human" if exc.choices else "stop",
        "human_action_required": bool(exc.choices),
        "next_action": exc.next_action,
        "choices": exc.choices,
    }


def _workspace(args: Mapping[str, Any], kwargs: Mapping[str, Any]) -> tuple[str, Any]:
    context = trusted_session_context(kwargs)
    value = args.get("workspace_id") or context.workspace_id
    if not isinstance(value, str) or not value:
        raise CompletionSubjectError(
            "trusted_session_context_missing",
            next_action="stop_and_report_runtime_context_missing",
        )
    if validate_workspace_id(value):
        raise CompletionSubjectError("workspace_binding_mismatch", detail="workspace_id_invalid")
    return value, context


def _safe_json(path: Path) -> dict[str, Any] | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _task_dirs(workspace_id: str) -> list[Path]:
    root = PROFILE_TASK_ROOT / workspace_id
    if not root.is_dir() or root.is_symlink():
        return []
    result: list[Path] = []
    try:
        for entry in sorted(root.iterdir(), key=lambda item: item.name):
            if entry.is_dir() and not entry.is_symlink() and (entry / "meta.json").is_file():
                result.append(entry)
    except OSError:
        return []
    return result


def _delivery(kwargs: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Read only trusted delivery projection from handler kwargs."""
    for name in ("completion_delivery", "completion_event", "delivery_payload"):
        value = kwargs.get(name)
        if isinstance(value, Mapping):
            return value
    return None


def _origin_sessions(context: Any) -> set[str]:
    return {
        value
        for value in (
            getattr(context, "session_id", ""),
            getattr(context, "origin_session_id", ""),
            getattr(context, "parent_session_id", ""),
        )
        if isinstance(value, str) and value
    }


def _binding_error(detail: str) -> CompletionSubjectError:
    return CompletionSubjectError("completion_subject_binding_mismatch", detail=detail)


def _execution_transport(execution: Mapping[str, Any]) -> tuple[str, bool]:
    """Validate the immutable transport binding projected at task start."""
    transport = execution.get("completion_transport") or execution.get("transport")
    expected = execution.get("completion_delivery_expected")
    if transport in (None, "") or not isinstance(expected, bool):
        raise CompletionSubjectError("completion_transport_context_missing")
    if transport == COMPLETION_TRANSPORT_TERMINAL_BACKGROUND:
        if expected:
            raise _binding_error("completion_transport_native_delivery_expected")
        return transport, False
    if transport == COMPLETION_TRANSPORT_LEGACY_DURABLE:
        if not expected:
            raise _binding_error("completion_transport_legacy_delivery_not_expected")
        return transport, True
    raise _binding_error("completion_transport_unknown")


def _outbox_delivery_state(workspace_id: str, task_id: str, start_id: str) -> str:
    """Read the compatibility outbox state without mutating historical data."""
    try:
        from ._delivery_outbox import (
            build_event_id,
            get_outbox_delivered_dir,
            get_outbox_pending_dir,
        )
        event_id = build_event_id(task_id, start_id)
        delivered = get_outbox_delivered_dir(workspace_id) / f"{event_id}.json"
        if delivered.is_file() and not delivered.is_symlink():
            return "delivered"
        pending = get_outbox_pending_dir(workspace_id) / f"{event_id}.json"
        if pending.is_file() and not pending.is_symlink():
            return "pending"
    except Exception:
        return "unknown"
    return "missing"


def _delivery_is_bound(
    workspace_id: str,
    task_id: str,
    start_id: str,
    *,
    delivery: Mapping[str, Any] | None,
) -> bool:
    """Require delivered evidence only for the legacy durable rail."""
    if delivery:
        state = str(delivery.get("delivery_state") or delivery.get("state") or "")
        if state in {"delivered", "received"}:
            return all(
                delivery.get(key) in (None, "", expected)
                for key, expected in (
                    ("workspace_id", workspace_id),
                    ("task_id", task_id),
                    ("start_id", start_id),
                )
            )
    try:
        from ._delivery_outbox import (
            build_event_id,
            get_outbox_delivered_dir,
            get_outbox_pending_dir,
        )

        event_id = build_event_id(task_id, start_id)
        delivered = get_outbox_delivered_dir(workspace_id) / f"{event_id}.json"
        if delivered.is_file() and not delivered.is_symlink():
            return True
        pending = get_outbox_pending_dir(workspace_id) / f"{event_id}.json"
        if pending.is_file() and not pending.is_symlink():
            raise CompletionSubjectError(
                "completion_delivery_pending",
                next_action="wait_for_completion_delivery",
            )
        # A missing outbox event is not a completion signal.  Keep the
        # delivery contract fail-closed rather than treating durable artifacts
        # as an implicit notification.
        raise CompletionSubjectError(
            "completion_delivery_pending",
            next_action="wait_for_completion_delivery",
        )
    except CompletionSubjectError:
        raise
    except Exception:
        return False
    return False


def _verify_candidate(
    workspace_id: str,
    handoff_id: str,
    handoff_path: Path,
    *,
    context: Any,
    delivery: Mapping[str, Any] | None = None,
    expected_task_id: str = "",
    expected_start_id: str = "",
) -> dict[str, Any] | None:
    handoff = _safe_json(handoff_path)
    if not handoff or handoff.get("workspace_id") != workspace_id:
        return None
    task_id = handoff.get("task_id", "")
    start_id = handoff.get("start_id", "")
    if validate_task_id(task_id) or not isinstance(start_id, str) or not start_id:
        return None
    if expected_task_id and task_id != expected_task_id:
        return None
    if expected_start_id and start_id != expected_start_id:
        return None
    task_dir = get_task_dir(workspace_id, task_id)
    meta = _safe_json(task_dir / "meta.json")
    if not meta or meta.get("workspace_id") != workspace_id or meta.get("task_id") != task_id:
        raise _binding_error("task_meta_identity")
    execution = meta.get("execution") if isinstance(meta.get("execution"), dict) else {}
    _transport, legacy_delivery_required = _execution_transport(execution)
    if execution.get("start_id") not in (None, "", start_id):
        raise _binding_error("start_id")
    if meta.get("status") not in {"done", "failed", "needs_input", "cancelled", "timeout", "scope_violation"}:
        return None
    if legacy_delivery_required and not _delivery_is_bound(
        workspace_id, task_id, start_id, delivery=delivery
    ):
        raise CompletionSubjectError(
            "completion_delivery_pending",
            next_action="wait_for_completion_delivery",
        )

    receipt_path = task_dir / f"completion.{start_id}.json"
    receipt = _safe_json(receipt_path)
    if not receipt:
        raise _binding_error("completion_receipt_missing")
    if receipt.get("status") not in (None, "", meta.get("status")):
        raise _binding_error("receipt:status")
    reconciliation_state = execution.get("reconciliation_state")
    if reconciliation_state not in (None, "", "reconciled"):
        raise _binding_error("reconciliation_state")
    for key, expected in (
        ("workspace_id", workspace_id),
        ("task_id", task_id),
        ("start_id", start_id),
    ):
        if receipt.get(key) not in (None, expected):
            raise _binding_error(f"receipt:{key}")
    for key in ("spec_revision", "spec_hash", "spec_sha256", "profile", "project_id"):
        receipt_value = receipt.get(key)
        meta_value = meta.get(key) or execution.get(key)
        if receipt_value not in (None, "") and meta_value not in (None, "") and receipt_value != meta_value:
            raise _binding_error(f"receipt:{key}")
    profile = meta.get("resolved_profile") or execution.get("profile") or handoff.get("profile")
    if handoff.get("profile") not in (None, "", profile):
        raise _binding_error("profile")
    for key in ("spec_revision", "revision"):
        handoff_value = handoff.get(key)
        meta_value = meta.get(key)
        if handoff_value is not None and meta_value is not None and handoff_value != meta_value:
            raise _binding_error(key)
    if handoff.get("spec_hash") and meta.get("spec_hash") and handoff["spec_hash"] != meta["spec_hash"]:
        raise _binding_error("spec_hash")
    if handoff.get("project_id") and meta.get("project_id") and handoff["project_id"] != meta["project_id"]:
        raise _binding_error("project_id")
    if handoff.get("terminal_status") not in (None, "", meta.get("status")):
        raise _binding_error("terminal_status")
    origin = meta.get("origin_session_id") or execution.get("parent_session_ref") or execution.get("origin_session_id")
    sessions = _origin_sessions(context)
    if sessions and origin and origin not in sessions:
        raise _binding_error("origin_session")

    decision = None
    decision_id = ""
    if getattr(context, "usable_for_active_spec", False):
        try:
            decision_pointer = read_pointer_for_context(POINTER_KIND_CURRENT_DECISION, context)
        except SessionStateError as exc:
            raise CompletionSubjectError(exc.code, detail=exc.detail)
        if decision_pointer is not None:
            decision_id = str(decision_pointer.get("decision_id") or "")
            decision_path = get_decision_dir(workspace_id) / get_decision_filename(decision_id)
            decision = _safe_json(decision_path)
            if not decision or decision.get("handoff_id") != handoff_id:
                raise CompletionSubjectError("current_decision_binding_mismatch")
    else:
        dec_dir = get_decision_dir(workspace_id)
        if dec_dir.is_dir():
            try:
                for entry in sorted(dec_dir.iterdir(), key=lambda item: item.name):
                    if not entry.name.startswith("DECISION.") or not entry.name.endswith(".json"):
                        continue
                    item = _safe_json(entry)
                    if item and item.get("handoff_id") == handoff_id:
                        decision = item
                        decision_id = item.get("decision_id", "")
                        break
            except OSError:
                pass
    ack = read_ack_artifact(workspace_id, handoff_id)
    if ack:
        for key, expected in (
            ("workspace_id", workspace_id),
            ("handoff_id", handoff_id),
            ("task_id", task_id),
            ("start_id", start_id),
            ("decision_id", decision_id),
        ):
            if ack.get(key) not in (None, "", expected):
                raise _binding_error(f"ack:{key}")
    role_artifact = handoff.get("role_artifact") if isinstance(handoff.get("role_artifact"), dict) else {}
    card_name = role_artifact.get("card_name")
    card_path = task_dir / card_name if isinstance(card_name, str) else None
    card_available = bool(card_path and card_path.is_file() and not card_path.is_symlink())
    card = _safe_json(card_path) if card_available and card_path is not None else {}
    title = f"{profile or handoff.get('task_kind', 'worker')} completion"
    return {
        "workspace_id": workspace_id,
        "task_id": task_id,
        "start_id": start_id,
        "handoff_id": handoff_id,
        "task_dir": task_dir,
        "handoff_path": handoff_path,
        "handoff": handoff,
        "meta": meta,
        "receipt": receipt,
        "outcome": _safe_json(task_dir / f"worker-outcome.{start_id}.json"),
        "decision": decision,
        "decision_id": decision_id,
        "ack": ack,
        "profile": profile or "",
        "task_kind": handoff.get("task_kind") or meta.get("spec_kind", ""),
        "created_at": handoff.get("created_at") or receipt.get("completed_at", ""),
        "completed_at": receipt.get("completed_at") or execution.get("completed_at", ""),
        "card_available": card_available,
        "card": card,
        "card_name": card_name,
        "title": title,
        "origin_session_id": origin or "",
        "completion_transport": _transport,
        "completion_delivery_expected": legacy_delivery_required,
        "delivery_state": (
            _outbox_delivery_state(workspace_id, task_id, start_id)
            if legacy_delivery_required
            else execution.get("delivery_state", "not_expected")
        ),
    }


def _choices(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "selector": f"choice:{index}",
            "title": item.get("title", "completion"),
            "task_kind": item.get("task_kind", ""),
            "profile": item.get("profile", ""),
            "created_at": item.get("created_at", ""),
            "completed_at": item.get("completed_at", ""),
        }
        for index, item in enumerate(candidates, 1)
    ]


def _pending_guard(workspace_id: str, context: Any) -> None:
    for task_dir in _task_dirs(workspace_id):
        meta = _safe_json(task_dir / "meta.json")
        task_id = meta.get("task_id") if meta else ""
        if not meta or not isinstance(task_id, str) or not task_id:
            continue
        observation = completion_observation_context(meta, task_id)
        if observation["active"] and observation.get("delivery_state") != "received":
            origin = observation.get("origin_session_id")
            if not _origin_sessions(context) or not origin or origin in _origin_sessions(context):
                raise CompletionSubjectError(
                    "completion_delivery_pending",
                    next_action="wait_for_completion_delivery",
                )


def resolve_current_completion_subject(
    args: Mapping[str, Any] | None = None,
    handler_kwargs: Mapping[str, Any] | None = None,
    *,
    ref: str = "current_completion",
    require_decision: bool = False,
) -> dict[str, Any]:
    """Resolve one current completion subject, or fail closed."""
    args = args or {}
    kwargs = handler_kwargs or {}
    if not isinstance(ref, str) or (ref not in _SEMANTIC_REFS and not ref.startswith("choice:")):
        raise CompletionSubjectError("completion_subject_reference_invalid")
    workspace_id, context = _workspace(args, kwargs)
    delivery = _delivery(kwargs)
    expected_task_id = ""
    expected_start_id = ""
    expected_handoff_id = ""
    if delivery:
        expected_task_id = str(delivery.get("task_id", "") or "")
        expected_start_id = str(delivery.get("start_id", "") or "")
        expected_handoff_id = str(delivery.get("handoff_id", "") or "")
        if not expected_task_id and not expected_handoff_id:
            raise CompletionSubjectError("completion_subject_missing")

    runtime_task_id = str(getattr(context, "execution_context", {}).get("task_id", "") or "")
    if not expected_task_id and runtime_task_id and not getattr(context, "worker_context", False):
        expected_task_id = runtime_task_id

    # Canonical current-completion authority.  A trusted session pointer is
    # resolved to one exact durable handoff; historical handoffs are never
    # inspected to guess the current subject.
    if getattr(context, "usable_for_active_spec", False):
        try:
            pointer = read_pointer_for_context(POINTER_KIND_CURRENT_COMPLETION, context)
        except SessionStateError as exc:
            raise CompletionSubjectError(exc.code, detail=exc.detail)
        if pointer is None:
            if ref == "current_completed_task":
                try:
                    completed_pointer = read_pointer_for_context(
                        POINTER_KIND_CURRENT_COMPLETED_TASK, context
                    )
                except SessionStateError as exc:
                    raise CompletionSubjectError(exc.code, detail=exc.detail)
                if completed_pointer and completed_pointer.get("state") == "active":
                    handoff_id = completed_pointer.get("handoff_id")
                    if isinstance(handoff_id, str) and handoff_id:
                        handoff_path = find_handoff_path(workspace_id, handoff_id)
                        if handoff_path is not None:
                            candidate = _verify_candidate(
                                workspace_id,
                                handoff_id,
                                handoff_path,
                                context=context,
                                delivery=delivery,
                                expected_task_id=str(completed_pointer.get("task_id") or ""),
                                expected_start_id=str(completed_pointer.get("start_id") or ""),
                            )
                            if candidate and candidate.get("ack"):
                                candidate["closure_lookup"] = "EXPECTED_CONSUMED_AFTER_CLOSURE"
                                return candidate
            # Old runtime fixtures/data may have no session-state partition at
            # all.  Preserve the bounded legacy handoff resolver for that
            # migration case; once a canonical session partition exists, a
            # missing pointer is authoritative and does not fall through.
            if (SESSION_STATE_ROOT / workspace_id).exists():
                raise CompletionSubjectError("completion_subject_missing", next_action="wait_for_completion_delivery")
        else:
            if pointer.get("state") in {"consumed", "closed"} and not require_decision and ref != "current_completed_task":
                raise CompletionSubjectError("current_completion_consumed", next_action="ack_current_handoff")
            consumed_after_ack = pointer.get("state") in {"consumed", "closed"}
            if pointer.get("state") != "active" and not (
                consumed_after_ack and (ref == "current_completed_task" or require_decision)
            ):
                raise CompletionSubjectError("current_completion_binding_stale", detail="UNEXPECTED_STALE_BEFORE_CLOSURE")
            handoff_id = pointer.get("handoff_id")
            if not isinstance(handoff_id, str) or not handoff_id:
                try:
                    completed_pointer = read_pointer_for_context(
                        POINTER_KIND_CURRENT_COMPLETED_TASK, context
                    )
                except SessionStateError as exc:
                    raise CompletionSubjectError(exc.code, detail=exc.detail)
                handoff_id = completed_pointer.get("handoff_id") if completed_pointer else ""
            if not isinstance(handoff_id, str) or not handoff_id:
                raise CompletionSubjectError("current_completion_binding_stale", detail="handoff_id_missing")
            handoff_path = find_handoff_path(workspace_id, handoff_id)
            if handoff_path is None:
                raise CompletionSubjectError(
                    "current_completion_binding_stale",
                    detail=("UNEXPECTED_STALE_BEFORE_CLOSURE" if not consumed_after_ack else "handoff_missing"),
                )
            candidate = _verify_candidate(
                workspace_id, handoff_id, handoff_path,
                context=context, delivery=delivery,
                expected_task_id=str(pointer.get("task_id") or ""),
                expected_start_id=str(pointer.get("start_id") or ""),
            )
            if candidate is None:
                raise CompletionSubjectError("current_completion_binding_mismatch")
            if consumed_after_ack and not candidate.get("ack"):
                raise CompletionSubjectError(
                    "current_completion_binding_stale",
                    detail="UNEXPECTED_STALE_BEFORE_CLOSURE",
                )
            candidate["closure_lookup"] = (
                "EXPECTED_CONSUMED_AFTER_CLOSURE" if consumed_after_ack else "CURRENT_ACTIVE_BINDING"
            )
            pointer_bindings = {
                "task_id": candidate.get("task_id"),
                "start_id": candidate.get("start_id"),
                "profile": candidate.get("profile"),
                "completion_transport": candidate.get("completion_transport"),
                "completion_delivery_expected": candidate.get("completion_delivery_expected"),
                "spec_hash": candidate.get("meta", {}).get("spec_hash"),
            }
            for key, candidate_value in pointer_bindings.items():
                pointer_value = pointer.get(key)
                if pointer_value not in (None, "") and pointer_value != candidate_value:
                    raise CompletionSubjectError("current_completion_binding_stale", detail=key)
            if require_decision and not candidate.get("decision"):
                raise CompletionSubjectError("decision_subject_missing", next_action="record_current_decision")
            return candidate

        # The terminal pointer is retained after ack as a deterministic,
        # session-contained fallback when older finalizer data did not leave a
        # current-completion pointer.  It is never a history scan.
        if ref == "current_completed_task":
            try:
                completed_pointer = read_pointer_for_context(
                    POINTER_KIND_CURRENT_COMPLETED_TASK, context
                )
            except SessionStateError as exc:
                raise CompletionSubjectError(exc.code, detail=exc.detail)
            if completed_pointer and completed_pointer.get("state") == "active":
                handoff_id = completed_pointer.get("handoff_id")
                if isinstance(handoff_id, str) and handoff_id:
                    handoff_path = find_handoff_path(workspace_id, handoff_id)
                    if handoff_path is not None:
                        candidate = _verify_candidate(
                            workspace_id,
                            handoff_id,
                            handoff_path,
                            context=context,
                            delivery=delivery,
                            expected_task_id=str(completed_pointer.get("task_id") or ""),
                            expected_start_id=str(completed_pointer.get("start_id") or ""),
                        )
                        if candidate and candidate.get("ack"):
                            candidate["closure_lookup"] = "EXPECTED_CONSUMED_AFTER_CLOSURE"
                            return candidate
            raise CompletionSubjectError(
                "current_completion_binding_stale",
                detail="UNEXPECTED_STALE_BEFORE_CLOSURE",
            )

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for directory in (get_handoff_pending_dir(workspace_id), get_handoff_ack_dir(workspace_id)):
        if not directory.is_dir():
            continue
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError:
            continue
        for path in entries:
            if path.is_symlink() or not path.name.startswith("handoff.") or not path.name.endswith(".json"):
                continue
            handoff_id = path.name[len("handoff.") : -len(".json")]
            if validate_handoff_id(handoff_id) or handoff_id in seen:
                continue
            if expected_handoff_id and handoff_id != expected_handoff_id:
                continue
            seen.add(handoff_id)
            candidate = _verify_candidate(
                workspace_id,
                handoff_id,
                path,
                context=context,
                delivery=delivery,
                expected_task_id=expected_task_id,
                expected_start_id=expected_start_id,
            )
            if candidate:
                candidates.append(candidate)

    if not candidates:
        _pending_guard(workspace_id, context)
        raise CompletionSubjectError("completion_subject_missing")
    if len(candidates) > 1:
        if isinstance(ref, str) and ref.startswith("choice:"):
            try:
                index = int(ref.split(":", 1)[1]) - 1
            except ValueError:
                raise CompletionSubjectError("completion_subject_ambiguous", choices=_choices(candidates), next_action="select_completion_subject")
            if 0 <= index < len(candidates):
                candidates = [candidates[index]]
            else:
                raise CompletionSubjectError("completion_subject_ambiguous", choices=_choices(candidates), next_action="select_completion_subject")
        else:
            raise CompletionSubjectError("completion_subject_ambiguous", choices=_choices(candidates), next_action="select_completion_subject")
    subject = candidates[0]
    if delivery:
        for key in ("workspace_id", "task_id", "start_id", "handoff_id", "profile"):
            value = delivery.get(key)
            if value not in (None, "") and value != subject.get(key):
                raise CompletionSubjectError(
                    "completion_subject_binding_mismatch", detail=f"delivery:{key}"
                )
    if require_decision and not subject.get("decision"):
        raise CompletionSubjectError("decision_subject_missing", next_action="record_current_decision")
    return subject


def resolved_context(subject: Mapping[str, Any], *, selector: str = "current_completion") -> dict[str, Any]:
    """Return a model-safe semantic projection without internal identifiers."""
    return {
        "selector": selector,
        "title": subject.get("title", "completion"),
        "task_kind": subject.get("task_kind", ""),
        "profile": subject.get("profile", ""),
        "created_at": subject.get("created_at", ""),
        "completed_at": subject.get("completed_at", ""),
    }


__all__ = [
    "CompletionSubjectError",
    "_result_error",
    "resolve_current_completion_subject",
    "resolved_context",
]
