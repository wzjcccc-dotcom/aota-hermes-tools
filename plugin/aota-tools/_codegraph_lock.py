"""Bounded, non-destructive CodeGraph lock diagnostics (PCF-WI-06B.3)."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

MAX_LOCK_BYTES = 128
MAX_PROC_STATUS_BYTES = 512
LOCK_RELATIVE_PATH = ".codegraph/codegraph.lock"


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _owner_alive(pid: int | None) -> bool | None:
    if pid is None or pid <= 0:
        return None
    proc_root = Path("/proc") / str(pid)
    try:
        proc_root.stat()
    except FileNotFoundError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    try:
        with (proc_root / "status").open("r", encoding="ascii", errors="replace") as handle:
            status = handle.read(MAX_PROC_STATUS_BYTES)
    except FileNotFoundError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    for line in status.splitlines():
        if line.startswith("State:"):
            fields = line.split()
            if len(fields) < 2:
                return None
            return fields[1] not in {"Z", "X"}
    return None


def diagnose_codegraph_lock(project_root: Path) -> dict[str, Any]:
    """Inspect the project-local lock without unlinking, killing, or trusting it."""
    root = project_root.resolve(strict=False)
    lock = project_root / LOCK_RELATIVE_PATH
    result: dict[str, Any] = {
        "path": LOCK_RELATIVE_PATH,
        "present": False,
        "owner_pid": None,
        "owner_alive": None,
        "age_seconds": None,
        "acquisition_result": "not_present",
        "diagnosis": "inferred",
    }
    try:
        parent = lock.parent
        if parent.is_symlink() or not _inside(parent.resolve(strict=False), root):
            result.update(acquisition_result="rejected_path_escape", diagnosis="inferred")
            return result
        if lock.is_symlink():
            result.update(acquisition_result="rejected_symlink", diagnosis="inferred")
            return result
        if not lock.exists():
            return result
        result["present"] = True
        stat_result = lock.stat()
        result["age_seconds"] = max(0, int(time.time() - stat_result.st_mtime))
        if stat_result.st_size > MAX_LOCK_BYTES:
            result["acquisition_result"] = "malformed"
            return result
        content = lock.read_bytes()[:MAX_LOCK_BYTES]
        if len(content) >= MAX_LOCK_BYTES and stat_result.st_size > MAX_LOCK_BYTES:
            result["acquisition_result"] = "malformed"
            return result
        try:
            pid_text = content.decode("ascii").strip()
            pid = int(pid_text, 10)
        except (UnicodeDecodeError, ValueError):
            result["acquisition_result"] = "malformed"
            return result
        if pid <= 0:
            result["acquisition_result"] = "malformed"
            return result
        result["owner_pid"] = pid
        result["owner_alive"] = _owner_alive(pid)
        result["acquisition_result"] = "present"
        return result
    except OSError:
        result["acquisition_result"] = "diagnosis_error"
        return result


def apply_cli_lock_evidence(diagnostics: dict[str, Any]) -> dict[str, Any]:
    """Project explicit CLI evidence without changing filesystem observations."""
    updated = dict(diagnostics)
    updated["acquisition_result"] = "explicit_cli_failure"
    updated["diagnosis"] = "confirmed_cli"
    return updated
