# PCF-WI-04 — Project Preparation and Unified Project Brief

## Scope

建立 `aota_project_prepare` 與 Unified Project Brief v1，復用既有 workspace resolution、strict Project Contract parser、observed validation、Project Card 與 Registry record schema。

## Acceptance

工具以 registered workspace + exact project ID 作為唯一輸入，重新驗證 canonical manifest，Registry 僅作 candidate lookup。輸出 declared paths/commands/constraints、Plan reference、readiness placeholders、bounded evidence 與 deterministic warnings；禁止執行 commands 或任何 source mutation。

## Validation tier

Tier 0/1：Python syntax、isolated temporary-workspace smoke、既有 project-continuity smoke、plugin registration、package fixture 與 `git diff --check`。不進行 deploy 或 runtime/live verification。

## Known limitations

Git/CodeGraph 未由工具觀測；observed refresh、Registry refresh、Project Bootstrap、Reconcile、Task SPEC/Handoff integration 與 Project Steward 都不在本 Work Item。

## Next handoff

PCF-WI-05 可建立 Bounded CodeGraph Read-only Wrapper，重用 Project Brief identity、relevant paths 與 readiness placeholders；不得提前開放 `init`、`sync` 或 `index`，且任何 runtime action 仍須 human checkpoint。