---
name: aota-tool-failure-fallback
description: Safe AOTA fallback guidance without privilege escalation.
category: forge
---

# AOTA Tool Failure Fallback

Missing workspace context falls back to workspace list, Steward recommendation,
task-main durable selection, or user clarification. Do not suggest switching
to a default Profile, terminal, or unrestricted file access to read an
attachment or discover a workspace.

For Profile Task launcher failures, use the task-local bounded redacted log and
canonical fallback finalizer. Preserve the frozen binding, failure stage, exit
code, and receipt reference in the failure handoff; do not require a worker
CARD/RESULT that could not have been produced.

Credential failures use `failure_stage=credential_bootstrap` and one of
`credential_missing`, `credential_authority_invalid`, or
`credential_provider_unsupported`. Diagnostics may identify provider, key env
name, source type, global-root summary, and existence booleans only; never
write API keys, OAuth tokens, `auth.json`, or full `.env` content.

Completion wakeups consume canonical receipt evidence. They report `done` only
when the receipt says `status=done`, `exit_code=0`, and `outcome=completed`;
missing receipt is `failed` when a nonzero exit is observed and otherwise
`unknown` when process completion cannot be established. Wakeups include task,
start, profile, receipt/handoff presence, and failure stage without secrets.

Scope population failures use `failure_stage=scope_population` and
`error_classification=invalid_frozen_scope` (or
`workspace_baseline_unavailable`), set `worker_started=false`, and preserve the
same bounded failure receipt/handoff path. A launcher scope/spec/task digest
mismatch is `scope_binding_mismatch` and stops before worker execution.

## Deployment and Runtime Guidance

- Profile runtime assembly is manifest-driven. The canonical assembly manifest
  (`deploy/profile-runtime-assembly.yaml`) declares active Skills per Profile.
- Profile-local plugin and Skill projections are required when the Hermes loader
  uses profile home (`~/.hermes/profiles/<name>/plugins/`,
  `~/.hermes/profiles/<name>/skills/`).
- Config declaration is not runtime availability evidence. A toolset declared
  in config may fail to register at runtime.
- SOUL declaration is not Skill-load evidence. A Skill referenced in SOUL.md may
  not be loaded if the file is missing or the cache is stale.
- Runtime verification requires loaded tool/Skill evidence or actual Profile
  Task execution. Hash parity is deploy evidence, not runtime evidence.
- Managed deploy and recreate requirements apply per the lifecycle inventory.
- Host/Codex construction vs Hermes runtime verification boundary: source PASS
  does not imply runtime PASS.
- Project Steward owns documentation continuity, not Skill source implementation.
- task-main must not manually fabricate deployment success. If a deployment
  receipt is missing or runtime evidence contradicts declared state, report
  the discrepancy.
- Runtime-generated files (logs, snapshots, receipts, backups) are not managed
  source.
- No generic `/aota-runtime` access; use bounded runtime tools.
