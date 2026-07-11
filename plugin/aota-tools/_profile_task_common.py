"""Shared utilities for AOTA profile task start (P5).

Provides: task ID validation, profile derivation from task_kind,
runner detection, worker prompt generation, and error types.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from ._task_spec_common import TASK_KIND_PROFILE_HINT, TASK_KINDS, STATUS_DRAFT
from ._workspace import WorkspaceError

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


# ---------------------------------------------------------------------------
# Runner detection
# ---------------------------------------------------------------------------

_RUNNER_CANDIDATES = (
    "/opt/hermes/hermes",
    "/opt/hermes/.venv/bin/hermes",
    "/usr/local/bin/hermes",
)


def resolve_runner() -> str:
    """Resolve the hermes runner executable.

    Checks AOTA_HERMES_RUNNER env var first (admin override for dev/testing),
    then falls back to known production candidate paths.
    Returns the absolute path to the first existing, executable candidate.
    Raises WorkspaceError if no runner is found.
    """
    # Admin-controlled env var override (for dev/testing)
    env_runner = os.environ.get("AOTA_HERMES_RUNNER", "").strip()
    if env_runner:
        p = Path(env_runner)
        if p.is_file() and os.access(str(p), os.X_OK):
            return str(p.resolve())
        raise WorkspaceError(
            f"runner_unavailable: AOTA_HERMES_RUNNER={env_runner} not found or not executable"
        )

    for candidate in _RUNNER_CANDIDATES:
        p = Path(candidate)
        if p.is_file() and os.access(str(p), os.X_OK):
            return str(p.resolve())
    # Last resort: check PATH
    import shutil

    found = shutil.which("hermes")
    if found:
        p = Path(found)
        if p.is_file() and os.access(str(p), os.X_OK):
            return str(p.resolve())
    raise WorkspaceError(
        "runner_unavailable: no hermes runner found at expected locations "
        f"({', '.join(_RUNNER_CANDIDATES)}) nor on PATH"
    )


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
}

_TIMEOUT_AWARE_INSTRUCTION = """\nThis task has a hard timeout configured. If you exceed the time limit, the\nprocess will be terminated with SIGTERM (and SIGKILL after a grace period).\nUse your time efficiently and submit your outcome before the deadline.\n"""




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
    scope_constraints = "\n".join(scope_lines)

    return _WORKER_PROMPT_TEMPLATE.format(
        task_id=task_id,
        workspace_id=workspace_id,
        task_kind=task_kind,
        derived_profile=derived_profile,
        meta_path=meta_path,
        spec_path=spec_path,
        role_specific_instructions=role_instructions,
        scope_constraints=scope_constraints,
    )


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------

def build_worker_command(
    runner: str,
    profile: str,
    worker_prompt: str,
) -> str:
    """Build a safe shell command that launches the profile worker via the runner.

    Uses shlex.quote for all dynamic values. Never uses shell=True.
    """
    import shlex

    return (
        f"{shlex.quote(runner)} "
        f"-p {shlex.quote(profile)} "
        f"-z {shlex.quote(worker_prompt)}"
    )
