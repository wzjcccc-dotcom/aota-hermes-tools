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
        "mutate_files": True,
        "submit_coder_report": True,
        "submit_worker_outcome": True,
        "allowed_tools": [
            "aota_file_copy",
            "aota_coder_report_submit",
            "aota_worker_outcome_submit",
        ],
    },
    "debugger": {
        "mutate_files": False,
        "submit_debugger_report": True,
        "submit_worker_outcome": True,
        "allowed_tools": [
            "aota_debugger_report_submit",
            "aota_worker_outcome_submit",
        ],
    },
    "reviewer": {
        "mutate_files": False,
        "submit_reviewer_report": True,
        "submit_worker_outcome": True,
        "allowed_tools": [
            "aota_reviewer_report_submit",
            "aota_worker_outcome_submit",
        ],
    },
    "architect": {
        "mutate_files": False,
        "submit_architect_report": True,
        "submit_worker_outcome": True,
        "allowed_tools": [
            "aota_architect_report_submit",
            "aota_worker_outcome_submit",
        ],
    },
    "task-main": {
        "mutate_files": True,
        "orchestrate": True,
        "operator": True,
        "allowed_tools": [
            "aota_task_spec_create",
            "aota_task_spec_update",
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
            "aota_file_copy",
        ],
    },
}

# ---------------------------------------------------------------------------
# Task-kind capabilities: maps task_kind -> dict of capabilities
# ---------------------------------------------------------------------------

TASK_KIND_CAPABILITIES: dict[str, dict[str, Any]] = {
    "implementation": {
        "mutate_files": True,
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
}


def is_capable(profile: str, task_kind: str, capability: str) -> bool:
    """Check if the given profile and task_kind support a capability.

    Both profile and task_kind must support the capability.
    Returns False if either is unknown.
    """
    profile_caps = PROFILE_CAPABILITIES.get(profile, {})
    task_caps = TASK_KIND_CAPABILITIES.get(task_kind, {})

    return bool(profile_caps.get(capability) and task_caps.get(capability))
