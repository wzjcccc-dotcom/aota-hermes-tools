# CodeGraph rebuild preflight — PCF-WI-08

The old maintenance gate has been replaced by one bounded rebuild preflight:

1. Resolve a registered `workspace_id` and `project_id` to the canonical project.
2. Reject path traversal, project mismatch, and symlink escape.
3. Reject a present/malformed lock or detected active CodeGraph process as `busy`.
4. Use the official fixed CodeGraph CLI semantics only: `init <project-root>` for
   a missing index and `index <project-root>` for a complete existing-index rebuild.
5. Run one bounded process with `shell=False`; do not retry on failure.
6. Return before/after status summaries without host paths, command text, or secrets.

There is no approval lifecycle, backup lifecycle, receipt authority, maintenance
environment, optimistic lifecycle binding, token, or file-backed checkpoint.
The human checkpoint is a natural conversation rule: task-main asks once before
calling rebuild. This document defines source behavior only; it does not execute
an index mutation, deployment, reload, or restart.
