# PCF-WI-09C — Worker Handoff and Card Contract

Every worker card retains its role-specific fields and additionally carries
the common envelope: schema version, role, task/spec ID, frozen revision/hash,
project/work item, outcome, verdict, summary, full-report reference, evidence,
full-report review flag, and recommended next action. Valid roles are coder,
debugger, reviewer, architect, and project-steward; their established filenames
remain unchanged.

The common handoff has canonical `spec_kind` plus equal deprecated
`task_kind`, task/spec binding, project/work-item IDs, card/report references,
outcome/verdict/summary/evidence, needs-input, full-report-review, and next
action fields. Operational outbox fields (`handoff_id`, workspace/start IDs,
created timestamp, pending state, and source) are explicitly transport
metadata; consumers must ignore unknown future transport fields.

The finalizer builds a WI-09C handoff from control-plane task metadata and then
validates the role Card binding. It never trusts worker-provided profile,
spec hash, project ID, or output path. Artifact paths are fixed names under
the task output root. Outcome vocabulary is completed/partial/needs_input/
failed; verdict vocabulary is pass/needs_fix/blocked/not_applicable.

task-main consumption remains `handoff → card → conditional full report →
durable decision → ack`. This contract carries no durable acceptance or final
closure decision and never embeds raw report content or secrets.
