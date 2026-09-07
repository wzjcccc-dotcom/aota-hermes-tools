---
name: workspace-file-access-strategy
description: Canonical AOTA workspace file access strategy — bounded read tools, path verification, and forbidden access patterns.
category: orchestration
tags: [aota, workspace, file-access, strategy, readonly]
---
> **W0 Replaced by AF — Non-Authoritative**
>
> ```text
> AOTA_SKILL_CANONICAL_SOURCE=aota_forge
> REPLACE_BY_FORGE=yes
> AF_CANONICAL=aota_forge/work_plane/workspace_tools.py (BoundedWorkspaceToolProvider)
> THIS_FILE_IS_RETAINED_AS_HISTORICAL_EVIDENCE_ONLY=yes
> ```
>
> This Skill is **superseded** by AF `work_plane/workspace_tools.py` and `mcp_transport.py`.
> Retained only as historical evidence / compatibility reference, not authority.


# Workspace File Access Strategy

This is the canonical reference for AOTA workspace file access strategy.
It defines which tools are used for file reads, path verification, and
search, and which access patterns are forbidden. The orchestration Skill
routes file-access-strategy situations here; this Skill does not itself
perform file operations.

## Authorized read tools

| Tool | Purpose | Replaces |
|------|---------|----------|
| `aota_path_info` | Path existence/type/size/mtime check | `stat`, `test -e`, `file`, `readlink` |
| `aota_read_file` | Bounded text file read | `cat`, `sed -n`, `head`, `tail` |
| `aota_search_files` | Filename/content search | `grep`, `rg`, `find`, `wc` |
| `aota_repo_status_readonly` | Git working-tree status | `git status` |
| `aota_repo_diff_readonly` | Git diff | `git diff` |
| `aota_active_task_artifact_open` | Active task SPEC/SCOPE/BINDING read | direct file access to `/aota-runtime` |
| `aota_runtime_info` | Runtime foundation metadata | direct runtime file access |
| `aota_codegraph_status` | CodeGraph index status | direct index file access |
| `aota_codegraph_query` | CodeGraph symbol query | `grep` for code relationships |
| `aota_codegraph_explore` | CodeGraph semantic explore | manual code traversal |

## Forbidden access patterns

- Unrestricted `file` tool for reading workspace files
- Unrestricted `terminal` for `cat`/`sed`/`head`/`tail`/`grep`/`find`/`wc`
- Direct access to `/aota-runtime` paths
- Direct access to `~/.hermes` runtime paths (outside workspace)
- Reading `.env` or credential files unless explicitly required
- Using `git` commands with model-supplied options
- Binary file reads via text read tools

## Path verification rules

Before acting on a path, verify its existence and type with `aota_path_info`.
Do not assume a path exists based on naming conventions or inference. Use
`aota_search_files` to locate files by name or content pattern, then read
with `aota_read_file`.

## CodeGraph fallback

CodeGraph is read-only status/query/explore. When the index is stale,
missing, broken, or busy, fall back to bounded `aota_read_file` and
`aota_search_files` — never rebuild or wait on a lock. Only task-main may
trigger a CodeGraph rebuild.

## Worker-specific access

Workers (coder, debugger, reviewer, architect) use `aota_project_file_read`
for project-tier reads within the frozen SPEC `read_scope`. This is a
separate tool from the general `aota_read_file` and is scoped to the active
task's allowed paths. Subject reads (reviewer/debugger/architect reading
the reviewed task's artifacts) require explicit frozen binding permission.

## Routing integration

The orchestration Skill routes file-access-strategy situations to this
Skill via the reference routing map. This Skill does not duplicate the
orchestration flow; it is a reference document for file access strategy.

## Activation classification

- Source class: `canonical_managed`
- Activation class: `reference` (loaded by task-main; not in active_skills)
- Owning profile: task-main

## Deployment and Runtime Guidance

- Source PASS does NOT imply runtime PASS. Runtime parity for changed
  source is PENDING_DEPLOY until next managed deploy.
- No runtime mutation, no managed deploy, no restart, no recreate, no Git
  commit, no CodeGraph mutation performed by this Skill.

`WORKSPACE_FILE_ACCESS_STRATEGY_SKILL_PASS`