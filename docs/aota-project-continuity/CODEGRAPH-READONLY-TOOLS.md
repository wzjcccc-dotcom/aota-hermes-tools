# Bounded CodeGraph Tools — PCF-WI-05B

Toolset: `aota_codegraph_readonly`.

| Tool | Required input | Fixed command | Bound |
|---|---|---|---|
| `aota_codegraph_status` | `workspace_id`, `project_id` | `status --json <project-root>` | 10 s |
| `aota_codegraph_query` | plus `search` | `query --path <project-root> --limit <n> --json [--kind <kind>] <search>` | 10 s; limit 1..20 |
| `aota_codegraph_explore` | plus `query` | `explore --path <project-root> --max-files <n> <tokens>` | 20 s; files 1..10 |

All schemas reject unknown fields. Workspace and project identity are resolved through the registered workspace and canonical manifest; arbitrary project paths, manifest paths, index paths, executable paths, raw argv and shell commands are absent from the schema.

The runner always uses an argv list with `shell=False`, a validated project root as `cwd`, a minimal child environment (`HOME`, `PATH`, locale, `TERM`, `NO_COLOR`), a new process group, Linux child-subreaper cleanup plus group termination on timeout/output overflow, and 64 KiB stdout / 16 KiB stderr hard caps. `NODE_OPTIONS`, `NODE_PATH`, tokens and the parent environment are not inherited.

Queries reject controls, newlines and leading dashes; `kind` is allowlisted. Explore accepts one bounded string, tokenizes only by whitespace and never accepts raw arguments. Outputs remove ANSI/control characters, convert project paths to relative paths, cap snippets/content and hide stderr. Expected errors use bounded codes such as `codegraph_runtime_unavailable`, `codegraph_index_missing`, `codegraph_query_invalid`, `codegraph_timeout`, `codegraph_output_invalid` and `codegraph_output_too_large`.

The toolset does not expose `init`, `sync`, `index`, `daemon`, `upgrade` or any mutation command. Every successful or error response carries `side_effects: ["telemetry_metadata_write"]`; this is source/index read-only, not strict read-only.