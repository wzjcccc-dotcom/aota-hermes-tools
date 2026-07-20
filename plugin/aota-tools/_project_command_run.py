"""Fixed-argv Coder validation runner; this is deliberately not a shell."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any

from ._coder_task_binding import CoderBindingError, load_coder_binding

TOOL_NAME = "aota_project_command_run"
TOOLSET_NAME = "aota_coder_command"
_OUTPUT_LIMIT = 8192
_TIMEOUT_MAX = 60
SCHEMA = {"name": TOOL_NAME, "description": "Run one frozen-SPEC-approved validation command by fixed command_id. It never accepts shell, cwd, executable, environment, stdin, or background controls.", "parameters": {"type": "object", "properties": {"task_id": {"type": "string", "maxLength": 128}, "spec_id": {"type": "string", "maxLength": 128}, "command_id": {"type": "string", "enum": ["python_compileall", "python_module_compile", "node_check", "ruff_check", "mypy_check", "pytest_isolated", "project_script"]}, "args": {"type": "array", "items": {"type": "string", "maxLength": 256}, "maxItems": 8}, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": _TIMEOUT_MAX}}, "required": ["task_id", "spec_id", "command_id", "args"], "additionalProperties": False}}


def _excerpt(value: str) -> str:
    return value[:_OUTPUT_LIMIT] + ("\n[truncated]" if len(value) > _OUTPUT_LIMIT else "")


def _allowed_command(spec: dict[str, Any], command_id: str) -> bool:
    commands = spec.get("payload", {}).get("validation_commands", [])
    for value in commands:
        if value == command_id or isinstance(value, dict) and value.get("command_id") == command_id:
            return True
    return False


def _safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or ".." in path.parts or "\\" in value
            or value.startswith("-") or not re.fullmatch(r"[A-Za-z0-9._/-]+", value)):
        raise CoderBindingError("invalid_command_argument")
    return path.as_posix()


def _fixed_argv(command_id: str, args: list[str], root: Path) -> list[str]:
    python = sys.executable
    if command_id == "python_compileall":
        if not args or len(args) > 2: raise CoderBindingError("invalid_command_arguments")
        return [python, "-m", "compileall", "-q", *[_safe_path(item) for item in args]]
    if command_id == "python_module_compile":
        if len(args) != 1 or not args[0].endswith(".py"): raise CoderBindingError("invalid_command_arguments")
        return [python, "-m", "py_compile", _safe_path(args[0])]
    if command_id == "node_check":
        if len(args) != 1 or not args[0].endswith((".js", ".mjs", ".cjs")): raise CoderBindingError("invalid_command_arguments")
        return ["node", "--check", _safe_path(args[0])]
    if command_id in {"ruff_check", "mypy_check"}:
        if not args: raise CoderBindingError("invalid_command_arguments")
        return [command_id.split("_", 1)[0], "check" if command_id == "ruff_check" else "--no-incremental", *[_safe_path(item) for item in args]]
    if command_id == "pytest_isolated":
        if len(args) != 1: raise CoderBindingError("invalid_command_arguments")
        return [python, "-m", "pytest", "-q", _safe_path(args[0])]
    if command_id == "project_script":
        if not args or args[0] != "validate" or len(args) > 2: raise CoderBindingError("invalid_project_script")
        manifest = root / ".aota" / "project.yaml"
        # Project manifests declare a fixed list of validation script paths.
        from ._project_common import load_project
        _, project = load_project(manifest)
        scripts = project.get("commands", {}).get("validate", [])
        if not isinstance(scripts, list) or len(scripts) != 1 or not isinstance(scripts[0], str): raise CoderBindingError("invalid_project_script_manifest")
        script = _safe_path(scripts[0])
        action = [] if len(args) == 1 else [args[1]]
        if action and action[0] not in {"fixture", "readiness"}: raise CoderBindingError("invalid_project_script_action")
        return [python, script, *action]
    raise CoderBindingError("unknown_command_id")


def handle(args: dict, **_kwargs: Any) -> str:
    started = time.monotonic(); wall_started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        if not isinstance(args, dict) or set(args) - set(SCHEMA["parameters"]["properties"]):
            raise CoderBindingError("unknown_command_field")
        command_id, raw_args = args.get("command_id"), args.get("args")
        if not isinstance(command_id, str) or not isinstance(raw_args, list) or any(not isinstance(item, str) for item in raw_args): raise CoderBindingError("invalid_command_request")
        root, spec = load_coder_binding(args.get("task_id"), args.get("spec_id"))
        if not spec.get("capability_contract", {}).get("bounded_project_command") or not _allowed_command(spec, command_id): raise CoderBindingError("command_not_authorized_by_spec")
        timeout = args.get("timeout_seconds", 30)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= _TIMEOUT_MAX: raise CoderBindingError("invalid_timeout_seconds")
        argv = _fixed_argv(command_id, raw_args, root)
        try:
            completed = subprocess.run(argv, cwd=root, shell=False, stdin=subprocess.DEVNULL, input=None, text=True, capture_output=True, timeout=timeout, env={"PATH": os.defpath, "PYTHONUTF8": "1"}, check=False)
            code, timed_out, stdout, stderr = completed.returncode, False, completed.stdout, completed.stderr
        except subprocess.TimeoutExpired as exc:
            code, timed_out = None, True
            stdout, stderr = exc.stdout or "", exc.stderr or ""
        if isinstance(stdout, bytes): stdout = stdout.decode("utf-8", "replace")
        if isinstance(stderr, bytes): stderr = stderr.decode("utf-8", "replace")
        return json.dumps({"schema_version": 1, "command_id": command_id, "resolved_executable": Path(argv[0]).name, "resolved_args": argv[1:], "working_directory": ".", "exit_code": code, "timed_out": timed_out, "stdout_excerpt": _excerpt(stdout), "stderr_excerpt": _excerpt(stderr), "started_at": wall_started, "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "duration_ms": int((time.monotonic() - started) * 1000)}, sort_keys=True)
    except (CoderBindingError, OSError, ValueError) as exc:
        return json.dumps({"status": "rejected", "error": str(exc)}, sort_keys=True)
