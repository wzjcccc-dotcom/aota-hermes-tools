"""AOTA Tools plugin for Hermes — P2 read-only narrow tools + P3 web/fetch + file copy + P4 task spec artifact + P5 profile task start + P6 profile task status + P7 profile task cancel + P8-D durable handoff + P10 operator inbox + PF-WI-05 Plan→SPEC traceability.

Provides:
  aota_core:          aota_runtime_info
  aota_fs_readonly:   aota_path_info, aota_read_file, aota_search_files
  aota_repo_readonly: aota_repo_status_readonly, aota_repo_diff_readonly
  aota_web_readonly:  aota_web_fetch
  aota_fs_copy:       aota_file_copy
  aota_task_spec:     aota_task_spec_create, aota_task_spec_update
  aota_profile_task:  aota_profile_task_start, aota_profile_task_status, aota_profile_task_cancel, aota_profile_task_approve
  aota_handoff:       aota_handoff_list, aota_handoff_open, aota_handoff_ack
  aota_orchestration: aota_orchestration_decision_record, aota_followup_task_create
  aota_operator:      aota_operator_inbox_list, aota_operator_inbox_open, aota_operator_consistency_check
  aota_plan_read:     aota_plan_open
"""

from __future__ import annotations

import json
import os

# P1 tool
from ._path_info import handle as _handle_path_info, SCHEMA as _path_info_schema, TOOL_NAME as _path_info_name
from ._read_file import handle as _handle_read_file, SCHEMA as _read_file_schema, TOOL_NAME as _read_file_name
from ._search_files import handle as _handle_search, SCHEMA as _search_schema, TOOL_NAME as _search_name
from ._repo_status import handle as _handle_repo_status, SCHEMA as _repo_status_schema, TOOL_NAME as _repo_status_name
from ._repo_diff import handle as _handle_repo_diff, SCHEMA as _repo_diff_schema, TOOL_NAME as _repo_diff_name

# P3 tools
from ._web_fetch import handle as _handle_web_fetch, SCHEMA as _web_fetch_schema, TOOL_NAME as _web_fetch_name
from ._file_copy import handle as _handle_file_copy, SCHEMA as _file_copy_schema, TOOL_NAME as _file_copy_name

# P4 tools
from ._task_spec_create import (
    handle as _handle_task_spec_create,
    SCHEMA as _task_spec_create_schema,
    TOOL_NAME as _task_spec_create_name,
)
from ._task_spec_update import (
    handle as _handle_task_spec_update,
    SCHEMA as _task_spec_update_schema,
    TOOL_NAME as _task_spec_update_name,
)

# P5 tools
from ._profile_task_start import (
    handle as _handle_profile_task_start,
    SCHEMA as _profile_task_start_schema,
    TOOL_NAME as _profile_task_start_name,
)

# P6 tools
from ._profile_task_status import (
    handle as _handle_profile_task_status,
    SCHEMA as _profile_task_status_schema,
    TOOL_NAME as _profile_task_status_name,
)

# P7 tools
from ._profile_task_cancel import (
    handle as _handle_profile_task_cancel,
    SCHEMA as _profile_task_cancel_schema,
    TOOL_NAME as _profile_task_cancel_name,
)

# P8-C tools
from ._profile_task_approve import (
    handle as _handle_profile_task_approve,
    SCHEMA as _profile_task_approve_schema,
    TOOL_NAME as _profile_task_approve_name,
)

# P8-C.5 tools
from ._worker_outcome_submit import (
    handle as _handle_worker_outcome_submit,
    SCHEMA as _worker_outcome_submit_schema,
    TOOL_NAME as _worker_outcome_submit_name,
    TOOLSET_NAME as _worker_outcome_submit_ts,
)
from ._debugger_report_submit import (
    handle as _handle_debugger_report_submit,
    SCHEMA as _debugger_report_submit_schema,
    TOOL_NAME as _debugger_report_submit_name,
    TOOLSET_NAME as _debugger_report_submit_ts,
)
from ._reviewer_report_submit import (
    handle as _handle_reviewer_report_submit,
    SCHEMA as _reviewer_report_submit_schema,
    TOOL_NAME as _reviewer_report_submit_name,
    TOOLSET_NAME as _reviewer_report_submit_ts,
)
from ._coder_report_submit import (
    handle as _handle_coder_report_submit,
    SCHEMA as _coder_report_submit_schema,
    TOOL_NAME as _coder_report_submit_name,
    TOOLSET_NAME as _coder_report_submit_ts,
)
from ._architect_report_submit import (
    handle as _handle_architect_report_submit,
    SCHEMA as _architect_report_submit_schema,
    TOOL_NAME as _architect_report_submit_name,
    TOOLSET_NAME as _architect_report_submit_ts,
)

# P8-D tools
from ._handoff_list import (
    handle as _handle_handoff_list,
    SCHEMA as _handoff_list_schema,
    TOOL_NAME as _handoff_list_name,
)
from ._handoff_open import (
    handle as _handle_handoff_open,
    SCHEMA as _handoff_open_schema,
    TOOL_NAME as _handoff_open_name,
)
from ._handoff_ack import (
    handle as _handle_handoff_ack,
    SCHEMA as _handoff_ack_schema,
    TOOL_NAME as _handoff_ack_name,
)

# P8-E tools
from ._orchestration_decision_record import (
    handle as _handle_orchestration_decision_record,
    SCHEMA as _orchestration_decision_record_schema,
    TOOL_NAME as _orchestration_decision_record_name,
)
from ._followup_task_create import (
    handle as _handle_followup_task_create,
    SCHEMA as _followup_task_create_schema,
    TOOL_NAME as _followup_task_create_name,
)

# P9 tools
from ._orchestration_decision_resume import (
    handle as _handle_orchestration_decision_resume,
    SCHEMA as _orchestration_decision_resume_schema,
    TOOL_NAME as _orchestration_decision_resume_name,
)
from ._orchestration_decision_list import (
    handle as _handle_orchestration_decision_list,
    SCHEMA as _orchestration_decision_list_schema,
    TOOL_NAME as _orchestration_decision_list_name,
)
from ._orchestration_decision_open import (
    handle as _handle_orchestration_decision_open,
    SCHEMA as _orchestration_decision_open_schema,
    TOOL_NAME as _orchestration_decision_open_name,
)
from ._orchestration_lineage import (
    handle as _handle_orchestration_lineage,
    SCHEMA as _orchestration_lineage_schema,
    TOOL_NAME as _orchestration_lineage_name,
)

from ._plan_open import (
    handle as _handle_plan_open,
    SCHEMA as _plan_open_schema,
    TOOL_NAME as _plan_open_name,
)
from ._plan_mutation import (
    handle_create as _handle_plan_create,
    handle_update as _handle_plan_update,
    CREATE_SCHEMA as _plan_create_schema,
    UPDATE_SCHEMA as _plan_update_schema,
    CREATE_TOOL_NAME as _plan_create_name,
    UPDATE_TOOL_NAME as _plan_update_name,
)
from ._work_classifier import (
    handle as _handle_work_classify,
    SCHEMA as _work_classify_schema,
    TOOL_NAME as _work_classify_name,
)

# P10 tools
from ._operator_inbox_list import (
    handle as _handle_operator_inbox_list,
    SCHEMA as _operator_inbox_list_schema,
    TOOL_NAME as _operator_inbox_list_name,
)
from ._operator_inbox_open import (
    handle as _handle_operator_inbox_open,
    SCHEMA as _operator_inbox_open_schema,
    TOOL_NAME as _operator_inbox_open_name,
)
from ._operator_consistency_check import (
    handle as _handle_operator_consistency_check,
    SCHEMA as _operator_consistency_check_schema,
    TOOL_NAME as _operator_consistency_check_name,
)

PLUGIN_NAME = "aota-tools"

# Toolsets
TOOLSET_CORE = "aota_core"
TOOLSET_FS_READONLY = "aota_fs_readonly"
TOOLSET_REPO_READONLY = "aota_repo_readonly"

# P3 toolsets
TOOLSET_WEB_READONLY = "aota_web_readonly"
TOOLSET_FS_COPY = "aota_fs_copy"

# P4 toolset
TOOLSET_TASK_SPEC = "aota_task_spec"

# P5 toolset
TOOLSET_PROFILE_TASK = "aota_profile_task"

# P8-C.5 toolsets
TOOLSET_WORKER_OUTCOME = "aota_worker_outcome"
TOOLSET_DEBUGGER_ARTIFACT = "aota_debugger_artifact"
TOOLSET_REVIEWER_ARTIFACT = "aota_reviewer_artifact"
TOOLSET_CODER_ARTIFACT = "aota_coder_artifact"
TOOLSET_ARCHITECT_ARTIFACT = "aota_architect_artifact"

# P8-D toolset
TOOLSET_HANDOFF = "aota_handoff"

# P8-E toolset
TOOLSET_ORCHESTRATION = "aota_orchestration"

# PF-WI-02 / PF-WI-03 toolsets
TOOLSET_PLAN_READ = "aota_plan_read"
TOOLSET_PLAN_WRITE = "aota_plan_write"
TOOLSET_WORK_INTAKE = "aota_work_intake"

# P10 toolset
TOOLSET_OPERATOR = "aota_operator"

# P1 tool (preserved)
TOOL_NAME_RUNTIME_INFO = "aota_runtime_info"

AOTA_RUNTIME_INFO_SCHEMA = {
    "name": TOOL_NAME_RUNTIME_INFO,
    "description": (
        "Return compact read-only AOTA runtime foundation metadata. "
        "This tool accepts no path or command input, runs no shell, and makes "
        "no filesystem mutations."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
}


def _aota_runtime_info(_args: dict, **_kwargs) -> str:
    runtime_root = os.environ.get("AOTA_RUNTIME_ROOT")
    profile_task_root = os.environ.get("AOTA_PROFILE_TASK_ROOT")

    payload = {
        "plugin": PLUGIN_NAME,
        "toolset": TOOLSET_CORE,
        "tool": TOOL_NAME_RUNTIME_INFO,
        "runtime_root": runtime_root,
        "profile_task_root": profile_task_root,
        "runtime_root_exists": bool(runtime_root and os.path.isdir(runtime_root)),
        "profile_task_root_exists": bool(
            profile_task_root and os.path.isdir(profile_task_root)
        ),
    }
    if not runtime_root or not profile_task_root:
        payload["available"] = False
        payload["error"] = "AOTA runtime environment is not fully configured."
    else:
        payload["available"] = True

    return json.dumps(payload, sort_keys=True)


def register(ctx) -> None:
    # P1: aota_core / aota_runtime_info (preserved)
    ctx.register_tool(
        name=TOOL_NAME_RUNTIME_INFO,
        toolset=TOOLSET_CORE,
        schema=AOTA_RUNTIME_INFO_SCHEMA,
        handler=_aota_runtime_info,
        description=AOTA_RUNTIME_INFO_SCHEMA["description"],
    )

    # P2: aota_fs_readonly
    ctx.register_tool(
        name=_path_info_name,
        toolset=TOOLSET_FS_READONLY,
        schema=_path_info_schema,
        handler=_handle_path_info,
        description=_path_info_schema["description"],
    )
    ctx.register_tool(
        name=_read_file_name,
        toolset=TOOLSET_FS_READONLY,
        schema=_read_file_schema,
        handler=_handle_read_file,
        description=_read_file_schema["description"],
    )
    ctx.register_tool(
        name=_search_name,
        toolset=TOOLSET_FS_READONLY,
        schema=_search_schema,
        handler=_handle_search,
        description=_search_schema["description"],
    )

    # P2: aota_repo_readonly
    ctx.register_tool(
        name=_repo_status_name,
        toolset=TOOLSET_REPO_READONLY,
        schema=_repo_status_schema,
        handler=_handle_repo_status,
        description=_repo_status_schema["description"],
    )
    ctx.register_tool(
        name=_repo_diff_name,
        toolset=TOOLSET_REPO_READONLY,
        schema=_repo_diff_schema,
        handler=_handle_repo_diff,
        description=_repo_diff_schema["description"],
    )

    # P3: aota_web_readonly
    ctx.register_tool(
        name=_web_fetch_name,
        toolset=TOOLSET_WEB_READONLY,
        schema=_web_fetch_schema,
        handler=_handle_web_fetch,
        description=_web_fetch_schema["description"],
    )

    # P3: aota_fs_copy
    ctx.register_tool(
        name=_file_copy_name,
        toolset=TOOLSET_FS_COPY,
        schema=_file_copy_schema,
        handler=_handle_file_copy,
        description=_file_copy_schema["description"],
    )

    # P4: aota_task_spec
    ctx.register_tool(
        name=_task_spec_create_name,
        toolset=TOOLSET_TASK_SPEC,
        schema=_task_spec_create_schema,
        handler=_handle_task_spec_create,
        description=_task_spec_create_schema["description"],
    )
    ctx.register_tool(
        name=_task_spec_update_name,
        toolset=TOOLSET_TASK_SPEC,
        schema=_task_spec_update_schema,
        handler=_handle_task_spec_update,
        description=_task_spec_update_schema["description"],
    )

    # P5: aota_profile_task
    ctx.register_tool(
        name=_profile_task_start_name,
        toolset=TOOLSET_PROFILE_TASK,
        schema=_profile_task_start_schema,
        handler=_handle_profile_task_start,
        description=_profile_task_start_schema["description"],
    )

    # P6: aota_profile_task_status (same toolset)
    ctx.register_tool(
        name=_profile_task_status_name,
        toolset=TOOLSET_PROFILE_TASK,
        schema=_profile_task_status_schema,
        handler=_handle_profile_task_status,
        description=_profile_task_status_schema["description"],
    )

    # P7: aota_profile_task_cancel (same toolset)
    ctx.register_tool(
        name=_profile_task_cancel_name,
        toolset=TOOLSET_PROFILE_TASK,
        schema=_profile_task_cancel_schema,
        handler=_handle_profile_task_cancel,
        description=_profile_task_cancel_schema["description"],
    )

    # P8-C: aota_profile_task_approve (same toolset)
    ctx.register_tool(
        name=_profile_task_approve_name,
        toolset=TOOLSET_PROFILE_TASK,
        schema=_profile_task_approve_schema,
        handler=_handle_profile_task_approve,
        description=_profile_task_approve_schema["description"],
    )

    # P8-C.5: aota_worker_outcome_submit (new toolset)
    ctx.register_tool(
        name=_worker_outcome_submit_name,
        toolset=TOOLSET_WORKER_OUTCOME,
        schema=_worker_outcome_submit_schema,
        handler=_handle_worker_outcome_submit,
        description=_worker_outcome_submit_schema["description"],
    )

    # P8-C.5: aota_debugger_report_submit (new toolset)
    ctx.register_tool(
        name=_debugger_report_submit_name,
        toolset=TOOLSET_DEBUGGER_ARTIFACT,
        schema=_debugger_report_submit_schema,
        handler=_handle_debugger_report_submit,
        description=_debugger_report_submit_schema["description"],
    )

    # P8-C.5: aota_reviewer_report_submit (new toolset)
    ctx.register_tool(
        name=_reviewer_report_submit_name,
        toolset=TOOLSET_REVIEWER_ARTIFACT,
        schema=_reviewer_report_submit_schema,
        handler=_handle_reviewer_report_submit,
        description=_reviewer_report_submit_schema["description"],
    )

    # P8-D: aota_coder_report_submit (new toolset)
    ctx.register_tool(
        name=_coder_report_submit_name,
        toolset=TOOLSET_CODER_ARTIFACT,
        schema=_coder_report_submit_schema,
        handler=_handle_coder_report_submit,
        description=_coder_report_submit_schema["description"],
    )

    # P11-J: aota_architect_artifact
    ctx.register_tool(
        name=_architect_report_submit_name,
        toolset=TOOLSET_ARCHITECT_ARTIFACT,
        schema=_architect_report_submit_schema,
        handler=_handle_architect_report_submit,
        description=_architect_report_submit_schema["description"],
    )

    # P8-D: aota_handoff
    ctx.register_tool(
        name=_handoff_list_name,
        toolset=TOOLSET_HANDOFF,
        schema=_handoff_list_schema,
        handler=_handle_handoff_list,
        description=_handoff_list_schema["description"],
    )
    ctx.register_tool(
        name=_handoff_open_name,
        toolset=TOOLSET_HANDOFF,
        schema=_handoff_open_schema,
        handler=_handle_handoff_open,
        description=_handoff_open_schema["description"],
    )
    ctx.register_tool(
        name=_handoff_ack_name,
        toolset=TOOLSET_HANDOFF,
        schema=_handoff_ack_schema,
        handler=_handle_handoff_ack,
        description=_handoff_ack_schema["description"],
    )

    # P8-E: aota_orchestration
    ctx.register_tool(
        name=_orchestration_decision_record_name,
        toolset=TOOLSET_ORCHESTRATION,
        schema=_orchestration_decision_record_schema,
        handler=_handle_orchestration_decision_record,
        description=_orchestration_decision_record_schema["description"],
    )
    ctx.register_tool(
        name=_followup_task_create_name,
        toolset=TOOLSET_ORCHESTRATION,
        schema=_followup_task_create_schema,
        handler=_handle_followup_task_create,
        description=_followup_task_create_schema["description"],
    )

    # P9: aota_orchestration (additional tools, same toolset)
    ctx.register_tool(
        name=_orchestration_decision_resume_name,
        toolset=TOOLSET_ORCHESTRATION,
        schema=_orchestration_decision_resume_schema,
        handler=_handle_orchestration_decision_resume,
        description=_orchestration_decision_resume_schema["description"],
    )
    ctx.register_tool(
        name=_orchestration_decision_list_name,
        toolset=TOOLSET_ORCHESTRATION,
        schema=_orchestration_decision_list_schema,
        handler=_handle_orchestration_decision_list,
        description=_orchestration_decision_list_schema["description"],
    )
    ctx.register_tool(
        name=_orchestration_decision_open_name,
        toolset=TOOLSET_ORCHESTRATION,
        schema=_orchestration_decision_open_schema,
        handler=_handle_orchestration_decision_open,
        description=_orchestration_decision_open_schema["description"],
    )
    ctx.register_tool(
        name=_orchestration_lineage_name,
        toolset=TOOLSET_ORCHESTRATION,
        schema=_orchestration_lineage_schema,
        handler=_handle_orchestration_lineage,
        description=_orchestration_lineage_schema["description"],
    )

    # PF-WI-02: aota_plan_read (registered but intentionally profile-disabled)
    ctx.register_tool(
        name=_plan_open_name,
        toolset=TOOLSET_PLAN_READ,
        schema=_plan_open_schema,
        handler=_handle_plan_open,
        description=_plan_open_schema["description"],
    )

    # PF-WI-03: aota_plan_write (registered but intentionally profile-disabled)
    ctx.register_tool(
        name=_plan_create_name,
        toolset=TOOLSET_PLAN_WRITE,
        schema=_plan_create_schema,
        handler=_handle_plan_create,
        description=_plan_create_schema["description"],
    )
    ctx.register_tool(
        name=_plan_update_name,
        toolset=TOOLSET_PLAN_WRITE,
        schema=_plan_update_schema,
        handler=_handle_plan_update,
        description=_plan_update_schema["description"],
    )

    # PF-WI-04: source capability only; intentionally profile-disabled.
    ctx.register_tool(
        name=_work_classify_name,
        toolset=TOOLSET_WORK_INTAKE,
        schema=_work_classify_schema,
        handler=_handle_work_classify,
        description=_work_classify_schema["description"],
    )

    # P10: aota_operator
    ctx.register_tool(
        name=_operator_inbox_list_name,
        toolset=TOOLSET_OPERATOR,
        schema=_operator_inbox_list_schema,
        handler=_handle_operator_inbox_list,
        description=_operator_inbox_list_schema["description"],
    )
    ctx.register_tool(
        name=_operator_inbox_open_name,
        toolset=TOOLSET_OPERATOR,
        schema=_operator_inbox_open_schema,
        handler=_handle_operator_inbox_open,
        description=_operator_inbox_open_schema["description"],
    )
    ctx.register_tool(
        name=_operator_consistency_check_name,
        toolset=TOOLSET_OPERATOR,
        schema=_operator_consistency_check_schema,
        handler=_handle_operator_consistency_check,
        description=_operator_consistency_check_schema["description"],
    )
