# CodeGraph minimal tools — PCF-WI-08

CodeGraph is a regenerable helper index, not a database or system authority. A
missing, stale, or failed index never blocks normal AOTA project delivery.

## Public tool surface

| Tool | Mode | Input | Purpose |
|---|---|---|---|
| `aota_codegraph_status` | readonly | registered `workspace_id`, `project_id` | reports index presence/readability, counts, pending changes, state, action, warnings, and busy evidence |
| `aota_codegraph_query` | readonly | registered IDs, bounded search/limit/kind | fixed-argv bounded symbol query |
| `aota_codegraph_explore` | readonly | registered IDs, bounded topic/max files | fixed-argv bounded structural exploration |
| `aota_codegraph_rebuild` | mutation | registered `workspace_id`, `project_id` | complete index rebuild with post-status summary |

No schema accepts an arbitrary path, executable, backend URL, shell command, raw
argv, approval ID, backup receipt, token, environment activation, or receipt.

## Rebuild policy

Use `status → rebuild → status` only when an index is needed. Small source edits
do not require a rebuild. A missing/broken index, a deep-development handoff, or
a Git checkpoint are normal reasons to offer one. Rebuild failure is reported;
it is not retried automatically and does not block unrelated work.

Before calling rebuild, task-main obtains one explicit conversational user
confirmation. Workers do not rebuild unless their frozen SPEC explicitly records
that confirmation.

## Safety retained

Registered workspace/project resolution, canonical-root validation, path-traversal
and symlink-escape rejection, fixed executable and argv, bounded output/timeout,
`shell=False`, and conservative busy detection remain mandatory. A present lock
or detected active CodeGraph process returns `busy`/`wait`; the tool never deletes
a lock, steals it, or kills a process.

`status` states are `missing`, `ready`, `stale`, `busy`, or `broken`; recommended
actions are `none`, `rebuild`, or `wait`. Oversized excluded sources can remain
`ready` with a partial-coverage warning.
