#!/usr/bin/env python3
"""Isolated finalizer loader and session-state projection verifier."""
from __future__ import annotations

import importlib.util
import inspect
import concurrent.futures
import os
import sys
import tempfile
import threading
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
FINALIZER_PATH = ROOT / "plugin" / "aota-tools" / "_profile_task_finalize.py"
WORKSPACE = "fixture-workspace"
PROJECT = "fixture-project"
SESSION = "fixture-session"
TASK = "pt_20260729T000000_deadbeef"
HANDOFF = "ho_20260729T000000_deadbeef"


def load_finalizer():
    spec = importlib.util.spec_from_file_location(
        "aota_profile_task_finalize_fixture", FINALIZER_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_module(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def old_loader(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        return module, exc
    return module, None


def main() -> int:
    finalizer = load_finalizer()
    with tempfile.TemporaryDirectory(prefix="aota-finalizer-projection-") as raw:
        root = Path(raw)
        dataclass_path = root / "dataclass_fixture.py"
        write_module(
            dataclass_path,
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class Example:\n"
            "    value: str\n"
            "MODULE_REGISTERED_AT_EXEC = __name__ in __import__('sys').modules\n",
        )
        old_name = "aota_old_loader_fixture"
        old_module, old_error = old_loader(dataclass_path, old_name)
        old_registered = sys.modules.get(old_name) is old_module
        assert old_error is not None or not old_registered
        sys.modules.pop(old_name, None)
        print("OLD_LOADER_REGISTRATION_CONTRACT=PASS")

        fixed_name = "aota_registered_loader_fixture"
        fixed = finalizer._load_module_from_path_registered(fixed_name, dataclass_path)
        assert fixed.Example("ok").value == "ok"
        assert fixed.MODULE_REGISTERED_AT_EXEC is True
        assert sys.modules[fixed_name] is fixed
        print("MODULE_REGISTERED_BEFORE_EXEC=yes")
        print("DATACLASS_MODULE_LOAD=PASS")

        failing_path = root / "failing_fixture.py"
        write_module(failing_path, "raise RuntimeError('fixture import failure')\n")
        failed_name = "aota_failed_loader_fixture"
        try:
            finalizer._load_module_from_path_registered(failed_name, failing_path)
        except RuntimeError as exc:
            assert str(exc) == "fixture import failure"
        else:
            raise AssertionError("failing fixture unexpectedly loaded")
        assert failed_name not in sys.modules
        print("IMPORT_FAILURE_CLEANUP=PASS")

        prior_name = "aota_prior_loader_fixture"
        prior = types.ModuleType(prior_name)
        prior.__file__ = str(root / "prior.py")
        sys.modules[prior_name] = prior
        try:
            finalizer._load_module_from_path_registered(prior_name, failing_path)
        except ImportError:
            pass
        else:
            raise AssertionError("prior module collision was overwritten")
        assert sys.modules[prior_name] is prior
        print("PRIOR_MODULE_PRESERVATION=PASS")

        repeated = finalizer._load_module_from_path_registered(fixed_name, dataclass_path)
        assert repeated is fixed
        assert repeated.Example.__module__ == fixed_name
        assert sys.modules[repeated.Example.__module__] is repeated
        print("REPEATED_LOAD=PASS")

        concurrent_name = "aota_concurrent_loader_fixture"
        barrier = threading.Barrier(4)

        def concurrent_load():
            barrier.wait()
            return finalizer._load_module_from_path_registered(
                concurrent_name, dataclass_path
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            concurrent_modules = list(pool.map(lambda _item: concurrent_load(), range(4)))
        assert all(module is concurrent_modules[0] for module in concurrent_modules)
        print("CONCURRENT_LOAD=PASS")

        old_runtime = os.environ.get("AOTA_RUNTIME_ROOT")
        old_session_root = os.environ.get("AOTA_SESSION_STATE_ROOT")
        os.environ["AOTA_RUNTIME_ROOT"] = str(root / "runtime")
        os.environ["AOTA_SESSION_STATE_ROOT"] = str(root / "runtime" / "session-state")
        try:
            meta = {
                "origin_session_id": SESSION,
                "project_id": PROJECT,
                "revision": 1,
                "spec_hash": "b" * 64,
                "spec_sha256": "a" * 64,
                "execution": {"outcome": "completed"},
            }
            projected = finalizer._write_session_state_pointers(
                workspace_id=WORKSPACE,
                task_id=TASK,
                start_id=TASK,
                profile="debugger",
                meta=meta,
                task_dir=root / "task",
                handoff_id=HANDOFF,
                terminal_status="done",
                completed_at="2026-07-29T00:00:00Z",
            )
            assert projected["status"] == "written"
            assert projected["active_task_terminal"] is True
            assert projected["current_completion_written"] is True
            assert projected["current_handoff_written"] is True
            assert projected["completion_subject_ready"] is True
            authority = sys.modules[finalizer._SESSION_STATE_AUTHORITY_MODULE_NAME]
            context = SimpleNamespace(
                session_id=SESSION,
                principal="task-main",
                profile="task-main",
                workspace_id=WORKSPACE,
                worker_context=False,
            )
            active = authority.read_pointer_for_context(
                authority.POINTER_KIND_ACTIVE_TASK, context
            )
            completion = authority.read_pointer_for_context(
                authority.POINTER_KIND_CURRENT_COMPLETION, context
            )
            handoff = authority.read_pointer_for_context(
                authority.POINTER_KIND_CURRENT_HANDOFF, context
            )
            assert active and active["state"] == "terminal"
            assert completion and completion["state"] == "active"
            assert handoff and handoff["state"] == "active"
            assert completion["task_id"] == TASK and handoff["handoff_id"] == HANDOFF
            print("FINALIZER_AUTHORITY_LOAD=PASS")
            print("ACTIVE_TASK_TERMINAL_PROJECTION=PASS")
            print("CURRENT_COMPLETION_PROJECTION=PASS")
            print("CURRENT_HANDOFF_PROJECTION=PASS")
            print("COMPLETION_SUBJECT_READY=PASS")

            original_loader = finalizer._load_module_from_path_registered
            finalizer._load_module_from_path_registered = lambda *_args: (_ for _ in ()).throw(RuntimeError("loader fixture failure"))
            try:
                load_failed = finalizer._write_session_state_pointers(
                    workspace_id=WORKSPACE,
                    task_id=TASK,
                    start_id=TASK,
                    profile="debugger",
                    meta=meta,
                    task_dir=root / "task",
                    handoff_id=HANDOFF,
                    terminal_status="done",
                    completed_at="2026-07-29T00:00:00Z",
                )
            finally:
                finalizer._load_module_from_path_registered = original_loader
            assert load_failed["failure_stage"] == "authority_module_load"
            assert load_failed["retryable"] is False
            assert load_failed["exception_type"] == "RuntimeError"
            print("AUTHORITY_LOAD_FAILURE_CONTRACT=PASS")

            invalid = dict(meta, project_id="invalid/project")
            failed = finalizer._write_session_state_pointers(
                workspace_id=WORKSPACE,
                task_id=TASK,
                start_id=TASK,
                profile="debugger",
                meta=invalid,
                task_dir=root / "task",
                handoff_id=HANDOFF,
                terminal_status="done",
                completed_at="2026-07-29T00:00:01Z",
            )
            assert failed["status"] == "failed"
            assert failed["failure_stage"] == "pointer_projection"
            assert failed["retryable"] is False
            assert failed["exception_type"]
            print("FAILURE_PATH_FAIL_CLOSED=PASS")
        finally:
            if old_runtime is None:
                os.environ.pop("AOTA_RUNTIME_ROOT", None)
            else:
                os.environ["AOTA_RUNTIME_ROOT"] = old_runtime
            if old_session_root is None:
                os.environ.pop("AOTA_SESSION_STATE_ROOT", None)
            else:
                os.environ["AOTA_SESSION_STATE_ROOT"] = old_session_root

        source = inspect.getsource(finalizer._write_session_state_pointers)
        assert "rglob" not in source and "workspace-wide" not in source
        print("NO_HISTORY_FALLBACK=PASS")
    print("AOTA_SESSION_STATE_FINALIZER_PROJECTION_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
