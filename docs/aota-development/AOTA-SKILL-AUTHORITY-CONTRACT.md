# AOTA Skill Authority and Projection Contract

## Canonical source

The minimal Skill authority and projection contract has exactly one canonical
source:

```
plugin/aota-tools/_skill_authority_contract.py
```

It is also declared in `deploy/aota-lifecycle-inventory.yaml` under the
`skill_authority_contract` key. Skills, SOUL files, and verifiers reference or
verify the canonical source; no second authority or duplicated list is created.

## Contract elements

### Source classes

Every Skill file in the repository or runtime belongs to exactly one source
class:

- `canonical_managed` — Skills in `skills/<id>/SKILL.md` deployed via the
  managed manifest (`deploy/aota-forge-plan-files.yaml`). The repository source
  is the single canonical authority; runtime projections are copies.
- `profile_specific_managed` — Skills explicitly declared as profile-local in
  the lifecycle inventory, managed via a profile-specific manifest entry.
  These override `canonical_managed` only when explicitly declared.
- `bundled_or_global` — Skills that ship with Hermes natively or are installed
  globally outside the AOTA managed manifest. Not AOTA-managed but may coexist
  in the runtime skills directory.
- `runtime_generated` — Files produced at runtime (logs, snapshots, receipts,
  backups). NOT managed source and must not be added to the managed manifest
  or treated as Skills.
- `unmanaged` — Files present at runtime but not in any managed manifest. May
  be stale, manually copied, or orphaned.

### Activation classes

Every Skill has exactly one activation class per profile context:

- `active` — Skill is loaded in the profile's prompt context (verified via
  Skill-loaded prompt inspection or actual Profile Task execution).
- `reference` — Skill is available as a reference but not loaded in the
  current profile's active prompt context. It may be loaded by other profiles.
- `discoverable` — Skill file exists on disk but is not loaded in any
  profile's prompt context. Filesystem presence does not equal activation.
- `disabled` — Skill is explicitly disabled in profile config
  (`skills.disabled` list). Not loaded even if the file exists.

### Source authority policy

The repository source (`skills/<id>/SKILL.md`) is the single canonical
authority for AOTA-managed Skill content. Runtime projections are copies, not
authorities. Modifications must go back to the repository source, not to
runtime paths.

Key invariants:

- **owning_profiles != active_skills**: `owning_profiles` (declared in the
  lifecycle inventory) declares responsibility; `active_skills` (declared in
  `profile-runtime-assembly.yaml`) declares runtime loading. A Skill may be
  owned by task-main but active only in coder.
- **runtime_discoverable != managed**: A file may exist at runtime without
  being managed, and a managed file may not yet be deployed.
- **runtime_discoverable != authoritative**: A runtime file may diverge from
  the canonical source; the canonical source is always authoritative.
- **Runtime projection is NOT canonical**: Modifying a runtime projection
  without updating the repository source creates divergence.

### Destination policy

- Global Skill destination: `~/.hermes/skills/<id>/SKILL.md`
- Profile-local Skill destination:
  `~/.hermes/profiles/<profile>/skills/<id>/SKILL.md`
- Managed manifest source: `skills/<id>/SKILL.md`

### Collision policy

When a same-name Skill exists in multiple source classes, the resolution
order is:

1. `profile_specific_managed` (explicitly declared override)
2. `canonical_managed` (repository source)
3. `bundled_or_global` (Hermes native)
4. `unmanaged` (orphaned/stale)
5. `runtime_generated` (never a Skill)

An undeclared collision (same-name Skill in both `canonical_managed` and
`unmanaged` without explicit override declaration) is a FAIL condition.

### Override policy

A `profile_specific_managed` Skill may override a `canonical_managed` Skill
only when:

1. The override is explicitly declared in the lifecycle inventory
   (`scope: profile_local`, `deployed_path` points to profile-local path)
2. The owning profile is declared
3. The source path is a valid managed source

An override without explicit declaration is an undeclared collision.

### Reference routing policy

When task-main encounters a specific situation, it routes to the appropriate
reference Skill. These are routing policies, not hard bindings — the Skill
must be loaded (active) for the routing to take effect at runtime.

| Situation | Routes to |
|-----------|-----------|
| Intake | `aota-work-classify` |
| SPEC create | `aota-spec-driven-implementation` |
| SPEC retry | `aota-spec-driven-implementation` |
| Task start/wait/handoff | `aota-task-lifecycle` |
| Tool failure | `aota-tool-failure-fallback` |
| Coder | `aota-spec-driven-implementation` |
| Reviewer | `aota-implementation-review` |

The orchestration Skill (`aota-profile-task-orchestration`) is the entry
point for task-main. It routes to sub-Skills based on the situation.

### Wait mode classes

- `wakeup_capable_normal` — The transport supports wakeup notifications.
  Polling is PROHIBITED. The system must wait for a wakeup signal, not poll
  for completion.
- `non_wakeup_transport` — The transport does not support wakeup notifications.
  Explicit retrieval (checking status on demand) is allowed.
- `isolated_probe` — A bounded, one-time status check. Polling is allowed
  ONLY when the `POLLING_ALLOWED_FOR_ISOLATED_PROBE_ONLY` marker is present.
- `recovery` — Retry after a failure. Requires an explicit reason string.

## Fail-closed reject rules

The contract rejects:

1. `undeclared_collision` — same-name Skill in both managed and unmanaged
   without explicit override
2. `override_missing_explicit_declaration` — profile-specific override
   without explicit declaration
3. `runtime_projection_treated_as_canonical` — runtime projection used as
   authoritative source
4. `inventory_as_deployment_authority` — lifecycle inventory used as
   deployment authority instead of governance metadata
5. `unknown_source_class` — source class not in SOURCE_CLASSES
6. `unknown_activation_class` — activation class not in ACTIVATION_CLASSES
7. `unknown_wait_mode` — wait mode not in WAIT_MODE_CLASSES
8. `isolated_probe_polling_without_marker` — isolated probe polling without
   the required marker
9. `recovery_without_reason` — recovery wait mode without a reason
10. `path_length_authority_inference` — using path length to infer authority

## Migration findings (resolved in source; pending deploy)

All migration findings have been resolved in the repository canonical source.
Runtime parity is PENDING_DEPLOY until the next managed deploy recreates
importing processes and projects the updated source.

### MF-01: orchestration/ namespace contains Skills not formally managed

The `orchestration/` namespace in the runtime profile-local directory contained
Skills that were not formally included in the managed source manifest. These
have been brought into canonical managed source as reference Skills.

- Classification: `unmanaged_runtime_projection`
- Status: `resolved_in_source`
- Resolution: 5 reference Skills created in canonical source; profile-runtime-assembly.yaml updated with reference_skills declarations; lifecycle inventory updated with canonical_managed entries
- Pending deploy: true

### MF-02: aota-task-lifecycle source version diverges from profile-local detailed version

The repository canonical source (`skills/aota-task-lifecycle/SKILL.md`) has
been updated with wait mode semantics and binding/receipt/handoff authority
sections. The canonical source is authoritative; the profile-local version
will be updated via managed deploy.

- Classification: `source_runtime_divergence`
- Status: `resolved_in_source`
- Resolution: Wait mode semantics and binding/receipt/handoff authority sections merged into canonical source
- Pending deploy: true

### MF-03: Missing source for 5 profile-local Skills

Five profile-local Skills existed in the runtime but had no corresponding
canonical source in the repository. Canonical source has been created for all
five.

- Classification: `missing_canonical_source`
- Status: `resolved_in_source`
- Resolution: Canonical source created for aota-canonical-spec-contract, aota-canonical-spec-pitfalls, aota-work-classify-and-plan-gate, workspace-file-access-strategy, aota-multi-phase-doc-closure
- Pending deploy: true

## Verification

`scripts/verify-skill-authority-contract-minimum.py` covers Cases A-H
(contract invariants):

- **Case A**: Canonical managed Skill (pass)
- **Case B**: Legitimate profile-specific managed category (pass)
- **Case C**: Unmanaged runtime Skill (unmanaged)
- **Case D**: Undeclared collision (fail)
- **Case E**: Legitimate override (pass)
- **Case F**: Inventory is not deployment authority (fail_or_metadata_mismatch)
- **Case G**: Activation classes separated from source classes and owning_profiles
- **Case H**: Wait mode classified with correct rules

`scripts/verify-profile-local-skill-source-integration.py` covers Cases A-J
(profile-local source integration):

- **Case A**: Runtime-only Skill (unmanaged finding)
- **Case B**: Canonical merge — all 8 orchestration Skills have canonical source
- **Case C**: Conflicting rule — content drift detection (source PASS, runtime PENDING_DEPLOY)
- **Case D**: Qualified probe polling — isolated_probe with marker
- **Case E**: Explicit profile-specific managed Skill (override declared)
- **Case F**: Undeclared shadow — same-name collision without override (fail)
- **Case G**: Declared override — same-name collision with override (pass)
- **Case H**: Lifecycle inventory authority violation — inventory as deployment authority (fail)
- **Case I**: Routing map — no duplication of reference Skill content into orchestration Skill
- **Case J**: Legacy coder/reviewer/project-steward active Skills unaffected

Fixture data: `fixtures/skill-authority-contract-cases.json`

## Compatibility

This contract preserves:

- Existing 59 tools, 28 toolsets, 6 Named Profiles
- Profile Task lifecycle
- Managed deploy chain
- Project Steward runtime
- Coder/reviewer active Skills
- task-main orchestration Skill loading
- Global Skill deployment
- Hermes native category layout

No parallel Skill registry system is created. No runtime projection is
modified. No `/home/latios/.hermes` runtime files are modified.

`AOTA_SKILL_AUTHORITY_CONTRACT_DOC_PASS`