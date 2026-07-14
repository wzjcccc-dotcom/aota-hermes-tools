# PF-WI-07-2 Level 1 Smoke Plan

Design only. No smoke was executed in PF-WI-07-2.

1. Registration: verify plugin `0.17.6`, `35` tools, and `18` toolsets.
2. Classifier: submit fixed low-risk facts `P0 / A0 / fast`; expect no side
   effect.
3. Missing context: without trusted context, expect
   `ORCHESTRATOR_CONTEXT_UNAVAILABLE`.
4. Wrong principal/authority: reject the request.
5. Worker profile visibility: each of `architect`, `reviewer`, `coder`, and
   `debugger` visibly denies `aota_work_intake`, `aota_plan_read`, and
   `aota_plan_write`.
6. Worker environment: a minimal Profile Task worker may output only:

   ```text
   AOTA_TRUSTED_PRINCIPAL=absent
   AOTA_TRUSTED_AUTHORITIES=absent
   AOTA_TRUSTED_WORKSPACE_ID=absent
   ```

7. Marker defense: a worker marker plus trusted env must be rejected with
   `PLAN_WRITE_FORBIDDEN_FOR_WORKER`.
8. Temporary P1: in a temporary registered workspace, classify P1, create a
   Plan, add one milestone and Work Item, set active IDs, open the Plan,
   create linked SPEC, and freeze SPEC. Do not start an implementation worker.
9. Cleanup: do not partially delete audit ledger data; retain or remove the
   temporary workspace as one whole unit by Human decision.

Level 2 `Plan -> SPEC -> freeze -> Profile Task -> link_task -> result ->
evidence -> review -> closure` is deferred and is not part of activation.
