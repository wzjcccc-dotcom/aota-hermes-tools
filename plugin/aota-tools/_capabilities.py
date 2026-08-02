"""Profile and task-kind capability registry for AOTA Tool Layer.

Defines PROFILE_CAPABILITIES and TASK_KIND_CAPABILITIES dicts
plus is_capable() for checking what a profile/task-kind can do.
Orchestration/control-plane tools (profile_task_start, approve,
task_spec_create/update, handoff_*, orchestration_*, followup_*,
operator_*) are EXPLICITLY OUT OF SCOPE — they are operator tools
used by task-main, not worker mutation tools.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Profile capabilities: maps profile name -> dict of capabilities
# ---------------------------------------------------------------------------

PROFILE_CAPABILITIES: dict[str, dict[str, Any]] = {
    "coder": {
        "source_read": True,
        "source_write": True,
        "terminal": False,
        "bounded_project_command": True,
        "project_metadata_write": False,
        "codegraph_read": True,
        "codegraph_rebuild": False,
        "mutate_files": True,
        "submit_coder_report": True,
        "submit_worker_outcome": True,
        "allowed_tools": [
            "aota_project_file_read",
            "aota_project_file_write",
            "aota_project_file_patch",
            "aota_project_command_run",
            "aota_codegraph_status",
            "aota_codegraph_query",
            "aota_codegraph_explore",
            "aota_coder_report_submit",
            "aota_worker_outcome_submit",
        ],
    },
    "debugger": {
        "source_read": True, "source_write": False, "terminal": False,
        "project_metadata_write": False, "codegraph_read": True, "codegraph_rebuild": False,
        "mutate_files": False,
        "submit_debugger_report": True,
        "submit_worker_outcome": True,
        "allowed_tools": [
            "aota_debugger_report_submit",
            "aota_worker_outcome_submit",
            "aota_codegraph_status", "aota_codegraph_query", "aota_codegraph_explore",
        ],
    },
    "reviewer": {
        "source_read": True, "source_write": False, "terminal": False,
        "project_metadata_write": False, "codegraph_read": True, "codegraph_rebuild": False,
        "mutate_files": False,
        "submit_reviewer_report": True,
        "submit_worker_outcome": True,
        "allowed_tools": [
            "aota_reviewer_report_submit",
            "aota_worker_outcome_submit",
            "aota_codegraph_status", "aota_codegraph_query", "aota_codegraph_explore",
        ],
    },
    "architect": {
        "source_read": True, "source_write": False, "terminal": False,
        "project_metadata_write": False, "codegraph_read": True, "codegraph_rebuild": False,
        "mutate_files": False,
        "submit_architect_report": True,
        "submit_worker_outcome": True,
        "allowed_tools": [
            "aota_architect_report_submit",
            "aota_worker_outcome_submit",
            "aota_codegraph_status", "aota_codegraph_query", "aota_codegraph_explore",
        ],
    },
    "task-main": {
        "source_read": True, "source_write": False, "terminal": False,
        "project_metadata_write": True, "codegraph_read": True, "codegraph_rebuild": True,
        "mutate_files": False,
        "orchestrate": True,
        "operator": True,
        "allowed_tools": [
            "aota_task_spec_create",
            "aota_task_spec_update",
            "aota_profile_task_dispatch",
            "aota_profile_task_start",
            "aota_profile_task_status",
            "aota_profile_task_cancel",
            "aota_profile_task_approve",
            "aota_handoff_list",
            "aota_handoff_open",
            "aota_handoff_ack",
            "aota_orchestration_decision_record",
            "aota_followup_task_create",
            "aota_orchestration_decision_resume",
            "aota_orchestration_decision_list",
            "aota_orchestration_decision_open",
            "aota_orchestration_lineage",
            "aota_operator_inbox_list",
            "aota_operator_inbox_open",
            "aota_operator_consistency_check",
            "aota_codegraph_status", "aota_codegraph_query", "aota_codegraph_explore", "aota_codegraph_rebuild",
        ],
    },
    "project-steward": {
        "source_read": True, "source_write": False, "terminal": False,
        "web": False, "control_plane_write": False,
        "project_metadata_write": True, "codegraph_read": True,
        "codegraph_rebuild": False, "deploy": False, "restart": False,
        "recreate": False, "mutate_files": False, "source_mutation": False,
        "control_plane_mutation": False, "project_metadata_mutation": True,
        "submit_project_steward_report": True, "submit_worker_outcome": True,
        "allowed_tools": [
            "aota_project_scan", "aota_project_search", "aota_project_open",
            "aota_project_relationship_brief", "aota_project_prepare",
            "aota_project_registry_refresh", "aota_project_docs_update",
            "aota_project_artifact_link", "aota_codegraph_status",
            "aota_codegraph_query", "aota_codegraph_explore",
            "aota_project_steward_report", "aota_worker_outcome_submit",
        ],
    },
}

# ---------------------------------------------------------------------------
# Task-kind capabilities: maps task_kind -> dict of capabilities
# ---------------------------------------------------------------------------

TASK_KIND_CAPABILITIES: dict[str, dict[str, Any]] = {
    "implementation": {
        "mutate_files": True,
        "source_write": True,
        "bounded_project_command": True,
        "submit_coder_report": True,
    },
    "diagnosis": {
        "mutate_files": False,
        "submit_debugger_report": True,
    },
    "review": {
        "mutate_files": False,
        "submit_reviewer_report": True,
    },
    "architecture": {
        "mutate_files": False,
        "submit_architect_report": True,
    },
    "stewardship": {
        "mutate_files": False, "source_mutation": False,
        "project_metadata_mutation": True,
        "submit_project_steward_report": True,
    },
}


def is_capable(profile: str, task_kind: str, capability: str) -> bool:
    """Check if the given profile and task_kind support a capability.

    Both profile and task_kind must support the capability.
    Returns False if either is unknown.
    """
    profile_caps = PROFILE_CAPABILITIES.get(profile, {})
    task_caps = TASK_KIND_CAPABILITIES.get(task_kind, {})

    return bool(profile_caps.get(capability) and task_caps.get(capability))
