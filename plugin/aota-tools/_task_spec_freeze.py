"""Freeze a canonical WI-09C SPEC revision without allowing in-place edits."""
from __future__ import annotations

import json

from ._task_spec_common import acquire_lock, atomic_write, compute_sha256, get_task_dir, load_meta, release_lock, utc_now_iso, write_json
from ._workspace import WorkspaceError
from ._spec_contract import ContractError, canonical_hash, validate_spec
from ._spec_traceability import validate_snapshot_for_freeze
from ._session_active_spec_binding import (
    SessionBindingError,
    TrustedSessionContext,
    trusted_session_context,
    write_session_active_spec,
)
from ._trusted_runtime_context import missing_context_result
from ._next_tool_contract import attach_next_tool_option
from ._session_state_authority import (
    POINTER_KIND_CURRENT_DRAFT_SPEC,
    SessionStateError,
    consume_pointer,
    write_active_frozen_spec_pointer,
)

TOOL_NAME = "aota_task_spec_freeze"
TOOLSET_NAME = "aota_task_spec"
SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Freeze one validated canonical SPEC revision. Frozen content is immutable.\n\n"
        "CANONICAL vs LEGACY SPEC:\n"
        "- Canonical SPEC: has contract_version=1 in meta.json. Uses WI-09C "
        "envelope with spec_id, spec_kind, spec_hash, workspace_context. "
        "CAN be frozen. After freeze, spec_hash is computed via canonical_hash() "
        "and the status becomes 'frozen'.\n"
        "- Legacy SPEC: does NOT have contract_version=1. Uses older "
        "task_kind-based envelope without spec_hash. CANNOT be frozen by this "
        "tool. When attempting to freeze a legacy SPEC, error includes "
        "detected_format=legacy and required_action=create_or_migrate_canonical_spec.\n\n"
        "FREEZE REQUIREMENTS:\n"
        "- SPEC must be a canonical SPEC (contract_version=1)\n"
        "- SPEC must have status='draft'\n"
        "- expected_revision must match the current revision\n"
        "- If already frozen at the same revision, returns idempotent success\n"
        "- Use spec_ref='current_draft_spec'; workspace, SPEC id, revision, and\n"
        "  session identity are resolved by the control plane. Legacy explicit\n"
        "  fields remain handler-only compatibility and are not model fields.\n"
        "- A trusted task-main/coordinator session receives an atomic session-active\n"
        "  SPEC binding. Idempotent freeze does not invent missing runtime context.\n\n"
        "VALID EXAMPLE:\n"
        '  {"workspace_id": "my-ws", "spec_id": "pt_20260721T120000_abcdef01", '
        '"expected_revision": 3}\n'
        "  → returns {status: frozen, spec_hash: <64-char hex>, ...}\n\n"
        "LEGACY ERROR EXAMPLE:\n"
        '  {"workspace_id": "my-ws", "spec_id": "pt_...", "expected_revision": 1}\n'
        "  → error: legacy SPEC has no canonical freeze contract. "
        "detected_format=legacy. required_action=create_or_migrate_canonical_spec. "
        "Create a new canonical SPEC and migrate content."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "spec_ref": {"type": "string", "enum": ["current_draft_spec"], "description": "Bounded semantic reference to the unique current draft SPEC in the trusted workspace."},
        },
        "required": ["spec_ref"],
        "additionalProperties": False,
    },
}


def _attach_start_option(result: dict[str, object]) -> dict[str, object]:
    if result.get("next_action") != "start_active_frozen_spec":
        return result
    from ._profile_task_start import SCHEMA as start_schema
    attach_next_tool_option(
        result,
        start_schema,
        arguments={"task_ref": "active_frozen_spec"},
        include=("timeout_seconds",),
        reason="The exact frozen SPEC is bound to this session and is ready to start.",
    )
    result.setdefault("same_call_retryable", False)
    result.setdefault("flow_disposition", "continue")
    return result

def handle(args: dict, **kwargs: object) -> str:
    try:
        context = trusted_session_context(kwargs)
        if args.get("spec_ref"):
            if not context.workspace_id or not context.usable_for_active_spec:
                return json.dumps(missing_context_result(operation="freeze"), sort_keys=True)
            args = _resolve_current_draft_args(context.workspace_id, context, args["spec_ref"])
        return _do_freeze(args, trusted_context=context)
    except WorkspaceError as exc:
        error = str(exc)
        code = error.split(":", 1)[0]
        if code in {"reference_missing", "current_draft_spec_missing"}:
            return json.dumps({"status": "rejected", "operation_result": "spec_freeze", "error": "current_draft_spec_missing", "retryable": False, "human_action_required": False, "next_action": "create_spec"}, sort_keys=True)
        if code in {"reference_stale", "current_draft_spec_binding_stale"}:
            return json.dumps({"status": "rejected", "operation_result": "spec_freeze", "error": "current_draft_spec_binding_stale", "retryable": False, "human_action_required": False, "next_action": "reconcile_session_state"}, sort_keys=True)
        if code in {"binding_mismatch", "workspace_binding_mismatch", "current_draft_spec_binding_mismatch"}:
            return json.dumps({"status": "rejected", "operation_result": "spec_freeze", "error": "current_draft_spec_binding_mismatch", "retryable": False, "human_action_required": False, "next_action": "stop_and_report_control_plane_inconsistency"}, sort_keys=True)
        if code == "reference_consumed":
            return json.dumps({"status": "rejected", "operation_result": "spec_freeze", "error": "current_draft_spec_consumed", "retryable": False, "human_action_required": False, "next_action": "create_spec"}, sort_keys=True)
        return json.dumps({
            "status": "rejected", "operation_result": "spec_freeze", "error": error, "retryable": False,
            "human_action_required": "ambiguous" in error,
            "human_action_required": "ambiguous" in error,
            "next_action": "select_current_draft_spec" if "ambiguous" in error else "stop_and_report_freeze_failure",
        }, sort_keys=True)
    except Exception as exc:
        return json.dumps({"status": "failed", "operation_result": "spec_freeze", "error": str(exc), "retryable": False, "human_action_required": False, "next_action": "stop_and_report_freeze_failure"}, sort_keys=True)


def _resolve_current_draft_args(workspace_id: str, context: TrustedSessionContext, spec_ref: str) -> dict[str, object]:
    """Resolve one exact draft; never choose the newest/last candidate."""
    if spec_ref != "current_draft_spec":
        raise WorkspaceError("reference_invalid: unsupported_spec_ref")
    from ._reference_resolver import ReferenceError, resolve_current_draft_spec
    try:
        resolved = resolve_current_draft_spec(workspace_id, context)
    except ReferenceError as exc:
        raise WorkspaceError(f"{exc.code}: {exc.detail}") from exc
    return {"workspace_id": workspace_id, "spec_id": resolved["spec_id"], "expected_revision": resolved["revision"]}

def _write_active_binding(
    *, workspace_id: str, task_dir: object, meta: dict, trusted_context: TrustedSessionContext
) -> dict[str, object]:
    """Publish only a trusted task-main/coordinator session binding."""
    if not trusted_context.usable_for_active_spec:
        return {"status": "failed", "error": "trusted_session_context_missing", "retryable": False}
    spec = meta.get("spec") if isinstance(meta.get("spec"), dict) else {}
    context = spec.get("workspace_context") if isinstance(spec.get("workspace_context"), dict) else {}
    if not context:
        project_id = meta.get("project_id") or spec.get("project_id")
        if isinstance(project_id, str) and project_id.startswith("standalone:"):
            context = {
                "workspace_id": workspace_id,
                "project_id": project_id,
                "workspace_registry_digest": "standalone",
                "project_manifest_digest": "standalone",
                "project_registry_revision": 0,
            }
        else:
            return {"status": "failed", "error": "workspace_binding_missing", "retryable": False}
    if any(
        context.get(key) in (None, "")
        for key in ("workspace_id", "project_id", "workspace_registry_digest", "project_manifest_digest")
    ):
        return {"status": "failed", "error": "workspace_binding_missing", "retryable": False}
    binding = {
        "session_id": trusted_context.session_id,
        "workspace_id": workspace_id,
        "project_id": meta.get("project_id") or spec.get("project_id"),
        "task_id": meta.get("task_id") or meta.get("spec_id") or spec.get("spec_id"),
        "spec_id": meta.get("spec_id") or spec.get("spec_id"),
        "revision": meta.get("revision") or spec.get("revision"),
        "spec_hash": meta.get("spec_hash") or spec.get("spec_hash"),
        "spec_sha256": meta.get("spec_sha256"),
        "frozen_at": meta.get("frozen_at") or spec.get("frozen_at"),
        "resolved_profile": meta.get("resolved_profile") or spec.get("resolved_profile"),
        "approval_status": "required" if (meta.get("spec_kind") or spec.get("spec_kind")) == "implementation" else "not_required",
        "workspace_registry_digest": context.get("workspace_registry_digest"),
        "project_manifest_digest": context.get("project_manifest_digest"),
        "project_registry_revision": context.get("project_registry_revision"),
        "binding_source": "task_spec_freeze",
    }
    try:
        write_session_active_spec(binding)
    except SessionBindingError as exc:
        raise WorkspaceError(f"session_binding_write_failed:{exc.code}") from exc
    return {"status": "written", "retryable": False}


def _write_canonical_lifecycle_bindings(
    *, meta: dict, trusted_context: TrustedSessionContext,
) -> dict[str, object]:
    """Publish active-frozen and consume the exact current-draft pointer."""
    if not trusted_context.usable_for_active_spec:
        return {"status": "failed", "error": "trusted_session_context_missing", "retryable": False}
    try:
        pointer, pointer_path = write_active_frozen_spec_pointer(trusted_context, meta)
        project_id = meta.get("project_id") or (meta.get("spec") or {}).get("project_id")
        consume_pointer(
            POINTER_KIND_CURRENT_DRAFT_SPEC,
            meta.get("workspace_id"), project_id, trusted_context.session_id,
            consumed_at=meta.get("frozen_at") or utc_now_iso(),
        )
    except SessionStateError as exc:
        return {"status": "failed", "error": exc.code, "detail": exc.detail, "retryable": False}
    return {"status": "written", "pointer_kind": pointer["pointer_kind"], "pointer_path": str(pointer_path), "artifact_digest": pointer["artifact_digest"]}


def _do_freeze(args: dict, *, trusted_context: TrustedSessionContext | None = None) -> str:
    trusted_context = trusted_context or TrustedSessionContext()
    workspace_id, spec_id, expected = args.get("workspace_id", ""), args.get("spec_id", ""), args.get("expected_revision")
    task_dir = get_task_dir(workspace_id, spec_id)
    if not task_dir.is_dir(): raise WorkspaceError("spec not found: " + spec_id)
    lock = acquire_lock(workspace_id, spec_id, timeout=5.0)
    try:
        meta = load_meta(task_dir)
        if meta.get("contract_version") != 1:
            raise WorkspaceError(
                "legacy SPEC has no canonical freeze contract. "
                "detected_format=legacy. "
                "contract_version=" + str(meta.get("contract_version", "missing")) + ". "
                "required_action=create_or_migrate_canonical_spec. "
                "Create a new canonical SPEC with spec_kind and migrate content, "
                "then freeze the new canonical SPEC."
            )
        if meta.get("status") == "frozen":
            if meta.get("revision") == expected:
                binding_result = _write_active_binding(
                    workspace_id=workspace_id,
                    task_dir=task_dir,
                    meta=meta,
                    trusted_context=trusted_context,
                )
                canonical_result = _write_canonical_lifecycle_bindings(meta=meta, trusted_context=trusted_context)
                return json.dumps(_attach_start_option({
                    "status": "frozen", "operation_result": "spec_frozen", "spec_id": spec_id, "revision": expected,
                    "spec_hash": meta.get("spec_hash"),
                    "spec_sha256": meta.get("spec_sha256"),
                    "approval_status": "required" if meta.get("spec_kind") == "implementation" else "not_required",
                    "session_binding": binding_result,
                    "session_state_binding": canonical_result,
                    "session_binding_written": binding_result.get("status") == "written",
                    "semantic_start_ready": binding_result.get("status") == "written",
                    "next_action": "start_active_frozen_spec" if binding_result.get("status") == "written" else "stop_and_report_runtime_context_missing",
                    "retryable": False, "human_action_required": False,
                    "idempotent": True,
                }), sort_keys=True)
            raise WorkspaceError(
                "frozen SPEC revision mismatch: already frozen at revision=" +
                str(meta.get("revision")) + ", expected=" + str(expected) +
                ". This SPEC is already frozen at a different revision."
            )
        if meta.get("status") != "draft" or meta.get("revision") != expected:
            raise WorkspaceError(
                "freeze requires the exact current draft revision. "
                "status=" + str(meta.get("status")) + ", "
                "revision=" + str(meta.get("revision")) + ", "
                "expected=" + str(expected) + ". "
                "Ensure the SPEC is in draft status and expected_revision matches."
            )
        spec = dict(meta.get("spec", {}))
        if meta.get("source_traceability") is not None:
            validate_snapshot_for_freeze(workspace_id, meta.get("source_traceability"))
        if spec.get("workspace_context") is not None:
            from ._workspace_context import validate_workspace_context
            validate_workspace_context(spec["workspace_context"])
        spec["status"] = "frozen"; spec["frozen_at"] = utc_now_iso(); spec["updated_at"] = spec["frozen_at"]
        spec["spec_hash"] = canonical_hash(spec)
        try: validate_spec(spec, frozen=True)
        except ContractError as exc: raise WorkspaceError(str(exc)) from exc
        spec_md = json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        meta.update(spec)
        meta.update({"spec": spec, "spec_hash": spec["spec_hash"], "spec_sha256": compute_sha256(spec_md), "frozen_revision": spec["revision"]})
        atomic_write(task_dir / "SPEC.md", spec_md); write_json(task_dir / "meta.json", meta)
        binding_result = _write_active_binding(
            workspace_id=workspace_id,
            task_dir=task_dir,
            meta=meta,
            trusted_context=trusted_context,
        )
        canonical_result = _write_canonical_lifecycle_bindings(meta=meta, trusted_context=trusted_context)
        ready = binding_result.get("status") == "written"
        canonical_ready = canonical_result.get("status") == "written"
        ready = ready and canonical_ready
        return json.dumps(_attach_start_option({"status": "frozen", "operation_result": "spec_frozen", "spec_id": spec["spec_id"], "revision": spec["revision"], "spec_hash": spec["spec_hash"], "spec_sha256": meta["spec_sha256"], "approval_status": "required" if spec.get("spec_kind") == "implementation" else "not_required", "resolved_context": {"binding": "current_spec", "profile": spec.get("resolved_profile")}, "session_binding": binding_result, "session_state_binding": canonical_result, "session_binding_written": ready, "semantic_start_ready": ready, "next_action": "start_active_frozen_spec" if ready else "stop_and_report_control_plane_inconsistency", "retryable": False, "human_action_required": False}), sort_keys=True)
    finally:
        release_lock(lock)
