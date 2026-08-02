#!/usr/bin/env python3
"""Isolated verifier for session-bound AOTA frozen-SPEC resolution.

The fixture is stdlib-only, uses temporary control-plane roots, and never
starts a Profile Task or touches a managed runtime.  It proves the original
workspace-wide ambiguity and the session-local correction, including stale
binding fail-closed behavior and the unchanged semantic start schema.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import types
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "aota-tools"


def load_module(name: str) -> Any:
    package = sys.modules.get("aota_tools")
    if package is None:
        package = types.ModuleType("aota_tools")
        package.__path__ = [str(PLUGIN)]
        sys.modules["aota_tools"] = package
    qualified = f"aota_tools.{name}"
    spec = importlib.util.spec_from_file_location(qualified, PLUGIN / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    spec.loader.exec_module(module)
    return module


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def expect_error(fn: Any, code: str) -> None:
    try:
        fn()
    except Exception as exc:
        check(getattr(exc, "code", None) == code, f"expected {code}, got {exc!r}")
    else:
        raise AssertionError(f"expected {code}")


def make_meta(root: Path, *, task_id: str, status: str = "frozen") -> dict[str, Any]:
    task_dir = root / "profile-tasks" / "ws" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    raw = b"fixture SPEC\n"
    (task_dir / "SPEC.md").write_bytes(raw)
    context = {
        "schema_version": 1,
        "workspace_id": "ws",
        "project_id": "project-one",
        "workspace_registry_digest": "registry-digest",
        "project_manifest_digest": "manifest-digest",
        "project_registry_revision": 1,
        "decision_id": "od_fixture",
    }
    spec = {
        "spec_id": task_id,
        "project_id": "project-one",
        "work_item_id": "wi-one",
        "spec_kind": "diagnosis",
        "resolved_profile": "debugger",
        "revision": 1,
        "workspace_context": context,
        "status": "frozen",
        "frozen_at": "2026-07-27T00:00:00Z",
        "canonical_fixture_hash": "canonical-fixture",
    }
    meta = {
        "contract_version": 1,
        "schema_version": 3,
        "task_id": task_id,
        "workspace_id": "ws",
        "spec_id": task_id,
        "spec_kind": "diagnosis",
        "resolved_profile": "debugger",
        "revision": 1,
        "frozen_revision": 1,
        "status": status,
        "frozen_at": "2026-07-27T00:00:00Z",
        "spec_hash": "canonical-fixture",
        "spec_sha256": hashlib.sha256(raw).hexdigest(),
        "project_id": "project-one",
        "work_item_id": "wi-one",
        "subject_task_id": None,
        "spec": spec,
    }
    (task_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return meta


def write_binding(binding: Any, meta: dict[str, Any], session_id: str) -> None:
    context = meta["spec"]["workspace_context"]
    binding.write_session_active_spec({
        "session_id": session_id,
        "workspace_id": "ws",
        "project_id": meta["project_id"],
        "task_id": meta["task_id"],
        "spec_id": meta["spec_id"],
        "revision": meta["revision"],
        "spec_hash": meta["spec_hash"],
        "spec_sha256": meta["spec_sha256"],
        "frozen_at": meta["frozen_at"],
        "resolved_profile": meta["resolved_profile"],
        "approval_status": "not_required",
        "workspace_registry_digest": context["workspace_registry_digest"],
        "project_manifest_digest": context["project_manifest_digest"],
        "project_registry_revision": context["project_registry_revision"],
        "binding_source": "task_spec_freeze",
    })


def run() -> dict[str, Any]:
    task_common = load_module("_task_spec_common")
    binding = load_module("_session_active_spec_binding")
    resolver = load_module("_reference_resolver")
    freeze = load_module("_task_spec_freeze")
    start = load_module("_profile_task_start")
    approve = load_module("_profile_task_approve")

    original = {
        "profile_root": resolver.PROFILE_TASK_ROOT,
        "task_common_root": task_common.PROFILE_TASK_ROOT,
        "validate_spec": resolver.validate_spec,
        "canonical_hash": resolver.canonical_hash,
        "context_validator": resolver._full_workspace_context,
        "binding_root": binding.SESSION_ACTIVE_SPEC_ROOT,
        "start_do": start._do_start,
        "start_resolve": start.resolve_for_start,
    }
    checks: list[str] = []
    try:
        with tempfile.TemporaryDirectory(prefix="aota-ref-fixture-") as temp:
            fixture = Path(temp)
            resolver.PROFILE_TASK_ROOT = fixture / "profile-tasks"
            task_common.PROFILE_TASK_ROOT = fixture / "profile-tasks"
            binding.SESSION_ACTIVE_SPEC_ROOT = fixture / "session-active-spec"
            resolver.validate_spec = lambda spec, frozen=False: spec
            resolver.canonical_hash = lambda spec: spec["canonical_fixture_hash"]

            def fixture_context(meta: dict[str, Any]) -> dict[str, Any]:
                context = meta["spec"]["workspace_context"]
                if context.get("workspace_id") != meta.get("workspace_id"):
                    raise resolver.ReferenceError("workspace_binding_mismatch")
                if context.get("project_id") != meta["spec"].get("project_id"):
                    raise resolver.ReferenceError("workspace_binding_mismatch")
                if context.get("workspace_registry_digest") != "registry-digest":
                    raise resolver.ReferenceError("workspace_binding_mismatch")
                if context.get("project_manifest_digest") != "manifest-digest":
                    raise resolver.ReferenceError("workspace_binding_mismatch")
                return dict(context)

            resolver._full_workspace_context = fixture_context
            task_a = make_meta(fixture, task_id="task-1")
            resolved = resolver.resolve("ws", "active_frozen_spec")
            check(resolved["task_ref"]["task_id"] == "task-1", "unique fallback did not resolve")
            checks.append("workspace_fallback_unique")

            task_b = make_meta(fixture, task_id="task-2")
            expect_error(lambda: resolver.resolve("ws", "active_frozen_spec"), "reference_ambiguous")
            checks.append("workspace_wide_ambiguity_evidence")

            write_binding(binding, task_a, "session-a")
            write_binding(binding, task_b, "session-b")
            a = resolver.resolve("ws", "active_frozen_spec", trusted_session_id="session-a", trusted_principal="task-main")
            b = resolver.resolve("ws", "active_frozen_spec", trusted_session_id="session-b", trusted_principal="task-main")
            check(a["task_ref"]["task_id"] == "task-1", "session A resolved the wrong SPEC")
            check(b["task_ref"]["task_id"] == "task-2", "session B resolved the wrong SPEC")
            checks.append("multi_frozen_session_bound_resolution")
            write_binding(binding, task_b, "session-a")
            superseded = resolver.resolve("ws", "active_frozen_spec", trusted_session_id="session-a", trusted_principal="task-main")
            check(superseded["task_ref"]["task_id"] == "task-2", "same-session supersede failed")
            checks.append("same_session_freeze_supersedes_binding")
            write_binding(binding, task_a, "session-a")
            isolated = resolver.resolve("ws", "active_frozen_spec", trusted_session_id="session-a", trusted_principal="task-main")
            check(isolated["task_ref"]["task_id"] == "task-1", "session isolation failed")
            checks.append("cross_session_isolation")

            task_a_dir = fixture / "profile-tasks" / "ws" / "task-1"
            meta_path = task_a_dir / "meta.json"
            before = meta_path.read_text(encoding="utf-8")
            mutated = json.loads(before)
            mutated["status"] = "running"
            meta_path.write_text(json.dumps(mutated), encoding="utf-8")
            expect_error(lambda: resolver.resolve("ws", "active_frozen_spec", trusted_session_id="session-a", trusted_principal="task-main"), "reference_stale")
            checks.append("stale_status_fail_closed")
            mutated["status"] = "frozen"
            mutated["revision"] = 2
            meta_path.write_text(json.dumps(mutated), encoding="utf-8")
            expect_error(lambda: resolver.resolve("ws", "active_frozen_spec", trusted_session_id="session-a", trusted_principal="task-main"), "reference_stale")
            checks.append("stale_revision_fail_closed")
            mutated["revision"] = 1
            mutated["spec_hash"] = "wrong-canonical"
            meta_path.write_text(json.dumps(mutated), encoding="utf-8")
            expect_error(lambda: resolver.resolve("ws", "active_frozen_spec", trusted_session_id="session-a", trusted_principal="task-main"), "reference_stale")
            checks.append("stale_canonical_hash_fail_closed")
            mutated["spec_hash"] = "canonical-fixture"
            mutated["spec_sha256"] = "wrong-raw"
            meta_path.write_text(json.dumps(mutated), encoding="utf-8")
            expect_error(lambda: resolver.resolve("ws", "active_frozen_spec", trusted_session_id="session-a", trusted_principal="task-main"), "reference_stale")
            checks.append("stale_raw_sha_fail_closed")
            mutated["spec_sha256"] = task_a["spec_sha256"]
            mutated["spec"]["workspace_context"]["project_manifest_digest"] = "wrong-manifest"
            meta_path.write_text(json.dumps(mutated), encoding="utf-8")
            expect_error(lambda: resolver.resolve("ws", "active_frozen_spec", trusted_session_id="session-a", trusted_principal="task-main"), "reference_stale")
            checks.append("stale_workspace_project_fail_closed")
            meta_path.write_text(before, encoding="utf-8")

            foreign = binding.SESSION_ACTIVE_SPEC_ROOT / "ws" / "session-foreign" / "active-frozen-spec.json"
            foreign.parent.mkdir(parents=True, exist_ok=True)
            foreign.write_text(json.dumps({"session_id": "session-foreign", "workspace_id": "other-ws"}), encoding="utf-8")
            expect_error(lambda: resolver.resolve("ws", "active_frozen_spec", trusted_session_id="session-foreign", trusted_principal="task-main"), "workspace_binding_mismatch")
            checks.append("foreign_workspace_binding_rejected")

            # Freeze's actual publisher is exercised with the isolated fixture.
            freeze_result = freeze._write_active_binding(
                workspace_id="ws", task_dir=task_a_dir, meta=task_a,
                trusted_context=binding.TrustedSessionContext("session-freeze", "task-main", "fixture"),
            )
            check(freeze_result and binding.read_session_active_spec("ws", "session-freeze")["task_id"] == "task-1", "freeze publisher did not write binding")
            checks.append("freeze_creates_atomic_session_binding")

            # Resolver is read-only: no task artifact changed during reads.
            snapshot = meta_path.read_text(encoding="utf-8")
            resolver.resolve("ws", "active_frozen_spec", trusted_session_id="session-a", trusted_principal="task-main")
            check(meta_path.read_text(encoding="utf-8") == snapshot, "resolver mutated task metadata")
            checks.append("no_task_mutation")

            prior_task = os.environ.get("AOTA_PROFILE_TASK_ID")
            prior_start = os.environ.get("AOTA_PROFILE_TASK_START_ID")
            os.environ["AOTA_PROFILE_TASK_ID"] = "worker-task"
            os.environ["AOTA_PROFILE_TASK_START_ID"] = "worker-start"
            expect_error(lambda: resolver.resolve_for_start("ws", "active_frozen_spec"), "worker_binding_change_forbidden")
            checks.append("worker_binding_change_forbidden")
            if prior_task is None:
                os.environ.pop("AOTA_PROFILE_TASK_ID", None)
            else:
                os.environ["AOTA_PROFILE_TASK_ID"] = prior_task
            if prior_start is None:
                os.environ.pop("AOTA_PROFILE_TASK_START_ID", None)
            else:
                os.environ["AOTA_PROFILE_TASK_START_ID"] = prior_start

            captured: list[dict[str, Any]] = []

            def fake_start(args: dict[str, Any], trusted_session_id: Any = None, **_kwargs: Any) -> str:
                captured.append(dict(args))
                return json.dumps({"status": "fixture"})

            start._do_start = fake_start
            legacy = json.loads(start.handle({
                "workspace_id": "ws", "task_id": "legacy-task", "expected_revision": 1,
                "expected_spec_hash": "canonical", "expected_spec_sha256": "raw",
            }))
            check(legacy["status"] == "fixture" and captured[-1]["expected_spec_hash"] == "canonical", "legacy handler compatibility failed")
            checks.append("legacy_explicit_start_compatibility")
            start.resolve_for_start = lambda workspace_id, ref, **kwargs: {
                "workspace_id": "ws", "task_id": "task-1", "expected_revision": 1,
                "expected_spec_hash": "canonical", "expected_spec_sha256": "raw",
                "resolution_source": "session_active_spec",
            }
            semantic = json.loads(start.handle({"task_ref": "active_frozen_spec", "timeout_seconds": 600}, session_id="session-a", profile="task-main"))
            check(semantic["status"] == "fixture" and captured[-1]["task_id"] == "task-1", "semantic start path failed")
            check("workspace_id" not in start.SCHEMA["parameters"]["properties"], "legacy workspace_id remains model-visible")
            check(start.SCHEMA["parameters"]["required"] == ["task_ref"], "semantic start required fields drifted")
            checks.append("canonical_semantic_start_schema")
            check(approve.SCHEMA["parameters"]["required"] == ["decision", "rationale"], "approval schema drifted")
            check(set(approve.SCHEMA["parameters"]["properties"]) == {"decision", "rationale"}, "approval semantic decision surface drifted")
            check(set(approve.SCHEMA["parameters"]["properties"]["decision"]["enum"]) == {"approve", "reject", "request_changes"}, "approval decisions missing")
            check("expected_spec_sha256" not in approve.SCHEMA["parameters"]["properties"], "legacy approval raw hash remains model-visible")
            checks.append("canonical_approval_hash_schema")
            create_source = (PLUGIN / "_task_spec_create.py").read_text(encoding="utf-8")
            freeze_source = (PLUGIN / "_task_spec_freeze.py").read_text(encoding="utf-8")
            check('"draft_spec_sha256"' in create_source, "create response does not label draft SHA")
            check('"spec_sha256": meta["spec_sha256"]' in freeze_source, "freeze response does not expose frozen raw SHA")
            checks.append("draft_frozen_sha_semantics")
    finally:
        resolver.PROFILE_TASK_ROOT = original["profile_root"]
        task_common.PROFILE_TASK_ROOT = original["task_common_root"]
        resolver.validate_spec = original["validate_spec"]
        resolver.canonical_hash = original["canonical_hash"]
        resolver._full_workspace_context = original["context_validator"]
        binding.SESSION_ACTIVE_SPEC_ROOT = original["binding_root"]
        start._do_start = original["start_do"]
        start.resolve_for_start = original["start_resolve"]
    return {"status": "PASS", "checks": checks, "check_count": len(checks)}


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
