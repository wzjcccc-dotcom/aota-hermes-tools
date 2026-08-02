#!/usr/bin/env python3
"""Generate the runtime directory authority inventory from source constants."""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "aota-tools"
OUTPUT = ROOT / "deploy" / "evidence" / "aota-runtime-directory-authority-inventory.json"


def load_package() -> None:
    package = types.ModuleType("aota_tools")
    package.__path__ = [str(PLUGIN)]  # type: ignore[attr-defined]
    sys.modules["aota_tools"] = package
    spec = importlib.util.spec_from_file_location("aota_tools", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)])
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["aota_tools"] = module
    spec.loader.exec_module(module)


def main() -> int:
    load_package()
    from aota_tools import _handoff_common as handoff
    from aota_tools import _orchestration_common as orchestration
    from aota_tools import _session_state_authority as authority
    from aota_tools import _task_spec_common as spec

    runtime_root = str(authority.RUNTIME_ROOT)
    rows = [
        {"path_pattern": "profile-tasks/", "responsibility": "durable SPEC, task, receipt, outcome, card/report artifacts", "authority_type": "durable_history", "mutability": "control-plane lifecycle", "writer": ["task_spec_create", "task_spec_update", "profile_task_start", "finalizer"], "reader": ["reference_resolver", "status", "completion_subject_resolver"], "current_resolver_usage": "legacy compatibility only; current semantic refs use session-state", "durable_history": True, "derived_index": False, "session_scoped": False, "migration_required": False},
        {"path_pattern": "session-state/<workspace>/<project-or-standalone>/<session_digest>/", "responsibility": "current semantic subject pointers, including current_work_classification", "authority_type": "session_state_authority", "mutability": "atomic control-plane pointer writes", "writer": ["session_state_authority", "work_classifier", "task_spec_create", "task_spec_freeze", "profile_task_start", "finalizer", "decision_record", "handoff_ack"], "reader": ["reference_resolver", "task_spec_create", "completion_subject_resolver", "lifecycle handlers"], "current_resolver_usage": "canonical first authority; classification is session-scoped and consumed after SPEC draft publication", "durable_history": False, "derived_index": False, "session_scoped": True, "migration_required": False},
        {"path_pattern": "session-active-spec/<workspace>/<session>/", "responsibility": "legacy active frozen SPEC binding", "authority_type": "legacy_session_pointer", "mutability": "compatibility writer", "writer": ["task_spec_freeze"], "reader": ["reference_resolver"], "current_resolver_usage": "legacy fallback after canonical pointer", "durable_history": False, "derived_index": False, "session_scoped": True, "migration_required": True},
        {"path_pattern": "handoffs/<workspace>/{pending,acknowledged}/", "responsibility": "durable completion handoff history", "authority_type": "durable_history", "mutability": "append/move/acknowledge", "writer": ["finalizer", "handoff_ack"], "reader": ["handoff_open", "completion_subject_resolver"], "current_resolver_usage": "exact artifact named by current-handoff/current-completion pointer", "durable_history": True, "derived_index": False, "session_scoped": False, "migration_required": False},
        {"path_pattern": "decisions/<workspace>/", "responsibility": "durable orchestration decision history", "authority_type": "durable_history", "mutability": "append/update state", "writer": ["decision_record", "decision_resume"], "reader": ["decision_open", "completion_subject_resolver"], "current_resolver_usage": "exact artifact named by current-decision pointer", "durable_history": True, "derived_index": False, "session_scoped": False, "migration_required": False},
        {"path_pattern": "receipts/", "responsibility": "delivery/finalizer receipt transport", "authority_type": "durable_history", "mutability": "append", "writer": ["finalizer", "delivery_adapter"], "reader": ["completion_subject_resolver", "status"], "current_resolver_usage": "validated through current completion binding", "durable_history": True, "derived_index": False, "session_scoped": False, "migration_required": False},
        {"path_pattern": "indexes/", "responsibility": "reserved rebuildable derived indexes", "authority_type": "derived_index", "mutability": "future rebuild only", "writer": [], "reader": [], "current_resolver_usage": "never current authority", "durable_history": False, "derived_index": True, "session_scoped": False, "migration_required": False},
        {"path_pattern": "locks/", "responsibility": "per-artifact and per-pointer concurrency locks", "authority_type": "coordination", "mutability": "ephemeral", "writer": ["atomic writers"], "reader": ["atomic writers"], "current_resolver_usage": "none", "durable_history": False, "derived_index": False, "session_scoped": False, "migration_required": False},
        {"path_pattern": "logs/", "responsibility": "bounded launcher/worker diagnostics", "authority_type": "diagnostic", "mutability": "append/rotate", "writer": ["launcher", "worker", "finalizer"], "reader": ["status", "operator diagnostics"], "current_resolver_usage": "never selects current subject", "durable_history": True, "derived_index": False, "session_scoped": False, "migration_required": False},
        {"path_pattern": "temporary artifacts", "responsibility": "same-directory atomic write temps", "authority_type": "ephemeral", "mutability": "temporary", "writer": ["atomic writers"], "reader": [], "current_resolver_usage": "never", "durable_history": False, "derived_index": False, "session_scoped": True, "migration_required": False},
    ]
    data = {"schema_version": 1, "generated_from": {"session_state_module": str(PLUGIN / "_session_state_authority.py"), "task_spec_common": str(PLUGIN / "_task_spec_common.py"), "handoff_common": str(PLUGIN / "_handoff_common.py"), "orchestration_common": str(PLUGIN / "_orchestration_common.py")}, "runtime_root": runtime_root, "durable_history_root": f"{runtime_root}/profile-tasks", "session_state_root": str(authority.SESSION_STATE_ROOT), "derived_index_root": str(authority.INDEXES_ROOT), "maintenance_root": f"{runtime_root}/maintenance", "source_constants": {"profile_task_root": str(spec.PROFILE_TASK_ROOT), "handoff_root": str(handoff.HANDOFF_ROOT), "decision_root_name": "decisions", "legacy_session_active_spec_root": str(authority.LEGACY_SESSION_ACTIVE_SPEC_ROOT)}, "entries": rows}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"RUNTIME_DIRECTORY_AUTHORITY_INVENTORY=PASS path={OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
