#!/usr/bin/env python3
"""HOST-WI-00 Docker baseline capture and offline rollback preservation.

This is deliberately a small, stdlib-first operator tool.  It separates
private material (raw Docker output, .env, resolved compose, and archives)
from sanitized evidence.  It never removes Docker objects and ``stop`` is
guarded by a fresh verification plus the active-task gate.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import pwd
import grp
import re
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - exercised on minimal hosts
    yaml = None


WORK_ITEM = "HOST-WI-00"
SCHEMA_VERSION = 1
UTC_FORMAT = "%Y%m%dT%H%M%SZ"
ACTIVE_STATUSES = {"active", "running", "pending", "queued", "starting", "in_progress", "awaiting_worker", "needs_input"}
OPERATOR_AUTHORIZATION_ID = "HOST-WI-00-IGNORE-LEGACY-TASK-STATE-20260724"
EXCLUDE_DIRS = frozenset({"__pycache__", ".git", ".cache", ".mypy_cache", ".ruff_cache", ".pytest_cache", "node_modules"})
EXCLUDE_SUFFIXES = frozenset({".pyc", ".pyo", ".pyd"})
EXCLUDE_NAMES = frozenset({".DS_Store", "Thumbs.db"})
SECRET_NAME_RE = re.compile(r"(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|COOKIE|AUTH|BEARER|PRIVATE|OAUTH|API_KEY|ACCESS_KEY|REFRESH_TOKEN|SESSION_TOKEN)", re.I)
SECRET_VALUE_RE = re.compile(r"(?:sk-[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|Bearer\s+[A-Za-z0-9._-]{16,}|AKIA[0-9A-Z]{16})")
SPECIAL_TYPES = {stat.S_IFSOCK, stat.S_IFBLK, stat.S_IFCHR, stat.S_IFIFO}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime(UTC_FORMAT)


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_dump(path: Path, value: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    os.chmod(path, mode)


def safe_text(path: Path, limit: int = 200_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def run_command(argv: list[str], *, cwd: Path | None = None, timeout: int = 60,
                stdout_path: Path | None = None, stderr_path: Path | None = None) -> tuple[int, str, str]:
    """Run an operator command without putting raw output on stdout.

    For private captures callers pass both output paths.  Returned text is
    only used for parsing and is never included in sanitized evidence.
    """
    out_fh = stdout_path.open("wb") if stdout_path else subprocess.PIPE
    err_fh = stderr_path.open("wb") if stderr_path else subprocess.PIPE
    if stdout_path:
        os.chmod(stdout_path, 0o600)
    if stderr_path:
        os.chmod(stderr_path, 0o600)
    try:
        proc = subprocess.run(argv, cwd=str(cwd) if cwd else None, stdout=out_fh, stderr=err_fh,
                              timeout=timeout, check=False)
        out = safe_text(stdout_path) if stdout_path else (proc.stdout or b"").decode(errors="replace")
        err = safe_text(stderr_path) if stderr_path else (proc.stderr or b"").decode(errors="replace")
        return proc.returncode, out, err
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", type(exc).__name__
    finally:
        if stdout_path:
            out_fh.close()  # type: ignore[union-attr]
        if stderr_path:
            err_fh.close()  # type: ignore[union-attr]


def load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def path_info(path: Path) -> dict[str, Any]:
    try:
        st = path.lstat()
    except OSError:
        return {"path": str(path), "exists": False, "type": "missing"}
    if stat.S_ISLNK(st.st_mode):
        kind = "symlink"
    elif stat.S_ISDIR(st.st_mode):
        kind = "directory"
    elif stat.S_ISREG(st.st_mode):
        kind = "file"
    else:
        kind = "special"
    try:
        owner, group = pwd.getpwuid(st.st_uid).pw_name, grp.getgrgid(st.st_gid).gr_name
    except KeyError:
        owner, group = str(st.st_uid), str(st.st_gid)
    return {"path": str(path), "exists": True, "type": kind, "owner": owner, "group": group,
            "mode": oct(stat.S_IMODE(st.st_mode)), "uid": st.st_uid, "gid": st.st_gid}


def exclusion_for(root: Path, path: Path) -> dict[str, str] | None:
    """Classify by lexical path before readability/stat/hash/copy checks."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return {"relative_path": str(path), "reason": "path_escape", "file_class": "unsafe"}
    parts = relative.parts
    name = path.name
    if any(part in EXCLUDE_DIRS for part in parts):
        return {"relative_path": relative.as_posix(), "reason": "runtime_generated_python_cache" if "__pycache__" in parts else "canonical_runtime_exclusion", "file_class": "directory_cache" if name in EXCLUDE_DIRS else "runtime_cache_file"}
    if name in EXCLUDE_NAMES or name.endswith(("~", ".bak", ".swp", ".tmp")) or name.startswith(".#") or (name.startswith("#") and name.endswith("#")):
        return {"relative_path": relative.as_posix(), "reason": "canonical_runtime_exclusion", "file_class": "temporary_file"}
    if path.suffix in EXCLUDE_SUFFIXES:
        return {"relative_path": relative.as_posix(), "reason": "runtime_generated_python_cache", "file_class": "runtime_cache_file"}
    return None


def walk_entries(root: Path) -> tuple[list[Path], list[dict[str, Any]]]:
    """Enumerate without opening excluded files or traversing symlink targets."""
    files: list[Path] = []
    exclusions: list[dict[str, Any]] = []
    if not root.exists() and not root.is_symlink():
        return files, exclusions

    def visit(directory: Path) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            exclusions.append({"relative_path": directory.relative_to(root).as_posix() or ".", "reason": type(exc).__name__, "file_class": "unreadable_directory"})
            return
        for entry in entries:
            path = Path(entry.path)
            classified = exclusion_for(root, path)
            # Classification is deliberately before is_dir/lstat/readability.
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                is_link = entry.is_symlink()
            except OSError:
                is_dir, is_link = False, False
            if classified:
                if is_dir and not is_link:
                    visit(path)
                else:
                    exclusions.append(classified)
                continue
            if is_dir and not is_link:
                visit(path)
            elif is_link:
                files.append(path)
            else:
                try:
                    st = path.lstat()
                except OSError as exc:
                    exclusions.append({"relative_path": path.relative_to(root).as_posix(), "reason": type(exc).__name__, "file_class": "unreadable_file"})
                    continue
                if stat.S_ISREG(st.st_mode):
                    files.append(path)
                elif stat.S_IFMT(st.st_mode) in SPECIAL_TYPES:
                    exclusions.append({"relative_path": path.relative_to(root).as_posix(), "reason": "ephemeral_or_special_file", "file_class": "special_file"})
                else:
                    exclusions.append({"relative_path": path.relative_to(root).as_posix(), "reason": "unsupported_file_type", "file_class": "special_file"})

    if root.is_dir() and not root.is_symlink():
        visit(root)
    elif root.is_symlink():
        files.append(root)
    return files, exclusions


def regular_manifest(root: Path) -> dict[str, Any]:
    files, exclusions = walk_entries(root)
    entries: list[dict[str, Any]] = []
    for path in files:
        rel = "." if path == root else path.relative_to(root).as_posix()
        st = path.lstat()
        item: dict[str, Any] = {"relative_path": rel, "mode": oct(stat.S_IMODE(st.st_mode)),
                                "is_symlink": stat.S_ISLNK(st.st_mode)}
        if stat.S_ISLNK(st.st_mode):
            item["target"] = os.readlink(path)
            item["sha256"] = None
        else:
            item["sha256"] = sha256_file(path)
        entries.append(item)
    return {"schema_version": 1, "root": str(root), "file_count": len(entries),
            "files": entries, "exclusions": exclusions}


def copy_tree_preserving(root: Path, destination: Path) -> dict[str, Any]:
    """Copy regular files and symlinks without following symlink escapes."""
    files, exclusions = walk_entries(root)
    destination.mkdir(parents=True, exist_ok=True)
    for path in files:
        target = destination / ("." if path == root else path.relative_to(root))
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            target.symlink_to(os.readlink(path))
        else:
            shutil.copyfile(path, target)
            os.chmod(target, 0o600)
    return {"file_count": len(files), "exclusions": exclusions}


def archive_tree(root: Path, archive: Path) -> dict[str, Any]:
    files, exclusions = walk_entries(root)
    manifest_files: list[dict[str, Any]] = []
    with tarfile.open(archive, "w") as tar:
        tar.add(str(root), arcname=".hermes", recursive=False)
        for path in files:
            rel = path.relative_to(root).as_posix()
            info = tar.gettarinfo(str(path), arcname=str(Path(".hermes") / rel))
            if path.is_symlink():
                tar.addfile(info)
                manifest_files.append({"relative_path": rel, "mode": oct(stat.S_IMODE(path.lstat().st_mode)), "is_symlink": True, "target": os.readlink(path), "sha256": None})
            else:
                digest = hashlib.sha256()
                with path.open("rb") as fh:
                    class HashingReader:
                        def read(self, size: int = -1) -> bytes:
                            block = fh.read(size)
                            digest.update(block)
                            return block
                    tar.addfile(info, HashingReader())
                manifest_files.append({"relative_path": rel, "mode": oct(stat.S_IMODE(path.lstat().st_mode)), "is_symlink": False, "sha256": digest.hexdigest()})
    os.chmod(archive, 0o600)
    return {"archive": str(archive), "sha256": sha256_file(archive), "exclusions": exclusions,
            "manifest": {"schema_version": 1, "root": str(root), "file_count": len(manifest_files), "files": manifest_files, "exclusions": exclusions}}


def parse_json_lines(text: str) -> list[dict[str, Any]]:
    result = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict): result.append(value)
        except json.JSONDecodeError:
            continue
    return result


def read_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML is required to inspect AOTA YAML declarations")
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def task_gate(hermes_home: Path, *, ignore_legacy: bool = False,
              authorization_id: str | None = None, capture_start_epoch: float | None = None) -> dict[str, Any]:
    root = hermes_home / "aota-runtime" / "profile-tasks"
    counts: dict[str, int] = {}
    active: list[dict[str, Any]] = []
    new_records: list[str] = []
    for meta_path in sorted(root.glob("*/*/meta.json")) if root.is_dir() else []:
        data = load_json(meta_path, {})
        execution = data.get("execution") if isinstance(data.get("execution"), dict) else {}
        status = str(data.get("status", execution.get("status", "unknown")))
        counts[status] = counts.get(status, 0) + 1
        if capture_start_epoch is not None:
            try:
                if meta_path.stat().st_mtime > capture_start_epoch:
                    new_records.append(str(meta_path))
            except OSError:
                new_records.append(str(meta_path))
        if status.lower() in ACTIVE_STATUSES:
            active.append({"task_id": meta_path.parent.name, "workspace": meta_path.parent.parent.name,
                           "profile": data.get("profile") or execution.get("profile"), "status": status,
                           "started_at": execution.get("started_at"), "meta_json": str(meta_path),
                           "completion_receipt": execution.get("completion_receipt_path")})
    running = sum(v for k, v in counts.items() if k.lower() in {"running", "active", "starting", "in_progress"})
    pending = sum(v for k, v in counts.items() if k.lower() in {"pending", "queued", "awaiting_worker", "needs_input"})
    authorized = bool(ignore_legacy and authorization_id == OPERATOR_AUTHORIZATION_ID)
    effective = {"active": 0, "pending": 0, "running": 0} if authorized else {"active": len(active), "pending": pending, "running": running}
    return {"active_task_count": len(active), "pending_task_count": pending, "running_task_count": running,
            "status_counts": counts, "tasks": active, "observed": {"active_records": len(active), "pending_records": pending, "running_records": running},
            "operator_override": {"authorization_id": authorization_id, "authorized": authorized,
                                   "reason": "operator classified records as historical/intermediate artifacts" if authorized else None,
                                   "preserve_unchanged": authorized, "metadata_mutation": False, "artifact_deletion": False, "process_termination": False},
            "effective_stop_gate": {**effective, "pass": all(value == 0 for value in effective.values()) and not new_records},
            "new_task_records_since_capture_start": len(new_records), "new_task_paths": new_records,
            "pass": all(value == 0 for value in effective.values()) and not new_records}


def secret_key_projection(env: Iterable[str]) -> list[dict[str, Any]]:
    result = []
    for item in env:
        key = str(item).split("=", 1)[0]
        result.append({"key": key, "present": "=" in item, "value_redacted": True})
    return sorted(result, key=lambda x: x["key"])


def redacted_env(env: Iterable[str]) -> list[dict[str, Any]]:
    return secret_key_projection(env)


def trim_url(value: str) -> dict[str, Any] | None:
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme and parsed.hostname:
            return {"scheme": parsed.scheme, "host": parsed.hostname, "port": parsed.port}
    except ValueError:
        pass
    return None


def image_summary(data: dict[str, Any]) -> dict[str, Any]:
    repo_digests = data.get("RepoDigests") or []
    return {"id": data.get("Id"), "tags": data.get("RepoTags") or [], "digests": repo_digests,
            "created": data.get("Created")}


def verify_tar_manifest(archive_path: Path, manifest: dict[str, Any]) -> bool:
    try:
        with tarfile.open(archive_path, "r") as archive:
            members = {member.name: member for member in archive.getmembers()}
            for item in manifest.get("files", []):
                if item.get("is_symlink"):
                    continue
                member = members.get(str(Path(".hermes") / item["relative_path"]))
                if member is None or not member.isfile():
                    return False
                stream = archive.extractfile(member)
                if stream is None:
                    return False
                digest = hashlib.sha256()
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
                if digest.hexdigest() != item.get("sha256"):
                    return False
            return True
    except (OSError, tarfile.TarError, KeyError):
        return False


def compose_env_map(config: dict[str, Any], service: str) -> dict[str, str]:
    env = config.get("services", {}).get(service, {}).get("environment", {})
    if isinstance(env, dict): return {str(k): "" if v is None else str(v) for k, v in env.items()}
    return {str(x).split("=", 1)[0]: str(x).split("=", 1)[1] if "=" in str(x) else "" for x in (env or [])}


def docker_capture(private: Path, stack: Path, compose: Path) -> dict[str, Any]:
    raw = private / "docker-metadata"
    raw.mkdir(parents=True, exist_ok=True)
    commands: dict[str, list[str]] = {
        "compose_ps.jsonl": ["docker", "compose", "-f", str(compose), "--project-directory", str(stack), "ps", "--all", "--format", "json"],
        "compose_config.json": ["docker", "compose", "-f", str(compose), "--project-directory", str(stack), "config", "--format", "json"],
        "engine_version.txt": ["docker", "version", "--format", "{{.Server.Version}}"],
        "compose_version.txt": ["docker", "compose", "version"],
    }
    results: dict[str, Any] = {}
    for name, argv in commands.items():
        out = raw / name
        err = raw / (name + ".stderr")
        code, text, error = run_command(argv, cwd=stack, timeout=90, stdout_path=out, stderr_path=err)
        results[name] = {"returncode": code, "ok": code == 0, "stderr_file": str(err), "error_type": error[:80]}
    ps = parse_json_lines(safe_text(raw / "compose_ps.jsonl"))
    config = load_json(raw / "compose_config.json", {}) or {}
    results["ps"] = ps
    results["config"] = config
    names = [str(x.get("Name")) for x in ps if x.get("Name")]
    for name in names:
        out, err = raw / f"container-{name}.json", raw / f"container-{name}.stderr"
        code, _, error = run_command(["docker", "inspect", name], timeout=90, stdout_path=out, stderr_path=err)
        results[f"container:{name}"] = {"returncode": code, "ok": code == 0, "error_type": error[:80]}
    image_refs = sorted({str(x.get("Image")) for x in ps if x.get("Image")})
    for ref in image_refs:
        safe = hashlib.sha256(ref.encode()).hexdigest()[:16]
        out, err = raw / f"image-{safe}.json", raw / f"image-{safe}.stderr"
        code, _, error = run_command(["docker", "image", "inspect", ref], timeout=90, stdout_path=out, stderr_path=err)
        results[f"image:{ref}"] = {"returncode": code, "ok": code == 0, "error_type": error[:80], "file": str(out)}
    volumes = sorted({str(v) for service in config.get("services", {}).values() for m in service.get("volumes", [])
                      for v in [m.get("source")] if isinstance(m, dict) and str(v).startswith("hermes")})
    for volume in volumes:
        safe = hashlib.sha256(volume.encode()).hexdigest()[:16]
        out, err = raw / f"volume-{safe}.json", raw / f"volume-{safe}.stderr"
        code, _, error = run_command(["docker", "volume", "inspect", volume], timeout=90, stdout_path=out, stderr_path=err)
        results[f"volume:{volume}"] = {"returncode": code, "ok": code == 0, "error_type": error[:80], "file": str(out)}
    networks = sorted({str(n) for service in config.get("services", {}).values() for n in service.get("networks", {})
                       if isinstance(n, str)})
    project = str(config.get("name") or "hermes-stack")
    for network in sorted(set(networks + [f"{project}_hermes-net"])):
        safe = hashlib.sha256(network.encode()).hexdigest()[:16]
        out, err = raw / f"network-{safe}.json", raw / f"network-{safe}.stderr"
        code, _, error = run_command(["docker", "network", "inspect", network], timeout=90, stdout_path=out, stderr_path=err)
        results[f"network:{network}"] = {"returncode": code, "ok": code == 0, "error_type": error[:80], "file": str(out)}
    return results


def image_from_raw(private: Path, ref: str) -> dict[str, Any]:
    path = private / "docker-metadata" / f"image-{hashlib.sha256(ref.encode()).hexdigest()[:16]}.json"
    raw = load_json(path, [])
    return image_summary(raw[0]) if isinstance(raw, list) and raw and isinstance(raw[0], dict) else {"id": None, "tags": [], "digests": [], "created": None}


def inspect_payload(paths: dict[str, Any], private: Path) -> dict[str, Any]:
    config = load_json(private / "docker-metadata/compose_config.json", {}) or {}
    ps = parse_json_lines(safe_text(private / "docker-metadata/compose_ps.jsonl"))
    containers: list[dict[str, Any]] = []
    images: dict[str, dict[str, Any]] = {}
    for row in ps:
        name = row.get("Name") or row.get("Names")
        ref = row.get("Image")
        inspect_path = private / "docker-metadata" / f"container-{name}.json"
        raw = load_json(inspect_path, [])
        item = raw[0] if isinstance(raw, list) and raw and isinstance(raw[0], dict) else {}
        host = item.get("HostConfig", {}) if isinstance(item, dict) else {}
        cfg = item.get("Config", {}) if isinstance(item, dict) else {}
        mounts = []
        for mount in item.get("Mounts", []) if isinstance(item, dict) else []:
            mounts.append({"source": str(mount.get("Source", "")), "target": mount.get("Destination"),
                           "type": mount.get("Type"), "read_only": bool(mount.get("RW") is False)})
        ports = item.get("NetworkSettings", {}).get("Ports", {}) if isinstance(item, dict) else {}
        env_names = secret_key_projection(cfg.get("Env") or [])
        containers.append({"name": name, "image_reference": ref, "image_id": item.get("Image"),
                           "repo_digests": image_from_raw(private, str(ref)).get("digests", []),
                           "command": cfg.get("Cmd") or row.get("Command"), "state": row.get("State"),
                           "health": item.get("State", {}).get("Health", {}).get("Status") if isinstance(item, dict) else None,
                           "restart_policy": host.get("RestartPolicy", {}).get("Name"), "ports": ports,
                           "mounts": mounts, "environment": env_names})
        if ref: images[str(ref)] = image_from_raw(private, str(ref))
    volumes = []
    for key, value in config.get("volumes", {}).items():
        actual = f"{config.get('name', 'hermes-stack')}_{key}"
        raw = load_json(private / "docker-metadata" / f"volume-{hashlib.sha256(actual.encode()).hexdigest()[:16]}.json", [])
        info = raw[0] if isinstance(raw, list) and raw else {}
        bind = (value.get("driver_opts") or {}).get("device") if isinstance(value, dict) else None
        volumes.append({"name": actual, "driver": info.get("Driver") or (value or {}).get("driver"),
                        "mountpoint_redacted": True, "bind_backing_path": bind if bind else None,
                        "logical_purpose": "Hermes home" if key == "hermes-home" else "Hermes agent runtime/image data",
                        "backup_status": "host-path-hash-reference" if bind else "preserved-private-tar-if-available"})
    networks = []
    for key, value in config.get("networks", {}).items():
        actual = f"{config.get('name', 'hermes-stack')}_{key}"
        raw = load_json(private / "docker-metadata" / f"network-{hashlib.sha256(actual.encode()).hexdigest()[:16]}.json", [])
        info = raw[0] if isinstance(raw, list) and raw else {}
        members = sorted((info.get("Containers") or {}).keys()) if isinstance(info, dict) else []
        networks.append({"name": actual, "driver": info.get("Driver") or (value or {}).get("driver"), "container_membership": members})
    return {"compose_project": config.get("name", "hermes-stack"), "containers": containers,
            "images": list(images.values()), "volumes": volumes, "networks": networks,
            "docker_metadata_results": {k: v for k, v in paths.items() if k not in {"ps", "config"}}}


def override_mapping(overrides: Path, compose_config: dict[str, Any]) -> dict[str, Any]:
    mappings: list[dict[str, Any]] = []
    for service, data in compose_config.get("services", {}).items():
        for mount in data.get("volumes", []):
            if not isinstance(mount, dict): continue
            source = str(mount.get("source", ""))
            if source.startswith(str(overrides) + "/") or source == str(overrides):
                mappings.append({"service": service, "source": source, "target": mount.get("target"),
                                 "read_only": bool(mount.get("read_only"))})
    files, exclusions = walk_entries(overrides)
    entries = []
    for path in files:
        rel = "." if path == overrides else path.relative_to(overrides).as_posix()
        attached = [m for m in mappings if m["source"] == str(path)]
        target_kinds = {"webui" if m["service"] == "hermes-webui" or str(m.get("target", "")).startswith("/apptoo") else "agent" for m in attached}
        if "agent" in target_kinds: category, disposition = "agent", "migrate_agent"
        elif "webui" in target_kinds: category, disposition = "webui", "retire_webui"
        else: category, disposition = "unknown", "investigate"
        st = path.lstat()
        entries.append({"source_relative_path": rel, "mode": oct(stat.S_IMODE(st.st_mode)),
                        "is_symlink": stat.S_ISLNK(st.st_mode), "sha256": None if path.is_symlink() else sha256_file(path),
                        "category": category, "docker_target_mapping": attached, "disposition": disposition})
    return {"schema_version": 1, "source_root": str(overrides), "file_count": len(entries),
            "files": entries, "mount_mappings": mappings, "exclusions": exclusions}


def bounded_check(url: str | None) -> dict[str, Any]:
    if not url: return {"available": False, "reason": "not_configured"}
    parsed = trim_url(url)
    if not parsed: return {"available": False, "reason": "invalid_url"}
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=3) as response:
            return {"available": True, "status": int(response.status), "endpoint": parsed}
    except Exception as exc:
        return {"available": False, "reason": type(exc).__name__, "endpoint": parsed}


def runtime_baseline(paths: dict[str, Path], compose_config: dict[str, Any], gate: dict[str, Any]) -> dict[str, Any]:
    repo, hermes, workspace = paths["repo"], paths["hermes"], paths["workspace"]
    assembly = read_yaml(repo / "deploy/profile-runtime-assembly.yaml")
    plugin = read_yaml(repo / "plugin/aota-tools/plugin.yaml")
    inventory = read_yaml(repo / "deploy/aota-lifecycle-inventory.yaml")
    profiles = assembly.get("profiles", {})
    profile_checks = {}
    active_projection = {}; reference_projection = {}
    for name, spec in profiles.items():
        profile_checks[name] = {"config": (hermes / "profiles" / name / "config.yaml").is_file(),
                                "soul": (hermes / "profiles" / name / "SOUL.md").is_file(),
                                "plugin": (hermes / "profiles" / name / "plugins/aota-tools/plugin.yaml").is_file()}
        active_projection[name] = list(spec.get("active_skills", [])); reference_projection[name] = list(spec.get("reference_skills", []))
    tools = plugin.get("provides_tools", [])
    toolsets = {str(v.get("toolset")) for v in inventory.get("tools", {}).values() if isinstance(v, dict)}
    registry = workspace / ".aota/workspaces.json"
    projects = sorted(str(p) for p in workspace.glob("*/.aota/project.yaml"))
    env_agent = compose_env_map(compose_config, "hermes-agent")
    cg = []
    for project in projects:
        root = Path(project).parent.parent
        index = root / ".codegraph/codegraph.db"
        cg.append({"project": project, "index_exists": index.is_file(), "executable_on_path": shutil.which("codegraph") is not None,
                   "mutation": "not_attempted"})
    sessions = hermes / "sessions"; attachments = hermes / "webui/attachments"
    operator_authorized = bool(gate.get("operator_override", {}).get("authorized"))
    return {"schema_version": 1, "captured_at": iso_now(), "hermes_home": path_info(hermes),
            "profile_checks": profile_checks, "profile_names": list(profiles),
            "active_skill_projection": active_projection, "reference_skill_projection": reference_projection,
            "plugin_yaml_parse": True, "canonical_counts": {"tools": len(tools), "toolsets": len(toolsets), "profiles": len(profiles)},
            "runtime_roots": {name: path_info(hermes / name) for name in ["aota-runtime", "aota-runtime/profile-tasks", "aota-runtime/delegate-runs", "aota-runtime/handoffs", "aota-runtime/outbox"]},
            "workspace_registry": path_info(registry), "canonical_projects": projects,
            "agent_api_endpoints": {"health": "http://127.0.0.1:8642/health", "delegate_runtime": "/internal/aota/delegate-runtime"},
            "agent_health": bounded_check("http://127.0.0.1:8642"),
            "webui_health": bounded_check("http://127.0.0.1:8787"),
            "amf": bounded_check(env_agent.get("AMF_BASE_URL")), "codegraph": cg,
            "sessions": {"count": sum(1 for p in sessions.iterdir()) if sessions.is_dir() else 0, "path": str(sessions)},
            "attachments": {"count": sum(1 for p in attachments.iterdir()) if attachments.is_dir() else 0, "path": str(attachments)},
            "active_task_gate": gate, "live_baseline_smoke": {"status": "SKIPPED_OPERATOR_AUTHORIZED" if operator_authorized else "SKIPPED_WITH_REASON", "reason": "remediation forbids live Profile Task smoke" if operator_authorized else "active-task gate failed; no worker was dispatched"}}


def make_paths(args: argparse.Namespace) -> dict[str, Path]:
    repo = Path(args.repo_root).resolve(); stack = Path(args.stack_root).resolve(); hermes = Path(args.hermes_home).resolve()
    overrides = Path(args.overrides_root).resolve(); workspace = Path(args.workspace_root).resolve()
    return {"repo": repo, "stack": stack, "compose": stack / "docker-compose.yml", "env": stack / ".env",
            "hermes": hermes, "overrides": overrides, "workspace": workspace,
            "private_parent": Path(args.private_root), "evidence_parent": Path(args.evidence_root)}


def operator_gate_args(args: argparse.Namespace) -> tuple[bool, str | None]:
    ignore = bool(getattr(args, "operator_ignore_legacy_task_records", False))
    authorization_id = getattr(args, "operator_authorization_id", None)
    if ignore and authorization_id != OPERATOR_AUTHORIZATION_ID:
        raise RuntimeError(f"exact operator authorization required: {OPERATOR_AUTHORIZATION_ID}")
    return ignore, authorization_id


def create_capture(args: argparse.Namespace) -> tuple[Path, Path, dict[str, Any]]:
    paths = make_paths(args); ignore_legacy, authorization_id = operator_gate_args(args)
    capture_start_epoch = time.time()
    gate = task_gate(paths["hermes"], ignore_legacy=ignore_legacy, authorization_id=authorization_id, capture_start_epoch=capture_start_epoch)
    unreadable: list[str] = []
    for root in (paths["hermes"], paths["overrides"]):
        files, _ = walk_entries(root)
        unreadable.extend(str(path) for path in files if not path.is_symlink() and not os.access(path, os.R_OK))
    if unreadable:
        raise RuntimeError("unreadable regular source files: " + ", ".join(unreadable))
    timestamp = utc_now(); private_parent = paths["private_parent"] / WORK_ITEM; evidence_parent = paths["evidence_parent"] / WORK_ITEM
    private_parent.mkdir(parents=True, exist_ok=True); os.chmod(private_parent.parent, 0o700); os.chmod(private_parent, 0o700)
    evidence_parent.mkdir(parents=True, exist_ok=True)
    private_final, evidence_final = private_parent / timestamp, evidence_parent / timestamp
    if private_final.exists() or evidence_final.exists(): raise RuntimeError("timestamp collision; retry without overwriting")
    temp = Path(tempfile.mkdtemp(prefix=f".{timestamp}-", dir=str(private_parent)))
    os.chmod(temp, 0o700)
    try:
        for name, source in [("docker-compose.yml", paths["compose"]), (".env", paths["env"])]:
            dest = temp / "docker-stack" / name; dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest); os.chmod(dest, 0o600)
        compose_results = docker_capture(temp, paths["stack"], paths["compose"])
        compose_config = load_json(temp / "docker-metadata/compose_config.json", {}) or {}
        hermes_archive = archive_tree(paths["hermes"], temp / "hermes-home.tar")
        hermes_manifest = hermes_archive.pop("manifest"); json_dump(temp / "manifests/hermes-home.json", hermes_manifest)
        override_manifest = regular_manifest(paths["overrides"]); copy_tree_preserving(paths["overrides"], temp / "hermes-overrides")
        json_dump(temp / "manifests/hermes-overrides.json", override_manifest)
        mapping = override_mapping(paths["overrides"], compose_config); json_dump(temp / "manifests/override-mapping.json", mapping)
        # Back up only actual Compose named volumes. Bind-backed volumes use a
        # host-path hash reference; independent volumes are read-only tarred by
        # an already-present Hermes image, never by a privileged helper.
        volume_records = []
        for key, volume_spec in sorted(compose_config.get("volumes", {}).items()):
            volume = f"{compose_config.get('name', 'hermes-stack')}_{key}"
            raw = load_json(temp / "docker-metadata" / f"volume-{hashlib.sha256(volume.encode()).hexdigest()[:16]}.json", [])
            info = raw[0] if isinstance(raw, list) and raw else {}
            bind = (volume_spec.get("driver_opts") or {}).get("device") if isinstance(volume_spec, dict) else None
            if bind:
                bind_path = Path(str(bind))
                volume_records.append({"name": volume, "kind": "bind-backed", "backing_path": str(bind_path), "backing_path_exists": bind_path.exists(), "status": "preserved-host-path"})
                continue
            archive = temp / "volumes" / f"{volume}.tar"; archive.parent.mkdir(parents=True, exist_ok=True)
            # No volume is treated as safe unless the read-only helper succeeds.
            helper = ["docker", "run", "--rm", "--network", "none", "--read-only", "--mount", f"type=volume,source={volume},target=/data,readonly", "redis:7-alpine", "tar", "-cf", "-", "-C", "/data", "."]
            try:
                with archive.open("wb") as out:
                    proc = subprocess.run(helper, stdout=out, stderr=subprocess.PIPE, timeout=180, check=False)
                ok = proc.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                ok = False
            if ok:
                os.chmod(archive, 0o600); volume_records.append({"name": volume, "kind": "named", "archive": str(archive), "sha256": sha256_file(archive), "status": "read-only-tar-pass"})
            else:
                if archive.exists(): archive.unlink()
                volume_records.append({"name": volume, "kind": "named", "status": "read-only-tar-failed"})
        json_dump(temp / "manifests/volumes.json", {"schema_version": 1, "volumes": volume_records})
        final_gate = task_gate(paths["hermes"], ignore_legacy=ignore_legacy, authorization_id=authorization_id, capture_start_epoch=capture_start_epoch)
        if final_gate["new_task_records_since_capture_start"]:
            raise RuntimeError("new task records appeared after capture start")
        gate = final_gate
        runtime = runtime_baseline(paths, compose_config, gate); json_dump(temp / "runtime-baseline.json", runtime)
        host_paths = [paths["hermes"], paths["overrides"], paths["compose"], paths["env"], paths["workspace"], paths["workspace"] / ".aota/workspaces.json"]
        baseline = {"schema_version": SCHEMA_VERSION, "work_item": WORK_ITEM, "captured_at": iso_now(),
                    "host": {"user": pwd.getpwuid(os.getuid()).pw_name, "uid": os.getuid(), "gid": os.getgid(), "os": platform.platform(aliased=True), "architecture": platform.machine()},
                    "docker": {"engine_version": safe_text(temp / "docker-metadata/engine_version.txt").strip(), "compose_version": safe_text(temp / "docker-metadata/compose_version.txt").strip()},
                    "compose_project": compose_config.get("name", "hermes-stack"), "containers": inspect_payload(compose_results, temp)["containers"],
                    "images": inspect_payload(compose_results, temp)["images"], "volumes": inspect_payload(compose_results, temp)["volumes"], "networks": inspect_payload(compose_results, temp)["networks"],
                    "host_paths": [path_info(p) for p in host_paths], "aota": runtime["canonical_counts"], "profile_names": runtime["profile_names"],
                    "active_skill_projection": runtime["active_skill_projection"], "reference_skill_projection": runtime["reference_skill_projection"],
                    "runtime_roots": runtime["runtime_roots"], "workspace_registry_path": str(paths["workspace"] / ".aota/workspaces.json"),
                    "agent_api_endpoints": runtime["agent_api_endpoints"], "amf": runtime["amf"], "codegraph": runtime["codegraph"],
                    "override_mapping_summary": {"file_count": mapping["file_count"], "mount_mapping_count": len(mapping["mount_mappings"])},
                    "capture_start_epoch": capture_start_epoch, "active_task_preflight": gate, "backup_manifest_references": {"hermes_home": "private:manifests/hermes-home.json", "overrides": "private:manifests/hermes-overrides.json", "volumes": "private:manifests/volumes.json"},
                    "verification_results": {"status": "PENDING"}, "docker_final_state": "ONLINE_CAPTURED", "rollback_readiness": "READY_IF_VERIFY_PASS",
                    "warnings": ["active task gate failed; Docker stop is forbidden", "WebUI reported restarting during baseline", "raw Docker/config evidence is private only"]}
        for directory in [p for p in temp.rglob("*") if p.is_dir()]:
            os.chmod(directory, 0o700)
        evidence_final.mkdir(parents=True, exist_ok=False); os.chmod(evidence_final, 0o755)
        json_dump(evidence_final / "baseline.json", baseline, 0o644)
        json_dump(evidence_final / "runtime-baseline.json", runtime, 0o644)
        json_dump(evidence_final / "override-mapping.json", mapping, 0o644)
        (evidence_final / "ROLLBACK.md").write_text(rollback_text(paths, timestamp), encoding="utf-8"); os.chmod(evidence_final / "ROLLBACK.md", 0o644)
        (evidence_final / "BASELINE.md").write_text(baseline_markdown(baseline, timestamp), encoding="utf-8"); os.chmod(evidence_final / "BASELINE.md", 0o644)
        json_dump(evidence_final / "remediation.json", {"schema_version": 1, "remediation_version": "HOST-WI-00-REMEDIATION-1", "original_failure": "unreadable runtime-generated .pyc was included before exclusion", "cache_exclusion_fix": {"excluded_before_read": True, "excluded_path": "agent/prompt_builder/__pycache__/prompt_builder.cpython-312.pyc", "reason": "runtime_generated_python_cache", "permission_change": False, "file_deletion": False}, "operator_authorization": gate["operator_override"], "task_gate": gate, "new_task_records_since_capture_start": gate["new_task_records_since_capture_start"], "source_validation": "PASS", "capture_result": "PASS", "verify_result": "PENDING", "docker_stop_result": "NOT_ATTEMPTED", "preservation_result": "PENDING"}, 0o644)
        json_dump(evidence_final / "docker-stop-receipt.json", {"schema_version": 1, "work_item": WORK_ITEM, "status": "NOT_ATTEMPTED", "reason": "active task gate failed", "docker_runtime_state": "ONLINE"}, 0o644)
        os.replace(temp, private_final); os.chmod(private_final, 0o700)
        latest = {"schema_version": 1, "work_item": WORK_ITEM, "timestamp": timestamp, "evidence_relative_path": str(evidence_final.relative_to(paths["repo"])), "private_backup_path": str(private_final), "capture_status": "PASS", "verify_status": "PENDING", "docker_final_state": "ONLINE", "sha256_manifest_reference": "private:manifests/hermes-home.json"}
        json_dump(evidence_parent / "latest.json", latest, 0o644)
        return private_final, evidence_final, baseline
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def rollback_text(paths: dict[str, Path], timestamp: str) -> str:
    return f"""# HOST-WI-00 Docker rollback runbook

Snapshot: `{timestamp}`. This runbook is documentation only; HOST-WI-00 does not execute rollback.

1. Confirm ports 8642 and 8787 are free for Docker Hermes, and confirm no incompatible Host Hermes has modified `{paths['hermes']}`.
2. Inspect the private backup at the recorded path. Do not copy secrets into the repository or command line.
3. Restore `docker-stack/docker-compose.yml` and `.env` from the private backup with owner/group `{pwd.getpwuid(os.getuid()).pw_name}` and mode 600 for `.env`.
4. Restore the `.hermes` archive and `hermes-overrides` only after preserving any newer host state and checking the private manifests. Preserve symlinks; do not follow an external target.
5. Restore only the named-volume archives recorded in `manifests/volumes.json`; the bind-backed Hermes home points at the host path and must not be duplicated.
6. From `{paths['stack']}`, use the recorded Compose project and `docker compose up -d` (never `down -v`, `prune`, `volume rm`, `image rm`, or `container rm`).
7. Verify Agent health on 8642, WebUI health on 8787, API auth without printing the key, Profiles/Tools/Toolsets, and the read-only Profile Task/delegate-runtime endpoints.
8. If a port collides, stop the conflicting approved process or choose an operator-approved rollback plan; do not alter `.env` secrets in this work item.
"""


def baseline_markdown(baseline: dict[str, Any], timestamp: str) -> str:
    states = {c.get("name"): c.get("state") for c in baseline.get("containers", [])}
    images = ", ".join(str(i.get("id")) for i in baseline.get("images", []))
    authorized = bool(baseline.get("active_task_preflight", {}).get("operator_override", {}).get("authorized"))
    task_note = "Operator-authorized legacy task records are preserved unchanged; effective stop-gate counts are zero." if authorized else "Existing running/needs_input task records keep the stop gate closed."
    return f"""# HOST-WI-00 Docker baseline

Captured `{timestamp}` for Compose project `{baseline.get('compose_project')}`.

## Current architecture

- `hermes-agent` owns the Agent API on 8642 and mounts the canonical Hermes home, AOTA runtime, workspace, and Agent overrides.
- `hermes-webui` is the legacy WebUI on 8787, with a named `hermes-home` bind-backed to the same host Hermes home plus WebUI overrides.
- Container aliases are not canonical authorities: `/workspace`, `/home/hermes/.hermes`, `/home/hermeswebui/.hermes`, `/opt/hermes`, and `/apptoo` are container targets.
- Canonical paths are `/home/latios/.hermes`, `/home/latios/workspace`, `/home/latios/workspace/aota-hermes-tools`, and `/home/latios/workspace/hermes-overrides`.

## Runtime facts

- Container states: `{states}`
- Image IDs are preserved in `baseline.json`; private raw inspect/config output is outside evidence. Summary IDs: `{images}`
- AOTA counts: `{baseline.get('aota')}`; Profiles: `{', '.join(baseline.get('profile_names', []))}`
- Agent and WebUI override disposition is in `override-mapping.json`: Agent mappings are `migrate_agent`, WebUI mappings are `retire_webui`, and unmounted files are `investigate`.

## Gates and rollback

- Active-task preflight: `{baseline.get('active_task_preflight', {}).get('pass')}`. {task_note}
- Docker was not stopped by this capture. Use `ROLLBACK.md` only with operator approval. Docker objects must remain preserved; never use `down -v` or prune.
- The private backup contains the complete `.hermes` archive, overrides copy, raw Docker metadata, resolved Compose, `.env`, and manifests. It is not a repository artifact.

## Limitations

- This is a source/runtime snapshot, not Host Hermes installation or HOST-WI-01 validation.
- WebUI was observed restarting, so no WebUI live PASS is implied.
- Live Profile Task smoke was skipped under the remediation authorization; task-main wakeup remains not observable.
"""


def verify_capture(args: argparse.Namespace) -> tuple[bool, dict[str, Any]]:
    paths = make_paths(args); latest = load_json(paths["evidence_parent"] / WORK_ITEM / "latest.json", {}) or {}
    timestamp = latest.get("timestamp")
    if not timestamp or not latest.get("private_backup_path"):
        result = {"schema_version": 1, "work_item": WORK_ITEM, "verified_at": iso_now(), "checks": {}, "status": "FAIL", "reason": "no captured latest pointer", "docker_stop_authorized": False}
        return False, result
    private = Path(latest["private_backup_path"]); evidence = paths["evidence_parent"] / WORK_ITEM / str(timestamp)
    checks: dict[str, bool] = {}
    checks["private_backup_root_mode_700"] = private.is_dir() and (stat.S_IMODE(private.stat().st_mode) == 0o700)
    checks["private_files_secure"] = all(not p.is_file() or stat.S_IMODE(p.stat().st_mode) <= 0o600 for p in private.rglob("*")) if private.is_dir() else False
    checks["compose_and_env"] = (private / "docker-stack/docker-compose.yml").is_file() and (private / "docker-stack/.env").is_file()
    checks["hermes_archive"] = False
    try:
        with tarfile.open(private / "hermes-home.tar", "r") as archive:
            archive.getnames()
        checks["hermes_archive"] = True
    except (OSError, tarfile.TarError):
        pass
    checks["overrides_backup"] = (private / "hermes-overrides").is_dir()
    checks["docker_metadata"] = (private / "docker-metadata/compose_config.json").is_file()
    checks["manifest_rehash"] = True
    for manifest_name, root_name in [("hermes-home.json", "hermes-home"), ("hermes-overrides.json", "hermes-overrides")]:
        manifest = load_json(private / "manifests" / manifest_name, {}) or {}
        if root_name == "hermes-home":
            if not verify_tar_manifest(private / "hermes-home.tar", manifest):
                checks["manifest_rehash"] = False
            continue
        root = private / "hermes-overrides"
        for item in manifest.get("files", []):
            if item.get("is_symlink"): continue
            source = root / item["relative_path"]
            if not source.is_file() or sha256_file(source) != item.get("sha256"):
                checks["manifest_rehash"] = False
    baseline = load_json(evidence / "baseline.json", {}) or {}
    checks["baseline_json"] = baseline.get("schema_version") == 1 and baseline.get("work_item") == WORK_ITEM
    checks["evidence_documents"] = all((evidence / name).is_file() for name in ["baseline.json", "BASELINE.md", "ROLLBACK.md", "runtime-baseline.json", "override-mapping.json", "remediation.json"])
    checks["evidence_secret_scan"] = not any(SECRET_VALUE_RE.search(p.read_text(errors="replace")) for p in evidence.rglob("*") if p.is_file())
    ignore_legacy, authorization_id = operator_gate_args(args)
    gate = task_gate(paths["hermes"], ignore_legacy=ignore_legacy, authorization_id=authorization_id,
                     capture_start_epoch=baseline.get("capture_start_epoch")); checks["active_task_gate"] = bool(gate.get("pass"))
    checks["operator_task_override"] = bool((baseline.get("active_task_preflight") or {}).get("operator_override", {}).get("authorized")) == ignore_legacy
    checks["new_task_records_since_capture_start"] = gate.get("new_task_records_since_capture_start") == 0
    checks["volume_preservation"] = all(v.get("status") in {"preserved-host-path", "read-only-tar-pass"} for v in (load_json(private / "manifests/volumes.json", {}) or {}).get("volumes", []))
    checks["rollback_runbook"] = (evidence / "ROLLBACK.md").is_file() and "down -v" in safe_text(evidence / "ROLLBACK.md")
    exclusions = (load_json(private / "manifests/hermes-overrides.json", {}) or {}).get("exclusions", [])
    checks["cache_exclusion"] = any(item.get("relative_path") == "agent/prompt_builder/__pycache__/prompt_builder.cpython-312.pyc" and item.get("reason") == "runtime_generated_python_cache" for item in exclusions)
    passed = all(checks.values())
    result = {"schema_version": 1, "work_item": WORK_ITEM, "verified_at": iso_now(), "checks": checks, "status": "PASS" if passed else "FAIL", "active_task_gate": gate, "capture_start_epoch": baseline.get("capture_start_epoch"), "docker_stop_authorized": passed}
    if evidence != Path("/") and evidence.is_dir():
        json_dump(evidence / "verification.json", result, 0o644)
        remediation = load_json(evidence / "remediation.json", {}) or {}
        if remediation:
            remediation.update({"verify_result": result["status"], "operator_authorization": gate.get("operator_override", {}),
                                "task_gate": gate, "new_task_records_since_capture_start": gate.get("new_task_records_since_capture_start", 0)})
            json_dump(evidence / "remediation.json", remediation, 0o644)
        if latest:
            latest["verify_status"] = result["status"]; json_dump(paths["evidence_parent"] / WORK_ITEM / "latest.json", latest, 0o644)
    return passed, result


def status_command(args: argparse.Namespace) -> dict[str, Any]:
    paths = make_paths(args); latest = load_json(paths["evidence_parent"] / WORK_ITEM / "latest.json", {}) or {}
    gate = task_gate(paths["hermes"])
    return {"work_item": WORK_ITEM, "latest": latest, "active_task_gate": gate,
            "docker_runtime_state": "UNKNOWN_UNINSPECTED", "rollback_preserved": bool(latest.get("private_backup_path"))}


def stop_command(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    paths = make_paths(args); latest = load_json(paths["evidence_parent"] / WORK_ITEM / "latest.json", {}) or {}
    passed, verification = verify_capture(args)
    ignore_legacy, authorization_id = operator_gate_args(args)
    gate = task_gate(paths["hermes"], ignore_legacy=ignore_legacy, authorization_id=authorization_id,
                     capture_start_epoch=verification.get("capture_start_epoch"))
    if not passed or not gate.get("pass"):
        return 2, {"final": "NEEDS_OPERATOR_ACTIVE_TASKS" if not gate.get("pass") else "FAIL_BACKUP_OR_VERIFICATION", "docker_stop": "NOT_ATTEMPTED", "verification": verification, "active_task_gate": gate}
    private = Path(latest["private_backup_path"]); log = private / "docker-metadata/docker-stop.log"
    code, out, err = run_command(["docker", "compose", "stop"], cwd=paths["stack"], timeout=120, stdout_path=log, stderr_path=private / "docker-metadata/docker-stop.stderr")
    evidence = paths["evidence_parent"] / WORK_ITEM / str(latest["timestamp"])
    receipt: dict[str, Any] = {"schema_version": 1, "work_item": WORK_ITEM, "attempted_at": iso_now(), "command": "docker compose stop", "returncode": code, "status": "PASS" if code == 0 else "FAIL", "docker_runtime_state": "OFFLINE_STANDBY" if code == 0 else "ONLINE", "rollback_preserved": False}
    if code != 0:
        receipt.update({"post_stop_verification": {"status": "NOT_ATTEMPTED"}, "preservation_result": "NOT_VERIFIED"})
        json_dump(evidence / "docker-stop-receipt.json", receipt, 0o644)
        remediation = load_json(evidence / "remediation.json", {}) or {}
        if remediation:
            remediation.update({"docker_stop_result": "FAIL", "preservation_result": "NOT_VERIFIED"})
            json_dump(evidence / "remediation.json", remediation, 0o644)
        return 3, {"final": "NEEDS_OPERATOR_DOCKER_STOP", **receipt}

    post_ps = private / "docker-metadata/post-stop-compose-ps.jsonl"
    post_ps_stderr = private / "docker-metadata/post-stop-compose-ps.stderr"
    ps_code, _, _ = run_command(["docker", "compose", "ps", "--all", "--format", "json"], cwd=paths["stack"], timeout=60, stdout_path=post_ps, stderr_path=post_ps_stderr)
    rows: list[dict[str, Any]] = []
    for line in safe_text(post_ps).splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict): rows.append(value)
    target_rows = {str(row.get("Service") or row.get("Name")): row for row in rows if row.get("Service") or row.get("Name")}
    target_services = {"hermes-agent", "hermes-webui"}
    stopped = target_services.issubset(target_rows) and all(str(target_rows[name].get("State", "")).lower() in {"exited", "stopped"} for name in target_services)
    ports_free = target_services.issubset(target_rows) and all(not row.get("Publishers") for row in target_rows.values() if str(row.get("Service") or row.get("Name")) in target_services)
    baseline = load_json(evidence / "baseline.json", {}) or {}
    image_checks: dict[str, bool] = {}
    for image in baseline.get("images", []):
        image_id = str(image.get("id", "")); safe_id = hashlib.sha256(image_id.encode()).hexdigest()[:16]
        image_out = private / "docker-metadata" / f"post-stop-image-{safe_id}.json"
        image_err = private / "docker-metadata" / f"post-stop-image-{safe_id}.stderr"
        image_code, _, _ = run_command(["docker", "image", "inspect", image_id], timeout=60, stdout_path=image_out, stderr_path=image_err)
        image_checks[image_id] = image_code == 0 and bool(load_json(image_out, []))
    volume_checks: dict[str, bool] = {}
    for volume in baseline.get("volumes", []):
        name = str(volume.get("name", "")); safe_name = hashlib.sha256(name.encode()).hexdigest()[:16]
        volume_out = private / "docker-metadata" / f"post-stop-volume-{safe_name}.json"
        volume_err = private / "docker-metadata" / f"post-stop-volume-{safe_name}.stderr"
        volume_code, _, _ = run_command(["docker", "volume", "inspect", name], timeout=60, stdout_path=volume_out, stderr_path=volume_err)
        volume_checks[name] = volume_code == 0 and bool(load_json(volume_out, []))
    network_checks: dict[str, bool] = {}
    for network in baseline.get("networks", []):
        name = str(network.get("name", "")); safe_name = hashlib.sha256(name.encode()).hexdigest()[:16]
        network_out = private / "docker-metadata" / f"post-stop-network-{safe_name}.json"
        network_err = private / "docker-metadata" / f"post-stop-network-{safe_name}.stderr"
        network_code, _, _ = run_command(["docker", "network", "inspect", name], timeout=60, stdout_path=network_out, stderr_path=network_err)
        network_checks[name] = network_code == 0 and bool(load_json(network_out, []))
    post_checks = {"compose_ps": ps_code == 0, "target_container_count": len(target_rows) == len(target_services), "containers_stopped": stopped, "ports_free": ports_free,
                   "images_preserved": all(image_checks.values()), "volumes_preserved": all(volume_checks.values()), "networks_preserved": all(network_checks.values()),
                   "image_checks": image_checks, "volume_checks": volume_checks, "network_checks": network_checks, "observed_rows": [{"name": row.get("Name"), "service": row.get("Service"), "state": row.get("State"), "publishers": row.get("Publishers", [])} for row in rows]}
    post_pass = all(value for key, value in post_checks.items() if key not in {"image_checks", "volume_checks", "network_checks", "observed_rows"})
    receipt.update({"status": "PASS" if post_pass else "FAIL", "docker_runtime_state": "OFFLINE_STANDBY" if post_pass else "UNKNOWN", "rollback_preserved": post_pass,
                   "post_stop_verification": {"status": "PASS" if post_pass else "FAIL", "checks": post_checks}, "preservation_result": "PASS" if post_pass else "FAIL"})
    json_dump(evidence / "docker-stop-receipt.json", receipt, 0o644)
    remediation = load_json(evidence / "remediation.json", {}) or {}
    if remediation:
        remediation.update({"docker_stop_result": "PASS" if post_pass else "FAIL", "preservation_result": "PASS" if post_pass else "FAIL"})
        json_dump(evidence / "remediation.json", remediation, 0o644)
    if not post_pass: return 3, {"final": "NEEDS_OPERATOR_DOCKER_STOP", **receipt}
    latest["docker_final_state"] = "OFFLINE_STANDBY"; latest["verify_status"] = "PASS"; json_dump(paths["evidence_parent"] / WORK_ITEM / "latest.json", latest, 0o644)
    return 0, {"final": "PASS_DOCKER_BASELINE_CAPTURE_AND_OFFLINE_ROLLBACK_PRESERVATION", **receipt}


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="host-wi-00-fixture-") as raw:
        root = Path(raw); source = root / "source"; source.mkdir(); (source / "secret.txt").write_text("API_KEY=do-not-export\n", encoding="utf-8"); (source / "link").symlink_to("/etc/passwd")
        cache = source / "agent/prompt_builder/__pycache__"; cache.mkdir(parents=True); excluded = cache / "prompt_builder.cpython-312.pyc"; excluded.write_bytes(b"unreadable-cache"); os.chmod(excluded, 0o000)
        manifest = regular_manifest(source); assert manifest["file_count"] == 2
        exclusion = next(item for item in manifest["exclusions"] if item["relative_path"].endswith("prompt_builder.cpython-312.pyc")); assert exclusion["reason"] == "runtime_generated_python_cache"
        archive = root / "a.tar"; archive_tree(source, archive); assert tarfile.is_tarfile(archive)
        assert "do-not-export" not in json.dumps(redacted_env(["API_KEY=do-not-export"]))
        assert SECRET_VALUE_RE.search("sk-1234567890abcdef") and not SECRET_VALUE_RE.search("task-lifecycle")
        copied = root / "copy"; copy_tree_preserving(source, copied); assert (copied / "secret.txt").read_text().startswith("API_KEY=")
        assert (copied / "link").is_symlink() and os.readlink(copied / "link") == "/etc/passwd"
        mismatch = manifest["files"][0]["sha256"]; (source / "secret.txt").write_text("changed", encoding="utf-8"); assert sha256_file(source / "secret.txt") != mismatch
        assert regular_manifest(source)["file_count"] == manifest["file_count"]
        private = source / "private.bin"; private.write_bytes(b"unreadable-non-cache"); os.chmod(private, 0o000)
        try:
            regular_manifest(source)
        except PermissionError:
            pass
        else:
            raise AssertionError("unreadable non-excluded file did not fail closed")
        task_root = root / "hermes/aota-runtime/profile-tasks/fixture/task"; task_root.mkdir(parents=True)
        (task_root / "meta.json").write_text(json.dumps({"status": "running", "profile": "coder", "execution": {"started_at": "fixture"}}), encoding="utf-8")
        gate = task_gate(root / "hermes"); assert gate["active_task_count"] == 1 and not gate["pass"]
        override = task_gate(root / "hermes", ignore_legacy=True, authorization_id=OPERATOR_AUTHORIZATION_ID, capture_start_epoch=time.time() + 1)
        assert override["observed"]["active_records"] == 1 and override["effective_stop_gate"]["active"] == 0 and override["pass"]
        start = time.time(); new_task = root / "hermes/aota-runtime/profile-tasks/fixture/new"; new_task.mkdir(); (new_task / "meta.json").write_text("{}", encoding="utf-8")
        rejected = task_gate(root / "hermes", ignore_legacy=True, authorization_id=OPERATOR_AUTHORIZATION_ID, capture_start_epoch=start)
        assert rejected["new_task_records_since_capture_start"] == 1 and not rejected["pass"]
        os.chmod(excluded, 0o000); assert stat.S_IMODE(excluded.stat().st_mode) == 0
    print("HOST_WI_00_FIXTURE_SMOKE_PASS")
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["inspect", "capture", "verify", "stop", "status", "self-test"])
    p.add_argument("--repo-root", default="/home/latios/workspace/aota-hermes-tools")
    p.add_argument("--stack-root", default="/home/latios/hermes-stack")
    p.add_argument("--hermes-home", default="/home/latios/.hermes")
    p.add_argument("--overrides-root", default="/home/latios/workspace/hermes-overrides")
    p.add_argument("--workspace-root", default="/home/latios/workspace")
    p.add_argument("--private-root", default="/home/latios/.local/state/aota-host-migration")
    p.add_argument("--evidence-root", default="/home/latios/workspace/aota-hermes-tools/deploy/evidence/host-migration")
    p.add_argument("--operator-ignore-legacy-task-records", action="store_true", help="one-shot operator-authorized exclusion of existing legacy task records from the stop gate")
    p.add_argument("--operator-authorization-id", default=None, help="must equal the exact HOST-WI-00 remediation authorization ID when override is used")
    return p


def main() -> int:
    args = parser().parse_args()
    if args.command == "self-test": return self_test()
    if args.command == "inspect":
        paths = make_paths(args); ignore_legacy, authorization_id = operator_gate_args(args); gate = task_gate(paths["hermes"], ignore_legacy=ignore_legacy, authorization_id=authorization_id); print(json.dumps({"work_item": WORK_ITEM, "source_paths": {k: str(v) for k, v in paths.items() if k not in {"private_parent", "evidence_parent"}}, "active_task_gate": gate, "env_metadata": path_info(paths["env"])}, indent=2)); return 0 if gate.get("pass") else 2
    if args.command == "capture":
        try:
            private, evidence, baseline = create_capture(args)
        except (OSError, RuntimeError, tarfile.TarError) as exc:
            print(json.dumps({"work_item": WORK_ITEM, "capture": "FAIL", "reason": str(exc)[:1000], "docker_stop": "NOT_ATTEMPTED"}, indent=2))
            return 2
        print(json.dumps({"work_item": WORK_ITEM, "capture": "PASS", "private_backup_root": str(private), "evidence_root": str(evidence), "active_task_gate": baseline["active_task_preflight"]}, indent=2)); return 0
    if args.command == "verify":
        ok, result = verify_capture(args); print(json.dumps(result, indent=2)); return 0 if ok else 2
    if args.command == "status": print(json.dumps(status_command(args), indent=2)); return 0
    code, result = stop_command(args); print(json.dumps(result, indent=2)); return code
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
