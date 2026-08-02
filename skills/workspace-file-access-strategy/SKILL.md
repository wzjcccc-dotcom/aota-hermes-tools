---
name: workspace-file-access-strategy
description: Compact AOTA bounded-read router for exact files, literal source search, structured probes, path verification, and forbidden access patterns.
metadata:
  hermes:
    tags: [aota, workspace, file-access, strategy, readonly]
---

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

When the user supplies one exact workspace-relative file path and asks for its
contents or a known line range, call the bounded read tool directly; it already
validates existence, containment, symlink escape, and file type. When the same
request asks for a named literal, enum, or schema fragment inside that known
file, call one literal search scoped to the exact file instead of reading the
whole source file. Do not add `aota_path_info` merely to preflight either route.
Use `aota_path_info` only when file type or metadata is itself required. When a
path is not known, use `aota_search_files` to locate it before reading. Search
is a fallback for unknown location, not an alternate first call for an exact
path. If the direct read returns a specific recoverable condition, follow that
condition; otherwise stop instead of broadening into a repository scan.

## Minimal model routes

Use the first matching route and do not describe or search unrelated tools.
If the exact tool name and argument shape appear below, call it directly through
the deferred-tool bridge; `tool_search` and `tool_describe` are unnecessary.

1. Exact first line or file contents:
   `aota_read_file({"path":"<path>","start_line":1,"end_line":1})`.
2. Exact small text/YAML files: call
   `aota_read_file({"path":"<path>"})` directly for each named file. Do not
   call `tool_search`, `tool_describe`, or `aota_path_info` first.
3. Literal symbol, enum, or schema probe in source, including when the exact
   file is already known: call exactly one
   `aota_search_files({"query":"<exact literal>","path":"<bounded file or dir>","max_results":10})`
   scoped to that exact file first. Treat the returned preview as authoritative
   when it contains the requested enum/schema; read only one narrow matching
   line range when the preview is insufficient. Never repeat the search or read
   a whole large source file for the same probe.
4. Exact manifest diagnosis: read only the named manifest. Do not run
   repository-wide search, CodeGraph, or path metadata checks.
5. A `same_call_retryable=false` result forbids replaying identical normalized
   arguments. A reported `retry_scope=changed_arguments_only` permits only the
   named semantic repair. Stop the failed branch for `flow_disposition=stop`,
   and return to the user for `await_human`.

## CodeGraph fallback

CodeGraph is read-only status/query/explore. When the index is stale,
missing, broken, or busy, fall back to bounded `aota_read_file` and
`aota_search_files` — never rebuild or wait on a lock. Only task-main may
trigger a CodeGraph rebuild.

## Worker-specific access

Workers with a frozen Profile Task binding use `aota_project_file_read` for
project-tier reads within the frozen SPEC `read_scope`. A raw no-binding probe
uses the read-only workspace tools above and must not submit a role report or
worker outcome. Subject reads require explicit frozen binding permission.

## Routing integration

The orchestration Skill routes file-access-strategy situations to this
Skill via the reference routing map. This Skill does not duplicate the
orchestration flow; it is a reference document for file access strategy.

## Activation classification

- Source class: `canonical_managed`
- Activation class: `active` for the six managed AOTA Profiles
- Owning profiles: task-main, architect, reviewer, coder, debugger, project-steward

## Deployment and Runtime Guidance

- Source PASS does NOT imply runtime PASS. Runtime parity for changed
  source is PENDING_DEPLOY until next managed deploy.
- No runtime mutation, no managed deploy, no restart, no recreate, no Git
  commit, no CodeGraph mutation performed by this Skill.

`WORKSPACE_FILE_ACCESS_STRATEGY_SKILL_PASS`
