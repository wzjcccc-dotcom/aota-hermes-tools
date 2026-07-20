# PCF-WI-05B — Bounded CodeGraph Read-only Wrapper

## Scope

Adds source implementations and registration for `aota_codegraph_status`, `aota_codegraph_query` and `aota_codegraph_explore`. The shared wrapper resolves one trusted executable, validates a registered workspace plus canonical project identity, runs fixed CodeGraph argv with process/output bounds and returns reduced safe output.

`aota_project_prepare` now projects either `source_wrapper_available_runtime_not_verified` or `runtime_unavailable`; it never executes CodeGraph during preparation.

## Acceptance coverage

The isolated smoke creates a temporary workspace, index, private runtime root and executable fake launcher. It verifies status/query/explore parsing, project-relative paths, ANSI removal, leading-dash rejection, argv preservation without shell injection, malformed JSON, non-zero exit, secret-stderr redaction, telemetry HOME permission failure, stdout/stderr overflow, timeout and whole process-group child/grandchild reaping. It asserts that the project source/index digest is unchanged and that temporary fixtures are removed. The source runner additionally enforces fixed subcommands, `shell=False`, a minimal environment, Linux child-subreaper cleanup, process-group kill on timeout/output cap and output caps.

## Validation tier

Tier 0/1 only: syntax/compile, project-continuity isolated smoke, fake-launcher subprocess smoke, registration and package checks. No deployment, reload, restart, live Profile smoke, CodeGraph activation, sync, init or index command is performed.

## Limitations

The actual plugin runtime launcher, bundled Node, runtime user/permissions, path/mount, controlled telemetry HOME and profile tool allowlists remain unverified. Telemetry policy is a source contract only. Existing external package/compose readiness is intentionally unchanged.

## Next handoff

**PCF-WI-05C — CodeGraph Runtime Activation and Live Verification**: confirm the trusted declaration and runtime identity, approve deploy/reload, verify launcher permissions and telemetry HOME, then use model-driven live status/query/explore smoke with rollback evidence.