"""PCF-WI-TASK-MAIN-SKILL-AUTHORITY-AND-PROJECTION-CONTRACT-MINIMUM canonical contract.

This is the single canonical source for the minimal Skill authority and
projection contract:

  - Source classes (canonical_managed, profile_specific_managed,
    bundled_or_global, runtime_generated, unmanaged)
  - Activation classes (active, reference, discoverable, disabled)
  - Source authority policy
  - Destination policy
  - Collision policy
  - Override policy
  - Reference routing policy
  - Wait mode classes (wakeup_capable_normal, non_wakeup_transport,
    isolated_probe, recovery)

Other modules, Skills, SOUL files, and verifiers reference or verify these
definitions.  No second authority or duplicated list is created.

Conventions follow existing architecture: Python constants, frozenset/tuple
enums, and a stdlib-only schema dict -- matching _project_lifecycle_contract.py
and _spec_contract.py patterns.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# 1. Source classes
# ---------------------------------------------------------------------------

#: Canonical managed source: Skills in skills/<id>/SKILL.md deployed via the
#: managed manifest (deploy/aota-forge-plan-files.yaml).  The repository source
#: is the single canonical authority; runtime projections are copies.
CANONICAL_MANAGED = "canonical_managed"

#: Profile-specific managed source: Skills explicitly declared as
#: profile-local in the lifecycle inventory, managed via a profile-specific
#: manifest entry.  These override canonical_managed only when explicitly
#: declared.
PROFILE_SPECIFIC_MANAGED = "profile_specific_managed"

#: Bundled or global source: Skills that ship with Hermes natively or are
#: installed globally outside the AOTA managed manifest.  These are not
#: AOTA-managed but may coexist in the runtime skills directory.
BUNDLED_OR_GLOBAL = "bundled_or_global"

#: Runtime generated source: files produced at runtime (logs, snapshots,
#: receipts, backups).  These are NOT managed source and must not be added
#: to the managed manifest or treated as Skills.
RUNTIME_GENERATED = "runtime_generated"

#: Unmanaged source: files present at runtime but not in any managed
#: manifest.  These may be stale, manually copied, or orphaned.
UNMANAGED = "unmanaged"

#: All valid source classes.
SOURCE_CLASSES = frozenset((
    CANONICAL_MANAGED,
    PROFILE_SPECIFIC_MANAGED,
    BUNDLED_OR_GLOBAL,
    RUNTIME_GENERATED,
    UNMANAGED,
))

# ---------------------------------------------------------------------------
# 2. Activation classes
# ---------------------------------------------------------------------------

#: Active: Skill is loaded in the profile's prompt context (verified via
#: Skill-loaded prompt inspection or actual Profile Task execution).
ACTIVE = "active"

#: Reference: Skill is available as a reference but not loaded in the current
#: profile's active prompt context.  It may be loaded by other profiles.
REFERENCE = "reference"

#: Discoverable: Skill file exists on disk but is not loaded in any profile's
#: prompt context.  Filesystem presence does not equal activation.
DISCOVERABLE = "discoverable"

#: Disabled: Skill is explicitly disabled in profile config
#: (skills.disabled list).  It is not loaded even if the file exists.
DISABLED = "disabled"

#: All valid activation classes.
ACTIVATION_CLASSES = frozenset((ACTIVE, REFERENCE, DISCOVERABLE, DISABLED))

# ---------------------------------------------------------------------------
# 3. Source authority policy
# ---------------------------------------------------------------------------

#: The repository source (skills/<id>/SKILL.md) is the single canonical
#: authority for AOTA-managed Skill content.  Runtime projections are copies,
#: not authorities.  Modifications must go back to the repository source,
#: not to runtime paths.
SOURCE_AUTHORITY = "repository_canonical"

#: Runtime projection is NOT canonical.  Modifying a runtime projection
#: without updating the repository source creates divergence.
RUNTIME_PROJECTION_NOT_CANONICAL = True

#: Explicit invariants separating source authority from runtime state.
#: owning_profiles (declared in lifecycle inventory) is NOT the same as
#: active_skills (declared in profile-runtime-assembly.yaml).  A Skill may
#: be owned by task-main but active only in coder.  owning_profiles declares
#: responsibility; active_skills declares runtime loading.
OWNING_PROFILES_NOT_ACTIVE_SKILLS = True

#: runtime_discoverable (file exists at runtime) is NOT the same as managed
#: (declared in the managed manifest).  A file may exist at runtime without
#: being managed, and a managed file may not yet be deployed.
RUNTIME_DISCOVERABLE_NOT_MANAGED = True

#: runtime_discoverable (file exists at runtime) is NOT the same as
#: authoritative (canonical source).  A runtime file may diverge from the
#: canonical source; the canonical source is always authoritative.
RUNTIME_DISCOVERABLE_NOT_AUTHORITATIVE = True

# ---------------------------------------------------------------------------
# 4. Destination policy
# ---------------------------------------------------------------------------

#: Global Skill destination pattern: ~/.hermes/skills/<id>/SKILL.md
GLOBAL_DESTINATION_TEMPLATE = "~/.hermes/skills/{skill_id}/SKILL.md"

#: Profile-local Skill destination pattern:
#: ~/.hermes/profiles/<profile>/skills/<id>/SKILL.md
PROFILE_LOCAL_DESTINATION_TEMPLATE = (
    "~/.hermes/profiles/{profile}/skills/{skill_id}/SKILL.md"
)

#: Managed manifest source pattern: skills/<id>/SKILL.md
SOURCE_PATH_TEMPLATE = "skills/{skill_id}/SKILL.md"

# ---------------------------------------------------------------------------
# 5. Collision policy
# ---------------------------------------------------------------------------

#: When a same-name Skill exists in multiple source classes, the resolution
#: order is:
#:   1. profile_specific_managed (explicitly declared override)
#:   2. canonical_managed (repository source)
#:   3. bundled_or_global (Hermes native)
#:   4. unmanaged (orphaned/stale)
#:   5. runtime_generated (never a Skill)
#:
#: An undeclared collision (same-name Skill in both canonical_managed and
#: unmanaged without explicit override declaration) is a FAIL condition.
COLLISION_RESOLUTION_ORDER = (
    PROFILE_SPECIFIC_MANAGED,
    CANONICAL_MANAGED,
    BUNDLED_OR_GLOBAL,
    UNMANAGED,
    RUNTIME_GENERATED,
)

#: Collision reject reason for undeclared same-name conflicts.
COLLISION_UNDECLARED = "undeclared_collision"

#: Collision accept reason for explicitly declared overrides.
COLLISION_LEGITIMATE_OVERRIDE = "legitimate_override"

# ---------------------------------------------------------------------------
# 6. Override policy
# ---------------------------------------------------------------------------

#: A profile_specific_managed Skill may override a canonical_managed Skill
#: only when:
#:   1. The override is explicitly declared in the lifecycle inventory
#:      (scope: profile_local, deployed_path points to profile-local path)
#:   2. The owning profile is declared
#:   3. The source path is a valid managed source
#:
#: An override without explicit declaration is an undeclared collision.
OVERRIDE_REQUIRES_EXPLICIT_DECLARATION = True

#: Override reject reason for missing explicit declaration.
OVERRIDE_UNDECLARED = "override_missing_explicit_declaration"

# ---------------------------------------------------------------------------
# 7. Reference routing policy
# ---------------------------------------------------------------------------

#: When task-main encounters a specific situation, it routes to the
#: appropriate reference Skill.  These are routing policies, not hard
#: bindings — the Skill must be loaded (active) for the routing to take
#: effect at runtime.
REFERENCE_ROUTING_MAP = {
    "intake": "aota-work-classify",
    "spec_create": "aota-spec-driven-implementation",
    "spec_retry": "aota-spec-driven-implementation",
    "task_start_wait_handoff": "aota-task-lifecycle",
    "tool_failure": "aota-tool-failure-fallback",
    "coder": "aota-spec-driven-implementation",
    "reviewer": "aota-implementation-review",
}

#: The orchestration Skill (aota-profile-task-orchestration) is the entry
#: point for task-main.  It routes to sub-Skills based on the situation.
ORCHESTRATION_ENTRY_POINT = "aota-profile-task-orchestration"

# ---------------------------------------------------------------------------
# 8. Wait mode classes
# ---------------------------------------------------------------------------

#: Wakeup-capable normal: the transport supports wakeup notifications.
#: Polling is PROHIBITED.  The system must wait for a wakeup signal, not
#: poll for completion.
WAIT_WAKEUP_CAPABLE_NORMAL = "wakeup_capable_normal"

#: Non-wakeup transport: the transport does not support wakeup notifications.
#: Explicit retrieval (checking status on demand) is allowed.
WAIT_NON_WAKEUP_TRANSPORT = "non_wakeup_transport"

#: Isolated probe: a bounded, one-time status check.  Polling is allowed
#: ONLY when the POLLING_ALLOWED_FOR_ISOLATED_PROBE_ONLY marker is present.
WAIT_ISOLATED_PROBE = "isolated_probe"

#: Recovery: retry after a failure.  Requires an explicit reason.
WAIT_RECOVERY = "recovery"

#: All valid wait mode classes.
WAIT_MODE_CLASSES = frozenset((
    WAIT_WAKEUP_CAPABLE_NORMAL,
    WAIT_NON_WAKEUP_TRANSPORT,
    WAIT_ISOLATED_PROBE,
    WAIT_RECOVERY,
))

#: Marker required for isolated probe polling.
POLLING_ALLOWED_FOR_ISOLATED_PROBE_ONLY = "POLLING_ALLOWED_FOR_ISOLATED_PROBE_ONLY"

#: Wait mode validation rules:
#: - wakeup_capable_normal: polling_prohibited=True
#: - non_wakeup_transport: explicit_retrieval_allowed=True
#: - isolated_probe: requires POLLING_ALLOWED_FOR_ISOLATED_PROBE_ONLY marker
#: - recovery: requires reason string
WAIT_MODE_RULES = {
    WAIT_WAKEUP_CAPABLE_NORMAL: {"polling_prohibited": True},
    WAIT_NON_WAKEUP_TRANSPORT: {"explicit_retrieval_allowed": True},
    WAIT_ISOLATED_PROBE: {"requires_marker": POLLING_ALLOWED_FOR_ISOLATED_PROBE_ONLY},
    WAIT_RECOVERY: {"requires_reason": True},
}

# ---------------------------------------------------------------------------
# 9. Fail-closed reject rules
# ---------------------------------------------------------------------------

#: Reject reasons for Skill authority/projection fail-closed enforcement.
SKILL_AUTHORITY_FAIL_CLOSED_REJECT_REASONS = frozenset((
    "undeclared_collision",
    "override_missing_explicit_declaration",
    "runtime_projection_treated_as_canonical",
    "inventory_as_deployment_authority",
    "unknown_source_class",
    "unknown_activation_class",
    "unknown_wait_mode",
    "isolated_probe_polling_without_marker",
    "recovery_without_reason",
    "path_length_authority_inference",
))

# ---------------------------------------------------------------------------
# 10. Migration findings (existing runtime anomalies — not fixed this round)
# ---------------------------------------------------------------------------

#: All existing runtime anomalies are classified as migration findings.
#: They are recorded here for traceability; they are NOT directly fixed
#: in this work item.
MIGRATION_FINDINGS = (
    {
        "id": "MF-01",
        "title": "orchestration/ namespace contains Skills not formally managed",
        "description": (
            "The orchestration/ namespace in the runtime profile-local "
            "directory contains Skills that are not formally included in "
            "the managed source manifest.  These are unmanaged runtime "
            "projections that need to be either brought into canonical "
            "managed source or removed."
        ),
        "classification": "unmanaged_runtime_projection",
        "status": "recorded_not_fixed",
    },
    {
        "id": "MF-02",
        "title": "aota-task-lifecycle source version diverges from profile-local detailed version",
        "description": (
            "The repository canonical source (skills/aota-task-lifecycle/"
            "SKILL.md) has different content from the profile-local runtime "
            "projection.  The canonical source is authoritative; the "
            "profile-local version should be updated via managed deploy."
        ),
        "classification": "source_runtime_divergence",
        "status": "recorded_not_fixed",
    },
    {
        "id": "MF-03",
        "title": "Missing source for 5 profile-local Skills",
        "description": (
            "Five profile-local Skills exist in the runtime but have no "
            "corresponding canonical source in the repository.  These need "
            "canonical source creation or removal from the runtime."
        ),
        "classification": "missing_canonical_source",
        "status": "recorded_not_fixed",
    },
)

# ---------------------------------------------------------------------------
# 11. Helper functions
# ---------------------------------------------------------------------------


def is_valid_source_class(source_class: str) -> bool:
    """Return True if the source class is known."""
    return source_class in SOURCE_CLASSES


def is_valid_activation_class(activation_class: str) -> bool:
    """Return True if the activation class is known."""
    return activation_class in ACTIVATION_CLASSES


def is_valid_wait_mode(wait_mode: str) -> bool:
    """Return True if the wait mode is known."""
    return wait_mode in WAIT_MODE_CLASSES


def check_collision(
    skill_id: str,
    source_classes_present: list[str],
    *,
    override_declared: bool = False,
) -> list[str]:
    """Check whether a same-name Skill collision is valid.

    Returns a list of fail-closed reject reasons (empty list = valid).
    """
    errors: list[str] = []
    managed_classes = {
        CANONICAL_MANAGED,
        PROFILE_SPECIFIC_MANAGED,
    }
    present_managed = [
        sc for sc in source_classes_present if sc in managed_classes
    ]
    unmanaged_present = UNMANAGED in source_classes_present

    # Undeclared collision: both managed and unmanaged present without override
    if present_managed and unmanaged_present and not override_declared:
        errors.append(COLLISION_UNDECLARED)

    # Override without explicit declaration
    if (
        PROFILE_SPECIFIC_MANAGED in source_classes_present
        and CANONICAL_MANAGED in source_classes_present
        and not override_declared
    ):
        errors.append(OVERRIDE_UNDECLARED)

    return errors


def check_wait_mode(
    wait_mode: str,
    *,
    has_polling_marker: bool = False,
    recovery_reason: str | None = None,
) -> list[str]:
    """Check whether a wait mode configuration is valid.

    Returns a list of fail-closed reject reasons (empty list = valid).
    """
    errors: list[str] = []
    if not is_valid_wait_mode(wait_mode):
        errors.append("unknown_wait_mode")
        return errors

    rules = WAIT_MODE_RULES[wait_mode]

    if wait_mode == WAIT_ISOLATED_PROBE and not has_polling_marker:
        errors.append("isolated_probe_polling_without_marker")

    if wait_mode == WAIT_RECOVERY and not recovery_reason:
        errors.append("recovery_without_reason")

    return errors


def resolve_collision(
    source_classes_present: list[str],
    *,
    override_declared: bool = False,
) -> str | None:
    """Resolve a same-name collision to the winning source class.

    Returns the winning source class, or None if no managed class is present.
    Does NOT validate; use check_collision for validation.
    """
    for source_class in COLLISION_RESOLUTION_ORDER:
        if source_class in source_classes_present:
            if source_class == PROFILE_SPECIFIC_MANAGED and not override_declared:
                continue
            return source_class
    return None


def classify_source(
    *,
    in_managed_manifest: bool,
    in_profile_specific_manifest: bool,
    is_runtime_generated: bool,
    is_bundled: bool,
) -> str:
    """Classify a Skill file into its source class.

    This is a deterministic classifier based on presence flags.
    """
    if is_runtime_generated:
        return RUNTIME_GENERATED
    if in_profile_specific_manifest:
        return PROFILE_SPECIFIC_MANAGED
    if in_managed_manifest:
        return CANONICAL_MANAGED
    if is_bundled:
        return BUNDLED_OR_GLOBAL
    return UNMANAGED


def classify_activation(
    *,
    is_loaded_in_prompt: bool,
    is_disabled_in_config: bool,
    file_exists: bool,
) -> str:
    """Classify a Skill into its activation class.

    This is a deterministic classifier based on runtime state flags.
    """
    if is_disabled_in_config:
        return DISABLED
    if is_loaded_in_prompt:
        return ACTIVE
    if file_exists:
        return DISCOVERABLE
    return REFERENCE


# ---------------------------------------------------------------------------
# 12. Schema dict for external reference
# ---------------------------------------------------------------------------

SKILL_AUTHORITY_CONTRACT_SCHEMA = {
    "schema_version": 1,
    "source_classes": sorted(SOURCE_CLASSES),
    "activation_classes": sorted(ACTIVATION_CLASSES),
    "source_authority": SOURCE_AUTHORITY,
    "runtime_projection_not_canonical": RUNTIME_PROJECTION_NOT_CANONICAL,
    "owning_profiles_not_active_skills": OWNING_PROFILES_NOT_ACTIVE_SKILLS,
    "runtime_discoverable_not_managed": RUNTIME_DISCOVERABLE_NOT_MANAGED,
    "runtime_discoverable_not_authoritative": RUNTIME_DISCOVERABLE_NOT_AUTHORITATIVE,
    "global_destination_template": GLOBAL_DESTINATION_TEMPLATE,
    "profile_local_destination_template": PROFILE_LOCAL_DESTINATION_TEMPLATE,
    "source_path_template": SOURCE_PATH_TEMPLATE,
    "collision_resolution_order": list(COLLISION_RESOLUTION_ORDER),
    "override_requires_explicit_declaration": OVERRIDE_REQUIRES_EXPLICIT_DECLARATION,
    "reference_routing_map": dict(REFERENCE_ROUTING_MAP),
    "orchestration_entry_point": ORCHESTRATION_ENTRY_POINT,
    "wait_mode_classes": sorted(WAIT_MODE_CLASSES),
    "wait_mode_rules": {
        mode: dict(rules) for mode, rules in WAIT_MODE_RULES.items()
    },
    "fail_closed_reject_reasons": sorted(SKILL_AUTHORITY_FAIL_CLOSED_REJECT_REASONS),
    "migration_findings": [
        {
            "id": f["id"],
            "title": f["title"],
            "classification": f["classification"],
            "status": f["status"],
        }
        for f in MIGRATION_FINDINGS
    ],
}