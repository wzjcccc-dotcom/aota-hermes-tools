---
name: aota-hermes-host-upgrade-recovery
description: Preserve AOTA Profile Skill-exposure behavior and verify the retired parent-wake core customization stays absent after a Hermes host-source update.
---

# AOTA Hermes Host Upgrade Recovery

Use this Skill when `/home/latios/workspace/hermes-agent-host` is updated,
rebased, reinstalled, or its runtime behavior is uncertain. The Host checkout
remains the authority for the minimal Profile Skill-exposure customization.
The former AOTA parent-wake adapter is retired; `hermes-overrides` is not an
active authority.

## Safety gates

- Read the canonical Host source and current dirty state before changing
  anything.
- Confirm `gateway/aota_parent_wake.py` is absent and `gateway/run.py` has no
  AOTA adapter import, start/stop wiring, or runtime-env loader.
- Do not claim, move, clear, resubmit, or repair historical pending events.
- Do not recreate parent resume through `gateway.wake.deliver_wake`; Profile
  Task completion uses Hermes-native terminal background and ProcessRegistry.

## Recovery procedure

1. Run the read-only retirement verifier:
   `python3 /home/latios/workspace/aota-hermes-tools/scripts/verify-aota-parent-wake-retirement.py`.
2. Confirm the retired adapter stays absent. Confirm `agent/skill_utils.py`,
   `agent/prompt_builder.py`, `tools/skills_tool.py`, and `toolsets.py` retain
   the active/reference allowlist and `skills-readonly` contracts. Compile only
   the touched Python files first and run the tracked focused tests.
3. Use the existing AOTA Forge Plan package to deploy the Skill/profile
   projection and record its receipt. The Host source remains canonical and is
   tracked as `source_only` for backup/rollback traceability.
4. Restart the Desktop-owned Hermes backend through the normal operator path,
   then start one isolated diagnosis Worker from task-main using the existing
   orchestration contract. Observe ProcessRegistry completion, task-main
   resume, handoff, decision, acknowledgement, and closure without outbox.
5. Inspect bounded native completion evidence. Do not print session content,
   result bodies, credentials, or full event payloads.

## Update and rollback

After an upstream Hermes update, reconcile only the Profile Skill-exposure
source files, run the verifier, compile check, and temporary-home Skill
visibility smoke. Never reapply the retired parent-wake module or `run.py`
wiring. If verification fails, keep the backend stopped and restore the last
managed Host source snapshot using the existing Forge rollback procedure.

## Failure handling

Stop with a blocked result when source/runtime parity is lost, the retired
adapter reappears, native ProcessRegistry completion is unavailable, or
task-main resume cannot be evidenced. A source-only PASS is not a live
end-to-end PASS.

## Verification output

Report the verifier status, exact source/runtime paths, adapter absence,
Profile Skill-exposure parity, native completion evidence, and whether
task-main resumed. Keep unrelated dirty worktree paths unchanged and list them
as preserved.
