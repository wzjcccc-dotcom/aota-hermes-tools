# PCF-WI-01

## Scope

建立 strict project.yaml / observed.json contract、deterministic Project Card，以及 workspace-bound 的 read-only scan/search/open tools。第一 adoption target 是 `aota-hermes-tools`；本 source work 不建立真實 `.aota/observed.json`，也不修改 `.deploy-receipts/`。

## Acceptance mapping

- Contract：`plugin/aota-tools/_project_common.py`
- Discovery：`_project_discovery.py`
- Search/open/card：`_project_open.py`
- Registration：`__init__.py`、`plugin.yaml`
- Fixtures/smoke：`fixtures/project-continuity/`、`scripts/verify-project-continuity.py`
- Documentation：`PROJECT-CONTRACT.md`、`PROJECT-CARD.md`

## Validation tier

Source-level only：Python compile、strict fixture smoke、plugin schema/manifest readiness。未執行 deploy、reload、restart、live worker、runtime tool load 或 CodeGraph runtime verification。

## Known limitations and deferred work

尚無 persisted Project Registry、project bootstrap/preparation/reconcile、Project Steward、Plan schema/project_id 整合、CodeGraph CLI wrapper、Profile routing 或 runtime smoke。PCF scan 每次 bounded walk allowed workspace，沒有 daemon、database、embedding 或 cache。

若未來需在 runtime 啟用新 tools，應另立 deployment/human-checkpoint work item，更新 profile toolset policy 並做真實 model-driven smoke。
