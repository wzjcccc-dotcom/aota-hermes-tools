# PCF-WI-09C — Canonical SPEC Contract

`spec_kind` is canonical. `task_kind` is a deprecated compatibility alias and,
when present, must equal `spec_kind`. Fixed routing is implementation→coder,
diagnosis→debugger, review→reviewer, architecture→architect, and
stewardship→project-steward. `resolved_profile` is derived and never caller
controlled.

New SPECs use schema version 1 with the closed envelope: identity
(`spec_id`, `project_id`, `work_item_id`), `spec_kind`, derived profile,
revision/status/timestamps, objective/summary, artifact refs, acceptance and
scope lists, capability contract, closed role payload, lineage, and hash.
Unknown top-level, capability, payload, and reference fields are rejected.
References are logical IDs plus bounded relative paths; absolute paths,
traversal, and symlink escape are rejected.

Role payloads are closed. Implementation requires write and forbidden scopes,
acceptance, and validation. Diagnosis requires symptom/evidence and is
read-only. Review requires subject SPEC/result references and dimensions.
Architecture requires a plan or SPEC subject, an allowed review mode, and
challenge questions. Stewardship supports intake, context_prepare, docs_update,
artifact_link, and close; docs_update additionally requires bounded scope,
approved content, and project-metadata capability.

Requested capabilities are intersected with the routed Profile ceiling. No
SPEC may request deploy, restart, recreate, or CodeGraph rebuild. Only coder
may request source write/terminal (the existing temporary terminal exposure is
not changed here); project-steward metadata write remains a bounded tool
surface, not arbitrary filesystem authority.

Create starts draft revision 1. Update requires `spec_id`, `expected_revision`,
and a closed patch, increments revision, and clears draft hash. Freeze validates
the entire envelope, writes status `frozen`, and computes SHA-256 over stable
UTF-8 JSON with sorted keys and deterministic separators. Frozen revisions are
immutable; changed intent uses a successor via `supersedes_spec_id`.

Legacy read adapter accepts task_kind-only artifacts only when mapping is
unambiguous. Conflicting kind fields, unknown routing, and legacy frozen tasks
without an authoritative canonical binding are rejected.
