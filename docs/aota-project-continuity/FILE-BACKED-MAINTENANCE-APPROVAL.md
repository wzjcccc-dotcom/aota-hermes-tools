# REMOVED_FROM_ACTIVE_ARCHITECTURE — file-backed maintenance approval

This is an archived PCF-WI-07 design note. PCF-WI-08 removed the CodeGraph
file-backed approval, atomic claim, TTL, backup receipt, maintenance receipt,
trusted activation, and maintenance environment contracts.

The active architecture is documented in `CODEGRAPH-MINIMAL-TOOLS.md` and uses
only registered project identity, canonical-path safety, fixed argv, bounded
timeout/output, conservative busy detection, and one conversational user
confirmation before a rebuild. This file must not be used as an operational
procedure.
