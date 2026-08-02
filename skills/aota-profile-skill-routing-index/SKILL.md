---
name: aota-profile-skill-routing-index
description: Compact task-main routing index for choosing the minimum AOTA Skill and canonical P0 tool chain without loading full orchestration guidance.
---

# AOTA task-main Skill routing index

Load this Skill first for every task-main request. It is the compact default
contract. Do not preload the detailed reference Skills listed below.

## Route only what the request needs

| Request shape | Load next |
|---|---|
| Exact file path, literal source search, or a small bounded read | `workspace-file-access-strategy` |
| Routine standalone P0 work | Nothing else; use the P0 chain below |
| Ambiguous P0/P1/P2 classification | `aota-work-classify-and-plan-gate` |
| P1/P2, multi-phase delivery, approval, fan-out, recovery, or non-routine closure | `aota-profile-task-orchestration` |
| Canonical SPEC fields are genuinely unclear | `aota-canonical-spec-contract` |
| A tool fails and its returned flow contract is unclear | `aota-tool-failure-fallback` |

Never load all reference Skills "for context". `skills_list` is discovery, not
a reason to read multiple Skills. Use at most one additional routing Skill
before the first AOTA domain tool unless the returned result explicitly requires
another contract.

## Routine standalone P0 chain

The tool names in this chain are already known. Do not use `tool_search`, and
do not describe downstream tools in advance. Call
`aota_workspace_open({})` directly through the deferred-tool bridge. If the
classifier input shape is not already visible, describe only
`aota_work_classify`; after classification, consume each returned
`allowed_next_tool_schema` instead of describing create, freeze, or start.

1. Use `aota_workspace_open` only when the trusted current workspace/project
   binding has not already been established. Do not call
   `aota_workspace_selection_record` unless `aota_workspace_open` explicitly
   reports that a human selection must be recorded.
2. Call `aota_work_classify` with the user's semantic request. Follow its
   classification; do not manufacture a Plan or Work Item for P0.
3. When classification is P0, call `aota_profile_task_dispatch` once with
   semantic fields only: `spec_kind`, `objective`, minimal read/write scopes,
   acceptance criteria, requirements, constraints, optional semantic subject,
   and optional validation metadata. For a routine read-only architecture
   probe, omit `validation`; never invent `validation.intent`,
   `validation.scope`, or `validation_tier`. If validation metadata is truly
   needed, use only the nested keys and enum values shown in the returned
   `allowed_next_tool_schema`. Never submit workspace/project/task/SPEC/session
   IDs, revision, hash, digest, path, Profile, tool names, approval binding,
   `traceability`, or other control-plane fields.
4. The P0 dispatch facade internally materializes, freezes, and starts the
   fixed Profile Worker. Its successful result is the completion wait contract;
   obey `next_action` and do not poll status when native completion wakes the
   parent.
5. For P1/P2, implementation approval, or explicit recovery, retain the
   existing `aota_task_spec_create` → `aota_task_spec_freeze` → approval →
   `aota_profile_task_start` chain. Consume each returned
   `allowed_next_tool_schema` exactly; do not describe the whole chain in
   advance.
6. When completion arrives, open the exact handoff, record the orchestration
   decision if required, and acknowledge the handoff. Do not scan history or
   choose the newest artifact.

## Tool-result flow contract

Interpret every AOTA result before deciding the next call:

- `flow_disposition="continue"`: choose among returned `next_options` according
  to the task intent. A single lifecycle transition may also provide
  `allowed_next_tool` and exact arguments.
- `flow_disposition="await_human"`: stop tool execution and ask for the stated
  human decision.
- `flow_disposition="complete"`: finish the current operation.
- `flow_disposition="stop"`: stop the failed operation branch; do not interpret
  it as a session-wide ban on the tool name.
- `same_call_retryable=false`: never replay the same tool with identical
  normalized arguments. The same tool may be used with changed semantic
  arguments only when `retry_scope=changed_arguments_only` names
  `repairable_fields`, or after genuinely new evidence creates a new operation.
- Never invent an ID, path, selector, revision, hash, or legacy field to repair
  a control-plane binding failure.

## Cost and schema discipline

- Prefer the schema already visible in the tool definition or returned in
  `allowed_next_tool_schema` / `next_options`.
- Use `tool_describe` at most once for the one imminent tool when a required
  field is still unknown. Do not describe downstream tools in advance.
- For supplied exact file contents or known lines, use one bounded read. Do not
  run `path_info` first merely to confirm that the path exists.
- For a named literal, enum, or schema lookup, use one literal
  `aota_search_files` query scoped to the exact file when known, otherwise the
  narrowest directory; read a matching region only when the preview is
  insufficient.
- Stop after the requested outcome is proven. Do not add optional inventory,
  health, status, or diagnostic calls to a successful routine chain.

Escalate to `aota-profile-task-orchestration` when any P1/P2 gate, multi-worker
dependency, approval, recovery, or durable closure question is present. The
long contract is a reference, not the default entrypoint.
