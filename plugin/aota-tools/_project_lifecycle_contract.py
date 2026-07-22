"""PCF-WI-PROJECT-LIFECYCLE-CONTRACT-MINIMUM canonical contract.

This is the single canonical source for the minimal project lifecycle contract:

  - Dispatch invariant (PROJECT_AND_PROFILE_MUTATION_REQUIRES_TASK_MAIN_DISPATCH)
  - Protected mutation classes
  - Runtime artifact write exemptions
  - Project Steward execution ownership vs task-main dispatch authority
  - Initialization states (initialized_core / initialized / failed)
  - Initialization receipt minimum fields
  - Codex escalation boundary

Other modules, Skills, SOUL files, and verifiers reference or verify these
definitions.  No second authority or duplicated list is created.

Conventions follow existing architecture: Python constants, frozenset/tuple
enums, and a stdlib-only schema dict -- matching _spec_contract.py and
_role_contracts.py patterns.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# 1. Dispatch invariant
# ---------------------------------------------------------------------------

#: The single canonical dispatch invariant name.
#:
#: Semantics: protected project/profile mutation requires a task-main
#: dispatched Profile Task with a frozen SPEC.  This covers ONLY protected
#: mutation classes (see PROTECTED_MUTATION_CLASSES), NOT all filesystem
#: writes.  Runtime artifact writes by workers (CARD/RESULT/worker_outcome
#: etc.) are exempt (see RUNTIME_ARTIFACT_WRITE_EXEMPTIONS) and are not
#: project mutation.
DISPATCH_INVARIANT = "PROJECT_AND_PROFILE_MUTATION_REQUIRES_TASK_MAIN_DISPATCH"

#: Profiles that may originate protected mutation via dispatch.
DISPATCH_AUTHORITY_PROFILES = frozenset(("task-main",))

#: Profiles that may execute dispatched protected mutation operations but
#: cannot originate un-dispatched protected mutation.
EXECUTION_OWNER_PROFILES = frozenset(("project-steward",))

# ---------------------------------------------------------------------------
# 2. Protected mutation classes
# ---------------------------------------------------------------------------

#: Mutation classes that require task-main dispatch.  These are project-level
#: structural mutations, not worker runtime artifacts.
PROTECTED_MUTATION_CLASSES = frozenset((
    "project_initialization",       # scaffold/metadata/registry creation
    "project_git_lifecycle",        # Git init/stage/commit/push
    "project_codegraph_lifecycle",  # CodeGraph init/index/reindex
    "project_registry_mutation",    # registry add/remove
    "project_closure",              # authoritative project closure
    "project_metadata_mutation",    # bounded project metadata mutation
))

# ---------------------------------------------------------------------------
# 3. Runtime artifact write exemptions
# ---------------------------------------------------------------------------

#: Runtime artifacts written by workers during Profile Task execution.
#: These are NOT protected mutations and do NOT require task-main dispatch.
#: They are bounded, task-local, identity-bound outputs produced by the
#: worker role under a frozen SPEC.
RUNTIME_ARTIFACT_WRITE_EXEMPTIONS = frozenset((
    "CARD",                 # worker role card (observation)
    "RESULT",               # worker full report (observation)
    "worker_outcome",       # terminal outcome submission
    "completion_receipt",   # trusted finalizer receipt
    "handoff",              # durable handoff artifact
    "review_decision",      # reviewer decision artifact
    "task_runtime_meta",    # task meta.json execution block
    "bounded_logs",         # worker/finalizer logs
    "bounded_temporary_state",  # scope events, baseline, temporary state
))

# ---------------------------------------------------------------------------
# 4. Project Steward execution ownership vs task-main dispatch authority
# ---------------------------------------------------------------------------

#: task-main authority: originate dispatch, create SPEC, approve, start,
#: make durable decisions, close work items.  task-main does NOT execute
#: project lifecycle operations itself.
TASK_MAIN_AUTHORITY = frozenset((
    "dispatch",
    "spec_creation",
    "spec_freeze",
    "task_approval",
    "task_start",
    "durable_decision",
    "work_item_closure",
    "workspace_selection",
))

#: Project Steward authority: execute dispatched lifecycle operations
#: (docs_update, artifact_link, registry_refresh, close checks) under a
#: frozen stewardship SPEC.  Steward CANNOT originate un-dispatched
#: protected mutation, create a SPEC, or make a durable project selection.
PROJECT_STEWARD_AUTHORITY = frozenset((
    "docs_update",
    "artifact_link",
    "registry_refresh",
    "close_checks",
    "context_prepare",
    "relationship_resolve",
))

# ---------------------------------------------------------------------------
# 5. Initialization states
# ---------------------------------------------------------------------------

#: initialized_core: scaffold/metadata/registry complete, but Git and
#: CodeGraph are not yet guaranteed.  This is the minimum for a project
#: to be recognized by the registry.
INITIALIZED_CORE = "initialized_core"

#: initialized: initialized_core + Git repository + CodeGraph index +
#: aggregate verification + authoritative receipt.  This is the full
#: project lifecycle ready state.
INITIALIZED = "initialized"

#: failed: initialization failed at some stage.  The failure stage and
#: error classification are recorded.
INITIALIZATION_FAILED = "failed"

#: All valid initialization states.
INITIALIZATION_STATES = frozenset((INITIALIZED_CORE, INITIALIZED, INITIALIZATION_FAILED))

# ---------------------------------------------------------------------------
# 6. Initialization receipt minimum fields
# ---------------------------------------------------------------------------

#: Minimum fields for an initialization receipt.  The receipt reuses the
#: completion/trusted finalizer authority: CARD/RESULT are worker
#: observations; the trusted finalizer / authoritative receipt is the
#: final lifecycle result.  Project Steward cannot self-declare
#: authoritative success.
INITIALIZATION_RECEIPT_MINIMUM_FIELDS = frozenset((
    "schema_version",
    "project_id",
    "workspace_id",
    "state",              # one of INITIALIZATION_STATES
    "git_summary",        # present/absent + commit head if initialized
    "codegraph_summary",  # present/absent + index status if initialized
    "authority",          # "trusted_finalizer" or "authoritative_receipt"
    "completed_at",
))

#: Receipt authority values.  A worker or Project Steward cannot
#: self-declare authoritative success.
RECEIPT_AUTHORITIES = frozenset(("trusted_finalizer", "authoritative_receipt"))

#: Worker observation artifacts (NOT authoritative lifecycle results).
WORKER_OBSERVATION_ARTIFACTS = frozenset(("CARD", "RESULT"))

# ---------------------------------------------------------------------------
# 7. Codex escalation boundary
# ---------------------------------------------------------------------------

#: Current state: Git/init tools are not yet complete, so Codex fallback
#: is REQUIRED for host/infrastructure-level Git initialization.  This is
#: a temporary gap, not a long-term architecture policy.
CURRENT_CODEX_FALLBACK_DEPENDENCY = "REQUIRED"

#: Target state: once Git/init tools are implemented as bounded Profile
#: Task tools, Codex fallback for project Git operations is NOT_REQUIRED.
#: Codex remains a Host/infra escalation path, not a general project
#: operator.
TARGET_CODEX_DEFAULT_PROJECT_OPERATOR = "NOT_REQUIRED"

#: Codex boundary: Codex is a Host/infrastructure escalation path for
#: operations outside the Hermes Profile Task tool surface, NOT a general
#: project operator or a replacement for task-main dispatch.
CODEX_BOUNDARY = "host_infra_escalation"

# ---------------------------------------------------------------------------
# 8. Fail-closed reject rules (work item section 10.2)
# ---------------------------------------------------------------------------

#: Reject reasons for fail-closed enforcement.
FAIL_CLOSED_REJECT_REASONS = frozenset((
    "non_task_main_protected_mutation_spec",
    "unfrozen_spec",
    "binding_mismatch",
    "profile_mismatch",
    "artifact_exemption_used_for_project_source_write",
    "steward_mutation_missing_binding",
    "initialized_receipt_missing_git_codegraph_summary",
    "receipt_authoritative_wrong_reconciliation_source",
    "unknown_initialization_state",
    "unknown_protected_mutation_class",
))


def is_protected_mutation(mutation_class: str) -> bool:
    """Return True if the mutation class requires task-main dispatch."""
    return mutation_class in PROTECTED_MUTATION_CLASSES


def is_runtime_artifact_exemption(artifact_type: str) -> bool:
    """Return True if the artifact type is a runtime write exemption."""
    return artifact_type in RUNTIME_ARTIFACT_WRITE_EXEMPTIONS


def is_valid_initialization_state(state: str) -> bool:
    """Return True if the state is a known initialization state."""
    return state in INITIALIZATION_STATES


def is_dispatch_authority(profile: str) -> bool:
    """Return True if the profile may originate protected mutation dispatch."""
    return profile in DISPATCH_AUTHORITY_PROFILES


def is_execution_owner(profile: str) -> bool:
    """Return True if the profile may execute dispatched protected mutation."""
    return profile in EXECUTION_OWNER_PROFILES


def validate_initialization_receipt(receipt: dict[str, Any]) -> list[str]:
    """Validate an initialization receipt against minimum fields.

    Returns a list of fail-closed reject reasons (empty list = valid).
    """
    errors: list[str] = []
    if not isinstance(receipt, dict):
        return ["unknown_initialization_state"]
    missing = INITIALIZATION_RECEIPT_MINIMUM_FIELDS - set(receipt)
    if missing:
        errors.append("initialized_receipt_missing_git_codegraph_summary")
    state = receipt.get("state")
    if not isinstance(state, str) or not is_valid_initialization_state(state):
        errors.append("unknown_initialization_state")
    authority = receipt.get("authority")
    if authority not in RECEIPT_AUTHORITIES:
        errors.append("receipt_authoritative_wrong_reconciliation_source")
    # If state is "initialized", git_summary and codegraph_summary must be present
    if state == INITIALIZED:
        git_summary = receipt.get("git_summary")
        codegraph_summary = receipt.get("codegraph_summary")
        if not isinstance(git_summary, dict) or not isinstance(codegraph_summary, dict):
            errors.append("initialized_receipt_missing_git_codegraph_summary")
    return errors


def check_dispatch(
    profile: str,
    mutation_class: str,
    *,
    spec_frozen: bool,
    binding_match: bool,
    profile_match: bool,
) -> list[str]:
    """Check whether a protected mutation satisfies the dispatch invariant.

    Returns a list of fail-closed reject reasons (empty list = allowed).
    """
    errors: list[str] = []
    if not is_protected_mutation(mutation_class):
        errors.append("unknown_protected_mutation_class")
        return errors
    if not is_dispatch_authority(profile):
        errors.append("non_task_main_protected_mutation_spec")
    if not spec_frozen:
        errors.append("unfrozen_spec")
    if not binding_match:
        errors.append("binding_mismatch")
    if not profile_match:
        errors.append("profile_mismatch")
    return errors


def check_steward_execution(
    profile: str,
    mutation_class: str,
    *,
    spec_frozen: bool,
    binding_match: bool,
) -> list[str]:
    """Check whether a Project Steward execution satisfies dispatch binding.

    Project Steward can execute dispatched operations but cannot originate
    un-dispatched protected mutation.  Returns reject reasons (empty = allowed).
    """
    errors: list[str] = []
    if profile != "project-steward":
        errors.append("profile_mismatch")
    if not spec_frozen:
        errors.append("unfrozen_spec")
    if not binding_match:
        errors.append("steward_mutation_missing_binding")
    if is_protected_mutation(mutation_class) and mutation_class not in (
        "project_metadata_mutation",
    ):
        # Steward may only execute metadata-mutation-class dispatched ops,
        # not structural initialization/git/codegraph/closure.
        errors.append("non_task_main_protected_mutation_spec")
    return errors


# ---------------------------------------------------------------------------
# 9. Schema dict for external reference
# ---------------------------------------------------------------------------

PROJECT_LIFECYCLE_CONTRACT_SCHEMA = {
    "schema_version": 1,
    "dispatch_invariant": DISPATCH_INVARIANT,
    "dispatch_authority_profiles": sorted(DISPATCH_AUTHORITY_PROFILES),
    "execution_owner_profiles": sorted(EXECUTION_OWNER_PROFILES),
    "protected_mutation_classes": sorted(PROTECTED_MUTATION_CLASSES),
    "runtime_artifact_write_exemptions": sorted(RUNTIME_ARTIFACT_WRITE_EXEMPTIONS),
    "task_main_authority": sorted(TASK_MAIN_AUTHORITY),
    "project_steward_authority": sorted(PROJECT_STEWARD_AUTHORITY),
    "initialization_states": sorted(INITIALIZATION_STATES),
    "initialization_receipt_minimum_fields": sorted(INITIALIZATION_RECEIPT_MINIMUM_FIELDS),
    "receipt_authorities": sorted(RECEIPT_AUTHORITIES),
    "worker_observation_artifacts": sorted(WORKER_OBSERVATION_ARTIFACTS),
    "codex_fallback_dependency": CURRENT_CODEX_FALLBACK_DEPENDENCY,
    "codex_default_project_operator": TARGET_CODEX_DEFAULT_PROJECT_OPERATOR,
    "codex_boundary": CODEX_BOUNDARY,
    "fail_closed_reject_reasons": sorted(FAIL_CLOSED_REJECT_REASONS),
}