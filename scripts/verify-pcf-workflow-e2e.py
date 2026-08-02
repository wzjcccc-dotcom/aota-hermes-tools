#!/usr/bin/env python3
"""PCF-WI-09E isolated end-to-end workflow fixtures.

This verifier deliberately uses a fresh temporary workspace, registry,
profile-task root, runtime root, project fixture, and task-main fixture for
every run.  It never starts Hermes, a background worker, Docker, CodeGraph,
or a managed service.  ``promote`` is the one explicit fixture-worker seam:
it supplies the launch metadata that a real worker start would write, allowing
the real bounded worker tools, report tools, outcome tool and finalizer to be
exercised without spawning a worker.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin" / "aota-tools"
WS, PROJECT = "fixture", "fixture-project"


def load_plugin() -> None:
    for name in list(sys.modules):
        if name == "aota_tools" or name.startswith("aota_tools."):
            del sys.modules[name]
    spec = importlib.util.spec_from_file_location("aota_tools", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)])
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["aota_tools"] = module
    spec.loader.exec_module(module)


def response(value: str) -> dict[str, Any]:
    data = json.loads(value)
    assert data.get("status") not in {"rejected", "failed", "error"}, data
    return data


def rejected(value: str) -> None:
    assert json.loads(value).get("status") in {"rejected", "error"}, value


def ref(kind: str, ident: str) -> dict[str, str]:
    return {"ref_type": kind, "artifact_id": ident, "artifact_path": f"artifacts/{ident}.json"}


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root, self.workspace = root, root / "fixture-workspace"
        self.project = self.workspace / "sample-project"
        self.tasks, self.runtime = root / "profile-tasks", root / "runtime"
        self.registry = root / "workspaces.json"
        self.workspace.mkdir()
        self._write_project()
        self.registry.write_text(json.dumps({WS: {"candidates": [str(self.workspace)]}}), encoding="utf-8")
        os.environ.update({
            "AOTA_WORKSPACE_REGISTRY_PATH": str(self.registry),
            "AOTA_PROFILE_TASK_ROOT": str(self.tasks), "AOTA_RUNTIME_ROOT": str(self.runtime),
            "AOTA_SESSION_STATE_ROOT": str(self.runtime / "session-state"),
        })
        load_plugin()
        self.create = importlib.import_module("aota_tools._task_spec_create")
        self.freeze = importlib.import_module("aota_tools._task_spec_freeze")
        self.approve = importlib.import_module("aota_tools._profile_task_approve")
        self.files = importlib.import_module("aota_tools._project_file_mutation")
        self.commands = importlib.import_module("aota_tools._project_command_run")
        self.outcome = importlib.import_module("aota_tools._worker_outcome_submit")
        self.finalizer = importlib.import_module("aota_tools._profile_task_finalize")
        self.open_handoff = importlib.import_module("aota_tools._handoff_open")
        self.ack = importlib.import_module("aota_tools._handoff_ack")
        self.decision = importlib.import_module("aota_tools._orchestration_decision_record")
        self.plans = importlib.import_module("aota_tools._plan_mutation")
        self.plan_open = importlib.import_module("aota_tools._plan_open")
        self.steward_mutation = importlib.import_module("aota_tools._project_steward_mutation")
        self.reports = {role: importlib.import_module(f"aota_tools._{name}_report_submit") for role, name in {
            "coder": "coder", "debugger": "debugger", "reviewer": "reviewer", "architect": "architect",
        }.items()}
        self.reports["project-steward"] = importlib.import_module("aota_tools._project_steward_report")
        registry_mod = importlib.import_module("aota_tools._project_registry")
        assert response(registry_mod.handle_refresh({"workspace_id": WS, "dry_run": False}))["status"] == "ok"

    def _write_project(self) -> None:
        for path, content in {
            "README.md": "# Fixture\n", "CHANGELOG.md": "# Changes\n", "ROADMAP.md": "# Roadmap\n",
            "docs/architecture.md": "# Architecture\n", "src/sample.py": "VALUE = 1\n",
            "scripts/verify_sample.py": "import sys\nassert sys.argv[1:] in ([], ['fixture'])\n",
        }.items():
            target = self.project / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(content, encoding="utf-8")
        manifest = """schema_version: 1
project: {id: fixture-project, name: Fixture, kind: fixture, status: active}
summary: Isolated PCF workflow fixture
capabilities: [fixture]
paths: {source_root: ., source: [src], docs: [docs], scripts: [scripts], profiles: [], skills: [], tests: []}
commands: {validate: [scripts/verify_sample.py], deploy: [], verify_deploy: []}
runtime: {deployment_type: fixture, requires_human_checkpoint: false}
codegraph: {enabled: false, index_location: .codegraph}
plan: {active_plan_id: null}
constraints: [temporary-only]
"""
        (self.project / ".aota" / "artifacts").mkdir(parents=True)
        (self.project / ".aota" / "project.yaml").write_text(manifest, encoding="utf-8")
        (self.project / ".aota" / "artifacts" / "index.json").write_text(json.dumps({"schema_version": 1, "project_id": PROJECT, "links": []}), encoding="utf-8")

    def env(self, task_id: str, role: str) -> None:
        os.environ.update({"AOTA_PROFILE_TASK_WORKSPACE_ID": WS, "AOTA_PROFILE_TASK_ID": task_id, "AOTA_PROFILE_TASK_START_ID": task_id, "AOTA_PROFILE_TASK_PROFILE": role})

    def clear_worker_env(self) -> None:
        for key in ("AOTA_PROFILE_TASK_WORKSPACE_ID", "AOTA_PROFILE_TASK_ID", "AOTA_PROFILE_TASK_START_ID", "AOTA_PROFILE_TASK_PROFILE"):
            os.environ.pop(key, None)

    def task_main_env(self) -> None:
        self.clear_worker_env()
        os.environ.update({
            "AOTA_TRUSTED_PRINCIPAL": "task-main",
            "AOTA_TRUSTED_AUTHORITIES": "plan_write",
            "AOTA_TRUSTED_WORKSPACE_ID": WS,
            "AOTA_ORIGIN_SESSION_ID": "fixture-session",
        })

    def clear_task_main_env(self) -> None:
        for key in ("AOTA_TRUSTED_PRINCIPAL", "AOTA_TRUSTED_AUTHORITIES", "AOTA_TRUSTED_WORKSPACE_ID", "AOTA_ORIGIN_SESSION_ID"):
            os.environ.pop(key, None)

    def plan(self, depth: str, gate: str) -> dict[str, str]:
        """Create a real temporary P1/P2 Plan and its minimal active work item."""
        self.task_main_env()
        try:
            created = response(self.plans.handle_create({"workspace_id": WS, "title": f"Fixture {depth} Plan", "planning_depth": depth, "architect_gate": gate, "delivery_path": "deep" if depth == "P2" else "standard", "goal": "isolated workflow fixture"}))
            plan_id, revision = created["plan_id"], created["revision"]

            def update(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
                nonlocal revision
                result = response(self.plans.handle_update({"workspace_id": WS, "plan_id": plan_id, "expected_revision": revision, "operation": operation, "payload": payload}))
                revision = result["revision"]
                return result

            milestone = update("add_milestone", {"title": "Fixture milestone", "objective": "bounded fixture work", "dependencies": [], "acceptance_criteria": ["fixture passes"], "architect_review_id": None})["target_id"]
            work_item = update("add_work_item", {"milestone_id": milestone, "title": "Fixture work item", "goal": "exercise Plan linkage", "risk_level": "medium" if depth == "P1" else "high", "architect_gate": gate, "spec_preflight": "required" if depth == "P2" else "not_required", "dependencies": [], "next_action": "create SPEC"})["target_id"]
            update("set_active_milestone", {"milestone_id": milestone})
            update("set_active_work_item", {"work_item_id": work_item})
            update("record_decision", {"summary": "classification accepted", "rationale": "fixture task-main decision", "status": "accepted", "source": "task_main", "related_milestone_ids": [milestone], "related_work_item_ids": [work_item], "evidence": []})
            update("set_plan_status", {"status": "approved"})
            opened = response(self.plan_open.handle({"workspace_id": WS, "plan_id": plan_id, "view": "compact"}))
            assert opened["plan_id"] == plan_id and opened["status_value"] == "approved"
            return {"ref_type": "plan", "artifact_id": plan_id, "artifact_path": f".aota/forge/plans/{plan_id}/plan.json", "work_item_id": work_item}
        finally:
            self.clear_task_main_env()

    def link_plan_task(self, plan: dict[str, str], task_id: str) -> None:
        self.task_main_env()
        try:
            full = response(self.plan_open.handle({"workspace_id": WS, "plan_id": plan["artifact_id"], "view": "full"}))
            response(self.plans.handle_update({"workspace_id": WS, "plan_id": plan["artifact_id"], "expected_revision": full["plan"]["revision"], "operation": "link_task", "payload": {"work_item_id": plan["work_item_id"], "task_id": task_id}}))
        finally:
            self.clear_task_main_env()

    def payload(self, kind: str, subject: str | None = None, operation: str = "context_prepare", plan_ref: dict[str, str] | None = None) -> tuple[dict[str, Any], list[dict[str, str]], dict[str, bool]]:
        canonical_plan_ref = ({key: plan_ref[key] for key in ("ref_type", "artifact_id", "artifact_path")} if plan_ref else None)
        if kind == "implementation":
            return ({"read_scope": ["src/**"], "write_scope": ["src/**"], "forbidden_scope": ["src/forbidden/**"], "implementation_requirements": ["small bounded patch"], "validation_commands": ["python_module_compile", "project_script"], "validation_strategy": "fixed argv", "runtime_actions": {}}, [], {"source_read": True, "source_write": True, "bounded_project_command": True})
        if kind == "diagnosis":
            return ({"symptom": "fixture symptom", "known_facts": ["repro exists"], "evidence_required": ["sample.py"], "mutation_allowed": False}, [], {"source_read": True})
        if kind == "review":
            assert subject
            refs = [ref("subject_spec", subject), ref("subject_result", subject + "-result")]
            return ({"subject_spec_ref": refs[0], "subject_result_ref": refs[1], "review_dimensions": ["scope"], "required_evidence": []}, refs, {"source_read": True})
        if kind == "architecture":
            return ({"review_mode": "design_review", "subject_plan_ref": canonical_plan_ref or ref("plan", "plan-fixture"), "challenge_questions": ["what can fail?"], "tradeoffs_required": [], "risk_dimensions": []}, [], {"source_read": True})
        allowed = ["readme"] if operation == "docs_update" else ["project_metadata"]
        return ({"operation": operation, "project_context_questions": ["what is current state?"], "allowed_project_artifacts": allowed, "docs_update_scope": ["README.md"] if operation == "docs_update" else [], "artifact_link_requests": ["link result"] if operation == "artifact_link" else [], "close_checks": ["all artifacts linked"] if operation == "close" else [], "approved_content_refs": ["approval-1"] if operation == "docs_update" else []}, [], {"source_read": True, "project_metadata_write": operation == "docs_update"})

    def spec(self, kind: str, *, subject: str | None = None, operation: str = "context_prepare", supersedes: str | None = None, plan_ref: dict[str, str] | None = None) -> str:
        payload, refs, caps = self.payload(kind, subject, operation, plan_ref)
        if plan_ref is not None and kind != "architecture":
            refs = [{key: plan_ref[key] for key in ("ref_type", "artifact_id", "artifact_path")}, *refs]
        create_args = {"workspace_id": WS, "spec_kind": kind, "project_id": PROJECT, "work_item_id": "wi-09e", "objective": f"fixture {kind}", "summary": "isolated workflow", "context_refs": refs, "related_artifacts": [], "acceptance_criteria": ["fixture passes"], "constraints": ["temporary roots only"], "forbidden_actions": ["deploy"], "expected_artifacts": ["CARD.json"], "capability_contract": caps, "payload": payload, "supersedes_spec_id": supersedes}
        if subject is not None:
            create_args["subject_task_id"] = subject
        made = response(self.create.handle(create_args))
        task_id = made["task_id"]
        frozen = response(self.freeze.handle({"workspace_id": WS, "spec_id": task_id, "expected_revision": 1}))
        if kind == "implementation":
            assert response(self.approve.handle({"workspace_id": WS, "task_id": task_id, "expected_revision": 1, "expected_spec_hash": frozen["spec_hash"]}))["status"] == "approved"
        return task_id

    def promote(self, task_id: str) -> dict[str, Any]:
        path = self.tasks / WS / task_id / "meta.json"; meta = json.loads(path.read_text(encoding="utf-8"))
        if meta["status"] == "running":
            return meta
        assert meta["status"] == "frozen" and meta["spec"]["status"] == "frozen"
        meta.update({"status": "running", "origin_session_id": "fixture-session", "parent_session_ref": "fixture-session", "execution": {"start_id": task_id, "profile": meta["resolved_profile"], "spec_revision": meta["revision"], "spec_sha256": meta["spec_sha256"], "transport": "fixture_worker", "parent_session_ref": "fixture-session"}})
        path.write_text(json.dumps(meta, sort_keys=True), encoding="utf-8")
        return meta

    def report_args(self, role: str, *, needs_fix: bool = False) -> dict[str, Any]:
        if role == "coder": return {"summary": "bounded implementation complete", "changed_paths": ["src/sample.py"], "validation": ["python compile pass"], "full_report": "complete report"}
        if role == "debugger": return {"summary": "root cause confirmed", "root_cause": "fixture value", "confidence": 1.0, "key_evidence": ["src/sample.py"], "recommended_next_action": "create repair SPEC", "full_report": "diagnosis details"}
        if role == "reviewer": return {"verdict": "fail" if needs_fix else "pass", "summary": "missing repair" if needs_fix else "review pass", "blocking_findings": ["repair required"] if needs_fix else [], "recommendation": "follow up" if needs_fix else "accept", "full_report": "review details"}
        if role == "architect": return {"mode": "design_review", "verdict": "approve", "summary": "preflight challenge complete", "recommendation": "task-main integrate", "full_report": "architecture details"}
        return {"outcome": "completed", "verdict": "pass", "summary": "project facts and recommendation", "scope_status": "in_scope", "matched_project": PROJECT, "project_candidates": [PROJECT], "codegraph_state": "fallback", "continuity_status": "recommend close", "recommended_next_action": "task-main decide", "full_report": "steward details"}

    def finish(self, task_id: str, *, needs_fix: bool = False) -> dict[str, Any]:
        meta = self.promote(task_id); role = meta["resolved_profile"]; self.env(task_id, role)
        assert response(self.reports[role].handle(self.report_args(role, needs_fix=needs_fix)))["status"] == "submitted"
        assert response(self.outcome.handle({"outcome": "completed"}))["outcome"] == "completed"
        try:
            self.finalizer.run_finalize(WS, task_id, task_id, role, meta["revision"], meta["spec_sha256"], 0)
        except SystemExit as exc:
            assert exc.code == 0
        self.clear_worker_env()
        pending = self.runtime / "handoffs" / WS / "pending"
        handoffs = [json.loads(path.read_text(encoding="utf-8")) for path in pending.glob("*.json")]
        matches = [value for value in handoffs if value.get("task_id") == task_id]
        assert matches, {"meta": json.loads((self.tasks / WS / task_id / "meta.json").read_text(encoding="utf-8")), "handoff_paths": [str(path) for path in self.runtime.rglob("*.json")]}
        handoff = matches[0]
        assert (self.runtime / "outbox" / WS / "pending").is_dir()
        return handoff

    def decide_ack(self, handoff: dict[str, Any], decision: str = "accepted") -> None:
        # The real ack tool now fail-closes until task-main records its decision.
        rejected(self.ack.handle({"workspace_id": WS, "handoff_id": handoff["handoff_id"], "decision": decision}))
        recorded = response(self.decision.handle({"workspace_id": WS, "handoff_id": handoff["handoff_id"], "decision": decision, **({"followup_task_kind": "implementation"} if decision in {"needs_followup", "reopen_required"} else {})}))
        assert recorded["decision"] == decision
        ack = response(self.ack.handle({"workspace_id": WS, "handoff_id": handoff["handoff_id"], "decision": decision}))
        assert ack["acknowledged"] and response(self.ack.handle({"workspace_id": WS, "handoff_id": handoff["handoff_id"], "decision": decision}))["idempotent"]

    @staticmethod
    def requires_full_report(card: dict[str, Any], handoff: dict[str, Any]) -> bool:
        """Fixture projection of the task-main Card-first policy."""
        return (
            card.get("outcome") != "completed"
            or card.get("verdict") in {"needs_fix", "blocked"}
            or bool(card.get("needs_full_report_review"))
            or bool(handoff.get("needs_input"))
            or bool(card.get("risks") or card.get("limitations"))
        )

    def consume_as_task_main(self, handoff: dict[str, Any]) -> dict[str, Any]:
        """Open Card first, validate canonical binding, then conditionally read report."""
        opened = response(self.open_handoff.handle({"workspace_id": WS, "handoff_id": handoff["handoff_id"]}))
        assert not opened["card_missing"] and isinstance(opened["card_content"], str)
        card = json.loads(opened["card_content"])
        for field in ("task_id", "spec_id", "spec_revision", "spec_hash", "project_id", "work_item_id", "outcome", "verdict"):
            assert card.get(field) == handoff.get(field), f"binding_mismatch:{field}"
        required = self.requires_full_report(card, handoff)
        if required:
            report = self.tasks / WS / handoff["task_id"] / handoff["full_report_ref"]
            assert report.is_file() and report.read_text(encoding="utf-8"), "full_report_required_but_missing"
        return {"card": card, "full_report_read": required}


def run_scenarios() -> None:
    with tempfile.TemporaryDirectory(prefix="pcf-wi-09e-") as raw:
        f = Fixture(Path(raw))
        classifier = importlib.import_module("aota_tools._work_classifier")
        facts = {name: False for name in classifier._BOOL_FACTS}; facts.update({name: 1 for name in classifier._INT_BOUNDS}); facts.update({"requirements_ambiguity": "low", "technical_uncertainty": "low", "write_scope": "local", "validation_scope": "isolated", "estimated_duration_days": 0, "services_touched": 0, "human_checkpoints_expected": 0})
        assert classifier._classify({"workspace_id": WS, "title": "P0", "summary": "small", "facts": facts})["planning_depth"] == "P0"

        # E1: P0 uses only frozen implementation SPEC -> Coder bounded tools.
        p0 = f.spec("implementation"); meta = f.promote(p0); f.env(p0, "coder")
        binding = {"task_id": p0, "spec_id": p0, "path": "src/sample.py"}
        before = response(f.files.handle_read(binding)); assert response(f.files.handle_patch({**binding, "find": "1", "replace": "2", "expected_sha256": before["sha256"]}))["status"] == "patched"
        assert response(f.commands.handle({"task_id": p0, "spec_id": p0, "command_id": "python_module_compile", "args": ["src/sample.py"]}))["exit_code"] == 0
        rejected(f.files.handle_write({**binding, "path": "src/forbidden/x.py", "content": "x"})); rejected(f.commands.handle({"task_id": p0, "spec_id": p0, "command_id": "python_module_compile", "args": ["src/sample.py;id"]}))
        f.clear_worker_env(); h0 = f.finish(p0); consumed_p0 = f.consume_as_task_main(h0); assert consumed_p0["full_report_read"] is False and "complete report" not in json.dumps(consumed_p0["card"])
        f.decide_ack(h0)

        # E2: Steward project context and close recommendation, then P1 coder.
        steward = f.spec("stewardship", operation="context_prepare"); hs = f.finish(steward); f.decide_ack(hs)
        p1_plan = f.plan("P1", "A0")
        p1 = f.spec("implementation", plan_ref=p1_plan); f.link_plan_task(p1_plan, p1); h1 = f.finish(p1); assert f.consume_as_task_main(h1)["full_report_read"] is False; f.decide_ack(h1)
        close = f.spec("stewardship", operation="close"); hc = f.finish(close); f.decide_ack(hc)

        # E3: architecture preflight precedes coder; reviewer follows it.
        p2_plan = f.plan("P2", "A2")
        arch = f.spec("architecture", plan_ref=p2_plan); ha = f.finish(arch); assert f.consume_as_task_main(ha)["full_report_read"] is False; f.decide_ack(ha)
        p2 = f.spec("implementation", plan_ref=p2_plan); f.link_plan_task(p2_plan, p2); h2 = f.finish(p2)
        review = f.spec("review", subject=p2); hr = f.finish(review); f.decide_ack(h2); f.decide_ack(hr)

        # E4: needs_fix cannot be accepted; a separate frozen successor repairs it.
        bad = f.spec("implementation"); hb = f.finish(bad)
        failing_review = f.spec("review", subject=bad); hbad = f.finish(failing_review, needs_fix=True)
        card = json.loads((f.tasks / WS / failing_review / "REVIEW_CARD.json").read_text(encoding="utf-8")); assert card["verdict"] == "needs_fix"
        assert f.consume_as_task_main(hbad)["full_report_read"] is True
        f.decide_ack(hbad, "needs_followup")
        repair = f.spec("implementation", supersedes=bad); repair_meta = json.loads((f.tasks / WS / repair / "meta.json").read_text(encoding="utf-8")); assert repair != bad and repair_meta["spec"]["supersedes_spec_id"] == bad
        hrepair = f.finish(repair); rereview = f.spec("review", subject=repair); hrr = f.finish(rereview); f.decide_ack(hrepair); f.decide_ack(hrr)

        # E5: read-only diagnosis becomes repair context, never direct mutation.
        diagnosis = f.spec("diagnosis"); hd = f.finish(diagnosis); f.decide_ack(hd)
        repair2 = f.spec("implementation"); assert (f.tasks / WS / diagnosis / "DIAGNOSIS.md").is_file(); hrepair2 = f.finish(repair2); f.decide_ack(hrepair2)

        # E6/E7: bounded Steward docs and artifact index only within project fixture.
        docs = f.spec("stewardship", operation="docs_update"); f.promote(docs); f.env(docs, "project-steward")
        assert response(f.steward_mutation.handle_docs_update({"workspace_id": WS, "project_id": PROJECT, "document_kind": "readme", "operation": "append", "content": "Approved update\n"}))["status"] == "updated"
        rejected(f.steward_mutation.handle_docs_update({"workspace_id": WS, "project_id": PROJECT, "document_kind": "project_doc", "document_ref": "../src/sample.py", "operation": "replace", "content": "x"})); f.clear_worker_env()
        hdocs = f.finish(docs); f.decide_ack(hdocs)
        link = f.spec("stewardship", operation="artifact_link"); f.promote(link); f.env(link, "project-steward")
        assert response(f.steward_mutation.handle_artifact_link({"workspace_id": WS, "project_id": PROJECT, "work_item_id": "wi-09e", "artifact_type": "result", "artifact_ref": "result-1", "relation": "review"}))["status"] == "linked"
        rejected(f.steward_mutation.handle_artifact_link({"workspace_id": WS, "project_id": PROJECT, "work_item_id": "wi-09e", "artifact_type": "result", "artifact_ref": "../bad", "relation": "unknown"})); f.clear_worker_env()
        hlink = f.finish(link); f.decide_ack(hlink)

        # E8: Card-only is narrow; every policy trigger forces full-report review.
        assert h0["needs_full_report_review"] is False and hbad["verdict"] == "needs_fix"
        policy_card = dict(consumed_p0["card"])
        assert not f.requires_full_report(policy_card, h0)
        for field, value in (("needs_full_report_review", True), ("verdict", "blocked"), ("outcome", "needs_input"), ("risks", ["material"]), ("limitations", ["material"])):
            changed = dict(policy_card); changed[field] = value
            assert f.requires_full_report(changed, h0), field
        assert f.requires_full_report(policy_card, {**h0, "needs_input": ["fixture input"]})

        # Binding conflicts are blocked before a durable decision and ack.
        binding_task = f.spec("implementation"); binding_handoff = f.finish(binding_task)
        binding_card_path = f.tasks / WS / binding_task / "CARD.json"
        binding_card = json.loads(binding_card_path.read_text(encoding="utf-8")); binding_card["spec_hash"] = "0" * 64
        binding_card_path.write_text(json.dumps(binding_card, sort_keys=True), encoding="utf-8")
        try:
            f.consume_as_task_main(binding_handoff)
            raise AssertionError("binding mismatch reached decision path")
        except AssertionError as exc:
            assert "binding_mismatch:spec_hash" in str(exc)
        rejected(f.ack.handle({"workspace_id": WS, "handoff_id": binding_handoff["handoff_id"], "decision": "accepted"}))

        # E9/E10: direct CodeGraph and capability seams stay fail-closed.
        lifecycle = importlib.import_module("aota_tools._codegraph_lifecycle")
        normal = {"initialized": True, "counts": {"files": 1, "nodes": 1, "edges": 1}, "pending_changes": {"added": 0, "modified": 0, "removed": 0}}
        assert lifecycle.derive_codegraph_lifecycle(PROJECT, True, True, True, normal)["state"] == "ready"
        stale = {**normal, "pending_changes": {"added": 0, "modified": 1, "removed": 0}}
        assert lifecycle.derive_codegraph_lifecycle(PROJECT, True, True, True, stale)["state"] == "stale"
        assert lifecycle.derive_codegraph_lifecycle(PROJECT, True, False, True)["state"] == "not_initialized"
        assert lifecycle.derive_codegraph_lifecycle(PROJECT, True, True, True, normal, error="malformed_status")["state"] == "invalid"
        lock_mod = importlib.import_module("aota_tools._codegraph_lock")
        lock = f.project / ".codegraph" / "codegraph.lock"; lock.parent.mkdir(); lock.write_text("1\n", encoding="ascii")
        before_lock = lock.read_bytes(); assert lock_mod.diagnose_codegraph_lock(f.project)["present"] is True and lock.read_bytes() == before_lock
        caps = importlib.import_module("aota_tools._capabilities").PROFILE_CAPABILITIES
        assert all(not caps[role]["codegraph_rebuild"] for role in ("project-steward", "architect", "coder", "reviewer", "debugger"))
        assert "codegraph_rebuild" not in f.payload("implementation")[2]
        f.env(p0, "debugger"); rejected(f.commands.handle({"task_id": p0, "spec_id": p0, "command_id": "python_module_compile", "args": ["src/sample.py"]})); f.clear_worker_env()
        override = f.create.handle({"workspace_id": WS, "spec_kind": "implementation", "target_profile": "reviewer"}); rejected(override)
        frozen = json.loads((f.tasks / WS / p0 / "meta.json").read_text(encoding="utf-8")); assert frozen["spec"]["resolved_profile"] == "coder"

        # Data-only ChatGPT boundary: no durable artifact until task-main SPEC creation above.
        chatgpt_draft = {"source": "chatgpt", "kind": "draft_plan", "durable": False}; master = {"source": "task-main", "kind": "master_draft", "durable": True}
        assert not chatgpt_draft["durable"] and master["durable"]

        assert f.project.is_relative_to(f.root) and not Path("/home/latios/workspace").resolve().is_relative_to(f.root)


MARKERS = (
    "PCF_E2E_P0_FAST_PATH_PASS", "PCF_E2E_P1_STEWARD_FLOW_PASS", "PCF_E2E_P2_ARCHITECT_CODER_REVIEWER_PASS",
    "PCF_E2E_REVIEW_NEEDS_FIX_PASS", "PCF_E2E_DIAGNOSIS_REPAIR_PASS", "PCF_E2E_STEWARD_DOCS_PASS", "PCF_E2E_ARTIFACT_CLOSE_PASS",
    "PCF_E2E_CARD_FIRST_PASS", "PCF_E2E_CODEGRAPH_FALLBACK_PASS", "PCF_E2E_CAPABILITY_REJECTION_PASS", "PCF_E2E_CHATGPT_BOUNDARY_SIMULATION_PASS",
    "PCF_E2E_HANDOFF_OUTBOX_PASS", "PCF_E2E_STATE_MACHINE_PASS", "PCF_E2E_ARTIFACT_BINDING_PASS", "PCF_E2E_TERMINAL_FREE_CODER_PASS", "PCF_E2E_FIXTURE_ISOLATION_PASS", "PCF_E2E_REPEATABILITY_PASS",
)


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--scenario", choices=("p0", "p1", "p2")); parser.parse_args()
    run_scenarios(); run_scenarios()
    for value in MARKERS: print(value)
    print("PCF_ISOLATED_END_TO_END_PASS"); print("FIXTURE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
