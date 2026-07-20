# PCF-WI-09A — Tool Ownership and Coder Command Gap

## Tool ownership matrix (target)

Legend: **yes** means Profile-owned within the stated contract; **bounded**
means only an allowlisted tool/schema; **no** means denied.  `consent` means
current-conversation user consent is mandatory.

| Capability | task-main | project-steward | architect | coder | reviewer | debugger |
| --- | --- | --- | --- | --- | --- | --- |
| Project scan/search/open | yes | yes | bounded | bounded | bounded | bounded |
| Project registry refresh | control-plane only | no | no | no | no | no |
| Relationship brief / prepare | dispatch/control-plane | bounded | read | read | read | read |
| Plan tools | yes | no | no | no | no | no |
| SPEC tools | yes | no | no | no | no | no |
| Profile task / handoff / operator | yes | no | no | no | no | no |
| Role report tool | no | steward only | architect only | coder only | reviewer only | debugger only |
| CodeGraph status/query/explore | yes | yes | yes | yes | yes | yes |
| CodeGraph rebuild | consent only | no | no | no | no | no |
| Project metadata write | limited control-plane | bounded | no | no | no | no |
| Source/file mutation | no | docs/metadata only | no | SPEC-bounded | no | no |
| Project command execution | no | no | no | fixed command IDs only | no | no |
| Unrestricted terminal | no | no | no (target) | no (target) | no | no |
| Deploy/restart/recreate | no | no | no | no | no | no |

### Important classifications

- `aota_project_registry_refresh` is a mutation, even if exposed in a
  read-oriented project toolset, because it writes `projects.json`.  It is not
  a worker read capability.  WI-09B/D must move or label it as an explicit
  control-plane write.
- CodeGraph read is distinct from rebuild.  Rebuild is a maintenance mutation
  owned by task-main only after explicit current-conversation consent.
- Content ownership and filesystem authority differ: Architect owns ADR
  content, but Steward writes approved ADR text through a bounded docs tool.
- Current registry evidence conflicts with intended role boundaries:
  `PROFILE_CAPABILITIES` says `task-main.mutate_files=True` and grants file
  copy, while task-main's config disables `aota_fs_copy`, `file`, and
  `terminal` (`_capabilities.py:57-81`; `profiles/task-main/config.yaml:7-27`).
  The target is source write **no** and limited control-plane mutation **yes**.

## Document ownership matrix

| Artifact | Content owner | Writer | Reviewer / approver |
| --- | --- | --- | --- |
| `README.md` | Project Steward | Project Steward bounded tool | Reviewer consistency; task-main acceptance |
| `CHANGELOG.md` | Project Steward | Project Steward bounded tool | task-main |
| `ROADMAP.md` | task-main | Project Steward bounded tool | task-main |
| General operations docs | Project Steward | Steward; Coder technical input when needed | Reviewer |
| `ARCHITECTURE.md` | Architect | Steward writes approved content | task-main |
| ADR | Architect | Steward writes approved content | task-main |
| API docs | Coder | Coder | Reviewer |
| Migration guide | Coder technical content | Steward integrates | Reviewer |
| Project Card | Project Steward | bounded project tool | task-main consumes |
| Plan | task-main | Plan tool | task-main |
| SPEC | task-main | SPEC tool | Architect optional preflight |
| Result | Executing Profile | role report tool | task-main / Reviewer |
| Review | Reviewer | review tool | task-main |
| Diagnosis | Debugger | diagnosis tool | task-main |

## Coder terminal gap analysis

### A. Existing bounded read tools

The plugin supplies bounded path, file, search, repository status/diff, web,
project, and CodeGraph read tools in `plugin.yaml`.  Coder's ordinary config
currently enables `aota_core` and `aota_fs_readonly`, but does not include
repo/web/CodeGraph toolsets (`profiles/coder/config.yaml:20-39`).

### B. Existing bounded file mutation tools

Only `aota_file_copy` is a bounded mutation tool.  Its implementation is a
byte-preserving copy and explicitly says it has no full SPEC scope/budget
enforcement.  More critically, Coder disables its `aota_fs_copy` toolset
(`profiles/coder/config.yaml:7-17`).  The capability registry's declaration
that Coder may mutate files therefore does not give it an adequate bounded
construction interface.

### C. Existing bounded validation tools

No fixed validation-command runner is exposed in `plugin.yaml`; report tools
record validation evidence but do not execute it.  `aota_coder_report_submit`
does provide bounded report fields for paths, validation, risks, and a full
report (`_coder_report_submit.py:21-67`).

### D. Actual construction command classes still needing an interface

| Command class | Fixed command runner candidate? | Why |
| --- | --- | --- |
| Targeted formatter / linter / typecheck | yes | Named per repo/language and restricted to SPEC paths |
| Targeted unit test / selected test file | yes | Require allowlisted test ID and bounded arguments |
| Build / package static check | yes | Allow only declared scripts and working directory |
| Migration syntax / config validation | yes | Read-only or temp-output validation with no runtime apply |
| File create/update/patch | not a command runner | Needs a structured, path- and diff-bounded mutation tool |
| Test fixture setup / generated expected output | partly | A dedicated fixture writer or fixed generator is safer than shell |
| Novel project command / debugger shell experiment | no, initially | Requires a new reviewed command ID or a user checkpoint |

### E. Required target design

WI-09D should add a fixed command runner whose command IDs encode executable,
allowed arguments, workspace root, timeout, output cap, and whether it can
write generated files.  It must bind execution to frozen `spec_id`, revision,
hash, capability contract, and allowed validation targets.  It must reject
shell text, command substitution, arbitrary environment, network, deploy,
restart, Docker, package installation, and paths outside the workspace.

WI-09D should add structured `create/update/patch` tools that enforce
`write_scope`, forbidden scope, change budget, and audit receipts.  `copy`
alone is not a general implementation primitive.

### F. Recommendation

**`add_bounded_command_runner_before_disable`**.

Evidence: Coder's platform projection currently exposes `file` and `terminal`
(`profiles/coder/config.yaml:25-39`), its only bounded mutation tool is
disabled, and no validation runner exists.  It is neither safe nor useful to
turn terminal off before structured mutation and fixed validation commands are
available.  Until then, retain terminal temporarily under the existing
approved-SPEC, scope, and forbidden-operation constraints; this is a temporary
implementation gap, not a final capability model.
