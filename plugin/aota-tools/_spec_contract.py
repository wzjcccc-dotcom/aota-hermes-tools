"""PCF-WI-09C canonical SPEC contract.

This is deliberately a small, stdlib-only contract core.  The tool modules
own storage and lifecycle; this module owns the closed data shape, fixed
routing, capability ceilings, canonical hashing, and legacy read adapter.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

SPEC_KINDS = ("implementation", "diagnosis", "review", "architecture", "stewardship")
ROUTING = {
    "implementation": "coder", "diagnosis": "debugger", "review": "reviewer",
    "architecture": "architect", "stewardship": "project-steward",
}
CAPABILITY_KEYS = (
    "source_read", "source_write", "terminal", "bounded_project_command", "web",
    "control_plane_write", "project_metadata_write", "codegraph_read",
    "codegraph_rebuild", "deploy", "restart", "recreate",
)
PROFILE_CEILINGS = {
    "coder": {"source_read": True, "source_write": True, "bounded_project_command": True, "codegraph_read": True},
    "debugger": {"source_read": True, "web": True, "codegraph_read": True},
    "reviewer": {"source_read": True, "codegraph_read": True},
    "architect": {"source_read": True, "codegraph_read": True},
    "project-steward": {"source_read": True, "project_metadata_write": True, "codegraph_read": True},
}
REF_TYPES = frozenset(("plan", "project_card", "relationship_brief", "unified_brief",
    "architect_review", "subject_spec", "subject_result", "review", "diagnosis",
    "steward_result", "user_approval", "external_research"))
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_MAX_TEXT, _MAX_LIST = 8000, 100

TOP_LEVEL = frozenset(("schema_version", "artifact_type", "spec_id", "project_id", "work_item_id",
    "spec_kind", "resolved_profile", "revision", "status", "created_at", "updated_at", "created_by",
    "objective", "summary", "context_refs", "related_artifacts", "acceptance_criteria", "constraints",
    "forbidden_actions", "expected_artifacts", "capability_contract", "payload", "supersedes_spec_id",
    "spec_hash", "frozen_at", "workspace_context", "subject_task_id"))

PAYLOAD_FIELDS = {
 "implementation": frozenset(("read_scope", "write_scope", "forbidden_scope", "implementation_requirements", "validation_commands", "validation_strategy", "runtime_actions", "human_checkpoints")),
 "diagnosis": frozenset(("symptom", "reproduction", "known_facts", "hypotheses", "evidence_required", "mutation_allowed")),
 "review": frozenset(("subject_spec_ref", "subject_result_ref", "review_dimensions", "required_evidence", "scope_review_required", "documentation_review_required")),
 "architecture": frozenset(("review_mode", "subject_plan_ref", "subject_spec_ref", "challenge_questions", "tradeoffs_required", "risk_dimensions")),
 "stewardship": frozenset(("operation", "project_context_questions", "allowed_project_artifacts", "docs_update_scope", "artifact_link_requests", "close_checks", "approved_content_refs")),
}

class ContractError(ValueError):
    pass

def _fail(message: str) -> None:
    raise ContractError(message)

def _id(name: str, value: Any, optional: bool = False) -> None:
    if value is None and optional: return
    if not isinstance(value, str) or not _ID.fullmatch(value): _fail(f"{name} must be a bounded logical id")

def _text(name: str, value: Any, required: bool = False) -> None:
    if value is None and not required: return
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > _MAX_TEXT: _fail(f"{name} must be bounded text" + (" and non-empty" if required else ""))

def _list(name: str, value: Any, required: bool = False) -> None:
    if not isinstance(value, list) or (required and not value) or len(value) > _MAX_LIST: _fail(f"{name} must be a bounded list" + (" and non-empty" if required else ""))
    for item in value:
        if not isinstance(item, (str, dict)) or (isinstance(item, str) and len(item) > _MAX_TEXT): _fail(f"{name} has an invalid item")

def validate_ref(ref: Any, *, required_type: str | None = None) -> None:
    if not isinstance(ref, dict): _fail("artifact ref must be an object")
    allowed = {"ref_type", "artifact_id", "artifact_path", "revision", "hash"}
    if set(ref) - allowed: _fail("artifact ref has unknown field")
    if ref.get("ref_type") not in REF_TYPES: _fail("artifact ref has invalid ref_type")
    if required_type and ref["ref_type"] != required_type: _fail(f"artifact ref must be {required_type}")
    _id("artifact_id", ref.get("artifact_id"))
    path = ref.get("artifact_path")
    if path is not None:
        if not isinstance(path, str) or path.startswith("/") or ".." in path.split("/") or "\\" in path: _fail("artifact_path must be a bounded relative path")
    if "revision" in ref and (not isinstance(ref["revision"], int) or ref["revision"] < 1): _fail("artifact ref revision invalid")
    if "hash" in ref and (not isinstance(ref["hash"], str) or not re.fullmatch(r"[0-9a-f]{64}", ref["hash"])): _fail("artifact ref hash invalid")

def normalize_legacy(meta: dict[str, Any]) -> dict[str, Any]:
    """Adapt safely readable old metadata; conflicts are never guessed."""
    meta = copy.deepcopy(meta)
    spec_kind, task_kind = meta.get("spec_kind"), meta.get("task_kind")
    if spec_kind and task_kind and spec_kind != task_kind: _fail("task_kind/spec_kind conflict")
    kind = spec_kind or task_kind
    if kind not in SPEC_KINDS: _fail("unknown or missing spec_kind")
    meta["spec_kind"] = kind
    meta.setdefault("task_kind", kind)  # deprecated compatibility alias
    meta.setdefault("resolved_profile", ROUTING[kind])
    if meta["resolved_profile"] != ROUTING[kind]: _fail("resolved_profile routing conflict")
    return meta

def canonical_hash(spec: dict[str, Any]) -> str:
    value = {k: v for k, v in spec.items() if k not in {"spec_hash", "frozen_at", "created_at", "updated_at", "status"}}
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

def _capabilities(kind: str, requested: Any) -> dict[str, bool]:
    if not isinstance(requested, dict) or set(requested) - set(CAPABILITY_KEYS): _fail("capability_contract has unknown field")
    out = {key: bool(requested.get(key, False)) for key in CAPABILITY_KEYS}
    if any(not isinstance(value, bool) for value in requested.values()): _fail("capability values must be booleans")
    ceiling = PROFILE_CEILINGS[ROUTING[kind]]
    for key, value in out.items():
        if value and not ceiling.get(key, False): _fail(f"capability exceeds profile maximum: {key}")
    if out["codegraph_rebuild"] or out["deploy"] or out["restart"] or out["recreate"]: _fail("runtime/codegraph mutation is not allowed in a SPEC")
    if kind != "implementation" and (out["source_write"] or out["terminal"]): _fail("readonly role cannot request source_write or terminal")
    return out

def _payload(kind: str, payload: Any, context_refs: list[Any], caps: dict[str, bool]) -> None:
    if not isinstance(payload, dict) or set(payload) - PAYLOAD_FIELDS[kind]: _fail("payload has unknown or cross-role field")
    for value in payload.values():
        if isinstance(value, list): _list("payload list", value)
        elif isinstance(value, str): _text("payload text", value)
    if kind == "implementation":
        _list("write_scope", payload.get("write_scope"), True); _list("forbidden_scope", payload.get("forbidden_scope"), True)
        _list("acceptance_criteria", []) if False else None
        if not payload.get("validation_commands") and not payload.get("validation_strategy"): _fail("implementation requires validation_commands or validation_strategy")
        actions = payload.get("runtime_actions", {})
        if not isinstance(actions, dict) or set(actions) - {"deploy_allowed", "restart_allowed", "recreate_allowed"} or any(actions.get(k, False) for k in actions): _fail("runtime_actions must remain false")
    elif kind == "diagnosis":
        _text("symptom", payload.get("symptom"), True); _list("evidence_required", payload.get("evidence_required"), True)
        if payload.get("mutation_allowed", False) is not False: _fail("diagnosis mutation_allowed must be false")
    elif kind == "review":
        validate_ref(payload.get("subject_spec_ref"), required_type="subject_spec"); validate_ref(payload.get("subject_result_ref"), required_type="subject_result")
        _list("review_dimensions", payload.get("review_dimensions"), True)
    elif kind == "architecture":
        if payload.get("review_mode") not in {"design_review", "spec_preflight"}: _fail("architecture review_mode invalid")
        if not payload.get("subject_plan_ref") and not payload.get("subject_spec_ref"): _fail("architecture requires a subject plan or spec ref")
        if payload.get("subject_plan_ref"): validate_ref(payload["subject_plan_ref"], required_type="plan")
        if payload.get("subject_spec_ref"): validate_ref(payload["subject_spec_ref"], required_type="subject_spec")
        _list("challenge_questions", payload.get("challenge_questions"), True)
    else:
        op = payload.get("operation")
        if op not in {"intake", "context_prepare", "relationship_resolve", "docs_update", "artifact_link", "close"}: _fail("stewardship operation invalid")
        if op in {"intake", "context_prepare", "relationship_resolve"} and not (payload.get("project_context_questions") or payload.get("allowed_project_artifacts")): _fail("stewardship context requires questions or inventory")
        if op == "docs_update" and (not payload.get("docs_update_scope") or not payload.get("approved_content_refs") or not caps["project_metadata_write"]): _fail("docs_update requires scope, approved refs, and metadata write")
        if op == "artifact_link" and not payload.get("artifact_link_requests"): _fail("artifact_link requires requests")
        if op == "close" and not payload.get("close_checks"): _fail("close requires checks")
    if kind == "review" and not any(isinstance(r, dict) and r.get("ref_type") == "subject_spec" for r in context_refs): _fail("review context_refs must include subject_spec")

def validate_spec(spec: dict[str, Any], *, frozen: bool = False) -> dict[str, Any]:
    if not isinstance(spec, dict) or set(spec) - TOP_LEVEL: _fail("SPEC has unknown top-level field")
    if spec.get("schema_version") != 1 or spec.get("artifact_type") != "spec": _fail("SPEC schema_version/artifact_type invalid")
    for field in ("spec_id", "project_id", "work_item_id"): _id(field, spec.get(field))
    _id("subject_task_id", spec.get("subject_task_id"), optional=True)
    kind = spec.get("spec_kind")
    if kind not in SPEC_KINDS: _fail("invalid spec_kind")
    if spec.get("resolved_profile") != ROUTING[kind]: _fail("resolved_profile must be derived fixed routing")
    if spec.get("status") not in {"draft", "frozen", "superseded"}: _fail("invalid SPEC status")
    if not isinstance(spec.get("revision"), int) or spec["revision"] < 1: _fail("SPEC revision invalid")
    if spec.get("created_by") != "task-main": _fail("SPEC created_by must be task-main")
    context = spec.get("workspace_context")
    if context is not None:
        required_context = {"schema_version", "workspace_id", "canonical_root", "workspace_registry_digest", "project_id", "project_root", "manifest_path", "project_manifest_digest", "project_registry_revision", "relationship", "source_type", "recommendation_id", "decision_id", "frozen_at", "frozen_by"}
        if not isinstance(context, dict) or set(context) != required_context or context.get("schema_version") != 1 or context.get("project_id") != spec.get("project_id") or context.get("frozen_by") != "task-main": _fail("workspace_context invalid")
    _text("objective", spec.get("objective"), True); _text("summary", spec.get("summary"), True)
    for field in ("context_refs", "related_artifacts", "acceptance_criteria", "constraints", "forbidden_actions", "expected_artifacts"):
        _list(field, spec.get(field, []), field == "acceptance_criteria")
    for ref in spec.get("context_refs", []): validate_ref(ref)
    caps = _capabilities(kind, spec.get("capability_contract", {}))
    _payload(kind, spec.get("payload"), spec.get("context_refs", []), caps)
    if kind == "review" and not spec.get("subject_task_id"):
        _fail("review requires subject_task_id")
    if kind == "implementation" and not spec["acceptance_criteria"]: _fail("implementation requires acceptance criteria")
    if frozen or spec.get("status") == "frozen":
        digest = canonical_hash(spec)
        if spec.get("spec_hash") and spec["spec_hash"] != digest: _fail("canonical spec_hash mismatch")
    return spec

def validate_task_binding(meta: dict[str, Any], expected_revision: int, expected_hash: str) -> dict[str, Any]:
    """Pure start/approval gate used by fixtures before any runner is invoked."""
    meta = normalize_legacy(meta)
    spec = meta.get("spec")
    if meta.get("contract_version") != 1 or meta.get("status") != "frozen": _fail("task requires a frozen canonical SPEC")
    validate_spec(spec, frozen=True)
    for key in ("spec_id", "spec_kind", "resolved_profile", "revision", "spec_hash", "project_id", "work_item_id", "subject_task_id"):
        if meta.get(key) != spec.get(key): _fail(f"task binding mismatch: {key}")
    if expected_revision != spec["revision"]: _fail("task binding revision mismatch")
    if expected_hash != spec["spec_hash"] or canonical_hash(spec) != spec["spec_hash"]: _fail("task binding hash mismatch")
    return meta

def common_card_fields(meta: dict[str, Any], role: str, card: dict[str, Any]) -> None:
    meta = normalize_legacy(meta)
    required = {"schema_version", "role", "task_id", "spec_id", "spec_revision", "spec_hash", "project_id", "work_item_id", "outcome", "verdict", "summary", "full_report_ref", "evidence_refs", "needs_full_report_review", "recommended_next_action"}
    if not required.issubset(card): _fail("card lacks common envelope fields")
    if role != ROUTING[meta["spec_kind"]] or card["role"] != role: _fail("card role/spec_kind mismatch")
    for key, meta_key in (("task_id", "task_id"), ("spec_id", "spec_id"), ("spec_revision", "revision"), ("spec_hash", "spec_hash"), ("project_id", "project_id"), ("work_item_id", "work_item_id")):
        if card[key] != meta.get(meta_key): _fail(f"card binding mismatch: {key}")
    context = meta.get("spec", {}).get("workspace_context")
    if context is not None and card.get("binding") != {"workspace_id": context["workspace_id"], "project_id": context["project_id"], "workspace_decision_id": context["decision_id"], "spec_id": meta["spec_id"], "spec_revision": meta["revision"], "spec_hash": meta["spec_hash"]}: _fail("card workspace binding mismatch")

def apply_common_card(meta: dict[str, Any], role: str, card: dict[str, Any], report_name: str) -> dict[str, Any]:
    """Add the WI-09C common envelope without removing role-owned fields."""
    if meta.get("contract_version") != 1:
        return card
    meta = normalize_legacy(meta)
    outcome = card.get("outcome", card.get("worker_outcome", "completed"))
    if outcome not in {"completed", "partial", "needs_input", "failed"}: outcome = "completed"
    verdict = card.get("verdict", "not_applicable")
    if verdict not in {"pass", "needs_fix", "blocked", "not_applicable"}: verdict = "not_applicable"
    card.update({"schema_version": 1, "role": role, "task_id": meta["task_id"], "spec_id": meta["spec_id"],
                 "spec_revision": meta["revision"], "spec_hash": meta["spec_hash"], "project_id": meta["project_id"],
                 "work_item_id": meta["work_item_id"], "outcome": outcome, "verdict": verdict,
                 "summary": card.get("summary", card.get("result_summary", "Worker report submitted.")),
                 "full_report_ref": report_name, "evidence_refs": card.get("evidence_refs", []),
                 "needs_full_report_review": bool(card.get("needs_full_report_review", False)),
                 "recommended_next_action": card.get("recommended_next_action", "task-main review")})
    # Worker cards are observations only.  Final scope counts/status are
    # produced by the completion finalizer and must never be inferred from
    # worker self-report fields.
    card["scope_observation"] = {
        "authority": "worker_observation",
        "finalizer_pending": True,
    }
    card["final_scope_compliance"] = "pending_finalizer"
    card.setdefault("worker_reported_scope_events", [])
    card.setdefault("worker_reported_denials", [])
    context = meta.get("spec", {}).get("workspace_context")
    if context is not None:
        card["binding"] = {"workspace_id": context["workspace_id"], "project_id": context["project_id"], "workspace_decision_id": context["decision_id"], "spec_id": meta["spec_id"], "spec_revision": meta["revision"], "spec_hash": meta["spec_hash"]}
    common_card_fields(meta, role, card)
    return card
