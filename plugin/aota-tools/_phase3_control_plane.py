"""Phase 3 semantic artifact/project control-plane adapters.

The existing project tools remain the implementation authority.  This module
is the model-facing projection: it resolves ``current_*`` references from
trusted runtime context, injects the legacy handler fields internally, and
returns bounded deterministic results.  Explicit legacy calls are delegated
unchanged so old callers keep the same path/authority checks.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Mapping

from ._project_common import ProjectError, load_observed, load_project
from ._project_registry import REGISTRY_FILE, _read_existing, _scan_sources
from ._trusted_runtime_context import get_trusted_runtime_context
from ._workspace import WorkspaceError, resolve_workspace
from ._next_tool_contract import attach_next_tool_option

MAX_READ_BYTES = 64 * 1024
_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

ARTIFACT_REFS = frozenset({
    "current_completion_card", "current_completion_report", "current_plan",
    "current_spec", "current_work_item", "current_receipt", "current_decision",
    "current_project_declaration", "current_project_observed_state",
    "current_lifecycle_evidence", "current_stewardship_subject",
})
PROJECT_REFS = frozenset({"current_project"})
FILE_REFS = frozenset({
    "current_project_declaration", "current_project_observed_state",
    "current_readme", "current_changelog", "current_roadmap",
})

_CURRENT_PROJECT_SETUP_ROUTE = {
    "contract_version": 1,
    "route_id": "raw_current_project_setup_v1",
    "domain_sequence": [
        "aota_project_registry_refresh",
        "aota_project_open",
    ],
    "max_domain_calls": 2,
    "forbidden_discovery_tools": [
        "aota_workspace_open",
        "aota_project_search",
        "aota_project_scan",
        "aota_project_registry_open",
        "tool_search",
        "tool_describe",
    ],
}


class Phase3ResolutionError(Exception):
    def __init__(self, code: str, *, choices: list[dict[str, Any]] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.choices = choices or []


def _json(value: Mapping[str, Any]) -> str:
    result = dict(value)
    result.setdefault("same_call_retryable", bool(result.get("retryable", False)))
    next_action = str(result.get("next_action") or "none")
    if result.get("human_action_required"):
        result.setdefault("flow_disposition", "await_human")
    elif next_action == "none":
        result.setdefault("flow_disposition", "complete")
    elif next_action.startswith("stop_and_report"):
        result.setdefault("flow_disposition", "stop")
    else:
        result.setdefault("flow_disposition", "continue")
    allowed = {
        "register_current_project": (
            "aota_project_registry_refresh",
            {"operation": "register_current_project"},
        ),
        "open_current_project": (
            "aota_project_open",
            {"project_ref": "current_project", "include_observed": True},
        ),
        "refresh_current_project_observed_state": (
            "aota_project_registry_refresh",
            {"operation": "refresh_current_project_observed_state"},
        ),
    }.get(next_action)
    if allowed is not None:
        schema = PHASE3_SCHEMAS.get(allowed[0])
        if schema is not None:
            attach_next_tool_option(
                result,
                schema,
                arguments=allowed[1],
                include=tuple(allowed[1]),
                reason=f"Control-plane result permits the {next_action} transition.",
            )
    return json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _failure(exc: Phase3ResolutionError, operation: str) -> dict[str, Any]:
    ambiguous = exc.code == "current_project_ambiguous" or bool(exc.choices)
    result: dict[str, Any] = {
        "status": "rejected",
        "operation_result": operation,
        "error": exc.code,
        "retryable": False,
        "human_action_required": ambiguous,
        "next_action": "select_current_project" if ambiguous else _next_action(exc.code),
    }
    if exc.choices:
        result["choices"] = exc.choices[:10]
    return result


def _next_action(code: str) -> str:
    return {
        "current_project_missing": "initialize_current_project",
        "project_registration_required": "register_current_project",
        "project_initialization_required": "initialize_current_project",
        "artifact_missing": "create_or_complete_current_artifact",
        "artifact_ambiguous": "select_current_artifact",
        "trusted_session_context_missing": "stop_and_report_runtime_context_missing",
        "workspace_not_found": "select_current_workspace",
        "project_root_conflict": "select_alternate_project_identity",
        "path_escape": "stop_and_report_path_authority_failure",
        "symlink_escape": "stop_and_report_path_authority_failure",
    }.get(code, "stop_and_report_control_plane_error")


def _trusted_workspace(kwargs: Mapping[str, Any]) -> str:
    context = get_trusted_runtime_context(kwargs)
    if context.workspace_id:
        return context.workspace_id
    raise Phase3ResolutionError("trusted_session_context_missing")


def _trusted_project_id(kwargs: Mapping[str, Any]) -> str:
    context = get_trusted_runtime_context(kwargs)
    values = (
        kwargs.get("trusted_project_id"),
        kwargs.get("current_project_id"),
        context.execution_context.get("project_id"),
        os.environ.get("AOTA_TRUSTED_PROJECT_ID"),
        os.environ.get("AOTA_CURRENT_PROJECT_ID"),
    )
    for value in values:
        if isinstance(value, str) and value.strip():
            if not _ID_RE.fullmatch(value.strip()):
                raise Phase3ResolutionError("project_id_invalid")
            return value.strip()
    return ""


def _selection_project_id(workspace_id: str, kwargs: Mapping[str, Any]) -> str:
    """Read an existing task-main selection; never create or guess one."""
    decision_id = kwargs.get("workspace_selection_decision_id") or os.environ.get("AOTA_WORKSPACE_SELECTION_DECISION_ID", "")
    if not isinstance(decision_id, str) or not decision_id:
        return ""
    try:
        from ._orchestration_common import find_decision_path, read_decision
        from ._workspace_context import validate_workspace_context
        path = find_decision_path(workspace_id, decision_id)
        if path is None:
            raise ValueError("workspace_decision_not_found")
        record = read_decision(path)
        if record.get("decision_type") != "workspace_selection" or record.get("decided_by") != "task-main":
            raise ValueError("workspace_selection_authority_denied")
        context = validate_workspace_context(record.get("workspace_context"))
    except Exception as exc:
        raise Phase3ResolutionError("workspace_selection_invalid") from exc
    return str(context["project_id"])


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_target(path: Path, root: Path, *, must_exist: bool = True) -> Path:
    if "\x00" in str(path) or path.is_symlink():
        raise Phase3ResolutionError("symlink_escape")
    if must_exist and not path.exists():
        raise Phase3ResolutionError("artifact_missing")
    try:
        resolved = path.resolve(strict=must_exist)
    except OSError as exc:
        raise Phase3ResolutionError("artifact_missing") from exc
    if not _inside(resolved, root.resolve()):
        raise Phase3ResolutionError("path_escape")
    return resolved


def _choices(records: list[dict[str, Any]], root: Path) -> list[dict[str, Any]]:
    return [{
        "selector": f"project:{item['project_id']}",
        "display_name": item.get("name", item["project_id"])[:120],
        "project_kind": item.get("kind", "")[:96],
        "canonical_root_summary": (item.get("root") or ".")[:120],
        "registration_state": "registered",
    } for item in records[:10]]


def _resolve_declared_current_project(*, kwargs: Mapping[str, Any]) -> tuple[str, Path, dict[str, Any]]:
    """Resolve one exact project declaration without requiring derived registry state."""
    workspace_id = _trusted_workspace(kwargs)
    try:
        workspace_root = resolve_workspace(workspace_id)
        records, _invalid, duplicates, _warnings = _scan_sources(workspace_root)
    except WorkspaceError as exc:
        raise Phase3ResolutionError("workspace_not_found") from exc
    except (OSError, ProjectError) as exc:
        raise Phase3ResolutionError("project_registry_unavailable") from exc
    project_id = _trusted_project_id(kwargs) or _selection_project_id(workspace_id, kwargs)
    if project_id and project_id in duplicates:
        raise Phase3ResolutionError("current_project_ambiguous", choices=_choices([r for r in records if r.get("project_id") == project_id], workspace_root))
    matches = [r for r in records if not project_id or r.get("project_id") == project_id]
    if not matches:
        raise Phase3ResolutionError("current_project_missing")
    if len(matches) > 1:
        raise Phase3ResolutionError("current_project_ambiguous", choices=_choices(matches, workspace_root))
    record = matches[0]
    return workspace_id, workspace_root, record


def resolve_current_project(*, kwargs: Mapping[str, Any], project_ref: str = "current_project") -> dict[str, Any]:
    """Resolve one exact registered project without cwd or latest fallback."""
    if project_ref not in PROJECT_REFS:
        raise Phase3ResolutionError("project_ref_invalid")
    workspace_id, workspace_root, record = _resolve_declared_current_project(kwargs=kwargs)
    registry_path = workspace_root / REGISTRY_FILE
    existing_registry, registry_error = _read_existing(registry_path)
    if registry_error is not None or not isinstance(existing_registry, dict):
        raise Phase3ResolutionError("project_registration_required")
    exact_registry = [item for item in existing_registry.get("projects", []) if isinstance(item, dict) and item.get("project_id") == record.get("project_id")]
    if len(exact_registry) != 1:
        raise Phase3ResolutionError("project_registration_required")
    if exact_registry[0].get("root") != record.get("root") or exact_registry[0].get("manifest_path") != record.get("manifest_path"):
        raise Phase3ResolutionError("project_root_conflict")
    project_root = workspace_root if record.get("root") == "." else workspace_root / str(record["root"])
    try:
        project_root = _safe_target(project_root, workspace_root)
        manifest = _safe_target(project_root / ".aota" / "project.yaml", project_root)
        declaration_root, declaration = load_project(manifest)
    except (ProjectError, Phase3ResolutionError, OSError) as exc:
        if isinstance(exc, Phase3ResolutionError):
            raise
        raise Phase3ResolutionError("project_declaration_invalid") from exc
    if declaration["project"]["id"] != record["project_id"] or declaration_root != project_root:
        raise Phase3ResolutionError("project_root_conflict")
    observed = None
    observed_status = "missing"
    observed_path = project_root / ".aota" / "observed.json"
    if observed_path.exists():
        try:
            observed = load_observed(_safe_target(observed_path, project_root))
            observed_status = "valid"
        except (ProjectError, Phase3ResolutionError, OSError):
            observed_status = "invalid"
    return {
        "workspace_id": workspace_id,
        "project_id": record["project_id"],
        "project_root": project_root,
        "manifest": manifest,
        "declaration": declaration,
        "observed": observed,
        "observed_status": observed_status,
        "registry_record": record,
    }


def _task_root(kwargs: Mapping[str, Any]) -> Path | None:
    context = get_trusted_runtime_context(kwargs)
    task_id = context.execution_context.get("task_id") or kwargs.get("task_id")
    workspace_id = context.workspace_id
    root_value = os.environ.get("AOTA_PROFILE_TASK_ROOT", "")
    if not isinstance(task_id, str) or not task_id or not root_value or not workspace_id:
        return None
    root = Path(root_value).resolve(strict=False)
    return _safe_target(root / workspace_id / task_id, root, must_exist=False)


def _artifact_candidates(ref: str, project: dict[str, Any], kwargs: Mapping[str, Any]) -> list[Path]:
    if ref == "current_project_declaration":
        return [project["manifest"]]
    if ref == "current_project_observed_state":
        return [project["project_root"] / ".aota" / "observed.json"]
    if ref == "current_lifecycle_evidence":
        return [project["project_root"] / ".aota" / "lifecycle.json", project["project_root"] / ".aota" / "lifecycle-evidence.json"]
    if ref == "current_stewardship_subject":
        return [project["project_root"] / ".aota" / "stewardship.json"]
    task = _task_root(kwargs)
    names = {
        "current_completion_card": ("COMPLETION_CARD.json", "completion-card.json"),
        "current_completion_report": ("COMPLETION.md", "COMPLETION_REPORT.md", "completion.md"),
        "current_receipt": ("completion.receipt.json", "completion.json"),
        "current_decision": ("decision.json",),
        "current_spec": ("SPEC.md",),
        "current_plan": ("PLAN.md", "plan.json"),
        "current_work_item": ("WORK_ITEM.md", "work-item.json"),
    }.get(ref, ())
    return [task / name for name in names] if task else []


def resolve_current_artifact(ref: str, *, kwargs: Mapping[str, Any]) -> dict[str, Any]:
    if ref not in ARTIFACT_REFS:
        raise Phase3ResolutionError("artifact_ref_invalid")
    project = resolve_current_project(kwargs=kwargs)
    candidates = _artifact_candidates(ref, project, kwargs)
    existing = []
    for candidate in candidates:
        base = project["project_root"] if candidate.is_relative_to(project["project_root"]) else (candidate.parents[2] if len(candidate.parents) > 2 else candidate.parent)
        try:
            target = _safe_target(candidate, base)
        except Phase3ResolutionError as exc:
            if exc.code in {"artifact_missing"}:
                continue
            raise
        if target not in existing:
            existing.append(target)
    if not existing:
        raise Phase3ResolutionError("artifact_missing")
    if len(existing) > 1:
        raise Phase3ResolutionError("artifact_ambiguous", choices=[{"selector": ref, "candidate": p.name} for p in existing])
    target = existing[0]
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise Phase3ResolutionError("artifact_missing") from exc
    if len(data) > MAX_READ_BYTES:
        raise Phase3ResolutionError("artifact_too_large")
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Phase3ResolutionError("artifact_encoding_invalid") from exc
    return {
        "artifact_ref": ref,
        "artifact_kind": ref.removeprefix("current_"),
        "path": target,
        "content": content,
        "sha256": hashlib.sha256(data).hexdigest(),
        "project_id": project["project_id"],
        "workspace_id": project["workspace_id"],
    }


def _observed(project: dict[str, Any]) -> dict[str, Any]:
    root = project["project_root"]
    declaration = project["declaration"]
    codegraph = declaration.get("codegraph", {})
    return {
        "schema_version": 1,
        "project_id": project["project_id"],
        "source": "control_plane_observation",
        "confidence": "bounded",
        "filesystem": {"root_exists": root.is_dir(), "manifest_exists": project["manifest"].is_file()},
        "git": {"available": (root / ".git").exists(), "branch": "", "head": "", "dirty": False},
        "codegraph": {"configured": bool(codegraph.get("enabled")), "index_present": (root / str(codegraph.get("index_location", ".codegraph")) / "codegraph.db").is_file(), "runtime_verified": False},
        "plan": {"active_plan_id": declaration.get("plan", {}).get("active_plan_id"), "revision": None, "sha256": None},
        "limitations": ["git_details_not_probed", "codegraph_runtime_not_probed"],
    }


def _reconcile(project: dict[str, Any]) -> dict[str, Any]:
    record = project["registry_record"]
    manifest_digest = hashlib.sha256(project["manifest"].read_bytes()).hexdigest()
    findings: list[dict[str, Any]] = []
    if record.get("project_id") != project["declaration"]["project"]["id"]:
        findings.append({"code": "project_id_mismatch", "severity": "error"})
    if record.get("manifest_sha256") != manifest_digest:
        findings.append({"code": "registry_manifest_stale", "severity": "warning"})
    if project["observed"] and project["observed"].get("project_id") != project["project_id"]:
        findings.append({"code": "observed_project_id_conflict", "severity": "error"})
    return {"declaration_facts": {"project_id": project["project_id"], "status": project["declaration"]["project"]["status"]}, "observed_facts": project["observed"] or _observed(project), "reconciliation_findings": findings, "proposed_mutations": [], "operator_required_mutations": []}


def _canonical_result(operation: str, result: dict[str, Any], *, next_action: str = "none") -> str:
    result.setdefault("status", "completed")
    result.setdefault("operation_result", operation)
    result.setdefault("resolved_context", {})
    result.setdefault("next_action", next_action)
    result.setdefault("retryable", False)
    result.setdefault("human_action_required", False)
    return _json(result)


def _legacy(name: str) -> Callable[..., str]:
    from importlib import import_module
    modules = {
        "aota_subject_task_artifact_open": ("_subject_task_artifact_open", "handle"),
        "aota_project_scan": ("_project_discovery", "handle"),
        "aota_project_search": ("_project_open", "handle_search"),
        "aota_project_open": ("_project_open", "handle_open"),
        "aota_project_registry_refresh": ("_project_registry", "handle_refresh"),
        "aota_project_registry_open": ("_project_registry", "handle_open"),
        "aota_project_relationship_brief": ("_project_relationship", "handle"),
        "aota_project_prepare": ("_project_prepare", "handle"),
        "aota_project_docs_update": ("_project_steward_mutation", "handle_docs_update"),
        "aota_project_artifact_link": ("_project_steward_mutation", "handle_artifact_link"),
        "aota_codegraph_status": ("_codegraph_readonly", "status_handle"),
        "aota_codegraph_query": ("_codegraph_readonly", "query_handle"),
        "aota_codegraph_explore": ("_codegraph_readonly", "explore_handle"),
        "aota_codegraph_rebuild": ("_codegraph_rebuild", "handle"),
        "aota_project_file_read": ("_project_file_mutation", "handle_read"),
        "aota_project_file_write": ("_project_file_mutation", "handle_write"),
        "aota_project_file_patch": ("_project_file_mutation", "handle_patch"),
        "aota_project_command_run": ("_project_command_run", "handle"),
        "aota_project_initialize_core": ("_project_initializer", "handle_initialize_core"),
        "aota_workspace_open": ("_workspace_context", "handle_open"),
        "aota_workspace_selection_record": ("_workspace_selection_record", "handle"),
    }
    module, function = modules[name]
    return getattr(import_module(f"{__package__}.{module}"), function)


def _legacy_call(name: str, args: dict[str, Any], kwargs: Mapping[str, Any]) -> str:
    return _legacy(name)(args, **dict(kwargs))


def handle(name: str, args: dict[str, Any], *, legacy_handler: Callable[..., str], **kwargs: Any) -> str:
    """Dispatch canonical semantic input or preserve an explicit legacy call."""
    if not isinstance(args, dict):
        return _json({"status": "rejected", "error": "invalid_arguments"})
    legacy_fields = {"workspace_id", "project_id", "project_root", "path", "task_id", "spec_id", "work_item_id", "subject_task_id", "artifact_name", "expected_sha256", "expected_index_sha256", "outcome", "verdict", "scope_status"}
    if legacy_fields & set(args):
        return legacy_handler(args, **kwargs)
    try:
        if name == "aota_subject_task_artifact_open":
            artifact = args.get("artifact_ref", "current_completion_report")
            opened = resolve_current_artifact(artifact, kwargs=kwargs)
            return _canonical_result("artifact_opened", {"artifact_kind": opened["artifact_kind"], "binding_verified": True, "content": opened["content"][:MAX_READ_BYTES], "sha256": opened["sha256"], "resolved_context": {"workspace_id": opened["workspace_id"], "project_id": opened["project_id"]}})
        if name in {"aota_project_scan", "aota_project_search", "aota_project_relationship_brief", "aota_project_registry_open"}:
            injected = dict(args); injected["workspace_id"] = _trusted_workspace(kwargs)
            if name == "aota_project_registry_open": injected = {"workspace_id": injected["workspace_id"]}
            raw = json.loads(_legacy_call(name, injected, kwargs))
            raw.update({"operation_result": name.removeprefix("aota_"), "next_action": "select_current_project" if name == "aota_project_relationship_brief" else "none", "retryable": False, "human_action_required": False})
            return _json(raw)
        if name == "aota_workspace_open":
            raw = json.loads(_legacy_call(name, {"workspace_id": _trusted_workspace(kwargs)}, kwargs))
            raw.update({"operation_result": "workspace_opened", "next_action": "none", "retryable": False, "human_action_required": False})
            return _json(raw)
        if name == "aota_workspace_selection_record":
            selector = args.get("project_selector")
            if selector is not None and (not isinstance(selector, str) or not selector.startswith("project:")):
                raise Phase3ResolutionError("project_selector_invalid")
            selected_id = selector.removeprefix("project:") if isinstance(selector, str) else ""
            selected_kwargs = dict(kwargs)
            if selected_id:
                selected_kwargs["trusted_project_id"] = selected_id
            selected = resolve_current_project(kwargs=selected_kwargs)
            internal = {"workspace_id": selected["workspace_id"], "project_id": selected["project_id"], "source_type": args.get("source_type"), "relationship": args.get("relationship"), "recommendation_id": None, "user_message_ref": None}
            raw = json.loads(_legacy_call(name, internal, kwargs))
            raw.update({"operation_result": "workspace_selection_recorded", "resolved_context": {"workspace_id": selected["workspace_id"], "project_id": selected["project_id"]}, "next_action": "continue_governance", "retryable": False, "human_action_required": False})
            return _json(raw)
        if name == "aota_project_registry_refresh" and args.get("operation", "refresh_current_project_registry") == "register_current_project":
            workspace_id, _workspace_root, record = _resolve_declared_current_project(kwargs=kwargs)
            raw = json.loads(_legacy_call(name, {"workspace_id": workspace_id, "dry_run": bool(args.get("dry_run", False)), "include_invalid": bool(args.get("include_invalid", False))}, kwargs))
            if raw.get("status") != "ok":
                raw.update({"operation_result": "project_registry_refresh", "next_action": "stop_and_report_control_plane_error", "retryable": False, "human_action_required": False})
                return _json(raw)
            if not bool(args.get("dry_run", False)):
                registered = resolve_current_project(kwargs=kwargs)
                if registered["project_id"] != record["project_id"]:
                    raise Phase3ResolutionError("project_root_conflict")
            raw.update({"operation_result": "project_registered", "registry_exact_match": 1, "resolved_context": {"workspace_id": workspace_id, "project_id": record["project_id"]}, "next_action": "open_current_project", "retryable": False, "human_action_required": False})
            _attach_current_project_setup_route(raw, name=name, args=args)
            return _json(raw)
        project = resolve_current_project(kwargs=kwargs)
        if name == "aota_project_steward_report":
            operation = args.get("operation", "reconcile_current_project")
            findings = _reconcile(project)
            return _canonical_result("stewardship_reconciled", {"operation": operation, "rationale": args.get("rationale", ""), **findings, "resolved_context": {"workspace_id": project["workspace_id"], "project_id": project["project_id"]}})
        if name == "aota_project_open":
            raw = json.loads(_legacy_call(name, {"workspace_id": project["workspace_id"], "project_id": project["project_id"], "include_observed": args.get("include_observed", True)}, kwargs))
            raw.update({"operation_result": "project_opened", "resolved_context": {"workspace_id": project["workspace_id"], "project_id": project["project_id"]}, "next_action": "refresh_current_project_observed_state" if project["observed_status"] != "valid" else "none", "retryable": False, "human_action_required": False})
            _attach_current_project_setup_route(raw, name=name, args=args)
            return _json(raw)
        if name == "aota_project_registry_refresh":
            operation = args.get("operation", "refresh_current_project_registry")
            if operation == "refresh_current_project_observed_state":
                return _canonical_result("observed_state_refreshed", {"observed": _observed(project), "resolved_context": {"workspace_id": project["workspace_id"], "project_id": project["project_id"]}, "codegraph_available": False}, next_action="reconcile_current_project")
            raw = json.loads(_legacy_call(name, {"workspace_id": project["workspace_id"], "dry_run": bool(args.get("dry_run", False)), "include_invalid": bool(args.get("include_invalid", False))}, kwargs))
            raw.update({"operation_result": "project_registered", "registry_exact_match": 1, "resolved_context": {"workspace_id": project["workspace_id"], "project_id": project["project_id"]}, "next_action": "refresh_current_project_observed_state", "retryable": False, "human_action_required": False})
            return _json(raw)
        if name == "aota_project_prepare":
            operation = args.get("operation", "prepare_current_project")
            if operation == "refresh_current_project_observed_state":
                return _canonical_result("observed_state_refreshed", {"observed": _observed(project), "codegraph_available": False, "resolved_context": {"workspace_id": project["workspace_id"], "project_id": project["project_id"]}}, next_action="reconcile_current_project")
            if operation == "reconcile_current_project":
                return _canonical_result("project_reconciled", {**_reconcile(project), "resolved_context": {"workspace_id": project["workspace_id"], "project_id": project["project_id"]}})
            if operation == "request_project_lifecycle_transition":
                return _canonical_result("lifecycle_checkpoint_required", {"lifecycle_action": args.get("action"), "rationale": args.get("rationale", ""), "resolved_context": {"workspace_id": project["workspace_id"], "project_id": project["project_id"]}, "human_action_required": True}, next_action="confirm_project_lifecycle_action")
            injected = {"workspace_id": project["workspace_id"], "project_id": project["project_id"], "include_commands": bool(args.get("include_commands", False)), "include_constraints": bool(args.get("include_constraints", False)), "include_plan_reference": bool(args.get("include_plan_reference", False)), "max_warnings": int(args.get("max_warnings", 20))}
            return _legacy_call(name, injected, kwargs)
        if name == "aota_project_artifact_link":
            source = resolve_current_artifact(str(args.get("artifact_ref", "")), kwargs=kwargs)
            target = args.get("target_ref")
            if target not in {"current_work_item", "current_project", "current_stewardship_subject"}:
                raise Phase3ResolutionError("target_ref_invalid")
            if target == "current_work_item":
                target_id = get_trusted_runtime_context(kwargs).execution_context.get("task_id")
            else:
                target_id = project["project_id"]
            if not isinstance(target_id, str) or not target_id:
                raise Phase3ResolutionError("current_work_item_missing")
            internal = {"workspace_id": project["workspace_id"], "project_id": project["project_id"], "work_item_id": target_id, "artifact_type": args.get("relation", "evidence_for"), "artifact_ref": source["path"].relative_to(project["project_root"]).as_posix() if _inside(source["path"], project["project_root"]) else source["artifact_ref"], "relation": args.get("relation", "evidence_for")}
            raw = json.loads(_legacy_call(name, internal, kwargs))
            raw.update({"operation_result": "artifact_linked", "binding_verified": True, "next_action": "none", "retryable": False, "human_action_required": False})
            return _json(raw)
        if name == "aota_project_initialize_core":
            title = args.get("project_name")
            if not isinstance(title, str) or not title.strip():
                raise Phase3ResolutionError("project_name_required")
            project_id = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")[:80]
            if not project_id:
                raise Phase3ResolutionError("project_name_invalid")
            # Parent selection is semantic; the canonical root and project ID
            # are derived here and never accepted from the model.
            internal = {"workspace_id": _trusted_workspace(kwargs), "project_id": project_id, "project_name": title.strip(), "project_kind": args.get("project_kind", "aota-project"), "project_root": project_id, "summary": args["summary"], "capabilities": args.get("capabilities", []), "source_root": "src"}
            return _canonical_result("project_initialization_planned", {"project_id": project_id, "canonical_parent": args.get("canonical_parent", "workspace_default"), "generated_fields": ["project_id", "canonical_root", "project.yaml", "observed state", "registry binding"], "real_mutation_performed": False, "resolved_context": {"workspace_id": internal["workspace_id"]}}, next_action="confirm_project_initialization")
        if name in {"aota_codegraph_status", "aota_codegraph_query", "aota_codegraph_explore", "aota_codegraph_rebuild"}:
            injected = dict(args); injected.update({"workspace_id": project["workspace_id"], "project_id": project["project_id"]})
            if name == "aota_codegraph_status": injected = {"workspace_id": project["workspace_id"], "project_id": project["project_id"]}
            return _legacy_call(name, injected, kwargs)
        if name in {"aota_project_file_read", "aota_project_file_write", "aota_project_file_patch"}:
            ref = args.get("file_ref")
            mapping = {"current_project_declaration": ".aota/project.yaml", "current_project_observed_state": ".aota/observed.json", "current_readme": "README.md", "current_changelog": "CHANGELOG.md", "current_roadmap": "ROADMAP.md"}
            if ref not in mapping:
                raise Phase3ResolutionError("file_ref_invalid")
            internal = dict(args); internal.pop("file_ref", None); internal["workspace_id"] = project["workspace_id"]; internal["project_id"] = project["project_id"]; internal["path"] = mapping[ref]
            if name in {"aota_project_file_write", "aota_project_file_patch"}:
                internal["task_id"] = get_trusted_runtime_context(kwargs).execution_context.get("task_id", "phase3-source"); internal["spec_id"] = get_trusted_runtime_context(kwargs).execution_context.get("task_id", "phase3-source")
            if name == "aota_project_file_patch":
                target = _safe_target(project["project_root"] / mapping[ref], project["project_root"])
                internal["expected_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
            return _legacy_call(name, internal, kwargs)
        if name == "aota_project_command_run":
            internal = {"task_id": get_trusted_runtime_context(kwargs).execution_context.get("task_id", "phase3-source"), "spec_id": get_trusted_runtime_context(kwargs).execution_context.get("task_id", "phase3-source"), "command_id": args.get("command_id"), "args": []}
            return _legacy_call(name, internal, kwargs)
        raise Phase3ResolutionError("phase3_tool_not_supported")
    except Phase3ResolutionError as exc:
        return _json(_failure(exc, name.removeprefix("aota_")))
    except (WorkspaceError, ProjectError, OSError, ValueError, KeyError) as exc:
        return _json({"status": "rejected", "operation_result": name.removeprefix("aota_"), "error": str(exc)[:120], "retryable": False, "human_action_required": False, "next_action": "stop_and_report_control_plane_error"})


def phase3_handler(name: str, legacy_handler: Callable[..., str]) -> Callable[..., str]:
    def _handle(args: dict[str, Any], **kwargs: Any) -> str:
        return handle(name, args, legacy_handler=legacy_handler, **kwargs)
    return _handle


def _attach_current_project_setup_route(
    result: dict[str, Any],
    *,
    name: str,
    args: Mapping[str, Any],
) -> None:
    """Expose the bounded raw setup route as a runtime contract.

    The contract is a control-plane hint, not a global tool lock: it describes
    the exact two domain calls for the raw current-project probe while leaving
    other stewardship operations available after the route completes.
    """
    if name == "aota_project_registry_refresh" and args.get("operation") == "register_current_project":
        result["route_contract"] = {
            **_CURRENT_PROJECT_SETUP_ROUTE,
            "step": 1,
            "next_action": "open_current_project",
            "allowed_next_tool": "aota_project_open",
            "allowed_next_arguments": {
                "project_ref": "current_project",
                "include_observed": True,
            },
        }
    elif name == "aota_project_open" and args.get("project_ref") == "current_project":
        result["route_contract"] = {
            **_CURRENT_PROJECT_SETUP_ROUTE,
            "step": 2,
            "complete": result.get("next_action") == "none",
            "next_action": result.get("next_action", "none"),
        }


def _schema(name: str, description: str, properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    return {"name": name, "description": description, "parameters": {"type": "object", "properties": properties or {}, "required": required or [], "additionalProperties": False}}


_COMMON = " Semantic references are resolved by trusted control-plane context; no path, ID, hash, digest, revision, or registry location is accepted."
PHASE3_SCHEMAS: dict[str, dict[str, Any]] = {
    "aota_subject_task_artifact_open": _schema("aota_subject_task_artifact_open", "Open one current semantic artifact." + _COMMON, {"artifact_ref": {"type": "string", "enum": sorted(ARTIFACT_REFS)}, "query": {"type": "string", "maxLength": 500}, "max_bytes": {"type": "integer", "minimum": 1, "maximum": MAX_READ_BYTES}}),
    "aota_workspace_open": _schema("aota_workspace_open", "Open the trusted current workspace." + _COMMON),
    "aota_project_scan": _schema("aota_project_scan", "Scan the trusted workspace for registered projects." + _COMMON, {"limit": {"type": "integer", "minimum": 1, "maximum": 50}}),
    "aota_project_search": _schema("aota_project_search", "Search registered projects using a semantic request." + _COMMON, {"query": {"type": "string", "maxLength": 256}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}, "include_inactive": {"type": "boolean"}}, ["query"]),
    "aota_project_open": _schema("aota_project_open", "Open the trusted current project. In raw_current_project_setup_v1 this is the second and final normal domain call; consume the returned route_contract." + _COMMON, {"project_ref": {"type": "string", "enum": ["current_project"]}, "include_observed": {"type": "boolean"}}),
    "aota_project_registry_refresh": _schema("aota_project_registry_refresh", "Refresh or register the trusted current project using the existing registry. In raw_current_project_setup_v1, register_current_project is the first domain call and returns the exact aota_project_open schema; do not perform discovery first." + _COMMON, {"operation": {"type": "string", "enum": ["refresh_current_project_registry", "register_current_project", "refresh_current_project_observed_state"]}, "dry_run": {"type": "boolean"}, "include_invalid": {"type": "boolean"}}),
    "aota_project_registry_open": _schema("aota_project_registry_open", "Open the trusted current project registry." + _COMMON),
    "aota_project_relationship_brief": _schema("aota_project_relationship_brief", "Return bounded project relationship evidence for a semantic request." + _COMMON, {"request_summary": {"type": "string", "maxLength": 256}, "limit": {"type": "integer", "minimum": 1, "maximum": 10}}, ["request_summary"]),
    "aota_project_prepare": _schema("aota_project_prepare", "Prepare, observe, reconcile, or request a checkpoint for the trusted current project." + _COMMON, {"operation": {"type": "string", "enum": ["prepare_current_project", "refresh_current_project_observed_state", "reconcile_current_project", "request_project_lifecycle_transition"]}, "action": {"type": "string", "enum": ["archive", "restore", "retire", "activate"]}, "rationale": {"type": "string", "maxLength": 1000}, "include_commands": {"type": "boolean"}, "include_constraints": {"type": "boolean"}, "include_plan_reference": {"type": "boolean"}, "max_warnings": {"type": "integer", "minimum": 1, "maximum": 50}}),
    "aota_project_docs_update": _schema("aota_project_docs_update", "Update an allowlisted current project document." + _COMMON, {"document_kind": {"type": "string", "enum": ["readme", "changelog", "roadmap"]}, "operation": {"type": "string", "enum": ["replace", "append"]}, "content": {"type": "string", "maxLength": 32768}, "document_ref": {"type": "string", "enum": ["current_readme", "current_changelog", "current_roadmap"]}, "rationale": {"type": "string", "maxLength": 1000}}, ["document_kind", "operation", "content"]),
    "aota_project_artifact_link": _schema("aota_project_artifact_link", "Link a semantic artifact to a semantic current subject." + _COMMON, {"artifact_ref": {"type": "string", "enum": sorted(ARTIFACT_REFS)}, "target_ref": {"type": "string", "enum": ["current_work_item", "current_project", "current_stewardship_subject"]}, "relation": {"type": "string", "enum": ["evidence_for", "result", "review", "steward_result", "architecture_review", "artifact"]}, "rationale": {"type": "string", "maxLength": 1000}}, ["artifact_ref", "target_ref", "relation"]),
    "aota_project_initialize_core": _schema("aota_project_initialize_core", "Plan initialization of a new project from semantic intent; paths and IDs are control-plane generated." + _COMMON, {"project_name": {"type": "string", "maxLength": 200}, "summary": {"type": "string", "maxLength": 4000}, "project_kind": {"type": "string", "maxLength": 96}, "canonical_parent": {"type": "string", "enum": ["workspace_default", "user_selected_parent"]}, "capabilities": {"type": "array", "items": {"type": "string", "maxLength": 96}, "maxItems": 32}}, ["project_name", "summary"]),
    "aota_project_steward_report": _schema("aota_project_steward_report", "Reconcile the trusted current project and separate facts, findings, and proposed mutations." + _COMMON, {"operation": {"type": "string", "enum": ["intake", "context_prepare", "reconcile_current_project", "close"]}, "rationale": {"type": "string", "maxLength": 1000}}, ["operation"]),
    "aota_codegraph_status": _schema("aota_codegraph_status", "Read current project CodeGraph status." + _COMMON),
    "aota_codegraph_query": _schema("aota_codegraph_query", "Query current project CodeGraph with a bounded semantic search." + _COMMON, {"search": {"type": "string", "maxLength": 256}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}, "kind": {"type": "string"}}, ["search"]),
    "aota_codegraph_explore": _schema("aota_codegraph_explore", "Explore current project CodeGraph with a bounded semantic query." + _COMMON, {"query": {"type": "string", "maxLength": 256}, "max_files": {"type": "integer", "minimum": 1, "maximum": 10}}, ["query"]),
    "aota_codegraph_rebuild": _schema("aota_codegraph_rebuild", "Request CodeGraph rebuild for the trusted current project." + _COMMON),
    "aota_project_file_read": _schema("aota_project_file_read", "Read one allowlisted semantic project file." + _COMMON, {"file_ref": {"type": "string", "enum": sorted(FILE_REFS)}, "max_bytes": {"type": "integer", "minimum": 1, "maximum": MAX_READ_BYTES}, "start_line": {"type": "integer", "minimum": 1}, "end_line": {"type": "integer", "minimum": 1}}, ["file_ref"]),
    "aota_project_file_write": _schema("aota_project_file_write", "Write one allowlisted semantic project document." + _COMMON, {"file_ref": {"type": "string", "enum": ["current_readme", "current_changelog", "current_roadmap"]}, "content": {"type": "string", "maxLength": MAX_READ_BYTES}}, ["file_ref", "content"]),
    "aota_project_file_patch": _schema("aota_project_file_patch", "Patch one allowlisted semantic project document." + _COMMON, {"file_ref": {"type": "string", "enum": ["current_readme", "current_changelog", "current_roadmap"]}, "find": {"type": "string", "maxLength": 10000}, "replace": {"type": "string", "maxLength": 10000}}, ["file_ref", "find", "replace"]),
    "aota_project_command_run": _schema("aota_project_command_run", "Run one fixed validation command for the trusted current project." + _COMMON, {"command_id": {"type": "string", "enum": ["python_compileall", "python_module_compile", "node_check", "ruff_check", "mypy_check", "pytest_isolated", "project_script"]}}, ["command_id"]),
    "aota_workspace_selection_record": _schema("aota_workspace_selection_record", "Record a bounded human project selection using the trusted workspace." + _COMMON, {"project_selector": {"type": "string", "maxLength": 160}, "project_ref": {"type": "string", "enum": ["current_project"]}, "source_type": {"type": "string", "enum": ["user_supplied", "steward_recommendation", "prior_decision"]}, "relationship": {"type": "string", "enum": ["existing", "adjacent", "new_project", "new_workspace"]}, "rationale": {"type": "string", "maxLength": 1000}}, ["source_type", "relationship"]),
}


__all__ = ["PHASE3_SCHEMAS", "phase3_handler", "resolve_current_project", "resolve_current_artifact", "Phase3ResolutionError"]
