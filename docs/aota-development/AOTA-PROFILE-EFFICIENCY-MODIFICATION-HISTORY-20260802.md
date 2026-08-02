# AOTA Profile Efficiency Modification History — 2026-08-02

This record captures the changes and validation completed for GitHub Issue #2.
It is an evidence index, not a replacement for the source contracts or the
managed deployment receipt.

## 1. Objective and baseline

The objective was to reduce model-facing workflow overhead while preserving
LLM semantic ownership, Profile isolation, bounded AOTA tools, durable
artifacts, and the native `terminal_background` completion path.

The comparable R7 30-sample baseline was 80.7% composite efficiency. The
formal cap remained fewer than 100 end-to-end LLM plus tool calls per sample.

## 2. Modification history

### P0/control-plane convergence

- Kept task-main responsible for semantic objective, acceptance, and decision;
  control-plane code owns binding, materialization, freeze/start, receipts, and
  current-subject resolution only.
- Added semantic subject/reference handling so the model does not need to
  invent artifact IDs, hashes, revisions, or internal paths.
- Added the minimal next-tool schema to the control-plane response, reducing
  repeated `tool_describe` calls.
- Preserved operation-scoped retry behavior; no global same-tool stop latch was
  introduced.
- Preserved P1/P2 plan, work-item, dependency, approval, review, and human
  checkpoint gates.

### Native completion and wake authority

- Kept new Profile Tasks on Hermes native
  `terminal_background` → ProcessRegistry → originating-session resume.
- Retired the legacy/API parent-wake adapter from the active Host path; the
  historical recovery record remains at
  [`HERMES_AGENT_UPDATE_AOTA_PARENT_WAKE_RECOVERY.md`](../HERMES_AGENT_UPDATE_AOTA_PARENT_WAKE_RECOVERY.md).
- No new WebUI/outbox wake dependency was added. Historical outbox receipts
  remain readable but are not used as native completion evidence.

### Profile and Skill changes

- `task-main`: clarified the P0 semantic dispatch envelope and the exact
  optional `validation` schema. Routine read-only architecture probes omit
  `validation`; invented keys such as `intent`, `scope`, and
  `validation_tier` are not accepted.
- `coder`: made `aota_search_files` and `aota_read_file` directly visible for
  the exact-file schema-probe route. The route keeps one exact-file search and
  at most one narrow read; it does not use `tool_search` or `tool_describe` to
  discover those projected schemas.
- Added the repeatable
  [`aota-profile-efficiency-rerun`](../../skills/aota-profile-efficiency-rerun/SKILL.md)
  Skill and runner for managed deploy, native reload, six-profile smoke, and
  the fixed 30-sample fixture.
- Added the Host upgrade note
  [`HERMES-TOOL-SEARCH-CODER-PROJECTION.md`](../aota-host-migration/HERMES-TOOL-SEARCH-CODER-PROJECTION.md)
  for `tools/tool_search.py`, its focused test, and rollback/rebase handling.

## 3. Deployment and runtime evidence

- Source/package version: `0.17.6`.
- Managed backup:
  `/home/latios/workspace/aota-hermes-tools/.deploy-backups/20260802T030100Z`.
- Managed deployment receipt:
  `/home/latios/workspace/aota-hermes-tools/.deploy-receipts/aota-forge-plan/20260802T030102Z/deployment.json`.
- Native reload stopped the previous backend with return code `0`, started a
  backend on port `45997`, and observed Proxy health
  `{"status":"ok","service":"aota-llm-proxy"}`.
- The backend was left running after the authorized validation run.

The generated evidence JSON is
[`aota-profile-efficiency-rerun-20260802.json`](../../deploy/evidence/aota-profile-efficiency-rerun-20260802.json).
Its deployment section predates the receipt-locator fallback and therefore has
`deployment.receipt: null`; the authoritative receipt is the path above and is
also present in the runner output tail. Future runs use the fixed locator.

## 4. Final 30-sample result

| Measure | Result |
|---|---:|
| Composite efficiency | **92.8%** |
| R7 baseline | 80.7% |
| Change from R7 | +12.1 points |
| Outcome completion | 100.0% (30/30) |
| Correct tool/parameter path | 93.3% (28/30) |
| Retry-stop safety | 100.0% (30/30) |
| Tool-budget pass rate | 80.0% (24/30) |
| Minimal Skill/tool-description path | 76.7% (23/30) |
| End-to-end calls | 368 total; 12.27 average/sample |
| Maximum calls in one sample | 41 |
| Samples over 100-call cap | 0 |

Per-Profile scores:

| Profile | Score |
|---|---:|
| project-steward | 90% |
| task-main | 91% |
| architect | 90% |
| reviewer | 98% |
| coder | 95% |
| debugger | 93% |

## 5. Findings and remaining work

- The 90% program target was met, but this is not a claim of perfect or fully
  stable behavior. The minimal-description rate remains the main shared loss.
- Coder direct projection worked: four of five route samples passed. The one
  route miss was a duplicate narrow read after the correct search; completion,
  safety, budget, and minimal-path criteria still passed.
- Task-main completed and routed all five samples correctly, but only two of
  five met the minimal-description criterion; remaining cost is discovery and
  workflow-context overhead rather than wake failure.
- Architect/project-steward lost budget/minimal-path points from extra discovery;
  debugger had one codegraph-heavy route miss. These are follow-up tuning items,
  not reasons to broaden the control plane or add a global tool lock.
- The contract verifier passed all nine zero-LLM gates, including next-schema,
  scoped repair, semantic subject, exact-path routing, 30-sample plan, and
  zero-LLM efficiency fixtures.

## 6. Reproduction and rollback

From the repository root:

```sh
rtk python3 skills/aota-profile-efficiency-rerun/scripts/run_profile_efficiency_rerun.py \
  --deploy --reload --samples 30
rtk python3 scripts/verify-aota-profile-efficiency-contracts.py
```

The runner uses Forge managed deployment and records a backup/receipt. Do not
manually copy runtime files, reset the dirty worktree, or use a WebUI wake
fallback. If the Host projection is incompatible after a Hermes update, follow
the coder projection upgrade/rollback note and keep the deferred-tool bridge for
all unrelated coder capabilities.

