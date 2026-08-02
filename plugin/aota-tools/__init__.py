"""AOTA Forge bounded control-plane tools for workspace, Plan/SPEC, Profile Task, handoff, and project lifecycle contracts.

Provides:
  aota_core:          aota_runtime_info
  aota_fs_readonly:   aota_path_info, aota_read_file, aota_search_files
  aota_repo_readonly: aota_repo_status_readonly, aota_repo_diff_readonly
  aota_web_readonly:  aota_web_fetch
  aota_fs_copy:       aota_file_copy
  aota_task_spec:     aota_task_spec_create, aota_task_spec_update
  aota_profile_task:  aota_profile_task_dispatch (P0 semantic facade), aota_profile_task_start (semantic frozen-SPEC reference plus legacy handler compatibility), aota_profile_task_status, aota_profile_task_cancel, aota_profile_task_approve
  aota_handoff:       aota_handoff_list, aota_handoff_open, aota_handoff_ack
  aota_orchestration: aota_orchestration_decision_record, aota_followup_task_create
  aota_operator:      aota_operator_inbox_list, aota_operator_inbox_open, aota_operator_consistency_check
  aota_plan_read:     aota_plan_open
  aota_project_readonly: aota_project_scan, aota_project_search, aota_project_open, aota_project_prepare
  aota_codegraph_readonly: aota_codegraph_status, aota_codegraph_query, aota_codegraph_explore
  aota_codegraph_rebuild: aota_codegraph_rebuild
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
from ._webui_attachment_read import (
    handle as _handle_webui_attachment_read,
    SCHEMA as _webui_attachment_read_schema,
    TOOL_NAME as _webui_attachment_read_name,
    TOOLSET_NAME as _webui_attachment_read_toolset,
)
from ._active_task_artifact_open import (
    handle as _handle_active_task_artifact_open,
    SCHEMA as _active_task_artifact_open_schema,
    TOOL_NAME as _active_task_artifact_open_name,
    TOOLSET_NAME as _active_task_artifact_open_toolset,
)
from ._subject_task_artifact_open import (
    handle as _handle_subject_task_artifact_open,
    SCHEMA as _subject_task_artifact_open_schema,
    TOOL_NAME as _subject_task_artifact_open_name,
    TOOLSET_NAME as _subject_task_artifact_open_toolset,
)

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
from ._task_spec_freeze import (
    handle as _handle_task_spec_freeze,
    SCHEMA as _task_spec_freeze_schema,
    TOOL_NAME as _task_spec_freeze_name,
)

# P5 tools
from ._profile_task_start import (
    handle as _handle_profile_task_start,
    SCHEMA as _profile_task_start_schema,
    TOOL_NAME as _profile_task_start_name,
)
from ._profile_task_dispatch import (
    handle as _handle_profile_task_dispatch,
    SCHEMA as _profile_task_dispatch_schema,
    TOOL_NAME as _profile_task_dispatch_name,
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
from ._project_steward_report import (
    handle as _handle_project_steward_report,
    SCHEMA as _project_steward_report_schema,
    TOOL_NAME as _project_steward_report_name,
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
from ._project_discovery import handle as _handle_project_scan, SCHEMA as _project_scan_schema, TOOL_NAME as _project_scan_name
from ._workspace_context import handle_list as _handle_workspace_list, handle_open as _handle_workspace_open, LIST_SCHEMA as _workspace_list_schema, OPEN_SCHEMA as _workspace_open_schema, LIST_TOOL_NAME as _workspace_list_name, OPEN_TOOL_NAME as _workspace_open_name, TOOLSET_NAME as TOOLSET_WORKSPACE_READONLY
from ._workspace_selection_record import handle as _handle_workspace_selection_record, SCHEMA as _workspace_selection_record_schema, TOOL_NAME as _workspace_selection_record_name
from ._project_open import handle_search as _handle_project_search, handle_open as _handle_project_open, SEARCH_SCHEMA as _project_search_schema, OPEN_SCHEMA as _project_open_schema, TOOL_NAME_SEARCH as _project_search_name, TOOL_NAME_OPEN as _project_open_name
from ._project_registry import handle_open as _handle_project_registry_open, handle_refresh as _handle_project_registry_refresh, OPEN_SCHEMA as _project_registry_open_schema, REFRESH_SCHEMA as _project_registry_refresh_schema, TOOL_NAME_OPEN as _project_registry_open_name, TOOL_NAME_REFRESH as _project_registry_refresh_name
from ._project_relationship import handle as _handle_project_relationship, SCHEMA as _project_relationship_schema, TOOL_NAME as _project_relationship_name
from ._project_prepare import handle as _handle_project_prepare, SCHEMA as _project_prepare_schema, TOOL_NAME as _project_prepare_name
from ._project_steward_mutation import handle_docs_update as _handle_project_docs_update, handle_artifact_link as _handle_project_artifact_link, DOCS_SCHEMA as _project_docs_update_schema, ARTIFACT_SCHEMA as _project_artifact_link_schema, DOCS_TOOL_NAME as _project_docs_update_name, ARTIFACT_TOOL_NAME as _project_artifact_link_name, TOOLSET_NAME as TOOLSET_PROJECT_STEWARD
from ._codegraph_readonly import status_handle as _handle_codegraph_status, query_handle as _handle_codegraph_query, explore_handle as _handle_codegraph_explore, STATUS_SCHEMA as _codegraph_status_schema, QUERY_SCHEMA as _codegraph_query_schema, EXPLORE_SCHEMA as _codegraph_explore_schema, STATUS_TOOL_NAME as _codegraph_status_name, QUERY_TOOL_NAME as _codegraph_query_name, EXPLORE_TOOL_NAME as _codegraph_explore_name
from ._codegraph_rebuild import handle as _handle_codegraph_rebuild, SCHEMA as _codegraph_rebuild_schema, TOOL_NAME as _codegraph_rebuild_name, TOOLSET_NAME as TOOLSET_CODEGRAPH_REBUILD
from ._project_file_mutation import handle_read as _handle_project_file_read, handle_write as _handle_project_file_write, handle_patch as _handle_project_file_patch, READ_SCHEMA as _project_file_read_schema, WRITE_SCHEMA as _project_file_write_schema, PATCH_SCHEMA as _project_file_patch_schema, READ_TOOL_NAME as _project_file_read_name, WRITE_TOOL_NAME as _project_file_write_name, PATCH_TOOL_NAME as _project_file_patch_name, TOOLSET_NAME as TOOLSET_CODER_FILE_MUTATION
from ._project_command_run import handle as _handle_project_command_run, SCHEMA as _project_command_run_schema, TOOL_NAME as _project_command_run_name, TOOLSET_NAME as TOOLSET_CODER_COMMAND
from ._project_initializer import handle_initialize_core as _handle_project_initialize_core, INITIALIZE_SCHEMA as _project_initialize_schema, TOOL_NAME as _project_initialize_name
from ._phase3_control_plane import PHASE3_SCHEMAS as _phase3_schemas, phase3_handler as _phase3_handler
from ._phase4_control_plane import PHASE4_SCHEMAS as _phase4_schemas, phase4_handler as _phase4_handler

PLUGIN_NAME = "aota-tools"

# Toolsets
TOOLSET_CORE = "aota_core"
TOOLSET_FS_READONLY = "aota_fs_readonly"
TOOLSET_REPO_READONLY = "aota_repo_readonly"
TOOLSET_WEBUI_ATTACHMENT_READ = _webui_attachment_read_toolset
TOOLSET_ACTIVE_TASK_CONTEXT = _active_task_artifact_open_toolset

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
TOOLSET_PROJECT_STEWARD_ARTIFACT = "aota_project_steward_artifact"

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
TOOLSET_PROJECT_READONLY = "aota_project_readonly"
TOOLSET_CODEGRAPH_READONLY = "aota_codegraph_readonly"
# Rebuild is the only CodeGraph mutation surface and accepts registered IDs only.
TOOLSET_CODEGRAPH_REBUILD = "aota_codegraph_rebuild"
# Coder construction is limited to frozen-SPEC-bound text mutation and fixed
# argv validation commands; neither toolset is a general file/shell surface.

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
        schema=_phase4_schemas[_path_info_name],
        handler=_phase4_handler(_path_info_name, _handle_path_info),
        description=_phase4_schemas[_path_info_name]["description"],
    )
    ctx.register_tool(
        name=_read_file_name,
        toolset=TOOLSET_FS_READONLY,
        schema=_phase4_schemas[_read_file_name],
        handler=_phase4_handler(_read_file_name, _handle_read_file),
        description=_phase4_schemas[_read_file_name]["description"],
    )
    ctx.register_tool(
        name=_search_name,
        toolset=TOOLSET_FS_READONLY,
        schema=_phase4_schemas[_search_name],
        handler=_phase4_handler(_search_name, _handle_search),
        description=_phase4_schemas[_search_name]["description"],
    )
    # WebUI attachments are conversation context, not registered workspace files.
    ctx.register_tool(
        name=_webui_attachment_read_name,
        toolset=TOOLSET_WEBUI_ATTACHMENT_READ,
        schema=_webui_attachment_read_schema,
        handler=_handle_webui_attachment_read,
        description=_webui_attachment_read_schema["description"],
    )
    # Active worker context is a read-only, allowlisted artifact surface.
    ctx.register_tool(
        name=_active_task_artifact_open_name,
        toolset=TOOLSET_ACTIVE_TASK_CONTEXT,
        schema=_active_task_artifact_open_schema,
        handler=_handle_active_task_artifact_open,
        description=_active_task_artifact_open_schema["description"],
    )
    ctx.register_tool(
        name=_subject_task_artifact_open_name,
        toolset=_subject_task_artifact_open_toolset,
        schema=_phase3_schemas[_subject_task_artifact_open_name],
        handler=_phase3_handler(_subject_task_artifact_open_name, _handle_subject_task_artifact_open),
        description=_phase3_schemas[_subject_task_artifact_open_name]["description"],
    )

    # P2: aota_repo_readonly
    ctx.register_tool(
        name=_repo_status_name,
        toolset=TOOLSET_REPO_READONLY,
        schema=_phase4_schemas[_repo_status_name],
        handler=_phase4_handler(_repo_status_name, _handle_repo_status),
        description=_phase4_schemas[_repo_status_name]["description"],
    )
    ctx.register_tool(
        name=_repo_diff_name,
        toolset=TOOLSET_REPO_READONLY,
        schema=_phase4_schemas[_repo_diff_name],
        handler=_phase4_handler(_repo_diff_name, _handle_repo_diff),
        description=_phase4_schemas[_repo_diff_name]["description"],
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
        schema=_phase4_schemas[_file_copy_name],
        handler=_phase4_handler(_file_copy_name, _handle_file_copy),
        description=_phase4_schemas[_file_copy_name]["description"],
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
    ctx.register_tool(
        name=_task_spec_freeze_name,
        toolset=TOOLSET_TASK_SPEC,
        schema=_task_spec_freeze_schema,
        handler=_handle_task_spec_freeze,
        description=_task_spec_freeze_schema["description"],
    )

    # P5: aota_profile_task
    ctx.register_tool(
        name=_profile_task_dispatch_name,
        toolset=TOOLSET_PROFILE_TASK,
        schema=_profile_task_dispatch_schema,
        handler=_handle_profile_task_dispatch,
        description=_profile_task_dispatch_schema["description"],
    )
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
        schema=_phase4_schemas[_profile_task_cancel_name],
        handler=_phase4_handler(_profile_task_cancel_name, _handle_profile_task_cancel),
        description=_phase4_schemas[_profile_task_cancel_name]["description"],
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
    ctx.register_tool(
        name=_project_steward_report_name,
        toolset=TOOLSET_PROJECT_STEWARD_ARTIFACT,
        schema=_phase3_schemas[_project_steward_report_name],
        handler=_phase3_handler(_project_steward_report_name, _handle_project_steward_report),
        description=_phase3_schemas[_project_steward_report_name]["description"],
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
    ctx.register_tool(name=_workspace_selection_record_name, toolset=TOOLSET_ORCHESTRATION,
                      schema=_phase3_schemas[_workspace_selection_record_name],
                      handler=_phase3_handler(_workspace_selection_record_name, _handle_workspace_selection_record),
                      description=_phase3_schemas[_workspace_selection_record_name]["description"])
    ctx.register_tool(
        name=_followup_task_create_name,
        toolset=TOOLSET_ORCHESTRATION,
        schema=_phase4_schemas[_followup_task_create_name],
        handler=_phase4_handler(_followup_task_create_name, _handle_followup_task_create),
        description=_phase4_schemas[_followup_task_create_name]["description"],
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
        schema=_phase4_schemas[_orchestration_lineage_name],
        handler=_phase4_handler(_orchestration_lineage_name, _handle_orchestration_lineage),
        description=_phase4_schemas[_orchestration_lineage_name]["description"],
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
        schema=_phase4_schemas[_work_classify_name],
        handler=_phase4_handler(_work_classify_name, _handle_work_classify),
        description=_phase4_schemas[_work_classify_name]["description"],
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
        schema=_phase4_schemas[_operator_consistency_check_name],
        handler=_phase4_handler(_operator_consistency_check_name, _handle_operator_consistency_check),
        description=_phase4_schemas[_operator_consistency_check_name]["description"],
    )

    for name, schema, handler in ((_workspace_list_name, _workspace_list_schema, _handle_workspace_list),
                                  (_workspace_open_name, _phase3_schemas[_workspace_open_name], _phase3_handler(_workspace_open_name, _handle_workspace_open))):
        ctx.register_tool(name=name, toolset=TOOLSET_WORKSPACE_READONLY, schema=schema, handler=handler, description=schema["description"])

    # PCF-WI-01: project continuity read-only discovery/card projection.
    # Registry refresh is excluded because it writes projects.json.
    for name, schema, handler in (
        (_project_scan_name, _phase3_schemas[_project_scan_name], _phase3_handler(_project_scan_name, _handle_project_scan)),
        (_project_search_name, _phase3_schemas[_project_search_name], _phase3_handler(_project_search_name, _handle_project_search)),
        (_project_open_name, _phase3_schemas[_project_open_name], _phase3_handler(_project_open_name, _handle_project_open)),
        (_project_registry_open_name, _phase3_schemas[_project_registry_open_name], _phase3_handler(_project_registry_open_name, _handle_project_registry_open)),
        (_project_relationship_name, _phase3_schemas[_project_relationship_name], _phase3_handler(_project_relationship_name, _handle_project_relationship)),
        (_project_prepare_name, _phase3_schemas[_project_prepare_name], _phase3_handler(_project_prepare_name, _handle_project_prepare)),
    ):
        ctx.register_tool(name=name, toolset=TOOLSET_PROJECT_READONLY, schema=schema, handler=handler, description=schema["description"])
    for name, schema, handler in (
        (_project_registry_refresh_name, _phase3_schemas[_project_registry_refresh_name], _phase3_handler(_project_registry_refresh_name, _handle_project_registry_refresh)),
        (_project_docs_update_name, _phase3_schemas[_project_docs_update_name], _phase3_handler(_project_docs_update_name, _handle_project_docs_update)),
        (_project_artifact_link_name, _phase3_schemas[_project_artifact_link_name], _phase3_handler(_project_artifact_link_name, _handle_project_artifact_link)),
        (_project_initialize_name, _phase3_schemas[_project_initialize_name], _phase3_handler(_project_initialize_name, _handle_project_initialize_core)),
    ):
        ctx.register_tool(name=name, toolset=TOOLSET_PROJECT_STEWARD, schema=schema, handler=handler, description=schema["description"])
    # PCF-WI-08: the public CodeGraph surface is exactly status/query/explore/rebuild.
    for name, schema, handler in (
        (_codegraph_status_name, _phase3_schemas[_codegraph_status_name], _phase3_handler(_codegraph_status_name, _handle_codegraph_status)),
        (_codegraph_query_name, _phase3_schemas[_codegraph_query_name], _phase3_handler(_codegraph_query_name, _handle_codegraph_query)),
        (_codegraph_explore_name, _phase3_schemas[_codegraph_explore_name], _phase3_handler(_codegraph_explore_name, _handle_codegraph_explore)),
    ):
        ctx.register_tool(name=name, toolset=TOOLSET_CODEGRAPH_READONLY, schema=schema, handler=handler, description=schema["description"])
    ctx.register_tool(name=_codegraph_rebuild_name, toolset=TOOLSET_CODEGRAPH_REBUILD,
                      schema=_phase3_schemas[_codegraph_rebuild_name],
                      handler=_phase3_handler(_codegraph_rebuild_name, _handle_codegraph_rebuild),
                      description=_phase3_schemas[_codegraph_rebuild_name]["description"])
    for name, schema, handler in (
        (_project_file_read_name, _phase3_schemas[_project_file_read_name], _phase3_handler(_project_file_read_name, _handle_project_file_read)),
        (_project_file_write_name, _phase3_schemas[_project_file_write_name], _phase3_handler(_project_file_write_name, _handle_project_file_write)),
        (_project_file_patch_name, _phase3_schemas[_project_file_patch_name], _phase3_handler(_project_file_patch_name, _handle_project_file_patch)),
    ):
        ctx.register_tool(name=name, toolset=TOOLSET_CODER_FILE_MUTATION, schema=schema, handler=handler, description=schema["description"])
    ctx.register_tool(name=_project_command_run_name, toolset=TOOLSET_CODER_COMMAND,
                      schema=_phase3_schemas[_project_command_run_name],
                      handler=_phase3_handler(_project_command_run_name, _handle_project_command_run),
                      description=_phase3_schemas[_project_command_run_name]["description"])
