# File-backed maintenance approval fixtures

The isolated smoke creates disposable 0600 approval receipts from these cases;
it never uses this directory as an approval authority and never invokes a real
CodeGraph binary.

| Case | Expected result |
|---|---|
| `valid-pending` | one atomic claim succeeds |
| `missing`, `invalid-id`, `path-traversal-id` | no runner start |
| `expired`, `already-claimed`, `already-completed`, `replay-completed` | denied without reuse |
| `workspace-mismatch`, `project-mismatch`, `action-mismatch`, `manifest-mismatch` | exact binding denial |
| `symlink`, `unsafe-mode`, `oversized`, `duplicate-key`, `unknown-field`, `unsupported-schema` | unsafe or invalid denial |
| `worker-marker` | worker context denial |
| `concurrent-claim` | exactly one claim succeeds |
| `cli-failure`, `timeout`, `successful-receipt` | claimed receipt remains consumed |

`refresh`, `reindex`, and `bootstrap` are each exercised by the isolated
maintenance integration fixture.
