"""Bounded CodeGraph status/query/explore wrappers (PCF-WI-05B).

These wrappers are source-and-index read-only, but CodeGraph appends telemetry
metadata under its controlled HOME.  They intentionally expose no mutation,
sync, index, daemon, executable-path, or arbitrary-project-path surface.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from ._codegraph_lock import diagnose_codegraph_lock
from ._project_common import ProjectError, json_result, load_project
from ._workspace import WorkspaceError, resolve_workspace

TOOLSET_NAME = "aota_codegraph_readonly"
STATUS_TOOL_NAME = "aota_codegraph_status"
QUERY_TOOL_NAME = "aota_codegraph_query"
EXPLORE_TOOL_NAME = "aota_codegraph_explore"
LOCK_DIAGNOSTICS_TOOL_NAME = "aota_codegraph_lock_diagnostics"
MAX_QUERY = 256
MAX_QUERY_TOKENS = 16
MAX_STDOUT = 64 * 1024
MAX_STDERR = 16 * 1024
MAX_EXPLORE_CONTENT = 24 * 1024
MAX_SNIPPET = 1200
_ALLOWED_KINDS = {"function", "class", "method", "module", "variable", "interface", "type"}
_PROJECT_ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties, "required": required,
        "additionalProperties": False,
    }}

STATUS_SCHEMA = _schema(STATUS_TOOL_NAME, "Return bounded CodeGraph index status for one registered project. Source/index are not modified; CodeGraph telemetry metadata may be written.", {
    "workspace_id": {"type": "string"}, "project_id": {"type": "string"},
}, ["workspace_id", "project_id"])
QUERY_SCHEMA = _schema(QUERY_TOOL_NAME, "Query a registered CodeGraph index through fixed argv and bounded JSON output. CodeGraph telemetry metadata may be written.", {
    "workspace_id": {"type": "string"}, "project_id": {"type": "string"},
    "search": {"type": "string", "maxLength": MAX_QUERY},
    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
    "kind": {"type": "string", "enum": sorted(_ALLOWED_KINDS)},
}, ["workspace_id", "project_id", "search"])
EXPLORE_SCHEMA = _schema(EXPLORE_TOOL_NAME, "Explore a registered CodeGraph index through fixed argv and bounded cleaned text. CodeGraph telemetry metadata may be written.", {
    "workspace_id": {"type": "string"}, "project_id": {"type": "string"},
    "query": {"type": "string", "maxLength": MAX_QUERY},
    "max_files": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
}, ["workspace_id", "project_id", "query"])
LOCK_DIAGNOSTICS_SCHEMA = _schema(LOCK_DIAGNOSTICS_TOOL_NAME, "Return bounded, read-only CodeGraph lock diagnostics for one registered project. No lock or index mutation is performed.", {
    "workspace_id": {"type": "string"}, "project_id": {"type": "string"},
}, ["workspace_id", "project_id"])


class CodegraphError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def index_present(index: Path) -> bool:
    """Require the canonical project-local CodeGraph database, not an empty directory."""
    return index.is_dir() and (index / "codegraph.db").is_file()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_text(value: object, limit: int) -> str:
    text = _ANSI_RE.sub("", str(value))
    text = _CONTROL_RE.sub("", text)
    return text[:limit]


def _private_runtime_root(environ: Mapping[str, str]) -> Path | None:
    raw = environ.get("AOTA_CODEGRAPH_RUNTIME_ROOT", "")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        raise CodegraphError("codegraph_executable_not_allowed")
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise CodegraphError("codegraph_runtime_unavailable") from exc


def _validate_executable(candidate: Path, workspace_root: Path, project_root: Path, managed_root: Path | None) -> Path:
    if not candidate.is_absolute():
        raise CodegraphError("codegraph_executable_invalid")
    try:
        resolved = candidate.resolve(strict=True)
        mode = resolved.stat().st_mode
    except OSError as exc:
        raise CodegraphError("codegraph_runtime_unavailable") from exc
    if not stat.S_ISREG(mode) or not os.access(resolved, os.X_OK):
        raise CodegraphError("codegraph_executable_invalid")
    if _inside(resolved, workspace_root) or _inside(resolved, project_root):
        raise CodegraphError("codegraph_executable_not_allowed")
    if managed_root is not None and not _inside(resolved, managed_root):
        raise CodegraphError("codegraph_executable_not_allowed")
    try:
        first_line = resolved.open("rb").readline(200)
    except OSError:
        first_line = b""
    if b"/usr/bin/env node" in first_line or b"npm" in first_line:
        raise CodegraphError("codegraph_executable_not_allowed")
    return resolved


def resolve_codegraph_executable(workspace_root: Path, project_root: Path, *, environ: Mapping[str, str] | None = None) -> tuple[Path, str, list[str]]:
    """Resolve only a trusted env candidate or deterministic managed runtime path."""
    env = os.environ if environ is None else environ
    managed_root = _private_runtime_root(env)
    explicit = env.get("AOTA_CODEGRAPH_EXECUTABLE", "")
    warnings: list[str] = []
    if explicit:
        path = _validate_executable(Path(explicit), workspace_root, project_root, managed_root)
        if path.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            warnings.append("executable_permissions_writable")
        return path, "trusted_env", warnings
    roots: list[Path] = []
    if managed_root is not None:
        roots.append(managed_root)
    raw_home = env.get("HERMES_HOME", "")
    if raw_home:
        home = Path(raw_home).resolve(strict=False)
        if home.parent.name == "profiles":
            home = home.parent.parent
        roots.append(home)
    else:
        roots.append(Path.home() / ".hermes")
    suffix = Path("home/lib/node_modules/@colbymchenry/codegraph/node_modules/@colbymchenry/codegraph-linux-x64/bin/codegraph")
    for root in roots:
        candidate = root / suffix
        if candidate.exists():
            return _validate_executable(candidate, workspace_root, project_root, managed_root), "managed_default", warnings
    raise CodegraphError("codegraph_runtime_unavailable")


def _resolve_project(args: dict[str, Any]) -> tuple[str, str, Path, Path, Path]:
    workspace_id = args.get("workspace_id")
    project_id = args.get("project_id")
    if not isinstance(workspace_id, str) or not workspace_id:
        raise CodegraphError("project_not_found")
    if not isinstance(project_id, str) or not _PROJECT_ID_RE.fullmatch(project_id):
        raise CodegraphError("project_not_found")
    try:
        workspace_root = resolve_workspace(workspace_id)
        manifest = workspace_root / ".aota" / "project.yaml"
        if manifest.is_symlink() or not manifest.is_file():
            raise CodegraphError("project_not_found")
        project_root, data = load_project(manifest)
    except WorkspaceError as exc:
        raise CodegraphError("project_not_found") from exc
    except ProjectError as exc:
        raise CodegraphError(exc.code if exc.code in {"manifest_invalid", "path_escape"} else "project_not_found") from exc
    if data["project"]["id"] != project_id or not _inside(project_root.resolve(), workspace_root.resolve()):
        raise CodegraphError("codegraph_project_mismatch")
    index = project_root / data["codegraph"]["index_location"]
    if index.exists():
        if index.is_symlink() or not _inside(index.resolve(), project_root.resolve()):
            raise CodegraphError("symlink_escape")
    return workspace_id, project_id, workspace_root, project_root.resolve(), index


def _child_env(environ: Mapping[str, str], runtime_root: Path | None) -> dict[str, str]:
    home = runtime_root if runtime_root is not None else Path(environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    return {"HOME": str(home), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TERM": "dumb", "NO_COLOR": "1"}


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _enable_child_subreaper() -> None:
    """Make this Linux worker reap killed CodeGraph descendants, not PID 1."""
    if sys.platform != "linux":
        raise CodegraphError("codegraph_process_failed")
    try:
        if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "PR_SET_CHILD_SUBREAPER")
    except OSError as exc:
        raise CodegraphError("codegraph_process_failed") from exc


def _reap_group_descendants(pgid: int, timeout: float = 2.0) -> None:
    """Reap only descendants from the killed process group after leader wait."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        members: list[int] = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                pid = int(entry.name)
                if os.getpgid(pid) == pgid:
                    members.append(pid)
            except (OSError, ValueError):
                continue
        if not members:
            return
        for pid in members:
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                continue
        time.sleep(0.02)
    raise CodegraphError("codegraph_process_failed")


def _run(executable: Path, argv: list[str], cwd: Path, timeout: int, environ: Mapping[str, str], *, terminate_on_output_overflow: bool = True) -> tuple[bytes, bytes]:
    """Run fixed argv with bounded capture; mutation callers may drain excess output."""
    _enable_child_subreaper()
    runtime_root = _private_runtime_root(environ)
    process = subprocess.Popen([str(executable), *argv], cwd=str(cwd), env=_child_env(environ, runtime_root), shell=False,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    chunks: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    exceeded = threading.Event()

    def read_stream(name: str, stream: Any, cap: int) -> None:
        while True:
            block = stream.read(4096)
            if not block:
                return
            if len(chunks[name]) + len(block) > cap:
                chunks[name].extend(block[:max(0, cap - len(chunks[name]))])
                exceeded.set()
                if terminate_on_output_overflow:
                    _kill_group(process)
                    return
                continue
            chunks[name].extend(block)

    readers = [threading.Thread(target=read_stream, args=("stdout", process.stdout, MAX_STDOUT), daemon=True), threading.Thread(target=read_stream, args=("stderr", process.stderr, MAX_STDERR), daemon=True)]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout
    timed_out = False
    while process.poll() is None and (not exceeded.is_set() or not terminate_on_output_overflow):
        if time.monotonic() >= deadline:
            timed_out = True
            _kill_group(process)
            break
        time.sleep(0.02)
    if process.poll() is None:
        _kill_group(process)
    process.wait(timeout=2)
    _reap_group_descendants(process.pid)
    for reader in readers:
        reader.join(timeout=2)
    if timed_out:
        raise CodegraphError("codegraph_timeout")
    if exceeded.is_set() and terminate_on_output_overflow:
        raise CodegraphError("codegraph_output_too_large")
    if process.returncode == 127:
        raise CodegraphError("codegraph_runtime_unavailable")
    if process.returncode != 0:
        raise CodegraphError("codegraph_process_failed")
    return bytes(chunks["stdout"]), bytes(chunks["stderr"])


def _base(project_id: str, source: str, warnings: list[str]) -> dict[str, Any]:
    return {"status": "ok", "project_id": project_id, "side_effects": ["telemetry_metadata_write"], "runtime": {"executable_source": source, "live_activation_verified": False}, "truncated": False, "warnings": warnings}


def _validate_args(args: dict[str, Any], allowed: set[str]) -> None:
    if not isinstance(args, dict) or set(args) - allowed:
        raise CodegraphError("codegraph_query_invalid")


def _query_text(value: object, error: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_QUERY or "\n" in value or _CONTROL_RE.search(value):
        raise CodegraphError(error)
    if value.startswith("-"):
        raise CodegraphError(error)
    return value


def _relative_path(value: object, project_root: Path) -> str:
    if not isinstance(value, str):
        return ""
    path = Path(value)
    try:
        resolved = path.resolve() if path.is_absolute() else (project_root / path).resolve()
        return resolved.relative_to(project_root).as_posix()
    except (OSError, ValueError):
        return "<path-redacted>"


def _decode_json(raw: bytes, expected: type, error: str) -> Any:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CodegraphError(error) from exc
    if not isinstance(value, expected):
        raise CodegraphError(error)
    return value


def status_handle(args: dict[str, Any], **_kwargs: Any) -> str:
    """Return the single minimal status projection, including conservative busy state."""
    project_id = str(args.get("project_id", ""))[:96] if isinstance(args, dict) else ""
    try:
        _validate_args(args, {"workspace_id", "project_id"})
        workspace_id, project_id, _workspace_root, root, index = _resolve_project(args)
        from ._codegraph_lifecycle import inspect_lifecycle
        detail = inspect_lifecycle(args)
        if detail.get("status") == "error":
            return json_result({"status": "error", "project_id": project_id, "error_code": detail.get("error_code", "unknown"), "side_effects": []})
        lock = diagnose_codegraph_lock(root)
        raw_state = str(detail.get("state", "unknown"))
        if lock.get("present") or lock.get("acquisition_result") not in {"not_present"}:
            state, action = "busy", "wait"
        elif raw_state == "ready":
            state, action = "ready", "none"
        elif raw_state in {"not_initialized", "initialized_empty"}:
            state, action = "missing", "rebuild"
        elif raw_state in {"stale", "refresh_recommended", "reindex_recommended"}:
            state, action = "stale", "rebuild"
        else:
            state, action = "broken", "rebuild"
        readable = bool(detail.get("index_present")) and raw_state not in {"invalid", "runtime_unavailable"}
        pending = detail.get("raw_pending", detail.get("pending_changes", {}))
        result = {"status": "ok", "workspace_id": workspace_id, "project_id": project_id,
            "index_present": bool(detail.get("index_present")), "index_readable": readable,
            "files": int(detail.get("files", 0)), "nodes": int(detail.get("nodes", 0)), "edges": int(detail.get("edges", 0)),
            "pending_added": int(pending.get("added", 0)), "pending_modified": int(pending.get("modified", 0)), "pending_removed": int(pending.get("removed", 0)),
            "state": state, "recommended_action": action,
            "last_updated": int(index.stat().st_mtime) if index.exists() else None,
            "warnings": list(detail.get("warnings", []))[:16],
            "lock": {"present": bool(lock.get("present")), "owner_alive": lock.get("owner_alive")}, "side_effects": ["telemetry_metadata_write"]}
        return json_result(result)
    except CodegraphError as exc:
        return json_result({"status": "error", "project_id": project_id, "error_code": exc.code, "side_effects": []})


def lock_diagnostics_handle(args: dict[str, Any], **_kwargs: Any) -> str:
    project_id = str(args.get("project_id", ""))[:96] if isinstance(args, dict) else ""
    try:
        _validate_args(args, {"workspace_id", "project_id"})
        workspace_id, project_id, _workspace_root, project_root, _index = _resolve_project(args)
        return json_result({
            "status": "ok", "workspace_id": workspace_id, "project_id": project_id,
            "lock_diagnostics": diagnose_codegraph_lock(project_root), "side_effects": [],
        })
    except CodegraphError as exc:
        return json_result({"status": "error", "project_id": project_id, "error_code": exc.code, "side_effects": []})


def query_handle(args: dict[str, Any], **_kwargs: Any) -> str:
    project_id = str(args.get("project_id", ""))[:96] if isinstance(args, dict) else ""
    try:
        _validate_args(args, {"workspace_id", "project_id", "search", "limit", "kind"})
        search = _query_text(args.get("search"), "codegraph_query_invalid")
        limit = args.get("limit", 5)
        kind = args.get("kind")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
            raise CodegraphError("codegraph_query_invalid")
        if kind is not None and (not isinstance(kind, str) or kind not in _ALLOWED_KINDS):
            raise CodegraphError("codegraph_kind_invalid")
        _, project_id, workspace_root, root, index = _resolve_project(args)
        if not index_present(index):
            raise CodegraphError("codegraph_index_missing")
        executable, source, warnings = resolve_codegraph_executable(workspace_root, root)
        argv = ["query", "--path", str(root), "--limit", str(limit), "--json"]
        if kind:
            argv.extend(["--kind", kind])
        argv.append(search)
        raw, _ = _run(executable, argv, root, 10, os.environ)
        values = _decode_json(raw, list, "codegraph_output_invalid")
        items: list[dict[str, Any]] = []
        for value in values[:limit]:
            if not isinstance(value, dict):
                raise CodegraphError("codegraph_output_invalid")
            item = {"name": _safe_text(value.get("name", ""), 256), "kind": _safe_text(value.get("kind", ""), 64),
                "path": _relative_path(value.get("path", value.get("file", "")), root), "line": value.get("line") if isinstance(value.get("line"), int) and value.get("line") > 0 else None,
                "signature": _safe_text(value.get("signature", ""), 512), "summary": _safe_text(value.get("summary", value.get("snippet", "")), MAX_SNIPPET)}
            if isinstance(value.get("score"), (int, float)) and not isinstance(value.get("score"), bool):
                item["score"] = value["score"]
            items.append(item)
        result = _base(project_id, source, warnings)
        result.update({"search": search, "results": items, "result_count": len(items), "truncated": len(values) > len(items)})
        return json_result(result)
    except CodegraphError as exc:
        return json_result({"status": "error", "project_id": project_id, "error_code": exc.code, "side_effects": ["telemetry_metadata_write"]})


def explore_handle(args: dict[str, Any], **_kwargs: Any) -> str:
    project_id = str(args.get("project_id", ""))[:96] if isinstance(args, dict) else ""
    try:
        _validate_args(args, {"workspace_id", "project_id", "query", "max_files"})
        query = _query_text(args.get("query"), "codegraph_query_invalid")
        tokens = query.split()
        max_files = args.get("max_files", 3)
        if not tokens or len(tokens) > MAX_QUERY_TOKENS or not isinstance(max_files, int) or isinstance(max_files, bool) or not 1 <= max_files <= 10:
            raise CodegraphError("codegraph_query_invalid")
        _, project_id, workspace_root, root, index = _resolve_project(args)
        if not index_present(index):
            raise CodegraphError("codegraph_index_missing")
        executable, source, warnings = resolve_codegraph_executable(workspace_root, root)
        raw, _ = _run(executable, ["explore", "--path", str(root), "--max-files", str(max_files), *tokens], root, 20, os.environ)
        content = _safe_text(raw.decode("utf-8", "replace"), MAX_EXPLORE_CONTENT)
        content = content.replace(str(root), ".")
        result = _base(project_id, source, warnings)
        result.update({"query": query, "format": "bounded_text", "content": content, "truncated": len(raw) > len(content.encode("utf-8"))})
        return json_result(result)
    except CodegraphError as exc:
        return json_result({"status": "error", "project_id": project_id, "error_code": exc.code, "side_effects": ["telemetry_metadata_write"]})


def run_isolated_smoke() -> dict[str, str]:
    """Exercise failure boundaries with a temporary fake launcher only."""
    import hashlib
    import tempfile
    from . import _workspace

    def tree_digest(root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*")):
            digest.update(path.relative_to(root).as_posix().encode())
            if path.is_file():
                digest.update(path.read_bytes())
        return digest.hexdigest()

    def set_mode(runtime: Path, mode: str) -> None:
        (runtime / "mode").write_text(mode, encoding="utf-8")

    def pids_terminated(pid_file: Path) -> bool:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            pids = [int(value) for value in pid_file.read_text(encoding="utf-8").split()]
            if pids and all(not (Path("/proc") / str(pid)).exists() for pid in pids):
                return True
            time.sleep(0.05)
        return False

    original_registry = _workspace._REGISTRY_PATH
    original_registry_env = os.environ.get("AOTA_WORKSPACE_REGISTRY_PATH")
    old_exec, old_root = os.environ.get("AOTA_CODEGRAPH_EXECUTABLE"), os.environ.get("AOTA_CODEGRAPH_RUNTIME_ROOT")
    temp_root: Path | None = None
    with tempfile.TemporaryDirectory(prefix="pcf-codegraph-") as raw:
        base = Path(raw); temp_root = base; workspace = base / "workspace"; workspace.mkdir(); (workspace / ".codegraph").mkdir(); (workspace / ".codegraph" / "codegraph.db").write_bytes(b"fixture")
        manifest = workspace / ".aota" / "project.yaml"; manifest.parent.mkdir()
        manifest.write_text("""schema_version: 1\nproject: {id: fixture-project, name: Fixture, kind: fixture, status: active}\nsummary: fixture\ncapabilities: []\npaths: {source_root: ., source: [], docs: [], scripts: [], profiles: [], skills: [], tests: []}\ncommands: {validate: [], deploy: [], verify_deploy: []}\nruntime: {deployment_type: test, requires_human_checkpoint: false}\ncodegraph: {enabled: true, index_location: .codegraph/}\nplan: {active_plan_id: null}\nconstraints: []\n""", encoding="utf-8")
        runtime = base / "runtime"; runtime.mkdir(); launcher = runtime / "codegraph"
        child_code = "import subprocess,sys,time; from pathlib import Path; p=Path(sys.argv[1]); g=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); p.open('a').write(str(g.pid)+'\\n'); time.sleep(60)"
        launcher.write_text(f"#!{sys.executable}\n" + f"""import json, os, subprocess, sys, time
from pathlib import Path
root = Path(__file__).parent
mode = (root / 'mode').read_text(encoding='utf-8').strip()
args = sys.argv[1:]
if mode != 'telemetry_unwritable':
    (root / 'argv.json').write_text(json.dumps(args), encoding='utf-8')
def telemetry():
    try:
        queue = Path(os.environ['HOME']) / '.codegraph' / 'telemetry-queue.jsonl'
        queue.parent.mkdir(parents=True, exist_ok=True)
        queue.open('a', encoding='utf-8').write('telemetry\\n')
    except OSError:
        print('SECRET_LIKE=telemetry-permission-secret', file=sys.stderr)
        sys.exit(73)
def spawn_tree():
    pids = root / 'pids'
    pids.write_text(str(os.getpid()) + '\\n', encoding='utf-8')
    child = subprocess.Popen([sys.executable, '-c', {child_code!r}, str(pids)])
    pids.open('a', encoding='utf-8').write(str(child.pid) + '\\n')
telemetry()
if mode == 'timeout':
    spawn_tree(); time.sleep(60)
if mode == 'stdout_overflow':
    spawn_tree(); sys.stdout.write('X' * 70000); sys.stdout.flush(); time.sleep(60)
if mode == 'stderr_overflow':
    sys.stderr.write('SECRET_LIKE=stderr-secret' * 2000); sys.stderr.flush(); sys.exit(8)
if mode == 'nonzero':
    print('SECRET_LIKE=nonzero-secret', file=sys.stderr); sys.exit(9)
if mode == 'malformed':
    print('{{malformed'); sys.exit(0)
if args[0] == 'status':
    print(json.dumps({{'initialized': True, 'version': '1.1.1', 'files': 1, 'nodes': 2, 'edges': 3}}))
elif args[0] == 'query':
    print(json.dumps([{{'name': 'resolve_workspace', 'kind': 'function', 'path': 'plugin/x.py', 'line': 7, 'summary': 'safe'}}]))
elif args[0] == 'explore':
    print('\\x1b[31mexplore plugin/x.py')
else:
    sys.exit(2)
""", encoding="utf-8")
        launcher.chmod(0o700)
        registry = base / "workspaces.json"; registry.write_text(json.dumps({"fixture": {"candidates": [str(workspace)]}}), encoding="utf-8")
        _workspace._REGISTRY_PATH = registry
        os.environ["AOTA_WORKSPACE_REGISTRY_PATH"] = str(registry)
        os.environ["AOTA_CODEGRAPH_EXECUTABLE"] = str(launcher); os.environ["AOTA_CODEGRAPH_RUNTIME_ROOT"] = str(runtime)
        original_project = tree_digest(workspace)
        try:
            set_mode(runtime, "ok")
            status_result = json.loads(status_handle({"workspace_id": "fixture", "project_id": "fixture-project"}))
            assert status_result["status"] == "ok", status_result
            assert (runtime / ".codegraph" / "telemetry-queue.jsonl").is_file()
            injection_marker = base / "injection-marker"
            injection = f"symbol;touch {injection_marker}"
            query_result = json.loads(query_handle({"workspace_id": "fixture", "project_id": "fixture-project", "search": injection}))
            assert query_result["status"] == "ok" and query_result["results"][0]["path"] == "plugin/x.py"
            assert json.loads((runtime / "argv.json").read_text(encoding="utf-8"))[-1] == injection and not injection_marker.exists()
            assert "\x1b" not in json.loads(explore_handle({"workspace_id": "fixture", "project_id": "fixture-project", "query": "resolve workspace"}))["content"]
            assert json.loads(query_handle({"workspace_id": "fixture", "project_id": "fixture-project", "search": "-x"}))["error_code"] == "codegraph_query_invalid"
            set_mode(runtime, "malformed")
            malformed_status = json.loads(status_handle({"workspace_id": "fixture", "project_id": "fixture-project"}))
            assert malformed_status["status"] == "ok" and malformed_status["state"] == "broken"
            malformed_query = json.loads(query_handle({"workspace_id": "fixture", "project_id": "fixture-project", "search": "safe"}))
            assert malformed_query["error_code"] == "codegraph_output_invalid" and str(launcher) not in json.dumps(malformed_query)
            for mode in ("nonzero", "stderr_overflow"):
                set_mode(runtime, mode)
                result = json.loads(status_handle({"workspace_id": "fixture", "project_id": "fixture-project"}))
                encoded = json.dumps(result)
                assert result["status"] == "ok" and result["state"] == "broken" and "SECRET_LIKE" not in encoded and str(launcher) not in encoded
            set_mode(runtime, "timeout")
            try:
                _run(launcher, ["status", "--json", str(workspace)], workspace, 0.2, os.environ)
            except CodegraphError as exc:
                assert exc.code == "codegraph_timeout"
            else:
                raise AssertionError("timeout was not bounded")
            assert pids_terminated(runtime / "pids"), "timeout left process-group member"
            set_mode(runtime, "stdout_overflow")
            result = json.loads(status_handle({"workspace_id": "fixture", "project_id": "fixture-project"}))
            assert result["status"] == "ok" and result["state"] == "broken"
            assert pids_terminated(runtime / "pids"), "output overflow left process-group member"
            set_mode(runtime, "telemetry_unwritable")
            telemetry_queue = runtime / ".codegraph" / "telemetry-queue.jsonl"
            telemetry_queue.unlink(); telemetry_queue.parent.rmdir(); runtime.chmod(0o500)
            result = json.loads(status_handle({"workspace_id": "fixture", "project_id": "fixture-project"}))
            assert result["status"] == "ok" and result["state"] == "broken" and "telemetry-permission-secret" not in json.dumps(result)
            runtime.chmod(0o700)
            assert tree_digest(workspace) == original_project
        finally:
            runtime.chmod(0o700)
            _workspace._REGISTRY_PATH = original_registry
            if original_registry_env is None:
                os.environ.pop("AOTA_WORKSPACE_REGISTRY_PATH", None)
            else:
                os.environ["AOTA_WORKSPACE_REGISTRY_PATH"] = original_registry_env
            if old_exec is None: os.environ.pop("AOTA_CODEGRAPH_EXECUTABLE", None)
            else: os.environ["AOTA_CODEGRAPH_EXECUTABLE"] = old_exec
            if old_root is None: os.environ.pop("AOTA_CODEGRAPH_RUNTIME_ROOT", None)
            else: os.environ["AOTA_CODEGRAPH_RUNTIME_ROOT"] = old_root
    assert temp_root is not None and not temp_root.exists(), "temporary fixture was retained"
    return {"marker": "PCF_CODEGRAPH_ISOLATED_SMOKE_PASS"}
