"""Compact Registry-backed relationship briefing for task-main decision support."""
from __future__ import annotations

from typing import Any

from ._project_common import MAX_QUERY, MAX_RESULTS, json_result
from ._project_registry import load_search_records, search_records
from ._workspace import WorkspaceError, resolve_workspace

TOOL_NAME = "aota_project_relationship_brief"
TOOLSET_NAME = "aota_project_readonly"
SCHEMA = {"name": TOOL_NAME, "description": "Summarize bounded Registry evidence and relationship options for task-main; never makes or executes a project decision.", "parameters": {"type": "object", "properties": {"workspace_id": {"type": "string"}, "request_summary": {"type": "string"}, "limit": {"type": "integer", "description": "1..10"}, "candidate_project_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 10}}, "required": ["workspace_id", "request_summary"], "additionalProperties": False}}


def _candidate(record: dict[str, Any]) -> dict[str, Any]:
    score = record.get("score", 0)
    if score >= 60:
        level, options = "strong", ["extend", "reuse"]
    elif score >= 25:
        level, options = "plausible", ["reuse", "extend", "experiment"]
    elif score > 0:
        level, options = "weak", ["experiment", "uncertain"]
    else:
        level, options = "uncertain", ["uncertain"]
    reusable = [
        {"capability": capability, "source_field": "capabilities", "project_id": record["project_id"]}
        for capability in record.get("capabilities", [])
    ]
    paths = []
    for field, values in record.get("relevant_paths", {}).items():
        values = [values] if isinstance(values, str) else values
        for value in values:
            paths.append({"path": value, "source_field": f"paths.{field}", "project_id": record["project_id"]})
    return {
        "project_id": record["project_id"],
        "relationship_options": options,
        "relevance": {"level": level, "score": score, "reasons": record.get("match_reasons", [])},
        "reusable_capabilities": reusable,
        "relevant_paths": paths[:32],
        "constraints": [{"value": value, "source_field": "constraints", "project_id": record["project_id"]} for value in record.get("constraints", [])[:32]],
        "active_plan_id": record.get("active_plan_id"),
        "warnings": sorted(set(record.get("warnings", []) + (["inactive_project"] if record.get("status") != "active" else []))),
        "name": record["name"],
        "kind": record["kind"],
        "status": record["status"],
        "summary": record["summary"][:400],
    }


def handle(args: dict[str, Any], **_kwargs) -> str:
    workspace_id = args.get("workspace_id", "")
    request = args.get("request_summary", "")
    limit = args.get("limit", 3)
    requested_ids = args.get("candidate_project_ids")
    if not isinstance(workspace_id, str) or not workspace_id:
        return json_result({"status": "error", "error_code": "workspace_not_found"})
    if not isinstance(request, str) or not request.strip() or len(request) > MAX_QUERY:
        return json_result({"status": "error", "error_code": "request_summary_invalid"})
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 10:
        return json_result({"status": "error", "error_code": "limit_invalid"})
    if requested_ids is not None and (not isinstance(requested_ids, list) or len(requested_ids) > 10 or not all(isinstance(item, str) for item in requested_ids)):
        return json_result({"status": "error", "error_code": "candidate_not_found"})
    try:
        root = resolve_workspace(workspace_id)
        records, registry = load_search_records(workspace_id, root)
    except WorkspaceError as exc:
        return json_result({"status": "error", "error_code": "workspace_not_found", "detail": str(exc)[:300]})
    by_id = {record["project_id"]: record for record in records}
    if requested_ids is not None:
        missing = sorted(set(requested_ids) - set(by_id))
        if missing:
            return json_result({"status": "error", "error_code": "candidate_not_found", "project_ids": missing})
        matches = search_records(records, request, MAX_RESULTS, True)
        scores = {item["project_id"]: item for item in matches}
        selected = []
        for project_id in requested_ids:
            selected.append(scores.get(project_id, {**by_id[project_id], "score": 0, "match_reasons": [], "matched_fields": []}))
        selected.sort(key=lambda item: (-item.get("score", 0), item["project_id"]))
        selected = selected[:limit]
    else:
        selected = search_records(records, request, limit, False)
    candidates = [_candidate(record) for record in selected]
    strongest = candidates[0]["project_id"] if candidates and candidates[0]["relevance"]["level"] in {"strong", "plausible"} else None
    warnings = [] if registry["status"] == "fresh" else [registry.get("warning") or "registry_stale"]
    return json_result({
        "status": "ok",
        "request_summary": request,
        "registry": {"status": registry["status"], "revision": registry.get("registry_revision"), "stale": registry["status"] != "fresh", "source": registry["source"]},
        "candidates": candidates,
        "decision_support": {
            "strongest_candidate": strongest,
            "alternatives": [item["project_id"] for item in candidates[1:]],
            "no_match_detected": not bool(candidates),
            "relationship_options": ["new", "experiment", "uncertain"] if not candidates else candidates[0]["relationship_options"],
            "suggested_next_step": "task-main evaluates reuse/extend evidence" if strongest else "task-main may investigate new or experiment; evidence is insufficient for an automatic decision",
        },
        "limitations": sorted(set(warnings + ["decision_support_only", "declared_manifest_fields_only"])),
    })
