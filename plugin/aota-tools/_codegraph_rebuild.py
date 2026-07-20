"""Minimal registered-project CodeGraph rebuild wrapper (PCF-WI-08).

CodeGraph is a regenerable source index.  This module deliberately has no
approval, backup, receipt, retry, or maintenance-environment authority.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from ._codegraph_lifecycle import inspect_lifecycle
from ._codegraph_lock import diagnose_codegraph_lock
from ._codegraph_readonly import CodegraphError, _resolve_project, _run, resolve_codegraph_executable
from ._project_common import ProjectError, json_result, load_project

TOOLSET_NAME = "aota_codegraph_rebuild"
TOOL_NAME = "aota_codegraph_rebuild"
_TIMEOUT_SECONDS = 300
SCHEMA = {
    "name": TOOL_NAME,
    "description": "Rebuild one registered CodeGraph project with fixed argv. No approval, backup, retry, or arbitrary path input is accepted.",
    "parameters": {"type": "object", "properties": {
        "workspace_id": {"type": "string"}, "project_id": {"type": "string"},
    }, "required": ["workspace_id", "project_id"], "additionalProperties": False},
}


def _summary(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "index_present": bool(status.get("index_present")),
        "index_readable": status.get("state") not in {"invalid", "runtime_unavailable"},
        "files": int(status.get("files", 0)), "nodes": int(status.get("nodes", 0)), "edges": int(status.get("edges", 0)),
        "pending": dict(status.get("raw_pending", status.get("pending_changes", {}))),
        "state": str(status.get("state", "unknown")),
        "warnings": list(status.get("warnings", []))[:16],
    }


def _busy(project_root: Path) -> bool:
    """Conservative process check; it only reports and never kills a process."""
    root_text = str(project_root)
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            command = (proc / "cmdline").read_bytes()[:4096]
        except OSError:
            continue
        if b"codegraph" in command and root_text.encode() in command:
            return True
    return False


def _error(project_id: str, code: str, **extra: Any) -> str:
    return json_result({"status": "error", "project_id": project_id, "action": "rebuild", "error_code": code, "side_effects": [], **extra})


def handle(args: dict[str, Any], **_kwargs: Any) -> str:
    project_id = str(args.get("project_id", ""))[:96] if isinstance(args, dict) else ""
    try:
        if not isinstance(args, dict) or set(args) != {"workspace_id", "project_id"}:
            raise CodegraphError("project_not_found")
        workspace_id, project_id, workspace_root, project_root, index = _resolve_project(args)
        data = load_project(project_root / ".aota" / "project.yaml")[1]
        if not data["codegraph"]["enabled"]:
            return _error(project_id, "codegraph_not_configured")
        lock = diagnose_codegraph_lock(project_root)
        if lock.get("present") or lock.get("acquisition_result") not in {"not_present"} or _busy(project_root):
            return json_result({"status": "busy", "workspace_id": workspace_id, "project_id": project_id,
                "action": "rebuild", "error_code": "busy", "recommended_action": "wait",
                "lock": {"present": bool(lock.get("present")), "owner_alive": lock.get("owner_alive")}, "side_effects": []})
        before = inspect_lifecycle(args)
        executable, _source, warnings = resolve_codegraph_executable(workspace_root, project_root)
        # The existing CodeGraph CLI contract is init for missing indexes and index
        # for complete rebuilds of existing indexes; neither argv is caller supplied.
        argv = ["init" if not index.exists() else "index", str(project_root)]
        try:
            _run(executable, argv, project_root, _TIMEOUT_SECONDS, os.environ, terminate_on_output_overflow=False)
        except CodegraphError as exc:
            code = "timeout" if exc.code == "codegraph_timeout" else "index_rebuild_failed"
            return _error(project_id, code, workspace_id=workspace_id, before=_summary(before),
                process={"started": True, "exit_code": None, "timed_out": code == "timeout"}, warnings=warnings)
        after = inspect_lifecycle(args)
        result = {"status": "ok", "workspace_id": workspace_id, "project_id": project_id, "action": "rebuild",
            "before": _summary(before), "process": {"started": True, "exit_code": 0, "timed_out": False},
            "after": _summary(after), "warnings": warnings + list(after.get("warnings", []))[:16], "side_effects": ["codegraph_index_mutation"]}
        if after.get("state") not in {"ready", "stale", "refresh_recommended", "reindex_recommended"}:
            result.update(status="error", error_code="invalid_result")
        return json_result(result)
    except (CodegraphError, ProjectError, OSError) as exc:
        return _error(project_id, getattr(exc, "code", "project_not_found"))


def run_isolated_smoke() -> dict[str, str]:
    """Run only temporary-project/fake-launcher rebuild cases."""
    import sys
    from . import _workspace

    with tempfile.TemporaryDirectory(prefix="pcf-codegraph-rebuild-") as raw:
        base = Path(raw); workspace = base / "workspace"; workspace.mkdir()
        manifest = workspace / ".aota" / "project.yaml"; manifest.parent.mkdir()
        manifest.write_text("""schema_version: 1
project: {id: fixture-project, name: Fixture, kind: fixture, status: active}
summary: fixture
capabilities: []
paths: {source_root: ., source: [], docs: [], scripts: [], profiles: [], skills: [], tests: []}
commands: {validate: [], deploy: [], verify_deploy: []}
runtime: {deployment_type: test, requires_human_checkpoint: false}
codegraph: {enabled: true, index_location: .codegraph/}
plan: {active_plan_id: null}
constraints: []
""", encoding="utf-8")
        runtime = base / "runtime"; runtime.mkdir(); launcher = runtime / "codegraph"
        launcher.write_text(f"#!{sys.executable}\n" + """import json, sys
from pathlib import Path
root=Path(__file__).parent; args=sys.argv[1:]; mode=(root/'mode').read_text() if (root/'mode').exists() else 'ok'
(root/'argv.json').write_text(json.dumps(args))
(root/'argv-history.jsonl').open('a', encoding='utf-8').write(json.dumps(args)+'\\n')
if mode == 'fail': sys.exit(9)
if mode == 'sleep': import time; time.sleep(2)
project=Path(args[-1]); index=project/'.codegraph'
if args[0] == 'init': index.mkdir(exist_ok=True); (index/'codegraph.db').write_bytes(b'fixture')
if args[0] == 'index': (index/'codegraph.db').write_bytes(b'fixture')
if args[0] == 'status': print(json.dumps({'initialized': True, 'files': 1, 'nodes': 2, 'edges': 3, 'pending_changes': {'added': 0, 'modified': 0, 'removed': 0}}))
""", encoding="utf-8")
        launcher.chmod(0o700)
        registry = base / "workspaces.json"; registry.write_text(json.dumps({"fixture": {"candidates": [str(workspace)]}}), encoding="utf-8")
        old_registry, old_registry_env, old_exec, old_runtime, old_timeout = _workspace._REGISTRY_PATH, os.environ.get("AOTA_WORKSPACE_REGISTRY_PATH"), os.environ.get("AOTA_CODEGRAPH_EXECUTABLE"), os.environ.get("AOTA_CODEGRAPH_RUNTIME_ROOT"), _TIMEOUT_SECONDS
        _workspace._REGISTRY_PATH = registry; os.environ["AOTA_WORKSPACE_REGISTRY_PATH"] = str(registry); os.environ["AOTA_CODEGRAPH_EXECUTABLE"] = str(launcher); os.environ["AOTA_CODEGRAPH_RUNTIME_ROOT"] = str(runtime)
        try:
            args = {"workspace_id": "fixture", "project_id": "fixture-project"}
            missing = json.loads(handle(args)); assert missing["status"] == "ok", missing
            history = [json.loads(line) for line in (runtime / "argv-history.jsonl").read_text(encoding="utf-8").splitlines()]
            assert history[0] == ["init", str(workspace)]
            ready = json.loads(handle(args)); assert ready["status"] == "ok"
            history = [json.loads(line) for line in (runtime / "argv-history.jsonl").read_text(encoding="utf-8").splitlines()]
            assert ["index", str(workspace)] in history
            (workspace / ".codegraph" / "codegraph.lock").write_text("1", encoding="ascii")
            busy = json.loads(handle(args)); assert busy["status"] == "busy"
            (workspace / ".codegraph" / "codegraph.lock").unlink()
            (runtime / "mode").write_text("fail", encoding="ascii")
            failed = json.loads(handle(args)); assert failed["error_code"] == "index_rebuild_failed"
            (runtime / "mode").write_text("sleep", encoding="ascii")
            globals()["_TIMEOUT_SECONDS"] = 0.01
            timed = json.loads(handle(args)); assert timed["error_code"] == "timeout"
            globals()["_TIMEOUT_SECONDS"] = old_timeout
        finally:
            _workspace._REGISTRY_PATH = old_registry; globals()["_TIMEOUT_SECONDS"] = old_timeout
            if old_registry_env is None: os.environ.pop("AOTA_WORKSPACE_REGISTRY_PATH", None)
            else: os.environ["AOTA_WORKSPACE_REGISTRY_PATH"] = old_registry_env
            if old_exec is None: os.environ.pop("AOTA_CODEGRAPH_EXECUTABLE", None)
            else: os.environ["AOTA_CODEGRAPH_EXECUTABLE"] = old_exec
            if old_runtime is None: os.environ.pop("AOTA_CODEGRAPH_RUNTIME_ROOT", None)
            else: os.environ["AOTA_CODEGRAPH_RUNTIME_ROOT"] = old_runtime
    return {"marker": "PCF_CODEGRAPH_REBUILD_PASS"}
