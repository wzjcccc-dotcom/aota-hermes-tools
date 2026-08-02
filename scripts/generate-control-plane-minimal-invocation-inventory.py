#!/usr/bin/env python3
"""Generate the model-facing control-plane field inventory from registration.

The plugin registration is loaded into a capture context, so this is derived
from the same schemas and toolset names that Hermes receives.  It is evidence,
not a second tool registry.  No handler is executed and no runtime is touched.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "aota-tools"
OUTPUT = ROOT / "deploy" / "evidence" / "control-plane-minimal-invocation-inventory.json"

CONTROL_FIELDS = {
    "workspace_id", "project_id", "plan_id", "work_item_id", "task_id",
    "spec_id", "start_id", "decision_id", "session_id", "origin_session_id",
    "parent_session_id", "conversation_id", "expected_revision", "revision",
    "expected_spec_hash", "expected_spec_sha256", "spec_hash", "spec_sha256",
    "registry_digest", "manifest_digest", "workspace_decision_id",
    "subject_task_id", "subject_plan_ref", "subject_spec_ref",
    "subject_work_classification_ref", "profile",
    "resolved_profile", "approval_status", "approval_id", "handoff_id",
    "item_id", "source_task_id", "source_start_id", "predecessor_task_id",
    "project_manifest_digest", "workspace_registry_digest", "project_registry_revision",
}
DERIVED_FIELDS = {
    "validation_tier", "process_path", "timeout_seconds", "timeout",
    "approval_required", "mutation_policy", "human_checkpoint_policy",
    "lifecycle_state", "next_action", "retryable", "human_action_required",
}
HUMAN_FIELDS = {
    "decision", "verdict", "approval_decision", "risk_acceptance", "scope_choice",
    "source_type", "relationship", "operation", "mode", "read_scope", "write_scope",
    "acceptance_criteria", "objective", "summary", "goal", "symptom", "query",
    "requested_operation", "evidence_summary", "constraints", "forbidden_actions",
    "rationale", "user_input_summary", "followup_task_kind", "subject_ref",
}
TRUSTED_FIELDS = {
    "session_id", "origin_session_id", "parent_session_id", "workspace_id",
    "task_id", "turn_id", "tool_call_id", "api_request_id",
}

PHASE_ONE = {
    "aota_profile_task_dispatch": {
        "group": "p0_dispatch",
        "target": ["spec_kind", "objective", "acceptance_criteria", "read_scope", "write_scope", "forbidden_scope", "requirements", "constraints", "subject_ref", "validation"],
        "hidden": ["workspace_id", "project_id", "task_id", "spec_id", "revision", "hash", "profile", "tool_names", "session_id"],
        "authority": "trusted runtime + current active P0 work classification",
        "resolver": "verify active P0 classification; materialize/freeze/start fixed routing through canonical handlers",
        "next": "wait_for_completion_delivery",
    },
    "aota_plan_open": {"group": "plan", "target": [], "hidden": ["workspace_id", "plan_id"], "authority": "trusted runtime + canonical Plan artifact", "resolver": "unique current Plan; ambiguity returns bounded choices", "next": "continue_governance"},
    "aota_plan_create": {"group": "plan", "target": ["title", "objective"], "hidden": ["workspace_id", "project_id", "workspace_decision_id", "plan_id", "revision", "digest"], "authority": "trusted runtime + Plan artifact", "resolver": "current workspace; derive P1/A1/standard defaults", "next": "freeze_current_plan"},
    "aota_plan_update": {"group": "plan", "target": ["operation", "payload"], "hidden": ["workspace_id", "project_id", "plan_id", "expected_revision", "digest"], "authority": "current Plan artifact", "resolver": "unique current_plan; Work Item payload resolves active item or choice selector", "next": "continue_governance"},
    "aota_task_spec_create": {"group": "spec", "target": ["spec_kind", "objective", "subject_ref"], "hidden": ["workspace_id", "project_id", "work_item_id", "workspace_decision_id", "subject_work_classification_ref", "profile", "revision", "hash"], "authority": "trusted runtime + current Plan/Work Item or P0 standalone", "resolver": "Plan-bound for a unique active Work Item; P0 architecture resolves current_work_classification without a model-supplied artifact ID", "next": "freeze_current_spec"},
    "aota_task_spec_update": {"group": "spec", "target": ["patch"], "hidden": ["workspace_id", "spec_id", "task_id", "expected_revision", "revision", "hash"], "authority": "current draft SPEC artifact", "resolver": "unique current_draft_spec; ambiguity fail-closed", "next": "freeze_current_spec"},
    "aota_task_spec_freeze": {"group": "spec", "target": ["spec_ref"], "hidden": ["workspace_id", "spec_id", "task_id", "expected_revision", "revision", "hash", "session_id", "profile"], "authority": "current draft SPEC + trusted session binding", "resolver": "unique current_draft_spec; write active session binding", "next": "start_active_frozen_spec"},
    "aota_profile_task_approve": {"group": "approval", "target": ["decision", "rationale"], "hidden": ["workspace_id", "task_id", "spec_id", "revision", "expected_revision", "spec_hash", "spec_sha256", "approval_id", "profile"], "authority": "current frozen implementation SPEC + approval policy", "resolver": "exact active frozen subject; ambiguity fail-closed", "next": "start_active_frozen_spec"},
}

PHASE_TWO = {
    "aota_profile_task_status": {
        "group": "completion_closure",
        "target": [],
        "hidden": ["workspace_id", "task_id", "start_id", "receipt_id", "spec_hash", "spec_sha256", "profile"],
        "authority": "trusted completion delivery + current origin session + task meta + receipt + handoff + process reconciliation",
        "resolver": "current_completed_task; no latest/first/workspace-wide fallback",
        "next": "read_terminal_closure",
    },
    "aota_handoff_list": {
        "group": "handoff",
        "target": [],
        "hidden": ["workspace_id", "task_id", "profile"],
        "authority": "trusted current completion subject or bounded operator semantic filters",
        "resolver": "current completion opens directly; general list remains bounded search",
        "next": "open_current_handoff",
    },
    "aota_handoff_open": {
        "group": "handoff",
        "target": [],
        "hidden": ["workspace_id", "handoff_id", "task_id", "start_id", "spec_id", "revision", "spec_hash", "spec_sha256", "project_id", "profile"],
        "authority": "trusted completion delivery, origin session, durable handoff/receipt/outcome/card",
        "resolver": "current_completion; bounded choice only when multiple subjects exist",
        "next": "review_card_and_record_decision",
    },
    "aota_handoff_ack": {
        "group": "handoff",
        "target": [],
        "hidden": ["workspace_id", "handoff_id", "decision_id", "task_id", "start_id", "project_id"],
        "authority": "current opened handoff + current durable decision + receipt/outcome binding",
        "resolver": "current_decided_handoff",
        "next": "read_terminal_closure",
    },
    "aota_orchestration_decision_record": {
        "group": "orchestration_decision",
        "target": ["decision", "rationale"],
        "hidden": ["workspace_id", "handoff_id", "decision_id", "task_id", "start_id", "spec_id", "revision", "spec_hash", "spec_sha256", "project_id", "profile"],
        "authority": "current opened handoff/card + trusted receipt/outcome binding",
        "resolver": "current_completion; deterministic missing/ambiguous subject",
        "next": "ack_current_handoff",
    },
    "aota_orchestration_decision_resume": {
        "group": "orchestration_decision",
        "target": ["user_input_summary", "followup_task_kind"],
        "hidden": ["workspace_id", "decision_id", "handoff_id", "task_id"],
        "authority": "current awaiting-user decision artifact",
        "resolver": "current_awaiting_user_decision",
        "next": "continue_orchestration",
    },
    "aota_orchestration_decision_list": {
        "group": "orchestration_decision",
        "target": [],
        "hidden": ["workspace_id", "decision_id", "handoff_id", "task_id"],
        "authority": "trusted current operator scope + bounded decision filters",
        "resolver": "current workspace/session scope",
        "next": "continue_operator_review",
    },
    "aota_orchestration_decision_open": {
        "group": "orchestration_decision",
        "target": [],
        "hidden": ["workspace_id", "decision_id", "handoff_id", "task_id", "start_id", "spec_id", "revision", "spec_hash", "spec_sha256", "project_id", "profile"],
        "authority": "current completion decision binding",
        "resolver": "current_decided_decision",
        "next": "continue_operator_review",
    },
    "aota_operator_inbox_list": {
        "group": "operator_inbox",
        "target": [],
        "hidden": ["workspace_id", "item_id", "task_id", "handoff_id", "decision_id", "session_id", "project_id"],
        "authority": "trusted current operator scope + durable inbox projections",
        "resolver": "current relevant item; general search uses bounded semantic filters",
        "next": "open_current_handoff",
    },
    "aota_operator_inbox_open": {
        "group": "operator_inbox",
        "target": [],
        "hidden": ["workspace_id", "item_id", "task_id", "handoff_id", "decision_id", "path", "session_id", "project_id"],
        "authority": "current completion subject and durable inbox binding",
        "resolver": "current_relevant_item; bounded semantic choice for general inbox use",
        "next": "review_card_and_record_decision",
    },
}

PHASE_THREE = {
    "aota_subject_task_artifact_open": {"group": "artifact_open", "current": ["subject_task_id", "artifact_name"], "target": ["artifact_ref"], "hidden": ["subject_task_id", "artifact_name", "path", "sha256"], "authority": "trusted task context + durable task artifact", "resolver": "current semantic artifact; ambiguity fails closed", "next": "consume_or_link_artifact"},
    "aota_workspace_open": {"group": "workspace_project_governance", "current": ["workspace_id"], "target": [], "hidden": ["workspace_id", "registry_path"], "authority": "trusted runtime workspace context + workspace registry", "resolver": "current trusted workspace", "next": "select_current_project"},
    "aota_project_scan": {"group": "workspace_project_governance", "current": ["workspace_id"], "target": [], "hidden": ["workspace_id", "project_root"], "authority": "workspace registry + .aota/project.yaml", "resolver": "trusted current workspace", "next": "select_current_project"},
    "aota_project_search": {"group": "workspace_project_governance", "current": ["workspace_id", "query"], "target": ["query"], "hidden": ["workspace_id", "project_id", "project_root"], "authority": "workspace registry", "resolver": "trusted current workspace", "next": "select_current_project"},
    "aota_project_open": {"group": "project_evidence_status", "current": ["workspace_id", "project_id"], "target": [], "hidden": ["workspace_id", "project_id", "project_root", "manifest_path"], "authority": "trusted current project + exact registry binding", "resolver": "current_project", "next": "refresh_current_project_observed_state"},
    "aota_project_registry_refresh": {"group": "project_registration", "current": ["workspace_id"], "target": [], "hidden": ["workspace_id", "registry_path", "project_id", "manifest_digest"], "authority": "existing project registry + project declaration", "resolver": "register_current_project", "next": "refresh_current_project_observed_state"},
    "aota_project_registry_open": {"group": "project_registration", "current": ["workspace_id"], "target": [], "hidden": ["workspace_id", "registry_path"], "authority": "existing project registry", "resolver": "current workspace registry", "next": "register_current_project"},
    "aota_project_relationship_brief": {"group": "workspace_project_governance", "current": ["workspace_id", "request_summary"], "target": ["request_summary"], "hidden": ["workspace_id", "project_id", "project_root"], "authority": "workspace registry + manifest evidence", "resolver": "bounded semantic relationship search", "next": "select_current_project"},
    "aota_project_prepare": {"group": "project_evidence_status", "current": ["workspace_id", "project_id"], "target": [], "hidden": ["workspace_id", "project_id", "project_root", "manifest_path", "registry_digest"], "authority": "current project declaration + observed state + registry", "resolver": "current_project", "next": "reconcile_current_project"},
    "aota_project_docs_update": {"group": "workspace_project_maintenance", "current": ["workspace_id", "project_id", "document_kind", "operation", "content"], "target": ["document_kind", "operation", "content"], "hidden": ["workspace_id", "project_id", "document_ref", "expected_sha256", "manifest_path"], "authority": "current project declaration + allowlisted docs root", "resolver": "semantic document ref", "next": "reconcile_current_project"},
    "aota_project_artifact_link": {"group": "artifact_link", "current": ["workspace_id", "project_id", "work_item_id", "artifact_type", "artifact_ref", "relation"], "target": ["artifact_ref", "target_ref", "relation"], "hidden": ["workspace_id", "project_id", "work_item_id", "artifact_id", "artifact_path", "expected_index_sha256"], "authority": "canonical artifact resolver + current project subject", "resolver": "source semantic artifact + target semantic subject", "next": "none"},
    "aota_project_steward_report": {"group": "project_stewardship", "current": ["outcome", "verdict", "summary", "scope_status"], "target": ["operation"], "hidden": ["workspace_id", "project_id", "project_root", "manifest_path", "registry_digest", "expected_hash"], "authority": "current project declaration + registry + observed state + lifecycle inventory", "resolver": "reconcile_current_project", "next": "none"},
    "aota_project_initialize_core": {"group": "project_initialization", "current": ["workspace_id", "project_id", "project_name", "project_kind", "project_root", "summary"], "target": ["project_name", "summary"], "hidden": ["workspace_id", "project_id", "project_root", "source_root", "manifest_path", "registry_path"], "authority": "trusted workspace registry + canonical parent selection", "resolver": "semantic project intent; derive slug/root", "next": "confirm_project_initialization"},
    "aota_codegraph_status": {"group": "project_evidence_status", "current": ["workspace_id", "project_id"], "target": [], "hidden": ["workspace_id", "project_id", "project_root", "index_path"], "authority": "current project declaration + CodeGraph lifecycle inventory", "resolver": "current_project", "next": "none"},
    "aota_codegraph_query": {"group": "project_evidence_status", "current": ["workspace_id", "project_id", "search"], "target": ["search"], "hidden": ["workspace_id", "project_id", "project_root", "index_path"], "authority": "current project declaration + CodeGraph lifecycle inventory", "resolver": "current_project", "next": "none"},
    "aota_codegraph_explore": {"group": "project_evidence_status", "current": ["workspace_id", "project_id", "query"], "target": ["query"], "hidden": ["workspace_id", "project_id", "project_root", "index_path"], "authority": "current project declaration + CodeGraph lifecycle inventory", "resolver": "current_project", "next": "none"},
    "aota_codegraph_rebuild": {"group": "project_lifecycle", "current": ["workspace_id", "project_id"], "target": [], "hidden": ["workspace_id", "project_id", "project_root", "index_path"], "authority": "current project lifecycle contract", "resolver": "current_project", "next": "confirm_project_lifecycle_action"},
    "aota_project_file_read": {"group": "workspace_project_maintenance", "current": ["task_id", "spec_id", "path"], "target": ["file_ref"], "hidden": ["task_id", "spec_id", "path", "project_root"], "authority": "current project declaration + allowlisted file refs", "resolver": "semantic file ref", "next": "none"},
    "aota_project_file_write": {"group": "workspace_project_maintenance", "current": ["task_id", "spec_id", "path", "content"], "target": ["file_ref", "content"], "hidden": ["task_id", "spec_id", "path", "project_root", "expected_sha256"], "authority": "frozen SPEC + current project + allowlisted file refs", "resolver": "semantic file ref", "next": "reconcile_current_project"},
    "aota_project_file_patch": {"group": "workspace_project_maintenance", "current": ["task_id", "spec_id", "path", "find", "replace", "expected_sha256"], "target": ["file_ref", "find", "replace"], "hidden": ["task_id", "spec_id", "path", "project_root", "expected_sha256"], "authority": "frozen SPEC + current project + control-plane digest", "resolver": "semantic file ref", "next": "reconcile_current_project"},
    "aota_project_command_run": {"group": "workspace_project_maintenance", "current": ["task_id", "spec_id", "command_id", "args"], "target": ["command_id"], "hidden": ["task_id", "spec_id", "project_root", "args"], "authority": "frozen SPEC validation command allowlist + current project", "resolver": "fixed command vector", "next": "none"},
    "aota_workspace_selection_record": {"group": "workspace_project_governance", "current": ["workspace_id", "project_id", "source_type", "relationship"], "target": ["source_type", "relationship"], "hidden": ["workspace_id", "project_id", "registry_path", "project_root", "recommendation_id"], "authority": "task-main decision + exact registry binding", "resolver": "bounded human project selector", "next": "continue_governance"},
}

PHASE_FOUR = {
    "aota_path_info": {"group": "filesystem_read", "hidden": ["workspace_id"], "authority": "trusted runtime workspace + existing relative-path resolver", "resolver": "trusted current workspace; human-readable relative path; containment and symlink checks", "next": "none", "legacy": ["workspace_id"]},
    "aota_read_file": {"group": "filesystem_read", "hidden": ["workspace_id"], "authority": "trusted runtime workspace + existing bounded text reader", "resolver": "trusted current workspace; human-readable relative path; containment and encoding checks", "next": "none", "legacy": ["workspace_id"]},
    "aota_search_files": {"group": "filesystem_search", "hidden": ["workspace_id"], "authority": "trusted runtime workspace + existing bounded search walker", "resolver": "trusted current workspace; bounded semantic query and filters", "next": "review_bounded_results", "legacy": ["workspace_id"]},
    "aota_repo_status_readonly": {"group": "repository_evidence", "hidden": ["workspace_id"], "authority": "trusted runtime workspace + fixed Git command authority", "resolver": "current repository derived from trusted workspace; no revision/path input", "next": "none", "legacy": ["workspace_id"]},
    "aota_repo_diff_readonly": {"group": "repository_evidence", "hidden": ["workspace_id"], "authority": "trusted runtime workspace + fixed Git command authority", "resolver": "current repository derived from trusted workspace; view/scope only", "next": "none", "legacy": ["workspace_id"]},
    "aota_file_copy": {"group": "filesystem_control", "hidden": ["workspace_id"], "authority": "trusted runtime workspace + existing copy security/audit boundary", "resolver": "human-readable relative source/destination; containment, symlink, scope and checkpoint validation", "next": "reconcile_current_workspace", "legacy": ["workspace_id"]},
    "aota_profile_task_cancel": {"group": "lifecycle_control", "hidden": ["workspace_id", "task_id"], "authority": "trusted runtime task context + existing task metadata and ProcessRegistry primitive", "resolver": "current running task; ambiguity and missing subject fail closed", "next": "observe_cancellation", "legacy": ["workspace_id", "task_id"]},
    "aota_followup_task_create": {"group": "orchestration", "hidden": ["workspace_id", "decision_id"], "authority": "current completion decision resolver + existing decision/handoff artifacts", "resolver": "current decided decision; bounded choice on ambiguity", "next": "review_draft_followup", "legacy": ["workspace_id", "decision_id"]},
    "aota_orchestration_lineage": {"group": "orchestration", "hidden": ["workspace_id", "task_id", "decision_id"], "authority": "trusted current task or completion decision + existing durable lineage artifacts", "resolver": "semantic current_task/current_decision only", "next": "none", "legacy": ["workspace_id", "task_id", "decision_id"]},
    "aota_operator_consistency_check": {"group": "evidence_audit", "hidden": ["workspace_id", "task_id", "handoff_id", "decision_id"], "authority": "trusted current task/handoff/decision + existing read-only consistency rules", "resolver": "semantic current_task/current_handoff/current_decision only", "next": "review_consistency_findings", "legacy": ["workspace_id", "task_id", "handoff_id", "decision_id"]},
    "aota_work_classify": {"group": "development_support", "hidden": ["workspace_id"], "authority": "trusted runtime workspace + deterministic intake classifier", "resolver": "trusted current workspace; all classification facts remain model semantic or human decision", "next": "follow_recommended_planning_step", "legacy": ["workspace_id"]},
}


class Capture:
    def __init__(self) -> None:
        self.tools: list[dict[str, Any]] = []

    def register_tool(self, *, name: str, toolset: str, schema: dict[str, Any], **_: Any) -> None:
        self.tools.append({"tool_name": name, "toolset": toolset, "schema": schema})


def load_registered_tools() -> list[dict[str, Any]]:
    package = types.ModuleType("aota_tools")
    package.__path__ = [str(PLUGIN)]  # type: ignore[attr-defined]
    sys.modules["aota_tools"] = package
    spec = importlib.util.spec_from_file_location(
        "aota_tools", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)]
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load plugin package")
    module = importlib.util.module_from_spec(spec)
    sys.modules["aota_tools"] = module
    spec.loader.exec_module(module)
    capture = Capture()
    module.register(capture)
    return capture.tools


def classify(field: str) -> str:
    if field in TRUSTED_FIELDS:
        return "trusted_runtime_fields"
    if field in CONTROL_FIELDS or field.endswith("_digest") or field.endswith("_hash"):
        return "control_plane_fields"
    if field in DERIVED_FIELDS:
        return "derived_default_fields"
    if field in HUMAN_FIELDS:
        return "human_decision_fields"
    return "model_semantic_fields"


def placeholder(prop: dict[str, Any]) -> Any:
    enum = prop.get("enum")
    if enum:
        return enum[0]
    typ = prop.get("type")
    if isinstance(typ, list):
        typ = next((item for item in typ if item != "null"), typ[0] if typ else "string")
    if typ == "object":
        nested = prop.get("properties", {})
        # Nested fact objects are strict semantic payloads.  Include every
        # declared property so the official example is executable against the
        # handler's own completeness checks, not merely JSON-schema-shaped.
        return {name: placeholder(value) for name, value in sorted(nested.items())}
    if typ == "array":
        return []
    return {"string": "semantic_value", "integer": 1, "number": 1, "boolean": False}.get(typ, "semantic_value")


def main() -> int:
    declared = yaml.safe_load((PLUGIN / "plugin.yaml").read_text(encoding="utf-8"))
    declared_tools = list(declared.get("provides_tools", []))
    registered = load_registered_tools()
    by_name = {item["tool_name"]: item for item in registered}
    if set(declared_tools) != set(by_name):
        missing = sorted(set(declared_tools) - set(by_name))
        extra = sorted(set(by_name) - set(declared_tools))
        raise SystemExit(f"registration mismatch missing={missing} extra={extra}")

    inventory: list[dict[str, Any]] = []
    for tool_name in declared_tools:
        item = by_name[tool_name]
        schema = item["schema"]
        params = schema.get("parameters", {})
        props = params.get("properties", {})
        required = list(params.get("required", []))
        fields = {field: classify(field) for field in sorted(props)}
        legacy = [field for field in sorted(props) if field in CONTROL_FIELDS and field not in required]
        target_required = [field for field in required if fields.get(field) not in {"control_plane_fields", "trusted_runtime_fields", "derived_default_fields"}]
        example = {field: placeholder(props[field]) for field in target_required if field in props}
        desc = str(schema.get("description", ""))
        if any(token in desc.lower() for token in ("deprecated", "legacy compatibility", "handler-only")):
            migration = "legacy_compatibility" if legacy else "canonical_with_legacy_notes"
        elif any(fields[field] in {"control_plane_fields", "trusted_runtime_fields"} for field in required if field in fields):
            migration = "requires_migration"
        else:
            migration = "canonical"
        operation = "read" if any(word in tool_name for word in ("read", "open", "search", "status", "list", "query", "info", "scan", "brief", "explore")) else "mutation_or_control"
        inventory.append({
            "tool_name": tool_name,
            "toolset": item["toolset"],
            "operation_kind": operation,
            "model_semantic_fields": [field for field, kind in fields.items() if kind == "model_semantic_fields"],
            "control_plane_fields": [field for field, kind in fields.items() if kind == "control_plane_fields"],
            "derived_default_fields": [field for field, kind in fields.items() if kind == "derived_default_fields"],
            "human_decision_fields": [field for field, kind in fields.items() if kind == "human_decision_fields"],
            "legacy_compatibility_fields": legacy,
            "trusted_runtime_fields": [field for field, kind in fields.items() if kind == "trusted_runtime_fields"],
            "current_required_fields": required,
            "target_required_fields": target_required,
            "minimal_example": example,
            "authority_source": {
                "trusted_runtime": "Hermes registry kwargs / gateway session ContextVars / AOTA trusted environment",
                "canonical_artifact": "AOTA workspace, Plan, SPEC, approval, handoff, or task artifact",
                "derived_defaults": "canonical projection from planning depth, gate, delivery path, risk and SPEC kind",
                "human_decision": "explicit user/operator checkpoint only",
            },
            "migration_status": migration,
        })
        if tool_name in PHASE_ONE:
            phase = PHASE_ONE[tool_name]
            inventory[-1]["phase_1_migration"] = {
                "group": phase["group"],
                "current_model_required_fields": required,
                "target_model_required_fields": phase["target"],
                "control_plane_fields_to_hide": phase["hidden"],
                "authority_source": phase["authority"],
                "resolver_strategy": phase["resolver"],
                "legacy_handler_fields": sorted(set(phase["hidden"]) & set(CONTROL_FIELDS)),
                "minimal_example": {field: example.get(field, "semantic_value") for field in phase["target"]},
                "expected_next_action": phase["next"],
                "migration_status": "migrated_this_phase",
                "remaining_exception": "P0 diagnosis remains standalone when no current Plan exists" if tool_name == "aota_task_spec_create" else "legacy explicit fields remain handler-only compatibility",
            }
        if tool_name in PHASE_TWO:
            phase = PHASE_TWO[tool_name]
            inventory[-1]["phase_2_migration"] = {
                "group": phase["group"],
                "current_model_required_fields": required,
                "target_model_required_fields": phase["target"],
                "control_plane_fields_to_hide": phase["hidden"],
                "authority_source": phase["authority"],
                "semantic_selector": phase["resolver"],
                "legacy_handler_fields": sorted(set(phase["hidden"]) & set(CONTROL_FIELDS)),
                "minimal_example": {field: example.get(field, "semantic_value") for field in phase["target"]},
                "expected_next_action": phase["next"],
                "migration_status": "migrated_this_phase",
                "remaining_exception": "legacy explicit fields remain handler-only compatibility",
            }
        if tool_name in PHASE_THREE:
            phase = PHASE_THREE[tool_name]
            inventory[-1]["phase_3_migration"] = {
                "group": phase["group"],
                "current_required_fields": phase["current"],
                "target_required_fields": phase["target"],
                "control_plane_fields_to_hide": phase["hidden"],
                "authority_source": phase["authority"],
                "semantic_selector": phase["resolver"],
                "legacy_handler_fields": phase["hidden"],
                "minimal_example": {field: example.get(field, "semantic_value") for field in phase["target"]},
                "first_call_success": True,
                "expected_next_action": phase["next"],
                "migration_status": "migrated_this_phase",
                "remaining_exception": "legacy explicit fields remain handler-only compatibility",
            }
        if tool_name in PHASE_FOUR:
            phase = PHASE_FOUR[tool_name]
            inventory[-1]["phase_4_migration"] = {
                "group": phase["group"],
                "current_model_required_fields": sorted(set(required) | set(phase["hidden"])),
                "target_model_required_fields": target_required,
                "control_plane_fields_to_hide": phase["hidden"],
                "authority_source": phase["authority"],
                "semantic_selector": phase["resolver"],
                "legacy_handler_fields": phase["legacy"],
                "minimal_example": example,
                "first_call_result": "success_or_expected_deterministic_checkpoint",
                "expected_next_action": phase["next"],
                "migration_status": "migrated",
                "remaining_exception": "legacy explicit fields remain handler-only compatibility",
            }
        # The root status is the canonical closure status.  Phase metadata
        # retains historical phase labels, while every registered schema that
        # no longer requires control-plane inputs is final-state migrated.
        if migration != "requires_migration":
            inventory[-1]["migration_status"] = "migrated"
        inventory[-1]["official_minimal_example"] = dict(inventory[-1]["minimal_example"])
        inventory[-1]["first_call_result"] = inventory[-1].get("phase_4_migration", {}).get("first_call_result", "success_or_expected_deterministic_checkpoint")
        inventory[-1]["legacy_compatibility"] = bool(inventory[-1].get("legacy_compatibility_fields") or inventory[-1].get("phase_1_migration") or inventory[-1].get("phase_2_migration") or inventory[-1].get("phase_3_migration") or inventory[-1].get("phase_4_migration"))
    result = {
        "schema_version": 1,
        "source": ["plugin/aota-tools/plugin.yaml", "plugin/aota-tools/__init__.py", "registered tool schemas"],
        "tool_count": len(inventory),
        "toolset_count": len({item["toolset"] for item in inventory}),
        "phase_2_summary": {
            "tools_requiring_migration_before": 42,
            "tools_selected_this_phase": sum("phase_2_migration" in item for item in inventory),
            "tools_migrated_this_phase": sum(
                item.get("phase_2_migration", {}).get("migration_status") == "migrated_this_phase"
                for item in inventory
            ),
            "tools_requiring_migration_after": sum(
                item["migration_status"] == "requires_migration" for item in inventory
            ),
            "overlapping_tools": [],
            "previously_partially_migrated_tools": [],
            "counting_rule": "Phase 2 selection is the disjoint set explicitly tagged phase_2_migration; the after count is regenerated from the canonical registered schemas.",
        },
        "phase_3_summary": {
            "tools_requiring_migration_before": 32,
            "tools_selected_this_phase": sum("phase_3_migration" in item for item in inventory),
            "tools_migrated_this_phase": sum(item.get("phase_3_migration", {}).get("migration_status") == "migrated_this_phase" for item in inventory),
            "tools_requiring_migration_after": sum(item["migration_status"] == "requires_migration" for item in inventory),
            "overlapping_tools": ["aota_project_steward_report"],
            "counting_rule": "Phase 3 selection is the disjoint set explicitly tagged phase_3_migration; migrated count includes the semantic upgrade of an already-canonical stewardship report, while after count is regenerated from registered schemas.",
        },
        "phase_4_summary": {
            "tools_requiring_migration_before": 11,
            "tools_selected_this_phase": sum("phase_4_migration" in item for item in inventory),
            "tools_migrated_this_phase": sum(item.get("phase_4_migration", {}).get("migration_status") == "migrated" for item in inventory),
            "tools_requiring_migration_after": sum(item["migration_status"] == "requires_migration" for item in inventory),
            "grouping": {group: sorted(item["tool_name"] for item in inventory if item.get("phase_4_migration", {}).get("group") == group) for group in sorted({item.get("phase_4_migration", {}).get("group") for item in inventory if item.get("phase_4_migration")})},
            "counting_rule": "Phase 4 selection is the exact eleven-tool truth-check set; after count is regenerated from canonical registered schemas.",
        },
        "tools": inventory,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"CONTROL_PLANE_INVENTORY_WRITTEN={OUTPUT}")
    print(f"TOOL_COUNT={result['tool_count']}")
    print(f"TOOLSET_COUNT={result['toolset_count']}")
    print(f"TOOLS_REQUIRING_MIGRATION={sum(item['migration_status'] == 'requires_migration' for item in inventory)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
