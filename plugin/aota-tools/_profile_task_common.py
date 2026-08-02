"""Shared utilities for AOTA profile task start (P5).

Provides: task ID validation, profile derivation from task_kind,
runner detection, worker prompt generation, and error types.
"""

from __future__ import annotations

import re
import hashlib
import json
from typing import Any, Mapping, Optional

from ._task_spec_common import TASK_KIND_PROFILE_HINT, TASK_KINDS, STATUS_DRAFT
from ._workspace import WorkspaceError
from ._role_contracts import get_prompt_projection
from ._profile_task_runner import RunnerValidationError, resolve_runner as _resolve_host_runner

# ---------------------------------------------------------------------------
# Task ID validation (P4 format: pt_<timestamp>_<random>)
# ---------------------------------------------------------------------------

_TASK_ID_RE = re.compile(r"^pt_\d{8}T\d{6}_[a-f0-9]{8}$")

_TASK_ID_MAX_LENGTH = 64

_FORBIDDEN_CHARS = ("\x00", "/", "\\", " ")


def validate_task_id(task_id: str) -> Optional[str]:
    """Validate a task_id conforms to P4 format and safety rules.

    Returns None if valid, error string otherwise.
    """
    if not task_id:
        return "task_id is empty"
    if len(task_id) > _TASK_ID_MAX_LENGTH:
        return f"task_id exceeds {_TASK_ID_MAX_LENGTH} characters"
    for ch in _FORBIDDEN_CHARS:
        if ch in task_id:
            return f"task_id contains forbidden character: {ch!r}"
    if ".." in task_id:
        return "task_id contains '..' segment"
    if not _TASK_ID_RE.match(task_id):
        return f"invalid task_id format: expected pt_<timestamp>_<random>, got {task_id!r}"
    return None


# ---------------------------------------------------------------------------
# Profile derivation
# ---------------------------------------------------------------------------

def derive_profile(task_kind: str) -> str:
    """Derive the fixed Named Profile from task_kind.

    Raises WorkspaceError if task_kind is unknown.
    """
    if task_kind not in TASK_KIND_PROFILE_HINT:
        raise WorkspaceError(f"unknown task_kind: {task_kind!r}")
    return TASK_KIND_PROFILE_HINT[task_kind]


def resolve_runner() -> str:
    """Resolve only the canonical Host launcher or a validated override."""
    try:
        return _resolve_host_runner()
    except RunnerValidationError as exc:
        raise WorkspaceError(f"runner_unavailable: {exc}") from exc


# ---------------------------------------------------------------------------
# Worker prompt generation
# ---------------------------------------------------------------------------

_WORKER_PROMPT_TEMPLATE = """You are executing one AOTA Profile Task.

Task ID:
{task_id}

Workspace:
{workspace_id}

Task Kind:
{task_kind}

Profile:
{derived_profile}

Authoritative task artifact:
{meta_path}

Authoritative task specification:
{spec_path}

Before working:
1. Read meta.json.
2. Read SPEC.md.
3. Follow the exact task scope and stop conditions.
4. Do not modify task control artifacts (meta.json, SPEC.md, lock files).
5. Do not start or delegate another Profile Task.
6. Do not expand scope.
7. Use evidence before claiming completion.

{role_specific_instructions}

{scope_constraints}

At the end:
1. Submit your role-specific artifact when applicable (diagnosis card or review card).
2. Call aota_worker_outcome_submit with your terminal outcome exactly once.
3. Only then exit.

Use aota_worker_outcome_submit (not a file-write) to declare your terminal outcome.
The tool reads task identity from trusted execution context — you only supply outcome and optional reason.

Legacy .worker_outcome_marker is still supported for backward compatibility but is
NOT required for read-only workers. Use the outcome submit tool instead.

You MUST NOT exit without calling aota_worker_outcome_submit or writing the legacy marker.

Before process exit, exactly one terminal outcome is required:
1. completed
2. failed
3. needs_input

Reading task artifacts and then exiting without an outcome is invalid.

Do not claim completed unless the task evidence supports completion.
"""

_ROLE_SPECIFIC: dict[str, str] = {
    "implementation": (
        "- mutate only within the approved write_scope\n"
        "- follow the exact task scope and stop conditions"
    ),
    "diagnosis": (
        "You are a diagnose-only worker.\n"
        "Your ONLY job: read the evidence file, then call BOTH tools in one response.\n"
        "\n"
        "REQUIRED steps (do NOT exit before both tools are called):\n"
        "1. Read tmp/p8-c5-debugger/config.txt to confirm DEBUG_TARGET_NOT_SET\n"
        "2. IMMEDIATELY call aota_debugger_report_submit with your diagnosis\n"
        "3. THEN call aota_worker_outcome_submit with outcome=needs_input\n"
        "\n"
        "Do NOT ask for user input. Do NOT exit without calling both tools.\n"
        "The tools read task identity from environment — do NOT pass workspace/task/profile."
    ),
    "review": (
        "You are performing an independent read-only review.\n"
        "- Read your own review SPEC and meta\n"
        "- Read the subject task SPEC and meta\n"
        "- Inspect lifecycle and scope-compliance evidence\n"
        "- Inspect workspace/repo evidence read-only\n"
        "- Compare evidence against review acceptance criteria\n"
        "- Distinguish facts, missing evidence, and inference\n"
        "- Do not modify files\n"
        "- Do not fix issues\n"
        "- Do not start or delegate another Profile Task\n"
        "\n"
        "Before exit:\n"
        "1. Call aota_reviewer_report_submit with your verdict and evidence.\n"
        "2. Call aota_worker_outcome_submit with your terminal outcome.\n"
        "\n"
        "Lifecycle outcome rules:\n"
        "- A completed review with pass/fail/inconclusive verdict:\n"
        "  call aota_worker_outcome_submit outcome=completed\n"
        "- Required review evidence is unavailable:\n"
        "  call aota_worker_outcome_submit outcome=needs_input reason=<bounded reason>\n"
        "- Review process itself fails:\n"
        "  call aota_worker_outcome_submit outcome=failed\n"
        "\n"
        "Do not modify files.\n"
        "Do not fix issues.\n"
        "Do not dispatch.\n"
        "Do not exit without submitting outcome."
    ),
    "architecture": (
        "You are performing an independent read-only architecture review.\n"
        "- Read your own architecture review SPEC and meta\n"
        "- Read the subject task SPEC, meta, and any design artifacts\n"
        "- Evaluate design adequacy, blast radius, rollback, compatibility\n"
        "- For spec_preflight: verify SPEC is ready for worker execution\n"
        "- For design_review: verify design addresses the right problem\n"
        "- Distinguish: blocking issues, required corrections, optional improvements, residual risks\n"
        "- Do not modify files\n"
        "- Do not fix issues\n"
        "- Do not start or delegate another Profile Task\n"
        "\n"
        "Before exit:\n"
        "1. Call aota_architect_report_submit with your verdict and evidence.\n"
        "2. Call aota_worker_outcome_submit with your terminal outcome.\n"
        "\n"
        "Lifecycle outcome rules:\n"
        "- A completed review with any verdict (approve/approve_with_changes/block/inconclusive):\n"
        "  call aota_worker_outcome_submit outcome=completed\n"
        "- Required evidence is unavailable:\n"
        "  call aota_worker_outcome_submit outcome=needs_input reason=<bounded reason>\n"
        "- Review process itself fails:\n"
        "  call aota_worker_outcome_submit outcome=failed\n"
        "\n"
        "Do not modify files. Do not fix issues. Do not dispatch.\n"
        "Do not exit without submitting outcome."
    ),
    "stewardship": (
        "You are the Project Steward. Provide project facts and continuity recommendations only.\n"
        "- Do not make a final project decision, Plan/SPEC decision, or durable workflow decision\n"
        "- Do not modify source, Profile config, runtime files, or CodeGraph\n"
        "- Use only bounded project metadata/docs tools when your frozen stewardship SPEC authorizes them\n"
        "- Never rebuild CodeGraph, deploy, restart, recreate, dispatch, or use terminal\n"
        "\nBefore exit:\n"
        "1. Call aota_project_steward_report.\n"
        "2. Call aota_worker_outcome_submit with the matching terminal outcome."
    ),
}

_TIMEOUT_AWARE_INSTRUCTION = """\nThis task has a hard timeout configured. If you exceed the time limit, the\nprocess will be terminated with SIGTERM (and SIGKILL after a grace period).\nUse your time efficiently and submit your outcome before the deadline.\n"""


# These fields are control-plane bindings, not Worker instructions.  They are
# intentionally removed from the role projection even when a legacy SPEC puts
# them inside ``payload`` or ``role_contract``.
_CONTROL_PLANE_FIELDS = frozenset({
    "artifact_id", "artifact_path", "hash", "sha", "sha256", "spec_hash",
    "spec_sha256", "revision", "spec_revision", "task_id", "start_id",
    "workspace_id", "project_id", "work_item_id", "subject_task_id",
    "spec_id", "meta_path", "spec_path", "task_path", "workspace_path",
    "subject_ref", "subject_spec_ref", "subject_plan_ref",
    "subject_work_classification_ref", "context_refs", "related_artifacts",
})


def _semantic_projection(value: Any) -> Any:
    """Project role data without copying control-plane identity bindings."""
    if isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in _CONTROL_PLANE_FIELDS:
                continue
            projected[str(key)] = _semantic_projection(item)
        return projected
    if isinstance(value, (list, tuple)):
        return [_semantic_projection(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _spec_value(spec: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in spec and spec[name] is not None:
            return spec[name]
    return default


def build_worker_execution_envelope(
    *,
    spec: Mapping[str, Any],
    task_kind: str,
    derived_profile: str,
    read_scope: list[str],
    write_scope: list[str],
    forbidden_scope: list[str],
    architecture_mode: str | None = None,
) -> dict[str, Any]:
    """Build the deterministic, model-visible semantic Worker envelope.

    The envelope deliberately contains objective/acceptance/scope/role intent,
    but never task IDs, hashes, revisions, subject IDs, or control-plane paths.
    Trusted active-task tools remain the only way to read bound artifacts.
    """
    payload = spec.get("payload") if isinstance(spec.get("payload"), Mapping) else {}
    role_contract = spec.get("role_contract") if isinstance(spec.get("role_contract"), Mapping) else {}
    role_data = dict(role_contract)
    # Canonical WI-09C stores role intent in payload.  Merge it only for fields
    # absent from the older role_contract so the projection is deterministic.
    for key, value in payload.items():
        role_data.setdefault(key, value)

    process_path = _spec_value(spec, "process_path", default="standard")
    validation_tier = _spec_value(spec, "validation_tier", default=0)
    human_checkpoints = _spec_value(spec, "human_checkpoints", default=[])
    validation = {
        "policy": _semantic_projection(_spec_value(spec, "validation_policy", default=[])),
        "commands": _semantic_projection(payload.get("validation_commands", [])),
        "strategy": _semantic_projection(payload.get("validation_strategy", "")),
        "process_path": process_path,
        "tier": validation_tier,
        "human_checkpoints": _semantic_projection(human_checkpoints),
    }

    return {
        "schema_version": 1,
        "role": task_kind,
        "profile_route": derived_profile,
        "objective": _semantic_projection(_spec_value(spec, "objective", "goal", default="")),
        "acceptance_criteria": _semantic_projection(_spec_value(spec, "acceptance_criteria", default=[])),
        "read_scope": _semantic_projection(read_scope),
        "write_scope": _semantic_projection(write_scope),
        "forbidden_scope": _semantic_projection(
            forbidden_scope or _spec_value(spec, "forbidden_actions", default=[])
        ),
        "constraints": _semantic_projection(_spec_value(spec, "constraints", default=[])),
        "stop_conditions": _semantic_projection(_spec_value(spec, "stop_conditions", default=[])),
        "evidence_required": _semantic_projection(_spec_value(spec, "evidence_required", default=[])),
        "architecture_mode": architecture_mode if task_kind == "architecture" else None,
        "role_requirements": _semantic_projection(role_data),
        "validation": validation,
    }


_EXECUTION_ENVELOPE_PROMPT_TEMPLATE = """You are executing one bounded AOTA Profile Task.

Execution Envelope (authoritative semantic projection):
{envelope_json}

Operating rules:
- The active Profile and its fixed tool surface are already selected. Do not use tool discovery or tool description calls for the routine path.
- Worker identity, task/artifact IDs, revisions, hashes, subject bindings, and control-plane paths come from trusted runtime context. Do not request, infer, copy, or repeat them.
- Do not scan task-control directories or rediscover a task. If the active Profile Skill requires a binding check, use the bounded active-task artifact tool for each required SPEC/SCOPE/BINDING artifact once, then proceed from this envelope.
- Treat read_scope, write_scope, forbidden_scope, constraints, and stop_conditions as authoritative. If the requested work cannot stay inside them, stop and report needs_input.
- Use evidence before claiming completion. Do not modify task-control artifacts.

Role execution:
{role_guidance}

At the end, submit the role artifact/report when applicable, then call aota_worker_outcome_submit exactly once with completed, failed, or needs_input. Do not exit without a terminal outcome.
"""


_ENVELOPE_ROLE_GUIDANCE = {
    "implementation": "Implement only the required changes. Use the spec-driven implementation Skill and its bounded active-task preflight before project-tier work.",
    "diagnosis": "Diagnose only; gather the evidence named by the envelope, submit the diagnosis card/report, and do not mutate source files.",
    "review": "Perform an independent read-only review against the envelope and the bound subject evidence. Do not fix issues.",
    "architecture": "Perform the requested read-only architecture review or preflight against the envelope and bound subject evidence. Do not dispatch or modify files.",
    "stewardship": "Provide only the project facts and continuity artifact authorized by the envelope. Do not make durable orchestration decisions or source changes.",
}


def generate_worker_execution_envelope_prompt(
    *,
    spec: Mapping[str, Any],
    task_kind: str,
    derived_profile: str,
    read_scope: list[str],
    write_scope: list[str],
    forbidden_scope: list[str],
    architecture_mode: str | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Return ``(prompt, digest, envelope)`` for the current start boundary."""
    envelope = build_worker_execution_envelope(
        spec=spec,
        task_kind=task_kind,
        derived_profile=derived_profile,
        read_scope=read_scope,
        write_scope=write_scope,
        forbidden_scope=forbidden_scope,
        architecture_mode=architecture_mode,
    )
    encoded = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    prompt = _EXECUTION_ENVELOPE_PROMPT_TEMPLATE.format(
        envelope_json=encoded,
        role_guidance=_ENVELOPE_ROLE_GUIDANCE.get(task_kind, _ENVELOPE_ROLE_GUIDANCE["implementation"]),
    )
    return prompt, digest, envelope




def generate_worker_prompt(
    task_id: str,
    workspace_id: str,
    task_kind: str,
    derived_profile: str,
    meta_path: str,
    spec_path: str,
    read_scope: list[str] | None = None,
    write_scope: list[str] | None = None,
    forbidden_scope: list[str] | None = None,
    subject_task_id: str | None = None,
    architecture_mode: str | None = None,
    role_contract: dict | None = None,
    process_path: str | None = None,
    validation_tier: str | None = None,
    human_checkpoints: list[str] | None = None,
) -> str:
    """Generate the fixed one-shot worker prompt.

    No model-supplied fragments are inserted. Only validated metadata is used.
    When *read_scope*, *write_scope*, or *forbidden_scope* are provided, scope
    constraint lines are injected between role-specific instructions and the
    "At the end" section.
    """
    role_instructions = _ROLE_SPECIFIC.get(task_kind, _ROLE_SPECIFIC["implementation"])

    # Build subject context for review tasks
    subject_context = ""
    if task_kind == "review" and subject_task_id:
        subject_context = (
            f"\nSubject Task ID:\n{subject_task_id}\n"
            f"\nThe subject task artifacts are at:\n"
            f"<AOTA_PROFILE_TASK_ROOT>/{workspace_id}/{subject_task_id}/\n"
            f"\nYou must also read:\n"
            f"- subject SPEC.md and meta.json\n"
            f"- subject scope.json (if exists)\n"
            f"- subject completion receipt (if exists)\n"
        )
        role_instructions = subject_context + role_instructions

    # Build subject context for architecture tasks
    if task_kind == "architecture" and subject_task_id:
        arch_context = (
            f"\nSubject Task ID:\n{subject_task_id}\n"
            f"\nArchitecture Mode: {architecture_mode or 'unknown'}\n"
            f"\nThe subject task artifacts are at:\n"
            f"<AOTA_PROFILE_TASK_ROOT>/{workspace_id}/{subject_task_id}/\n"
            f"\nYou must also read:\n"
            f"- subject SPEC.md and meta.json\n"
            f"- subject scope.json (if exists)\n"
        )
        role_instructions = arch_context + role_instructions

    # Build scope constraints section
    scope_lines: list[str] = []
    if read_scope is not None or write_scope is not None or forbidden_scope is not None:
        scope_lines.append("Scope constraints:")
        if read_scope:
            for item in read_scope:
                scope_lines.append(f"- Allowed read: {item}")
        if write_scope:
            for item in write_scope:
                scope_lines.append(f"- Allowed write: {item}")
        if forbidden_scope:
            for item in forbidden_scope:
                scope_lines.append(f"- Forbidden: {item}")
        scope_lines.append("- NEVER modify any path outside write_scope.")
        scope_lines.append("- If modification outside write_scope is required: STOP and report needs_input.")
    # Build role contract section
    role_contract_lines: list[str] = []
    if role_contract:
        projection_fields = get_prompt_projection(task_kind, architecture_mode)
        if projection_fields:
            role_contract_lines.append("")
            role_contract_lines.append("Role Contract:")
            for field_name in projection_fields:
                value = role_contract.get(field_name)
                if value is None:
                    continue
                if isinstance(value, (list, dict)) and len(value) == 0:
                    continue
                # Convert snake_case to Title Case
                title = field_name.replace("_", " ").title()
                if isinstance(value, list):
                    role_contract_lines.append(f"{title}:")
                    for item in value:
                        role_contract_lines.append(f"- {item}")
                elif isinstance(value, dict):
                    role_contract_lines.append(f"{title}:")
                    for k, v in value.items():
                        role_contract_lines.append(f"{k}: {v}")
                elif isinstance(value, bool):
                    role_contract_lines.append(f"{title}:")
                    role_contract_lines.append(str(value))
                else:
                    role_contract_lines.append(f"{title}:")
                    role_contract_lines.append(str(value))

    # Build combined middle section
    all_middle_lines = list(scope_lines)
    # Add process_path / validation_tier / human_checkpoints
    all_middle_lines.append(f"Process Path: {process_path or 'standard'}")
    all_middle_lines.append(f"Validation Tier: {validation_tier or '0'}")
    all_middle_lines.append(f"Human Checkpoints: {', '.join(human_checkpoints) if human_checkpoints else 'none'}")
    # Append role contract lines (which include their own blank line separator)
    all_middle_lines.extend(role_contract_lines)

    combined_middle = "\n".join(all_middle_lines)

    return _WORKER_PROMPT_TEMPLATE.format(
        task_id=task_id,
        workspace_id=workspace_id,
        task_kind=task_kind,
        derived_profile=derived_profile,
        meta_path=meta_path,
        spec_path=spec_path,
        role_specific_instructions=role_instructions,
        scope_constraints=combined_middle,
    )


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------

def build_worker_command(
    runner: str,
    profile: str,
    worker_prompt: str,
) -> list[str]:
    """Build the worker argv vector; prompt text remains one literal argument."""
    return [runner, "-p", profile, "-z", worker_prompt]
