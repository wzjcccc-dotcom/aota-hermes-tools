"""Bounded Project Steward result writer for a running stewardship task."""

from __future__ import annotations

import datetime
import json
import os
import tempfile
from pathlib import Path
from ._spec_contract import ContractError, apply_common_card, canonical_hash, validate_spec
from typing import Any
from ._active_task_context import ActiveTaskError, assert_artifact_target, load_active_task_context, validate_tool_args

TOOL_NAME = "aota_project_steward_report"
TOOLSET_NAME = "aota_project_steward_artifact"
ALLOWED_PROFILE = "project-steward"
VALID_OUTCOMES = {"completed", "partial", "needs_input", "failed"}
VALID_VERDICTS = {"pass", "needs_fix", "blocked", "not_applicable"}
VALID_SCOPE_STATUS = {"in_scope", "partial", "out_of_scope", "not_applicable"}

SCHEMA = {
    "name": TOOL_NAME,
    "description": "Submit a bounded STEWARD_CARD.json and STEWARD_RESULT.md for the running stewardship task.",
    "parameters": {
        "type": "object",
        "properties": {
            "outcome": {"type": "string", "enum": sorted(VALID_OUTCOMES)},
            "verdict": {"type": "string", "enum": sorted(VALID_VERDICTS)},
            "summary": {"type": "string", "maxLength": 2000},
            "key_findings": {"type": "array", "items": {"type": "string", "maxLength": 500}, "maxItems": 20},
            "validation_summary": {"type": "array", "items": {"type": "string", "maxLength": 500}, "maxItems": 20},
            "scope_status": {"type": "string", "enum": sorted(VALID_SCOPE_STATUS)},
            "limitations": {"type": "array", "items": {"type": "string", "maxLength": 500}, "maxItems": 20},
            "risks": {"type": "array", "items": {"type": "string", "maxLength": 500}, "maxItems": 20},
            "evidence_refs": {"type": "array", "items": {"type": "string", "maxLength": 500}, "maxItems": 30},
            "needs_full_report_review": {"type": "boolean"},
            "needs_user_input": {"type": "array", "items": {"type": "string", "maxLength": 500}, "maxItems": 20},
            "recommended_next_action": {"type": "string", "maxLength": 500},
            "matched_project": {"type": "string", "maxLength": 128},
            "project_candidates": {"type": "array", "items": {"type": "string", "maxLength": 128}, "maxItems": 20},
            "artifact_gaps": {"type": "array", "items": {"type": "string", "maxLength": 500}, "maxItems": 20},
            "docs_update_required": {"type": "boolean"},
            "docs_updated": {"type": "array", "items": {"type": "string", "maxLength": 256}, "maxItems": 20},
            "codegraph_state": {"type": "string", "maxLength": 500},
            "continuity_status": {"type": "string", "maxLength": 500},
            "full_report": {"type": "string", "maxLength": 10000},
            "workspace_recommendation": {"type": "object"},
        },
        "required": ["outcome", "verdict", "summary", "scope_status"],
        "additionalProperties": False,
    },
}


def _atomic_write(path: Path, content: str) -> None:
    fd, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _bounded_text(value: Any, name: str, maximum: int, *, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > maximum or "\x00" in value:
        raise ValueError(f"invalid {name}")
    return value


def _bounded_list(value: Any, name: str, maximum: int, item_maximum: int) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"invalid {name}")
    result: list[str] = []
    for item in value:
        result.append(_bounded_text(item, name, item_maximum, required=True))
    return result


def _trusted_task() -> tuple[str, str, str, Path, dict[str, Any], dict[str, Any]]:
    try:
        ctx = load_active_task_context(expected_profile=ALLOWED_PROFILE)
    except ActiveTaskError as exc:
        raise PermissionError(exc.code) from exc
    workspace_id, task_id, start_id, task_dir, meta = ctx.workspace_id, ctx.task_id, ctx.start_id, ctx.task_dir, ctx.meta
    if meta.get("status") != "running" or meta.get("spec_kind", meta.get("task_kind")) != "stewardship":
        raise RuntimeError("task is not a running stewardship task")
    execution = meta.get("execution", {})
    if execution.get("start_id") != start_id or execution.get("profile") != ALLOWED_PROFILE:
        raise RuntimeError("trusted task binding mismatch")
    spec = meta.get("spec", {})
    if meta.get("contract_version") == 1:
        try:
            validate_spec(spec, frozen=True)
        except ContractError as exc:
            raise RuntimeError("invalid canonical stewardship spec") from exc
        if meta.get("resolved_profile") != ALLOWED_PROFILE or meta.get("spec_hash") != spec.get("spec_hash") or canonical_hash(spec) != spec.get("spec_hash"):
            raise RuntimeError("canonical stewardship binding mismatch")
        role_contract = dict(spec.get("payload", {})); role_contract["project_id"] = spec.get("project_id")
    else:
        role_contract = spec.get("role_contract", {})
    if not isinstance(role_contract, dict) or not isinstance(role_contract.get("project_id"), str):
        raise RuntimeError("stewardship role contract unavailable")
    return workspace_id, task_id, start_id, task_dir, meta, role_contract


def handle(args: dict, **_kwargs: Any) -> str:
    try:
        validate_tool_args(args, SCHEMA)
        return json.dumps(_submit(args), sort_keys=True)
    except (PermissionError, RuntimeError, ValueError) as exc:
        return json.dumps({"status": "rejected", "error": str(exc)}, sort_keys=True)
    except Exception as exc:
        return json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True)


def _submit(args: dict) -> dict[str, Any]:
    if set(args) - set(SCHEMA["parameters"]["properties"]):
        raise ValueError("unknown report field")
    outcome = args.get("outcome")
    verdict = args.get("verdict")
    scope_status = args.get("scope_status")
    if outcome not in VALID_OUTCOMES or verdict not in VALID_VERDICTS or scope_status not in VALID_SCOPE_STATUS:
        raise ValueError("invalid outcome, verdict, or scope_status")
    summary = _bounded_text(args.get("summary"), "summary", 2000, required=True)
    workspace_id, task_id, start_id, task_dir, meta, contract = _trusted_task()
    list_fields = {name: _bounded_list(args.get(name), name, 30 if name == "evidence_refs" else 20, 500) for name in ("key_findings", "validation_summary", "limitations", "risks", "evidence_refs", "needs_user_input", "artifact_gaps")}
    project_candidates = _bounded_list(args.get("project_candidates"), "project_candidates", 20, 128)
    docs_updated = _bounded_list(args.get("docs_updated"), "docs_updated", 20, 256)
    role_summary = {
        "matched_project": _bounded_text(args.get("matched_project"), "matched_project", 128) or contract["project_id"],
        "project_candidates": project_candidates,
        "artifact_gaps": list_fields["artifact_gaps"],
        "docs_update_required": bool(args.get("docs_update_required", False)),
        "docs_updated": docs_updated,
        "codegraph_state": _bounded_text(args.get("codegraph_state"), "codegraph_state", 500),
        "continuity_status": _bounded_text(args.get("continuity_status"), "continuity_status", 500),
    }
    recommendation = args.get("workspace_recommendation")
    if contract.get("operation") == "relationship_resolve":
        required = {"schema_version", "recommendation_id", "request_digest", "relationship", "recommended", "alternatives", "evidence", "onboarding", "warnings"}
        if not isinstance(recommendation, dict) or set(recommendation) != required or recommendation.get("schema_version") != 1:
            raise ValueError("workspace_recommendation_invalid")
        relationship = recommendation.get("relationship")
        if not isinstance(relationship, dict) or relationship.get("classification") not in {"existing", "adjacent", "new_project", "new_workspace", "ambiguous"}:
            raise ValueError("workspace_recommendation_invalid")
        role_summary["workspace_recommendation_id"] = recommendation["recommendation_id"]
    for field in ("needs_full_report_review", "docs_update_required"):
        if field in args and not isinstance(args[field], bool):
            raise ValueError(f"invalid {field}")
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    work_item_id = contract.get("work_item_id") or (meta.get("source_traceability") or {}).get("work_item_id")
    card = {
        "schema_version": 1, "card_type": "worker_result", "role": ALLOWED_PROFILE,
        "task_id": task_id, "spec_id": task_id, "spec_revision": meta.get("revision"),
        "spec_hash": meta.get("spec_sha256"), "project_id": contract["project_id"],
        "work_item_id": work_item_id, "outcome": outcome, "verdict": verdict,
        "summary": summary, "key_findings": list_fields["key_findings"],
        "validation_summary": list_fields["validation_summary"], "scope_status": scope_status,
        "limitations": list_fields["limitations"], "risks": list_fields["risks"],
        "full_report_ref": "STEWARD_RESULT.md", "evidence_refs": list_fields["evidence_refs"],
        "needs_full_report_review": bool(args.get("needs_full_report_review", False)),
        "needs_user_input": list_fields["needs_user_input"],
        "recommended_next_action": _bounded_text(args.get("recommended_next_action"), "recommended_next_action", 500),
        "role_summary": role_summary, "created_at": now, "start_id": start_id,
    }
    extra = _bounded_text(args.get("full_report"), "full_report", 10000)
    sections = [("Status", f"Outcome: {outcome}\n\nVerdict: {verdict}"), ("Objective", meta.get("spec", {}).get("goal", "")), ("Project Match", role_summary["matched_project"]), ("Project Context", summary), ("Artifact Inventory", "\n".join(f"- {x}" for x in list_fields["key_findings"]) or "- None"), ("Relationship Summary", "\n".join(f"- {x}" for x in project_candidates) or "- None"), ("Documentation State", "\n".join(f"- {x}" for x in docs_updated) or "- No documentation updates"), ("CodeGraph State", role_summary["codegraph_state"] or "Not assessed"), ("Continuity Gaps", "\n".join(f"- {x}" for x in list_fields["artifact_gaps"]) or "- None"), ("Work Performed", extra or "Bounded stewardship analysis performed."), ("Evidence", "\n".join(f"- {x}" for x in list_fields["evidence_refs"]) or "- None"), ("Limitations", "\n".join(f"- {x}" for x in list_fields["limitations"]) or "- None"), ("Risks", "\n".join(f"- {x}" for x in list_fields["risks"]) or "- None"), ("Recommended Next Action", card["recommended_next_action"] or "None")]
    lines = [f"# Project Steward Result: {task_id}", ""]
    for heading, body in sections:
        lines.extend([f"## {heading}", str(body), ""])
    lines.extend(["## Machine-readable Final Block", "```json", json.dumps({"task_id": task_id, "spec_id": task_id, "outcome": outcome, "verdict": verdict, "project_id": contract["project_id"]}, sort_keys=True), "``", ""])
    card = apply_common_card(meta, ALLOWED_PROFILE, card, "STEWARD_RESULT.md")
    card_path = assert_artifact_target(task_dir, "STEWARD_CARD.json", allowed={"STEWARD_CARD.json", "STEWARD_RESULT.md", "WORKSPACE_RECOMMENDATION.json"})
    result_path = assert_artifact_target(task_dir, "STEWARD_RESULT.md", allowed={"STEWARD_CARD.json", "STEWARD_RESULT.md", "WORKSPACE_RECOMMENDATION.json"})
    recommendation_path = None
    if recommendation is not None:
        recommendation_path = assert_artifact_target(task_dir, "WORKSPACE_RECOMMENDATION.json", allowed={"STEWARD_CARD.json", "STEWARD_RESULT.md", "WORKSPACE_RECOMMENDATION.json"})
    _atomic_write(card_path, json.dumps(card, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    _atomic_write(result_path, "\n".join(lines))
    if recommendation is not None and recommendation_path is not None:
        _atomic_write(recommendation_path, json.dumps(recommendation, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    return {"status": "submitted", "card": "STEWARD_CARD.json", "artifact": "STEWARD_RESULT.md"}
