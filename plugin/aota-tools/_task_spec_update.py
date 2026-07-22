"""aota_task_spec_update — revise an existing draft AOTA task specification.

Requires optimistic revision matching. Does not approve or start execution.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ._task_spec_common import (
    MUTABLE_SPEC_FIELDS,
    TASK_KIND_PROFILE_HINT,
    HUMAN_CHECKPOINT_POLICY_MAP,
    SOURCE_MUTATION_POLICY_MAP,
    STATUS_DRAFT,
    STATUS_NEEDS_INPUT,
    utc_now_iso,
    compute_sha256,
    validate_scope_expressions,
    validate_semantic_rules,
    check_scope_overlap,
    validate_field_limits,
    render_spec_md,
    build_spec_dict,
    write_json,
    atomic_write,
    acquire_lock,
    release_lock,
    get_task_dir,
    load_meta,
    load_spec_md,
    WorkspaceError,
    SPEC_SCHEMA_VERSION_CURRENT,
    validate_process_path,
    validate_validation_tier,
    validate_human_checkpoints,
    validate_role_contract_for_task,
    role_contract_errors_to_message,
    apply_defaults,
)
from ._spec_traceability import build_trusted_snapshot, validate_snapshot_for_freeze, validate_traceability_input
from ._spec_contract import ContractError, canonical_hash, validate_spec

TOOL_NAME = "aota_task_spec_update"
TOOLSET_NAME = "aota_task_spec"

_IMMUTABLE_FIELDS = ("task_id", "spec_id", "workspace_id", "task_kind", "spec_kind", "created_at", "created_by", "profile_hint", "spec_hash", "spec_sha256", "frozen_at", "frozen_revision", "subject_task_id", "parent_task_id", "architecture_mode")
_MUTABLE_LIST = list(MUTABLE_SPEC_FIELDS)

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Use to revise an existing draft AOTA task specification. "
        "Requires optimistic revision matching. "
        "Does not approve or start execution.\n\n"
        "PATCH SHAPE: Pass only the fields you want to change as top-level "
        "arguments. Omitted fields are left unchanged. Set a field to null "
        "to clear it (where clearing is allowed). Unknown fields are rejected.\n\n"
        "ALLOWED MUTABLE FIELDS (legacy path): "
        + ", ".join(_MUTABLE_LIST) + "\n"
        "IMMUTABLE FIELDS (cannot change via update): "
        + ", ".join(sorted(_IMMUTABLE_FIELDS)) + "\n\n"
        "CANONICAL PATH: Use spec_id, expected_revision, and patch object. "
        "The patch is a closed dict of allowed canonical fields: objective, "
        "summary, context_refs, related_artifacts, acceptance_criteria, "
        "constraints, forbidden_actions, expected_artifacts, "
        "capability_contract, payload, supersedes_spec_id.\n\n"
        "FROZEN SPEC: Once a SPEC is frozen (status=frozen), it is immutable. "
        "Updates to a frozen SPEC are rejected. Create a successor SPEC instead.\n\n"
        "LEGACY SPEC: A legacy SPEC (no contract_version=1 in meta.json) is "
        "read-compatible but not mutable through the canonical update contract. "
        "Detected by: missing contract_version or contract_version != 1.\n\n"
        "NULL SEMANTICS: Passing null for a mutable field clears it (resets to "
        "empty/default). Omitted fields are left unchanged.\n\n"
        "UNKNOWN FIELDS: Fields not in the allowed mutable set are rejected "
        "with a list of accepted field names.\n\n"
        "VALID EXAMPLE (legacy path):\n"
        "  {workspace_id: my-ws, task_id: pt_..., expected_revision: 3, "
        "title: Updated title, goal: Updated goal}\n\n"
        "VALID EXAMPLE (canonical path):\n"
        "  {workspace_id: my-ws, spec_id: pt_..., expected_revision: 3, "
        "patch: {objective: Updated objective}}\n\n"
        "Role-specific contract (role_contract object):\n"
        "- implementation: required_changes (list[str], required), change_budget (dict with max_changed_files, allow_create, allow_delete, allow_move, allow_dependency_change), behavioral_invariants, allowed_validation_targets, forbidden_operations, checkpoint_conditions, compatibility_requirements\n"
        "- diagnosis: observed_symptoms (list[str], required), diagnostic_questions (list[str], required), reproduction_context, suspected_components, initial_hypotheses, evidence_plan, mutation_policy (readonly|isolated_reproduction_only), confidence_expectation (exploratory|probable|confirmed_required)\n"
        "- review: artifacts_under_review (list[str], required), review_dimensions (list[str], required), acceptance_mapping_required (bool), verdict_rules, inconclusive_conditions, independence_requirements\n"
        "- architecture: review_questions (list[str], required), gate_criteria (list[str], required), constraints, risk_focus + design_review: problem_statement (required), proposed_design (required), alternatives_considered, blast_radius, rollback_strategy, compatibility_strategy, unresolved_decisions, validation_strategy + spec_preflight: preflight_dimensions (required)\n"
        "- stewardship (temporary WI-09B compatibility): project_id, allowed_project_artifacts, operation, forbidden_actions; write_scope must be empty\n"
        "Forbidden fields from other task kinds will be rejected."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {
                "type": "string",
                "description": "Registered workspace identifier",
            },
            "task_id": {
                "type": "string",
                "description": "Existing task ID to update",
            },
            "expected_revision": {
                "type": "integer",
                "description": "Expected current revision for optimistic locking",
            },
            "title": {
                "type": "string",
                "description": "Updated title (max 200 characters). Mutable.",
            },
            "goal": {
                "type": "string",
                "description": "Updated goal (max 8000 characters). Mutable.",
            },
            "risk_level": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Updated risk level. Mutable.",
            },
            "known_inputs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Updated known inputs. Mutable.",
            },
            "read_scope": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Updated read scope globs. Mutable.",
            },
            "write_scope": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Updated write scope globs. Mutable.",
            },
            "forbidden_scope": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Updated forbidden scope globs. Mutable.",
            },
            "acceptance_criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Updated acceptance criteria. Mutable.",
            },
            "validation_policy": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Updated validation policy items. Mutable.",
            },
            "stop_conditions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Updated stop conditions. Mutable.",
            },
            "evidence_required": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Updated evidence required items. Mutable.",
            },
            "process_path": {
                "type": "string",
                "enum": ["fast", "standard", "deep"],
                "description": "Process path: fast (low-risk, local, reversible), standard (multi-file, medium risk), deep (control-plane, security, data, cross-service, high-risk). Default: standard. Mutable.",
            },
            "validation_tier": {
                "type": "integer",
                "enum": [0, 1, 2, 3, 4],
                "description": "Validation tier: 0 (Static), 1 (Local Smoke), 2 (Integration), 3 (Runtime), 4 (Live E2E). Default: 0. Mutable.",
            },
            "human_checkpoints": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Human checkpoint triggers (e.g. deploy, reload, restart, docker, host_write, runtime_write, migration, destructive_file_operation, secret_change, live_worker). Mutable.",
            },
            "role_contract": {
                "type": "object",
                "description": "Role-specific contract fields. Required fields and allowed fields depend on task_kind. See tool description for per-kind schema. Forbidden fields from other task kinds will be rejected. Mutable.",
            },
            "traceability": {
                "type": "object",
                "description": "Bounded draft operation: set_plan_traceability, clear_plan_traceability, or refresh_plan_traceability. Trusted revision/SHA fields are never accepted.",
            },
            "freeze": {
                "type": "boolean",
                "description": "Freeze the exact current draft revision after revalidating its verified Plan reference. Must not be combined with another update.",
            },
        },
        "required": ["workspace_id", "task_id", "expected_revision"],
        "additionalProperties": False,
    },
}

SCHEMA["parameters"]["properties"].update({
    "spec_id": {"type": "string", "description": "Canonical SPEC ID (for canonical update path). Replaces task_id for WI-09C SPECs."},
    "patch": {"type": "object", "description": "Closed canonical draft patch. Allowed keys: objective, summary, context_refs, related_artifacts, acceptance_criteria, constraints, forbidden_actions, expected_artifacts, capability_contract, payload, supersedes_spec_id. Immutable keys (schema_version, artifact_type, spec_id, project_id, work_item_id, created_at, created_by, resolved_profile, spec_hash, status, revision, frozen_at) are rejected with error."},
})
SCHEMA["description"] += " Canonical updates require spec_id, expected_revision, and closed patch."
SCHEMA["parameters"]["required"] = ["workspace_id"]


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handle(args: dict, **_kwargs) -> str:
    try:
        return _do_update(args)
    except WorkspaceError as e:
        return json.dumps(
            {"status": "rejected", "error": str(e)}, sort_keys=True
        )
    except Exception as e:
        return json.dumps(
            {"status": "failed", "error": str(e)}, sort_keys=True
        )


def _do_update(args: dict) -> str:
    if "patch" in args or "spec_id" in args:
        return _do_update_contract(args)
    # ------------------------------------------------------------------
    # Extract params
    # ------------------------------------------------------------------
    workspace_id: str = args.get("workspace_id", "")
    task_id: str = args.get("task_id", "")
    expected_revision: int = args.get("expected_revision", 0)
    process_path: str | None = args.get("process_path")
    validation_tier: int | None = args.get("validation_tier")
    human_checkpoints: list[str] | None = args.get("human_checkpoints")
    role_contract: dict | None = args.get("role_contract")
    traceability_request: dict | None = args.get("traceability")
    freeze_requested = args.get("freeze", False)
    if not isinstance(freeze_requested, bool):
        raise WorkspaceError("SPEC_TRACEABILITY_PAYLOAD_INVALID")

    # Reject unknown fields
    known_fields = set(SCHEMA["parameters"]["properties"].keys())
    unknown = set(args) - known_fields
    if unknown:
        raise WorkspaceError(f"unknown_fields: {sorted(unknown)} not in accepted fields: {sorted(known_fields)}. Only known mutable fields may be passed.")

    # ------------------------------------------------------------------
    # 1. Resolve workspace
    # ------------------------------------------------------------------
    from ._workspace import resolve_workspace

    workspace_root = resolve_workspace(workspace_id)

    task_dir = get_task_dir(workspace_id, task_id)

    if not task_dir.is_dir():
        raise WorkspaceError(f"task '{task_id}' not found in workspace '{workspace_id}'")

    # ------------------------------------------------------------------
    # 2. Load existing meta.json + SPEC.md
    # ------------------------------------------------------------------
    existing_meta = load_meta(task_dir)
    existing_spec_md = load_spec_md(task_dir)
    architecture_mode: str | None = existing_meta.get("architecture_mode")

    # ------------------------------------------------------------------
    # 3. Status gate: regular changes are draft/needs_input; freeze is draft only.
    # ------------------------------------------------------------------
    current_status = existing_meta.get("status", "")
    update_allowed = current_status in (STATUS_DRAFT, STATUS_NEEDS_INPUT)
    if freeze_requested:
        disallowed = set(args) - {"workspace_id", "task_id", "expected_revision", "freeze"}
        if disallowed:
            raise WorkspaceError("SPEC_TRACEABILITY_PAYLOAD_INVALID")
        if current_status != STATUS_DRAFT or existing_meta.get("frozen_revision") is not None:
            raise WorkspaceError("SPEC_TRACEABILITY_FROZEN")
    elif existing_meta.get("frozen_revision") is not None:
        raise WorkspaceError("SPEC_TRACEABILITY_FROZEN: this SPEC has been frozen (frozen_revision=" + str(existing_meta.get("frozen_revision")) + "). Frozen SPECs are immutable; create a successor SPEC with supersedes_spec_id to continue work.")
    elif not update_allowed:
        raise WorkspaceError(
            f"task_not_updatable: task '{task_id}' has status "
            f"'{current_status}', expected '{STATUS_DRAFT}' or '{STATUS_NEEDS_INPUT}'"
        )

    # ------------------------------------------------------------------
    # 4. Integrity check: compute current SPEC.md SHA-256
    # ------------------------------------------------------------------
    current_hash = compute_sha256(existing_spec_md)
    stored_hash = existing_meta.get("spec_sha256", "")
    if current_hash != stored_hash:
        raise WorkspaceError(
            f"artifact_integrity_mismatch: SPEC.md hash mismatch "
            f"(stored={stored_hash}, actual={current_hash})"
        )

    # ------------------------------------------------------------------
    # 5. Revision check
    # ------------------------------------------------------------------
    current_revision: int = existing_meta.get("revision", 0)
    if expected_revision != current_revision:
        raise WorkspaceError(
            f"revision_conflict: expected revision {expected_revision}, "
            f"actual revision {current_revision}. Re-read the current SPEC revision and retry."
        )

    # ------------------------------------------------------------------
    # 6. Acquire lock
    # ------------------------------------------------------------------
    lock_fd = acquire_lock(workspace_id, task_id, timeout=5.0)

    try:
        # ------------------------------------------------------------------
        # 7. Inside lock: re-load + re-validate
        # ------------------------------------------------------------------
        meta_after_lock = load_meta(task_dir)
        spec_md_after_lock = load_spec_md(task_dir)

        # Re-check integrity and revision inside lock
        hash_after_lock = compute_sha256(spec_md_after_lock)
        if hash_after_lock != meta_after_lock.get("spec_sha256", ""):
            raise WorkspaceError(
                "artifact_integrity_mismatch: SPEC.md changed between "
                "initial read and lock acquisition"
            )
        rev_after_lock: int = meta_after_lock.get("revision", 0)
        if rev_after_lock != current_revision:
            raise WorkspaceError(
                f"revision_conflict: revision changed from {current_revision} "
                f"to {rev_after_lock} during lock acquisition"
            )

        if freeze_requested:
            return _freeze_locked(
                workspace_id=workspace_id,
                task_id=task_id,
                task_dir=task_dir,
                meta=meta_after_lock,
                current_revision=current_revision,
            )

        # ------------------------------------------------------------------
        # 8. Build patched spec dict
        # ------------------------------------------------------------------
        existing_spec = meta_after_lock["spec"]
        patched_spec = dict(existing_spec)

        # Also track top-level meta changes
        meta_updates: dict = {}
        if "risk_level" in args and args["risk_level"] is not None:
            meta_updates["risk_level"] = args["risk_level"]

        change_detected = False
        for key in MUTABLE_SPEC_FIELDS:
            if key in args and args[key] is not None:
                if patched_spec.get(key) != args[key]:
                    patched_spec[key] = args[key]
                    change_detected = True

        target_schema_version = existing_meta.get("schema_version", SPEC_SCHEMA_VERSION_CURRENT)
        if traceability_request is not None:
            if not isinstance(traceability_request, dict):
                raise WorkspaceError("SPEC_TRACEABILITY_PAYLOAD_INVALID")
            operation = traceability_request.get("operation")
            existing_traceability = patched_spec.get("source_traceability")
            if operation == "set_plan_traceability":
                payload = dict(traceability_request)
                payload.pop("operation", None)
                payload["mode"] = "plan_linked"
                parsed = validate_traceability_input(payload, required=True)
                assert parsed is not None
                patched_spec["source_traceability"] = build_trusted_snapshot(workspace_id, parsed)
            elif operation == "clear_plan_traceability" and set(traceability_request) == {"operation"}:
                patched_spec["source_traceability"] = None
            elif operation == "refresh_plan_traceability" and set(traceability_request) == {"operation"}:
                if existing_traceability is None:
                    raise WorkspaceError("SPEC_TRACEABILITY_REQUIRED_FIELDS_MISSING")
                parsed = validate_traceability_input({
                    "mode": "plan_linked", "plan_id": existing_traceability.get("plan_id"),
                    "milestone_id": existing_traceability.get("milestone_id"),
                    "work_item_id": existing_traceability.get("work_item_id"),
                    "architect_review_id": existing_traceability.get("architect_review_id"),
                }, required=True)
                assert parsed is not None
                patched_spec["source_traceability"] = build_trusted_snapshot(workspace_id, parsed)
            else:
                raise WorkspaceError("SPEC_TRACEABILITY_PAYLOAD_INVALID")
            target_schema_version = SPEC_SCHEMA_VERSION_CURRENT
            change_detected = True

        # Also apply risk_level to patched_spec for re-validation
        # (risk_level appears in SPEC.md Identity section but not in spec dict)
        # Actually, risk_level is in meta only, not in spec dict.
        # It does appear in SPEC.md though, so we need it for rendering.
        new_risk_level = meta_updates.get("risk_level") or existing_meta.get("risk_level", "low")

        # Check that at least one mutable field changed
        if not change_detected and "risk_level" not in meta_updates:
            raise WorkspaceError(
                f"no_change: no mutable field was actually changed. "
                f"Accepted mutable fields: " + ", ".join(_MUTABLE_LIST)
            )

        # ------------------------------------------------------------------
        # 9. Validate scope expressions on changed scope fields
        # ------------------------------------------------------------------
        scope_fields = {
            "read_scope": patched_spec.get("read_scope", []),
            "write_scope": patched_spec.get("write_scope", []),
            "forbidden_scope": patched_spec.get("forbidden_scope", []),
        }
        for key, items in scope_fields.items():
            err = validate_scope_expressions(items)
            if err:
                raise WorkspaceError(f"{key}: {err}")

        # ------------------------------------------------------------------
        # 10. Full semantic re-validation
        # ------------------------------------------------------------------
        task_kind: str = existing_meta.get("task_kind", "")
        new_write_scope = patched_spec.get("write_scope", [])
        # subject_task_id cannot change via update, use existing
        subject_task_id = existing_meta.get("subject_task_id")

        err = validate_semantic_rules(task_kind, new_write_scope, subject_task_id, architecture_mode)
        if err:
            raise WorkspaceError(err)

        # Scope overlap check
        new_forbidden_scope = patched_spec.get("forbidden_scope", [])
        err = check_scope_overlap(new_write_scope, new_forbidden_scope)
        if err:
            raise WorkspaceError(err)

        # ------------------------------------------------------------------
        # 11. Validate field limits
        # ------------------------------------------------------------------
        err = validate_field_limits(
            title=patched_spec.get("title", ""),
            goal=patched_spec.get("goal", ""),
            known_inputs=patched_spec.get("known_inputs", []),
            read_scope=patched_spec.get("read_scope", []),
            write_scope=new_write_scope,
            forbidden_scope=new_forbidden_scope,
            acceptance_criteria=patched_spec.get("acceptance_criteria", []),
            validation_policy=patched_spec.get("validation_policy", []),
            stop_conditions=patched_spec.get("stop_conditions", []),
            evidence_required=patched_spec.get("evidence_required", []),
        )
        if err:
            raise WorkspaceError(err)

        # ------------------------------------------------------------------
        # 11a. P11-K: Validate new shared fields if provided
        # ------------------------------------------------------------------
        if process_path is not None:
            err = validate_process_path(process_path)
            if err:
                raise WorkspaceError(err)
            patched_spec["process_path"] = process_path
            change_detected = True

        if validation_tier is not None:
            err = validate_validation_tier(validation_tier)
            if err:
                raise WorkspaceError(err)
            patched_spec["validation_tier"] = validation_tier
            change_detected = True

        if human_checkpoints is not None:
            err = validate_human_checkpoints(human_checkpoints)
            if err:
                raise WorkspaceError(err)
            patched_spec["human_checkpoints"] = human_checkpoints
            change_detected = True

        if role_contract is not None:
            # Validate role contract
            role_errors = validate_role_contract_for_task(task_kind, role_contract, architecture_mode)
            if role_errors:
                msg = role_contract_errors_to_message(role_errors)
                raise WorkspaceError(f"role_contract_validation_failed: {msg}")
            # Apply defaults
            role_contract = apply_defaults(task_kind, role_contract, architecture_mode)
            patched_spec["role_contract"] = role_contract
            change_detected = True

        # Check that at least one mutable field changed (after P11-K additions)
        if not change_detected and "risk_level" not in meta_updates:
            raise WorkspaceError(
                f"no_change: no mutable field was actually changed. "
                f"Accepted mutable fields: " + ", ".join(_MUTABLE_LIST)
            )

        # ------------------------------------------------------------------
        # 12. Render new SPEC.md
        # ------------------------------------------------------------------
        profile_hint = TASK_KIND_PROFILE_HINT.get(task_kind, "coder")
        new_revision = current_revision + 1
        new_status = STATUS_DRAFT
        now = utc_now_iso()
        created_at = existing_meta.get("created_at", now)

        new_spec_md = render_spec_md(
            task_id=task_id,
            workspace_id=workspace_id,
            task_kind=task_kind,
            profile_hint=profile_hint,
            risk_level=new_risk_level,
            revision=new_revision,
            status=new_status,
            goal=patched_spec.get("goal", ""),
            known_inputs=patched_spec.get("known_inputs", []),
            read_scope=patched_spec.get("read_scope", []),
            write_scope=new_write_scope,
            forbidden_scope=new_forbidden_scope,
            acceptance_criteria=patched_spec.get("acceptance_criteria", []),
            validation_policy=patched_spec.get("validation_policy", []),
            stop_conditions=patched_spec.get("stop_conditions", []),
            evidence_required=patched_spec.get("evidence_required", []),
            subject_task_id=subject_task_id,
            parent_task_id=existing_meta.get("parent_task_id"),
            architecture_mode=architecture_mode,
            process_path=patched_spec.get("process_path"),
            validation_tier=patched_spec.get("validation_tier"),
            human_checkpoints=patched_spec.get("human_checkpoints", []),
            role_contract=patched_spec.get("role_contract", {}),
            source_traceability=patched_spec.get("source_traceability"),
            spec_schema_version=target_schema_version,
        )

        # Size check
        spec_md_bytes = len(new_spec_md.encode("utf-8"))
        if spec_md_bytes > 65536:
            raise WorkspaceError(
                f"SPEC.md exceeds 65536 bytes (got {spec_md_bytes})"
            )

        # ------------------------------------------------------------------
        # 13. Compute new hash
        # ------------------------------------------------------------------
        new_spec_sha256 = compute_sha256(new_spec_md)

        # ------------------------------------------------------------------
        # 14. Build updated meta
        # ------------------------------------------------------------------
        human_checkpoint_policy = HUMAN_CHECKPOINT_POLICY_MAP.get(
            task_kind, "required_before_start"
        )
        source_mutation_policy = SOURCE_MUTATION_POLICY_MAP.get(
            task_kind, "forbidden"
        )

        new_meta = dict(meta_after_lock)
        new_meta["revision"] = new_revision
        new_meta["updated_at"] = now
        new_meta["risk_level"] = new_risk_level
        new_meta["spec_sha256"] = new_spec_sha256
        new_meta["spec"] = patched_spec
        new_meta["schema_version"] = target_schema_version
        new_meta["status"] = new_status

        # Preserve created_at
        new_meta["created_at"] = created_at

        # ------------------------------------------------------------------
        # 14a. P8-C: If transitioning from needs_input → draft,
        # clear needs_input authoritative fields and invalidate old approval
        # ------------------------------------------------------------------
        if current_status == STATUS_NEEDS_INPUT:
            # Preserve execution as history but clear active fields
            exec_copy = dict(new_meta.get("execution", {}))
            exec_copy.pop("worker_outcome", None)
            exec_copy.pop("needs_input_reason", None)
            exec_copy.pop("needs_input_at", None)
            new_meta["execution"] = exec_copy if exec_copy else None
            # Remove stale APPROVAL.json
            approval_path = task_dir / "APPROVAL.json"
            try:
                approval_path.unlink()
            except OSError:
                pass

        # ------------------------------------------------------------------
        # 15. Atomic write
        # ------------------------------------------------------------------
        spec_path = task_dir / "SPEC.md"
        meta_path = task_dir / "meta.json"
        atomic_write(spec_path, new_spec_md)
        write_json(meta_path, new_meta)

    finally:
        release_lock(lock_fd)

    # ------------------------------------------------------------------
    # 16. Return compact output
    # ------------------------------------------------------------------
    return json.dumps(
        {
            "status": "updated",
            "task_id": task_id,
            "workspace_id": workspace_id,
            "task_kind": task_kind,
            "profile_hint": profile_hint,
            "risk_level": new_risk_level,
            "revision": new_revision,
            "spec_path": str(spec_path),
            "meta_path": str(meta_path),
            "spec_sha256": new_spec_sha256,
            "human_checkpoint_policy": human_checkpoint_policy,
            "approval_status": "required" if task_kind == "implementation" else "not_required",
            "spec_schema_version": target_schema_version,
            "error": None,
        },
        sort_keys=True,
    )


def _do_update_contract(args: dict) -> str:
    """Update one WI-09C draft with optimistic revision concurrency."""
    workspace_id = args.get("workspace_id", "")
    task_id = args.get("spec_id") or args.get("task_id", "")
    expected = args.get("expected_revision")
    patch = args.get("patch")
    if not isinstance(expected, int) or not isinstance(patch, dict):
        raise WorkspaceError("spec_id, expected_revision (int), and patch (object) are required for canonical update")
    task_dir = get_task_dir(workspace_id, task_id)
    if not task_dir.is_dir():
        raise WorkspaceError("spec not found: " + task_id)
    lock_fd = acquire_lock(workspace_id, task_id, timeout=5.0)
    try:
        meta = load_meta(task_dir)
        if meta.get("contract_version") != 1:
            raise WorkspaceError("legacy SPEC is read-compatible but not mutable through the WI-09C update contract. Detected contract_version=" + str(meta.get("contract_version")) + "; canonical updates require contract_version=1. Create a new canonical SPEC instead.")
        if meta.get("status") != "draft":
            raise WorkspaceError("frozen SPEC is immutable; create a successor SPEC with supersedes_spec_id. Current status=" + str(meta.get("status")))
        if meta.get("revision") != expected:
            raise WorkspaceError("revision_conflict: expected=" + str(expected) + ", actual=" + str(meta.get("revision")) + ". Re-read the current SPEC revision and retry.")
        immutable = {"schema_version", "artifact_type", "spec_id", "project_id", "work_item_id", "created_at", "created_by", "resolved_profile", "spec_hash", "status", "revision", "frozen_at"}
        if set(patch) & immutable:
            blocked = sorted(set(patch) & immutable)
            raise WorkspaceError("patch attempts to mutate immutable field(s): " + ", ".join(blocked) + ". Immutable fields: " + ", ".join(sorted(immutable)))
        allowed = {"objective", "summary", "context_refs", "related_artifacts", "acceptance_criteria", "constraints", "forbidden_actions", "expected_artifacts", "capability_contract", "payload", "supersedes_spec_id"}
        if set(patch) - allowed:
            unknown = sorted(set(patch) - allowed)
            raise WorkspaceError("patch has unknown field(s): " + ", ".join(unknown) + ". Allowed fields: " + ", ".join(sorted(allowed)))
        spec = dict(meta["spec"])
        spec.update(patch)
        spec["revision"] = expected + 1
        spec["updated_at"] = utc_now_iso()
        spec["spec_hash"] = None
        try:
            validate_spec(spec)
        except ContractError as exc:
            raise WorkspaceError(str(exc)) from exc
        spec_md = json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        meta.update(spec)
        meta.update({"spec": spec, "task_kind": spec["spec_kind"], "profile_hint": spec["resolved_profile"],
                     "spec_sha256": compute_sha256(spec_md), "frozen_revision": None})
        atomic_write(task_dir / "SPEC.md", spec_md)
        write_json(task_dir / "meta.json", meta)
        return json.dumps({"status": "updated", "spec_id": task_id, "revision": spec["revision"], "spec_hash": None}, sort_keys=True)
    finally:
        release_lock(lock_fd)


def _freeze_locked(*, workspace_id: str, task_id: str, task_dir: Path, meta: dict, current_revision: int) -> str:
    """Freeze one exact draft revision; linked references are revalidated first."""
    spec = dict(meta["spec"])
    source_traceability = spec.get("source_traceability")
    if source_traceability is not None:
        validate_snapshot_for_freeze(workspace_id, source_traceability)
    task_kind = meta["task_kind"]
    new_revision = current_revision + 1
    now = utc_now_iso()
    schema_version = meta.get("schema_version", SPEC_SCHEMA_VERSION_CURRENT)
    frozen_spec_md = render_spec_md(
        task_id=task_id, workspace_id=workspace_id, task_kind=task_kind,
        profile_hint=TASK_KIND_PROFILE_HINT[task_kind], risk_level=meta["risk_level"],
        revision=new_revision, status="frozen", goal=spec["goal"],
        known_inputs=spec["known_inputs"], read_scope=spec["read_scope"],
        write_scope=spec["write_scope"], forbidden_scope=spec["forbidden_scope"],
        acceptance_criteria=spec["acceptance_criteria"], validation_policy=spec["validation_policy"],
        stop_conditions=spec["stop_conditions"], evidence_required=spec["evidence_required"],
        subject_task_id=meta.get("subject_task_id"), parent_task_id=meta.get("parent_task_id"),
        architecture_mode=meta.get("architecture_mode"), subject_spec_revision=meta.get("subject_spec_revision"),
        subject_spec_sha256=meta.get("subject_spec_sha256"), process_path=spec.get("process_path"),
        validation_tier=spec.get("validation_tier"), human_checkpoints=spec.get("human_checkpoints", []),
        role_contract=spec.get("role_contract", {}), source_traceability=source_traceability,
        spec_schema_version=schema_version,
    )
    if len(frozen_spec_md.encode("utf-8")) > 65536:
        raise WorkspaceError("SPEC.md exceeds 65536 bytes")
    frozen_sha256 = compute_sha256(frozen_spec_md)
    frozen_meta = dict(meta)
    frozen_meta.update({"revision": new_revision, "updated_at": now,
                        "frozen_at": now, "frozen_revision": new_revision, "spec_sha256": frozen_sha256})
    atomic_write(task_dir / "SPEC.md", frozen_spec_md)
    write_json(task_dir / "meta.json", frozen_meta)
    return json.dumps({"status": "frozen", "task_id": task_id, "workspace_id": workspace_id,
                       "revision": new_revision, "spec_sha256": frozen_sha256,
                       "approval_status": "required" if meta.get("task_kind") == "implementation" else "not_required",
                       "spec_schema_version": schema_version, "error": None}, sort_keys=True)
