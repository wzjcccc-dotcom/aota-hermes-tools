"""Profile Task scope compliance with explicit path-tier classification.

Runtime imports, launcher files, task artifacts, Profile/Skill/plugin files,
and credential checks are observed for diagnostics but are not project-scope
violations.  Only explicit project operations (or formal project telemetry)
are evaluated against the frozen project scopes.
"""

from __future__ import annotations

import fnmatch
import fcntl
import json
import os
import subprocess
import hashlib
import datetime as _dt
import importlib.util
import sys
from pathlib import Path
from typing import Any, Iterable


PATH_TIERS = (
    "PROJECT_READ", "PROJECT_WRITE", "PROJECT_METADATA",
    "ACTIVE_TASK_READ", "ACTIVE_TASK_WRITE", "SUBJECT_TASK_READ",
    "RUNTIME_CONTROL_PLANE", "PROFILE_RUNTIME", "PLUGIN_RUNTIME",
    "CREDENTIAL_AUTHORITY", "SYSTEM_DEPENDENCY", "UNKNOWN_EXTERNAL",
)
MAX_EVENTS = 2000
MAX_VIOLATIONS = 50
MAX_BASELINE_PATHS = 4000
MAX_FINGERPRINT_BYTES = 8 * 1024 * 1024
BASELINE_SCHEMA_VERSION = 1


def _canonical(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _mapped_path(path: str, workspace_root: Path) -> Path:
    candidate = Path(path)
    return _canonical(candidate if candidate.is_absolute() else workspace_root / candidate)


def _semantic_aliases(path: Path) -> list[Path]:
    """Map the known host/container Hermes views to one semantic namespace."""
    aliases = [
        (Path("/home/hermes/.hermes"), Path("/home/latios/.hermes")),
        (Path("/home/hermeswebui/.hermes"), Path("/home/latios/.hermes")),
        (Path("/home/hermes/.hermes/aota-runtime"), Path("/home/latios/.hermes/aota-runtime")),
        (Path("/home/hermeswebui/.hermes/aota-runtime"), Path("/home/latios/.hermes/aota-runtime")),
    ]
    result = [path]
    for source, target in aliases:
        if _inside(path, source):
            result.append(_canonical(target / path.relative_to(source)))
    return result


def _match_scope(relative: str, patterns: object) -> bool:
    if not isinstance(patterns, list):
        return False
    for raw in patterns:
        if not isinstance(raw, str):
            continue
        pattern = raw.strip().replace("\\", "/")
        if not pattern:
            continue
        if fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(relative, pattern.rstrip("/") + "/**"):
            return True
    return False


def classify_path(
    path: str | Path,
    *,
    workspace_root: Path,
    operation: str = "read",
    source: str = "runtime",
    explicit_worker_action: bool = False,
    active_task_root: Path | None = None,
    active_task_dir: Path | None = None,
) -> dict[str, Any]:
    """Classify one observed path without using basename heuristics."""
    if operation not in {"read", "write", "create", "patch", "delete", "metadata"}:
        operation = "metadata"
    raw = str(path)
    if "\x00" in raw:
        return {"path": raw[:512], "operation": operation, "tier": "UNKNOWN_EXTERNAL", "source": source, "explicit_worker_action": explicit_worker_action, "scope_relevant": bool(explicit_worker_action), "allowed": False, "reason": "invalid_path"}
    original = _mapped_path(raw, workspace_root)
    candidates = _semantic_aliases(original)
    workspace = _canonical(workspace_root)
    task_root = _canonical(active_task_root) if active_task_root else None
    task_dir = _canonical(active_task_dir) if active_task_dir else None

    # Task artifacts are checked before the broader runtime root.  A task
    # different from the active one is a subject/authorization concern, not a
    # project forbidden-scope event.
    if task_root and any(_inside(candidate, task_root) for candidate in candidates):
        if task_dir and any(_inside(candidate, task_dir) for candidate in candidates):
            tier = "ACTIVE_TASK_WRITE" if operation in {"write", "create", "delete"} else "ACTIVE_TASK_READ"
            relevant = False
            reason = "active_task_artifact"
        elif operation in {"read", "metadata"}:
            tier, relevant, reason = "SUBJECT_TASK_READ", False, "subject_task_or_runtime_artifact"
        else:
            tier, relevant, reason = "UNKNOWN_EXTERNAL", bool(explicit_worker_action), "cross_task_write"
        return _event(raw, operation, tier, source, explicit_worker_action, relevant, True, reason)

    home_roots = [Path("/home/latios/.hermes"), Path("/home/hermes/.hermes"), Path("/home/hermeswebui/.hermes")]
    credential_paths = {root / ".env" for root in home_roots} | {root / "auth.json" for root in home_roots}
    for candidate in candidates:
        if candidate in credential_paths:
            return _event(raw, operation, "CREDENTIAL_AUTHORITY", source, explicit_worker_action, False, True, "credential_resolution")
        for root in home_roots:
            if _inside(candidate, root / "profiles") or _inside(candidate, root / "skills"):
                return _event(raw, operation, "PROFILE_RUNTIME", source, explicit_worker_action, False, True, "profile_or_skill_runtime")
            if _inside(candidate, root / "plugins"):
                return _event(raw, operation, "PLUGIN_RUNTIME", source, explicit_worker_action, False, True, "plugin_runtime")
            if _inside(candidate, root / "aota-runtime"):
                return _event(raw, operation, "RUNTIME_CONTROL_PLANE", source, explicit_worker_action, False, True, "runtime_control_plane")

    # The source checkout's plugin code is a runtime dependency when imported,
    # even though it physically sits below the source workspace.
    plugin_root = _canonical(Path(__file__).resolve().parent)
    if any(_inside(candidate, plugin_root) for candidate in candidates):
        return _event(raw, operation, "PLUGIN_RUNTIME", source, explicit_worker_action, False, True, "plugin_source_runtime")

    runtime_roots = [Path("/aota-runtime"), Path("/run"), Path("/var/lib/hermes")]
    if any(_inside(candidate, _canonical(root)) for candidate in candidates for root in runtime_roots):
        return _event(raw, operation, "RUNTIME_CONTROL_PLANE", source, explicit_worker_action, False, True, "runtime_dependency")

    system_roots = ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc", "/proc", "/sys", "/dev", "/tmp")
    if not any(_inside(candidate, workspace) for candidate in candidates) and any(any(_inside(candidate, _canonical(Path(root))) for root in system_roots) for candidate in candidates):
        return _event(raw, operation, "SYSTEM_DEPENDENCY", source, explicit_worker_action, False, True, "system_dependency")

    if any(_inside(candidate, workspace) for candidate in candidates):
        candidate = next(candidate for candidate in candidates if _inside(candidate, workspace))
        relative = candidate.relative_to(workspace)
        tier = "PROJECT_METADATA" if ".aota" in relative.parts or operation == "metadata" else (
            "PROJECT_WRITE" if operation in {"write", "create", "patch", "delete"} else "PROJECT_READ"
        )
        return _event(raw, operation, tier, source, explicit_worker_action, bool(explicit_worker_action), True, "explicit_project_operation" if explicit_worker_action else "workspace_path_without_explicit_operation", normalized=relative.as_posix())

    return _event(raw, operation, "UNKNOWN_EXTERNAL", source, explicit_worker_action, bool(explicit_worker_action), False, "unclassified_external_path")


def _event(path: str, operation: str, tier: str, source: str, explicit: bool, relevant: bool, allowed: bool, reason: str, *, normalized: str | None = None) -> dict[str, Any]:
    return {"path": normalized or path, "operation": operation, "tier": tier, "source": source, "explicit_worker_action": explicit, "scope_relevant": relevant, "allowed": allowed, "reason": reason}


def record_scope_event(
    task_dir: Path,
    *,
    path: str,
    operation: str,
    source: str,
    allowed: bool | None = None,
    decision_reason: str | None = None,
    attribution: str | None = None,
    authority: str | None = None,
    success: bool | None = None,
) -> None:
    """Append trusted, bounded, identity-bound worker telemetry.

    The task identity is loaded from the active worker context, never from the
    mutation tool request.  Telemetry remains best-effort so an audit sink
    outage cannot turn an otherwise authorized file operation into a runtime
    outage; postflight baseline comparison still fails closed on unattributed
    project deltas.
    """
    try:
        target = task_dir / "scope-events.jsonl"
        if task_dir.is_symlink() or not task_dir.is_dir() or target.is_symlink():
            return
        context_path = Path(__file__).with_name("_active_task_context.py")
        context_spec = importlib.util.spec_from_file_location("aota_scope_active_context", context_path)
        if not context_spec or not context_spec.loader:
            return
        context_loader = importlib.util.module_from_spec(context_spec)
        sys.modules[context_spec.name] = context_loader
        context_spec.loader.exec_module(context_loader)
        context = context_loader.load_active_task_context()
        workspace_root = Path(os.environ.get("AOTA_WORKSPACE_ROOT", ""))
        if not workspace_root.is_absolute():
            return
        scope = json.loads((task_dir / "scope.json").read_text(encoding="utf-8"))
        if not isinstance(scope, dict):
            return
        event = classify_path(
            path, workspace_root=workspace_root, operation=operation, source=source,
            explicit_worker_action=True,
            active_task_root=context.root, active_task_dir=context.task_dir,
        )
        event_allowed = bool(event.get("allowed", True))
        if event.get("tier") in {"PROJECT_READ", "PROJECT_WRITE", "PROJECT_METADATA"}:
            required = "read_scope" if operation in {"read", "metadata"} else "write_scope"
            relative = str(event.get("path", path))
            if _match_scope(relative, scope.get("forbidden_scope", [])):
                event_allowed, reason = False, "forbidden_scope_denied"
            elif not _match_scope(relative, scope.get(required, [])):
                event_allowed, reason = False, f"{required}_denied"
            else:
                reason = "scope_allowed"
        else:
            reason = str(event.get("reason", "non_project_operation"))
        if allowed is not None:
            event_allowed = bool(allowed)
        meta = context.meta
        process_session_id = os.environ.get("AOTA_PROFILE_TASK_PROCESS_SESSION_ID", "")
        record = {
            "schema_version": 1,
            "workspace_id": context.workspace_id,
            "project_id": meta.get("project_id", ""),
            "task_id": context.task_id,
            "start_id": context.start_id,
            "process_session_id": process_session_id,
            "timestamp": _utc_now(),
            "path": event.get("path", path),
            "operation": operation,
            "tier": event.get("tier", "UNKNOWN_EXTERNAL"),
            "source": source,
            "explicit_worker_action": True,
            "scope_relevant": bool(event.get("scope_relevant", False)),
            "allowed": event_allowed,
            "decision_reason": decision_reason or reason,
        }
        if allowed is not None:
            record["allowed"] = bool(allowed)
        if attribution:
            record["attribution"] = attribution
        if authority:
            record["authority"] = authority
        if success is not None:
            record["success"] = bool(success)
        encoded = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        if len(encoded) > 8192:
            return
        fd = os.open(str(target), os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            if os.fstat(fd).st_size + len(encoded) > 512 * 1024:
                return
            try:
                sequence = 1
                read_fd = os.open(str(target), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                try:
                    sequence += os.read(read_fd, 512 * 1024).count(b"\n")
                finally:
                    os.close(read_fd)
                record["sequence"] = sequence
                encoded = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
                if os.fstat(fd).st_size + len(encoded) > 512 * 1024:
                    return
            except OSError:
                return
            os.write(fd, encoded)
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError, ImportError):
        return


def has_current_task_prior_write(
    task_dir: Path,
    *,
    path: str,
    workspace_root: Path,
    scope: dict[str, Any],
) -> bool:
    """Return whether this task durably wrote exactly this project path first.

    The lookup is based only on the task-local, identity-bound event stream. It
    deliberately does not use the caller's claim, a prior task, or a baseline.
    The shared append/read lock makes the lookup race-safe with telemetry
    appenders.
    """
    try:
        context = _trusted_active_context()
        if _match_scope(path, scope.get("forbidden_scope", [])) or not _match_scope(path, scope.get("write_scope", [])):
            return False
        target = task_dir / "scope-events.jsonl"
        if target.is_symlink() or not target.is_file():
            return False
        expected_process = os.environ.get("AOTA_PROFILE_TASK_PROCESS_SESSION_ID", "")
        expected_process = expected_process or str((context.meta.get("execution") or {}).get("scope_process_session_id", ""))
        now = _dt.datetime.now(_dt.timezone.utc)
        normalized = classify_path(
            path,
            workspace_root=workspace_root,
            operation="read",
            source="aota_project_file_read",
            explicit_worker_action=True,
            active_task_root=context.root,
            active_task_dir=context.task_dir,
        ).get("path")
        if not isinstance(normalized, str):
            return False
        fd = os.open(str(target), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            raw_lines = os.read(fd, 512 * 1024).decode("utf-8").splitlines()
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        for index, line in enumerate(raw_lines, start=1):
            try:
                event = json.loads(line)
            except (UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(event, dict):
                continue
            if (
                event.get("workspace_id") != context.workspace_id
                or event.get("project_id", context.meta.get("project_id", "")) != context.meta.get("project_id", "")
                or event.get("task_id") != context.task_id
                or event.get("start_id") != context.start_id
                or (expected_process and event.get("process_session_id") != expected_process)
                or event.get("path") != normalized
                or event.get("tier") != "PROJECT_WRITE"
                or event.get("source") not in {"aota_project_file_write", "aota_project_file_patch"}
                or event.get("operation") not in {"create", "write", "patch"}
                or event.get("allowed") is not True
                or not (event.get("success") is True or ("success" not in event and event.get("decision_reason") == "scope_allowed"))
            ):
                continue
            event_time = _parse_time(event.get("timestamp"))
            if event_time is None or event_time > now:
                continue
            event_sequence = event.get("sequence", index)
            if not isinstance(event_sequence, int):
                event_sequence = index
            return True
    except (OSError, UnicodeError, AttributeError):
        return False
    return False


def _load_events(task_dir: Path) -> tuple[list[dict[str, Any]], int]:
    path = task_dir / "scope-events.jsonl"
    if path.is_symlink() or not path.is_file():
        return [], 0
    events: list[dict[str, Any]] = []
    invalid = 0
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[:MAX_EVENTS]:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                invalid += 1
                continue
            if isinstance(value, dict):
                events.append(value)
            else:
                invalid += 1
    except (OSError, UnicodeError):
        return [], invalid + 1
    return events, invalid


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _trusted_active_context():
    context_path = Path(__file__).with_name("_active_task_context.py")
    context_spec = importlib.util.spec_from_file_location("aota_scope_active_context_lookup", context_path)
    if not context_spec or not context_spec.loader:
        raise ImportError("active task context unavailable")
    context_loader = importlib.util.module_from_spec(context_spec)
    sys.modules[context_spec.name] = context_loader
    context_spec.loader.exec_module(context_loader)
    return context_loader.load_active_task_context()


def _git_output(workspace_root: Path, args: list[str]) -> bytes | None:
    try:
        if not (workspace_root / ".git").is_dir():
            return None
        result = subprocess.run(["git", "-C", str(workspace_root), *args], capture_output=True, timeout=20)
        if result.returncode != 0:
            return None
        return result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return None


def _fingerprint(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink():
            target = os.readlink(path)
            return {"exists": True, "kind": "symlink", "target_sha256": hashlib.sha256(target.encode()).hexdigest()}
        stat = path.stat()
        if not path.is_file():
            return {"exists": True, "kind": "other", "mode": stat.st_mode, "size": stat.st_size}
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            if stat.st_size <= MAX_FINGERPRINT_BYTES:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
            else:
                digest.update(handle.read(MAX_FINGERPRINT_BYTES // 2))
                handle.seek(max(0, stat.st_size - MAX_FINGERPRINT_BYTES // 2))
                digest.update(handle.read(MAX_FINGERPRINT_BYTES // 2))
        return {"exists": True, "kind": "file", "size": stat.st_size, "sha256": digest.hexdigest()}
    except OSError:
        return {"exists": False}


def _status_paths(status: bytes) -> tuple[list[tuple[str, str]], list[str]]:
    tracked: list[tuple[str, str]] = []
    untracked: list[str] = []
    for record in status.split(b"\0"):
        if len(record) < 4:
            continue
        text = record.decode("utf-8", errors="replace")
        code, name = text[:2], text[3:]
        if not name or name == ".":
            continue
        if code == "??":
            untracked.append(name)
        else:
            tracked.append((name, code))
        if len(tracked) + len(untracked) >= MAX_BASELINE_PATHS:
            break
    return tracked, untracked


def capture_workspace_snapshot(workspace_root: Path) -> dict[str, Any]:
    """Capture bounded pre/post state without treating it as worker telemetry."""
    root = _canonical(workspace_root)
    status = _git_output(root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    head = _git_output(root, ["rev-parse", "HEAD"])
    index = _git_output(root, ["ls-files", "-s", "-z"])
    if status is None or head is None or index is None:
        raise OSError("git baseline unavailable")
    tracked, untracked = _status_paths(status)
    paths = [name for name, _ in tracked] + untracked
    entries = {name: {"status": next((code for path, code in tracked if path == name), "??"), "fingerprint": _fingerprint(root / name)} for name in paths[:MAX_BASELINE_PATHS]}
    return {
        "head": head.decode("utf-8", errors="replace").strip(),
        "index_sha256": hashlib.sha256(index).hexdigest(),
        "tracked_dirty_paths": [name for name, _ in tracked[:MAX_BASELINE_PATHS]],
        "untracked_paths": untracked[:MAX_BASELINE_PATHS],
        "entries": entries,
        "captured_at": _utc_now(),
    }


def capture_workspace_baseline(*, workspace_root: Path, task_dir: Path, workspace_id: str, project_id: str, task_id: str, start_id: str) -> dict[str, Any]:
    baseline = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "workspace_id": workspace_id,
        "project_id": project_id,
        "task_id": task_id,
        "start_id": start_id,
        "workspace_root": str(_canonical(workspace_root)),
        "snapshot": capture_workspace_snapshot(workspace_root),
    }
    _atomic_write_json(task_dir / "workspace-baseline.json", baseline)
    return baseline


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _workspace_delta(task_dir: Path, workspace_root: Path) -> tuple[list[dict[str, Any]], int, bool]:
    baseline_path = task_dir / "workspace-baseline.json"
    if baseline_path.is_symlink() or not baseline_path.is_file():
        return [], 0, False
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        before = baseline["snapshot"]["entries"]
        after = capture_workspace_snapshot(workspace_root)["entries"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError):
        return [], 0, False
    delta: list[dict[str, Any]] = []
    preexisting = 0
    for name in sorted(set(before) | set(after)):
        old = before.get(name)
        new = after.get(name)
        if old is not None and new is not None and old == new:
            preexisting += 1
            continue
        if old is None and new is not None:
            kind = "new"
        elif old is not None and new is not None:
            kind = "changed"
        else:
            # A dirty path that disappeared is a task delta too.
            kind = "changed"
        delta.append({"path": name, "kind": kind, "before": old, "after": new})
    return delta[:MAX_BASELINE_PATHS], preexisting, True


def _parse_time(value: object) -> _dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(_dt.timezone.utc)
    except ValueError:
        return None


def verify_scope(task_dir: Path, workspace_root: Path) -> dict[str, Any]:
    """Verify trusted worker events plus task-local postflight deltas."""
    scope_path = task_dir / "scope.json"
    try:
        if scope_path.is_symlink() or not scope_path.is_file():
            return _result("unknown", [], unknown=1)
        scope = json.loads(scope_path.read_text(encoding="utf-8"))
        if not isinstance(scope, dict):
            return _result("unknown", [], unknown=1)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _result("unknown", [], unknown=1)

    active_root = Path(os.environ.get("AOTA_PROFILE_TASK_ROOT", str(task_dir.parent.parent)))
    events, malformed_events = _load_events(task_dir)
    try:
        meta = json.loads((task_dir / "meta.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        meta = {}
    execution = meta.get("execution", {}) if isinstance(meta, dict) else {}
    expected_workspace = scope.get("workspace_id", meta.get("workspace_id", ""))
    expected_project = scope.get("project_id", meta.get("project_id", ""))
    expected_task = scope.get("task_id", meta.get("task_id", ""))
    expected_start = scope.get("start_id", execution.get("start_id", ""))
    expected_process = execution.get("scope_process_session_id", scope.get("process_session_id", ""))
    start_time = _parse_time(execution.get("started_at")) or _parse_time(scope.get("created_at"))
    finalizer_time = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=30)
    delta, preexisting, baseline_available = _workspace_delta(task_dir, workspace_root)

    checked = worker_checked = worker_violations = 0
    active_reads = active_writes = ignored = unknown = 0
    project_reads = project_writes = project_metadata = 0
    post_write_verification_reads = 0
    invalid_foreign = malformed_events
    legacy_unattributed = 0
    postflight_unattributed = postflight_violations = 0
    violations: list[dict[str, Any]] = []
    valid_events: list[dict[str, Any]] = []
    seen_project_events: set[tuple[str, str, int | None]] = set()
    seen_project_write_paths: set[str] = set()
    successful_writes: dict[str, list[tuple[_dt.datetime, int, dict[str, Any]]]] = {}
    for event_index, raw in enumerate(events[:MAX_EVENTS], start=1):
        if not isinstance(raw, dict) or not isinstance(raw.get("path"), str):
            invalid_foreign += 1
            continue
        identity_fields = ("workspace_id", "task_id", "start_id", "timestamp")
        if any(not raw.get(field) for field in identity_fields):
            legacy_unattributed += 1
            continue
        timestamp = _parse_time(raw.get("timestamp"))
        if (
            raw.get("workspace_id") != expected_workspace
            or raw.get("project_id", expected_project) != expected_project
            or raw.get("task_id") != expected_task
            or raw.get("start_id") != expected_start
            or (expected_process and raw.get("process_session_id") != expected_process)
            or timestamp is None
            or (start_time is not None and timestamp < start_time)
            or timestamp > finalizer_time
        ):
            invalid_foreign += 1
            continue
        valid_events.append(raw)
        event = classify_path(raw["path"], workspace_root=workspace_root, operation=raw.get("operation", "read"), source=raw.get("source", "runtime"), explicit_worker_action=bool(raw.get("explicit_worker_action", False)), active_task_root=active_root, active_task_dir=task_dir)
        tier = event["tier"]
        if tier == "ACTIVE_TASK_READ":
            active_reads += 1
        elif tier == "ACTIVE_TASK_WRITE":
            active_writes += 1
        if tier not in {"PROJECT_READ", "PROJECT_WRITE", "PROJECT_METADATA"}:
            if tier == "UNKNOWN_EXTERNAL" and event["explicit_worker_action"]:
                unknown += 1
            else:
                ignored += 1
            continue
        raw_sequence = raw.get("sequence") if isinstance(raw.get("sequence"), int) else None
        dedupe_key = (tier, event["path"], event["operation"], raw_sequence)
        if dedupe_key in seen_project_events:
            continue
        seen_project_events.add(dedupe_key)
        if not event["scope_relevant"] or not raw.get("explicit_worker_action", False):
            ignored += 1
            continue
        checked += 1
        worker_checked += 1
        if tier == "PROJECT_READ":
            project_reads += 1
        elif tier == "PROJECT_WRITE":
            project_writes += 1
        else:
            project_metadata += 1
        relative = event["path"]
        if tier == "PROJECT_READ" or tier == "PROJECT_METADATA" and event["operation"] in {"read", "metadata"}:
            required = "read_scope"
        else:
            required = "write_scope"
        allowed = _match_scope(relative, scope.get(required, [])) and not _match_scope(relative, scope.get("forbidden_scope", []))
        if (
            tier == "PROJECT_READ"
            and event["operation"] == "read"
            and not allowed
            and not _match_scope(relative, scope.get("forbidden_scope", []))
        ):
            prior_candidates = successful_writes.get(relative, [])
            prior = next((item for item in reversed(prior_candidates) if item[0] <= timestamp and item[1] < event_index), None)
            if prior is not None and raw.get("source") == "aota_project_file_read":
                allowed = True
                event["attribution"] = "post_write_verification"
                event["authority"] = "current_task_prior_write"
                event["decision_reason"] = "post_write_verification_allowed"
                post_write_verification_reads += 1
        event["allowed"] = allowed
        if not allowed and len(violations) < MAX_VIOLATIONS:
            event["reason"] = "forbidden_scope" if _match_scope(relative, scope.get("forbidden_scope", [])) else "outside_required_scope"
            worker_violations += 1
            violations.append({"path": relative, "normalized_project_path": relative, "operation": event["operation"], "source": raw.get("source", "worker"), "attribution": "worker_action", "required_scope": required, "reason": event["reason"]})
        if tier == "PROJECT_WRITE" and event["operation"] in {"create", "write", "patch"} and allowed and (
            raw.get("source") in {"aota_project_file_write", "aota_project_file_patch"}
            and (raw.get("success") is True or ("success" not in raw and raw.get("decision_reason") == "scope_allowed"))
        ):
            successful_writes.setdefault(relative, []).append((timestamp, raw_sequence or event_index, raw))
        if tier == "PROJECT_WRITE" and event["operation"] in {"create", "write", "patch"}:
            seen_project_write_paths.add(relative)

    for item in delta:
        raw_path = item["path"]
        event = classify_path(raw_path, workspace_root=workspace_root, operation="write", source="postflight_workspace_delta", explicit_worker_action=False, active_task_root=active_root, active_task_dir=task_dir)
        tier = event["tier"]
        if tier in {"ACTIVE_TASK_READ", "ACTIVE_TASK_WRITE", "RUNTIME_CONTROL_PLANE", "PROFILE_RUNTIME", "PLUGIN_RUNTIME", "CREDENTIAL_AUTHORITY", "SYSTEM_DEPENDENCY"}:
            ignored += 1
            continue
        if tier == "UNKNOWN_EXTERNAL":
            unknown += 1
            continue
        checked += 1
        if tier == "PROJECT_READ":
            project_reads += 1
        elif tier == "PROJECT_WRITE":
            project_writes += 1
        else:
            project_metadata += 1
        relative = event["path"]
        matching = relative in seen_project_write_paths
        allowed = _match_scope(relative, scope.get("write_scope", [])) and not _match_scope(relative, scope.get("forbidden_scope", []))
        if not matching:
            postflight_unattributed += 1
            postflight_violations += 1
            if len(violations) < MAX_VIOLATIONS:
                violations.append({"path": relative, "normalized_project_path": relative, "operation": "write", "source": "postflight_workspace_delta", "attribution": "unattributed_workspace_delta", "reason": "missing_worker_action_event"})
        elif not allowed:
            postflight_violations += 1
            if len(violations) < MAX_VIOLATIONS:
                violations.append({"path": relative, "normalized_project_path": relative, "operation": "write", "source": "postflight_workspace_delta", "attribution": "worker_action_confirmed", "reason": "outside_required_scope" if not _match_scope(relative, scope.get("write_scope", [])) else "forbidden_scope"})

    if worker_violations or postflight_violations:
        status = "violated"
    elif unknown or invalid_foreign or legacy_unattributed or (not baseline_available and not events):
        status = "unknown"
    elif checked:
        status = "compliant"
    else:
        status = "not_applicable"
    return _result(status, violations, checked=checked, active_reads=active_reads, active_writes=active_writes, ignored=ignored, unknown=unknown, scope=scope, worker_checked=worker_checked, worker_violations=worker_violations, project_reads=project_reads, project_writes=project_writes, project_metadata=project_metadata, postflight_count=len(delta), postflight_unattributed=postflight_unattributed, postflight_violations=postflight_violations, preexisting=preexisting, invalid_foreign=invalid_foreign, legacy_unattributed=legacy_unattributed, post_write_verification_reads=post_write_verification_reads)


def _result(status: str, violations: list[dict[str, Any]], *, checked: int = 0, active_reads: int = 0, active_writes: int = 0, ignored: int = 0, unknown: int = 0, scope: dict[str, Any] | None = None, worker_checked: int = 0, worker_violations: int = 0, project_reads: int = 0, project_writes: int = 0, project_metadata: int = 0, postflight_count: int = 0, postflight_unattributed: int = 0, postflight_violations: int = 0, preexisting: int = 0, invalid_foreign: int = 0, legacy_unattributed: int = 0, post_write_verification_reads: int = 0) -> dict[str, Any]:
    scope = scope or {}
    compliance = {
        "status": status,
        "scope_source": scope.get("scope_source", ""),
        "scope_digest": scope.get("scope_digest", ""),
        "worker_action_checked_count": worker_checked,
        "worker_action_violation_count": worker_violations,
        "project_read_count": project_reads,
        "project_write_count": project_writes,
        "project_metadata_count": project_metadata,
        "post_write_verification_read_count": post_write_verification_reads,
        "project_checked_path_count": checked,
        "project_violated_path_count": min(worker_violations + postflight_violations, MAX_VIOLATIONS),
        "active_task_read_count": active_reads,
        "active_task_write_count": active_writes,
        "postflight_new_or_changed_path_count": postflight_count,
        "postflight_unattributed_path_count": postflight_unattributed,
        "postflight_violation_count": postflight_violations,
        "preexisting_dirty_path_count": preexisting,
        "ignored_runtime_path_count": ignored,
        "unknown_external_path_count": unknown,
        "invalid_or_foreign_event_count": invalid_foreign,
        "legacy_unattributed_event_count": legacy_unattributed,
        "violations": violations,
    }
    return {
        "scope_compliance": compliance,
        "violated_paths": [item["normalized_project_path"] for item in violations],
        "project_checked_path_count": checked,
        "project_violated_path_count": min(worker_violations + postflight_violations, MAX_VIOLATIONS),
        "active_task_read_count": active_reads,
        "active_task_write_count": active_writes,
        "ignored_runtime_path_count": ignored,
        "unknown_external_path_count": unknown,
        "worker_action_checked_count": worker_checked,
        "worker_action_violation_count": worker_violations,
        "project_read_count": project_reads,
        "project_write_count": project_writes,
        "project_metadata_count": project_metadata,
        "post_write_verification_read_count": post_write_verification_reads,
        "postflight_new_or_changed_path_count": postflight_count,
        "postflight_unattributed_path_count": postflight_unattributed,
        "postflight_violation_count": postflight_violations,
        "preexisting_dirty_path_count": preexisting,
        "invalid_or_foreign_event_count": invalid_foreign,
        "legacy_unattributed_event_count": legacy_unattributed,
    }
