# PCF-WI-09D — Profile Skill and Tool Boundary Alignment

This source-only implementation record applies the established WI-09A/WI-09C
contracts without activating a runtime Profile. `task-main` is the card-first
durable decision owner; its canonical flow is in
`aota-profile-task-orchestration`. SOUL files are role summaries.

All specialist Profiles receive CodeGraph status/query/explore and never the
rebuild toolset. `task-main` alone exposes rebuild, which remains subject to
current-conversation user consent. Busy, missing, and stale CodeGraph states
fall back to bounded read/search rather than blocking.

Coder has no unrestricted `file` or `terminal` exposure. Its only construction
surfaces are frozen-SPEC-bound text read/write/patch tools and a fixed-argv
validation command registry. Project Steward remains the sole bounded project
metadata/docs/artifact writer; architecture content is supplied by Architect
and independently checked for consistency by Reviewer.

The implementation is source-only. Managed deployment, runtime reload,
service recreation, real worker tasks, real project mutation, CodeGraph
rebuild, and Git writes are out of scope.
