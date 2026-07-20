# PCF-WI-09C — Migration Notes

New writes use the canonical SPEC and handoff contract. The old `task_kind`
field is retained only as an equal compatibility alias for existing consumers.
Read adapters map a task_kind-only legacy artifact to `spec_kind`; artifacts
with conflicting kinds, unmapped profiles, or an ambiguous frozen/approved
binding are rejected rather than guessed.

Existing role-specific Card and report filenames/shapes are preserved. New
canonical task writers add the common Card fields without removing
role-specific evidence. Existing handoff/outbox and completion receipts remain
readable; canonical finalizer output uses metadata-authoritative bindings.

PCF-WI-09B's stewardship routing and role contract were a temporary
compatibility layer. WI-09C replaces it with the stewardship closed payload,
fixed routing, freeze/hash binding, common Card, and common handoff contract.
The WI-09B document remains historical evidence.
