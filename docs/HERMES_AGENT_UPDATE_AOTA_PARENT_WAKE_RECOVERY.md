# Hermes Agent Host Update: AOTA Customization Recovery

> **Historical parent-wake recovery record.** Since 2026-08-01, new Profile
> Tasks are terminal-background-only. A finite/stateless caller is rejected
> before Worker launch and the AOTA finalizer does not create a new native
> outbox event. Do not reapply or enable the adapter merely to make a new task
> run. Keep this runbook only for audit, rollback evidence, and an explicitly
> authorized retirement of the old Host customization. The Skill-exposure
> recovery section remains current and independent.

Current source disposition: `gateway/aota_parent_wake.py` is removed and
`gateway/run.py` is restored to current Hermes HEAD. Do not execute the
archived parent-wake activation/recovery procedure below. Verify retirement
with `scripts/verify-aota-parent-wake-retirement.py`; recover only the separate
Profile Skill-exposure files after a Hermes update.

This runbook historically applied to the host-source Hermes Agent at
`/home/latios/workspace/hermes-agent-host`. It is the recovery authority for
the retired worker-completion outbox adapter and remains the recovery authority
for the independent AOTA Profile Skill-exposure efficiency patch. The old
`/home/latios/workspace/hermes-overrides` tree is historical Docker/WebUI
overlay material and is not part of the active path.

## Contract

The producer writes terminal events under:

`/home/latios/.hermes/aota-runtime/outbox/aota-hermes-tools/pending`

The host adapter consumes that same workspace partition and calls native
`gateway.wake.deliver_wake`. It must resolve the workspace as:

1. `AOTA_PARENT_WAKE_WORKSPACE_ID` when present;
2. otherwise `AOTA_TRUSTED_WORKSPACE_ID`;
3. blocked when both are missing, invalid, or conflicting.

The runtime env must contain these non-secret values:

```text
AOTA_PARENT_WAKE_ENABLED=true
AOTA_TRUSTED_WORKSPACE_ID=aota-hermes-tools
AOTA_PARENT_WAKE_WORKSPACE_ID=aota-hermes-tools
AOTA_PARENT_WAKE_CREATED_AFTER=<UTC activation boundary>
```

The activation boundary is a safety fence. Existing pending events must remain
untouched and be skipped. The current historical backlog was measured as 69;
it is not evidence of a live failure and must not be manually drained.

## Files and authority

| Role | Path |
| --- | --- |
| Canonical host adapter | `/home/latios/workspace/hermes-agent-host/gateway/aota_parent_wake.py` |
| Host lifecycle wiring | `/home/latios/workspace/hermes-agent-host/gateway/run.py` |
| Native wake entrypoint | `/home/latios/workspace/hermes-agent-host/gateway/wake.py` |
| Skill config reader | `/home/latios/workspace/hermes-agent-host/agent/skill_utils.py` |
| Skill prompt filter | `/home/latios/workspace/hermes-agent-host/agent/prompt_builder.py` |
| Skill list/view enforcement | `/home/latios/workspace/hermes-agent-host/tools/skills_tool.py` |
| Read-only Skill toolset | `/home/latios/workspace/hermes-agent-host/toolsets.py` |
| Runtime configuration | `/home/latios/.config/hermes-host/runtime.env` |
| AOTA managed manifest | `/home/latios/workspace/aota-hermes-tools/deploy/aota-forge-plan-files.yaml` |
| Readiness verifier | `/home/latios/workspace/aota-hermes-tools/scripts/verify-aota-parent-wake-upgrade-readiness.py` |
| Source authority record | `/home/latios/workspace/aota-hermes-tools/deploy/host-parent-wake/host-parent-wake.manifest.json` |

The gateway files and the four Skill/toolset files are host-source
customizations. The Forge manifest records their implementation and focused
tests as `source_only` for managed traceability and rollback; it does not create
a Docker overlay or a second runtime registry.

## AOTA Profile Skill-exposure contract

Managed Profile configs use two separate lists:

- `skills.allowlist`: active Skills visible in the system prompt and
  `skills_list`;
- `skills.reference_allowlist`: detailed Skills hidden from those discovery
  surfaces but loadable by exact `skill_view(name)`.

`agent/skill_utils.py` parses both lists without importing the heavy CLI config
chain. `agent/prompt_builder.py` filters prompt entries to the active allowlist
and includes that list in the prompt cache key. `tools/skills_tool.py` filters
`skills_list` to active Skills while permitting exact views from the union of
active and reference lists. `toolsets.py` defines `skills-readonly` as exactly
`skills_list` plus `skill_view`; it never grants `skill_manage`.

If an upstream Hermes update removes or conflicts with these changes, do not
fall back to exposing every globally installed Skill. Reconcile the four files
against the focused tests tracked in the same Forge `source_only` group, then
verify with a temporary `HERMES_HOME` that an active Skill appears in the
prompt/list, a reference Skill is absent there but exact-viewable, and an
unrelated Skill is neither visible nor viewable.

## Before an update or restart

1. Confirm no Hermes backend is serving the old source. Use the Desktop-owned
   lifecycle; do not start a second backend from a shell.
2. Record current pending/processing/delivered/failed counts and the latest
   bounded gateway log lines. Do not dump event bodies or session text.
3. Run the readiness verifier and compile the touched host Python files.
4. Back up the runtime env atomically to a timestamped file next to the env,
   preserving mode and ownership. Do not include its contents in reports.
5. If the host checkout update changes `gateway/run.py`, `gateway/wake.py`, or
   import resolution, stop and reconcile the canonical source before restart.

## Recovery after a Hermes update

1. Check that imports resolve to `/home/latios/workspace/hermes-agent-host`.
2. Rebase or merge the host checkout update, preserving the adapter file and
   the start/stop wiring. Resolve only the affected hunks.
3. Verify that startup logs resolve workspace, source, runtime root, and all
   five outbox paths. Verify claim, parent-resolution, native-wake, delivery,
   duplicate, retry, and blocked logs remain bounded and secret-free.
4. Reconcile `agent/skill_utils.py`, `agent/prompt_builder.py`,
   `tools/skills_tool.py`, and `toolsets.py`; run their focused tests and the
   active/reference temporary-home smoke before process recreation.
5. Reapply the four runtime env values above and create a new activation
   boundary. Keep the prior backup until live verification closes.
6. Run the existing AOTA Forge Plan readiness/deploy/verify flow so the new
   Skill and profile assembly reach the runtime. Record the package receipt.
7. Close/reopen the Desktop-owned backend through its normal control path.
   Confirm the new process imports the canonical host source and emits the
   structured armed or blocked startup line.

## Live smoke and closure

Use exactly one standalone P0 diagnosis Worker, dispatched by `task-main`,
with the target `README.md` and `debugger` profile. Keep its scope bounded and
do not create another Worker. Observe this ordered chain:

`worker terminal event -> claim -> parent resolution -> native wake -> task-main resume -> handoff -> decision -> acknowledgement -> closure`

The live smoke is PASS only when the task-main resume is evidenced by the
native route and the completion receipt is delivered. A source/config PASS
without that observation is `PASS_WITH_LIMITATIONS`.

## Rollback

If any gate fails, keep the backend stopped. Use the existing
`scripts/rollback-aota-forge-plan-activation.sh` for the managed AOTA package,
and restore the timestamped runtime env backup with its original permissions.
For host-source conflicts, restore the last known-good managed source snapshot
or revert the update through the host repository's normal VCS workflow. Do not
use `git reset --hard` in a dirty worktree and do not delete or move outbox
events.

## Evidence checklist

- [ ] canonical host source and runtime import root agree;
- [ ] explicit/trusted workspace authority agrees and is path-safe;
- [ ] missing authority fails closed;
- [ ] activation boundary skips historical pending events;
- [ ] env backup and managed Forge receipt are recorded;
- [ ] startup and delivery logs are bounded;
- [ ] pending count is unchanged by the safety gate;
- [ ] isolated claim/native-wake/delivered fixture passes;
- [ ] Skill exposure patch and focused tests are present;
- [ ] active prompt/list, reference exact-view, and unrelated hidden smoke passes;
- [ ] one live Worker proves task-main resume and terminal closure.
