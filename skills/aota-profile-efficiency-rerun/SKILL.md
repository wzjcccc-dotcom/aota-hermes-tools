---
name: aota-profile-efficiency-rerun
description: Run the managed AOTA deploy, headless Hermes reload, minimal smoke, and fixed 30-sample profile-efficiency rerun with conservative scoring and evidence capture. Use when validating AOTA tool/skill/profile changes or comparing a new run with the previous R7 evidence.
---

# AOTA Profile Efficiency Rerun

## Overview

This is the repeatable validation path for `/home/latios/workspace/aota-hermes-tools`. It is intentionally low-freedom: deploy the managed Forge package, reload the native Hermes host, run one representative smoke per profile, then run the same `r5-30` fixture (5 samples × 6 profiles) and write a bounded evidence JSON. Do not edit product source while running this skill.

## Run

From the repository root, execute the bundled runner. The runner leaves the headless Hermes process running and prints the evidence path:

```sh
rtk python3 skills/aota-profile-efficiency-rerun/scripts/run_profile_efficiency_rerun.py \
  --deploy --reload --samples 30
```

Use `--skip-deploy` or `--skip-reload` only when the user explicitly says that phase is already complete. `--raw-workers 3` is the default: profiles may run concurrently, but the five repetitions inside a profile remain sequential. Keep the fixture, model, provider, limits, and scoring unchanged unless the user authorizes a new experiment.

## Fixed contract

1. Deployment uses `scripts/aota_forge_plan_package.py deploy --allow-known-secret-false-positive`; do not manually copy plugin files.
2. Reload uses native `hermes serve --stop`, then `hermes serve --skip-build --port 0` with `HERMES_HOME=/home/latios/.hermes`. Confirm `HERMES_BACKEND_READY` and Proxy `/health` before samples.
3. Every raw sample gets a fresh `HERMES_SESSION_ID`/`HERMES_SESSION_KEY` plus trusted workspace runtime variables. A raw CLI without those variables is a harness failure, not a profile score.
4. `task-main` is persistent `--cli`; submit the prompt with carriage return (`\r`), wait for final completion/closure, and only then exit. A transient TUI `❯` is never a completion signal.
5. Completion requires parent closure plus worker closure evidence. If a PTY gate times out, query the session database; do not score an early-exited parent as success.
6. The formal cap is 100 end-to-end LLM + tool calls per sample. The task-main parent prompt budget remains 12 tool calls.
7. Conservative route scoring counts a parameter rejection as an incorrect first-pass route even when a changed-argument retry safely recovers. `retryable=false` and `same_call_retryable=false` must never be retried with identical arguments.

## Required checks

The runner must report all of the following before claiming a pass:

- managed deployment receipt and source version;
- native backend readiness and Proxy health;
- 6-profile minimal smoke result;
- 30 formal samples, zero samples over the 100-call cap;
- per-profile scores and weighted composite against the previous R7 evidence;
- evidence JSON under `deploy/evidence/`.

Run the repository contract verifier after the runner. It is a zero-LLM check and should not be replaced by a broad test sweep:

```sh
rtk python3 scripts/verify-aota-profile-efficiency-contracts.py
```

## Findings to report, not hide

Report separate rates for completion, correct route/parameters, retry safety, tool budget, and minimal skill/tool description. A composite above 90% does not mean task-main or coder is stable: call out first-pass dispatch repairs, repeated `tool_describe`, search/read violations, and any PTY or worker wake anomaly.

R8 interpretation anchors:

- Coder's schema-probe fixture intentionally requires one exact-file
  `aota_search_files` call followed by at most one narrow `aota_read_file`.
  The failure signature is `tool_search` or repeated `tool_describe`, not the
  scoped search itself.
- Task-main's P0 dispatch schema is semantic-only, but its optional
  `validation` object has a strict nested schema. For a simple read-only
  architecture probe, omit it; never invent `intent` or `scope` keys. A
  non-empty object may use only `commands`, `strategy`, `evidence_required`,
  `review_dimensions`, `risk_dimensions`, `review_mode` (`design_review` or
  `spec_preflight`), and `operation` from the tool schema.
  `validation_fields_invalid` response is a first-pass route failure even if
  the changed-argument retry later succeeds. A missing classifier fact (for
  example `requirements_ambiguity`) is counted separately as an intake/meta
  overhead signal.
- A successful terminal-background worker, handoff, decision, ack, and closure
  proves the wake path; do not attribute a task-main score loss to wakeup when
  the session database shows that chain closed.

Do not count discarded harness attempts in the 30 samples. Preserve unrelated dirty worktree paths and do not reset, clean, commit, or push as part of validation.
