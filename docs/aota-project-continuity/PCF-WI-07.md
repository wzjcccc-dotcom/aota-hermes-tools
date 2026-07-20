# PCF-WI-07 — File-backed one-time maintenance approval

This source change makes a file receipt the only mutation authority for CodeGraph `bootstrap`, `refresh`, and `reindex`. It deliberately adds no approval service, database, queue, daemon, tenant model, or arbitrary command surface.

The acceptance smoke uses temporary roots and a fake fixed-argv launcher. It covers missing approval, worker rejection, exact workspace/project/action/SHA binding, expiry, unsafe input IDs/modes, duplicate JSON keys, atomic claim, replay rejection, a failed claimed attempt, and all three actions. It does not create an approval in the host runtime and does not mutate a real `.codegraph`.

See [FILE-BACKED-MAINTENANCE-APPROVAL.md](FILE-BACKED-MAINTENANCE-APPROVAL.md) for the public contract and operator workflow.
