#!/usr/bin/env python3
"""PF-WI-07-2 manifest, readiness, backup, deploy and rollback primitives.

The command surface is deliberately package-oriented.  It never starts a
service, invokes Docker, reads private .env values, or performs Git writes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "deploy" / "aota-forge-plan-files.yaml"
DEFAULT_HERMES_HOME = "/home/latios/.hermes"
DEFAULT_HERMES_STACK = "/home/latios/hermes-stack"
TRUSTED_KEYS = (
    "AOTA_TRUSTED_PRINCIPAL",
    "AOTA_TRUSTED_AUTHORITIES",
    "AOTA_TRUSTED_WORKSPACE_ID",
)
WORKER_PROFILES = ("architect", "reviewer", "coder", "debugger")
PLAN_TOOLSETS = ("aota_work_intake", "aota_plan_read", "aota_plan_write")


def expand(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        defaults = {
            "AOTA_SOURCE_ROOT": str(REPO_ROOT),
            "AOTA_HERMES_HOME_HOST": os.environ.get("AOTA_HERMES_HOME_HOST", DEFAULT_HERMES_HOME),
            "AOTA_HERMES_STACK_ROOT": os.environ.get("AOTA_HERMES_STACK_ROOT", DEFAULT_HERMES_STACK),
        }
        return defaults.get(name, os.environ.get(name, match.group(0)))

    return re.sub(r"\$\{([A-Z0-9_]+)\}", replace, value)


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("managed manifest schema_version must be 1")
    return data


def absolute_root(value: str) -> Path:
    expanded = Path(expand(value))
    return expanded if expanded.is_absolute() else REPO_ROOT / expanded


def safe_relative(value: str) -> str:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe manifest relative path: {value}")
    return path.as_posix()


def within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def safe_target(path: Path, root: Path) -> None:
    root_resolved = root.resolve(strict=False)
    if path.is_symlink():
        resolved = path.resolve(strict=False)
    else:
        resolved = path.parent.resolve(strict=False) / path.name
    if not within(resolved, root_resolved):
        raise ValueError(f"managed target escapes root: {path}")


def format_destination(spec: str, relative: str) -> str:
    rel = Path(relative)
    return spec.format(relative_path=rel.as_posix(), name=rel.name, stem=rel.stem)


def build_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for group_name, group in manifest.get("groups", {}).items():
        if not isinstance(group, dict):
            raise ValueError(f"manifest group is not a mapping: {group_name}")
        kind = group.get("kind")
        if kind not in {"copy", "source_only"}:
            raise ValueError(f"unsupported manifest group kind: {group_name}")
        source_root = absolute_root(str(group["source_root"]))
        runtime_root = absolute_root(str(group["runtime_root"]))
        for spec in group.get("files", []):
            if not isinstance(spec, dict):
                raise ValueError(f"manifest file entry is not a mapping: {group_name}")
            destination_spec = str(spec.get("destination", "{relative_path}"))
            if "pattern" in spec:
                pattern = safe_relative(str(spec["pattern"]))
                matches = sorted(path for path in source_root.glob(pattern) if path.is_file() or path.is_symlink())
                if not matches and spec.get("required", False):
                    raise FileNotFoundError(f"required manifest pattern has no files: {group_name}/{pattern}")
                for source in matches:
                    relative = source.relative_to(source_root).as_posix()
                    destination = runtime_root / format_destination(destination_spec, relative)
                    entries.append(_entry(group_name, kind, source, destination, runtime_root, relative, spec))
            elif "source" in spec:
                relative = safe_relative(str(spec["source"]))
                source = source_root / relative
                destination = runtime_root / format_destination(destination_spec, relative)
                entries.append(_entry(group_name, kind, source, destination, runtime_root, relative, spec))
            else:
                raise ValueError(f"manifest file entry needs source or pattern: {group_name}")
    if not entries:
        raise ValueError("managed manifest has no files")
    return entries


def _entry(group: str, kind: str, source: Path, destination: Path, root: Path, relative: str, spec: dict[str, Any]) -> dict[str, Any]:
    safe_relative(relative)
    safe_target(destination, root)
    return {
        "id": f"{group}/{relative}",
        "group": group,
        "kind": kind,
        "source": source,
        "destination": destination,
        "root": root,
        "relative_path": relative,
        "file_type": str(spec.get("file_type", "file")),
        "required": bool(spec.get("required", False)),
        "expected_mode": str(spec.get("expected_mode", "preserve")),
        "managed": bool(spec.get("managed", False)),
    }


def sha256(path: Path) -> str:
    if path.is_symlink():
        return hashlib.sha256(f"symlink:{os.readlink(path)}".encode()).hexdigest()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_type(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_file():
        return "file"
    if path.exists():
        return "directory"
    return "missing"


def record_path(path: Path) -> dict[str, Any]:
    kind = file_type(path)
    if kind == "missing":
        return {"file_type": kind, "mode": None, "symlink_target": None, "sha256": None}
    if kind == "directory":
        raise ValueError(f"managed path must be a file, not directory: {path}")
    stat = path.lstat()
    return {
        "file_type": kind,
        "mode": oct(stat.st_mode & 0o777),
        "symlink_target": os.readlink(path) if path.is_symlink() else None,
        "sha256": sha256(path),
    }


def copy_preserving(path: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        if destination.exists() or destination.is_symlink():
            destination.unlink()
        destination.symlink_to(os.readlink(path))
    else:
        shutil.copy2(path, destination)


def backup_package(entries: list[dict[str, Any]], root: Path, quiet: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = root / timestamp
    suffix = 0
    while backup_dir.exists():
        suffix += 1
        backup_dir = root / f"{timestamp}-{suffix:02d}"
    backup_dir.mkdir()
    records: list[dict[str, Any]] = []
    for entry in entries:
        original = entry["source"] if entry["kind"] == "source_only" else entry["destination"]
        safe_target(original, entry["root"])
        relative_backup = Path("files") / entry["group"] / entry["relative_path"]
        backup_path = backup_dir / relative_backup
        state = record_path(original)
        existed = state["file_type"] != "missing"
        if existed:
            copy_preserving(original, backup_path)
        records.append({
            "id": entry["id"],
            "group": entry["group"],
            "kind": entry["kind"],
            "original_path": str(original),
            "destination_path": str(entry["destination"]),
            "root_path": str(entry["root"]),
            "backup_path": str(relative_backup),
            "existed_before": existed,
            "managed": entry["managed"],
            "file_type": state["file_type"],
            "mode": state["mode"],
            "symlink_target": state["symlink_target"],
            "sha256": state["sha256"],
        })
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "package": "aota-forge-plan",
        "managed_file_inventory": [
            {"id": e["id"], "source": str(e["source"]), "destination": str(e["destination"]), "kind": e["kind"], "file_type": e["file_type"], "managed": e["managed"]}
            for e in entries
        ],
        "files": records,
    }
    manifest_path = backup_dir / "backup-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checksum_lines = []
    for path in sorted(p for p in (backup_dir / "files").rglob("*") if p.is_file() or p.is_symlink()):
        checksum_lines.append(f"{sha256(path)}  {path.relative_to(backup_dir).as_posix()}")
    checksum_lines.append(f"{sha256(manifest_path)}  backup-manifest.json")
    (backup_dir / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
    if not quiet:
        print(f"BACKUP_CREATED {backup_dir}")
    return backup_dir


def restore_file(record: dict[str, Any], backup_dir: Path) -> None:
    destination = Path(record["original_path"] if record["kind"] == "source_only" else record["destination_path"])
    if not record.get("root_path"):
        raise ValueError(f"rollback record has no managed root: {record['id']}")
    safe_target(destination, Path(record["root_path"]))
    backup_path = backup_dir / record["backup_path"]
    if record["existed_before"]:
        if not backup_path.exists() and not backup_path.is_symlink():
            raise ValueError(f"backup payload missing: {record['id']}")
        if destination.exists() or destination.is_symlink():
            if destination.is_dir() and not destination.is_symlink():
                raise ValueError(f"refusing to replace directory: {destination}")
            destination.unlink()
        copy_preserving(backup_path, destination)
        if record["mode"] and not destination.is_symlink():
            destination.chmod(int(record["mode"], 8))
    elif destination.exists() or destination.is_symlink():
        if destination.is_dir() and not destination.is_symlink():
            raise ValueError(f"refusing to remove directory: {destination}")
        destination.unlink()


def verify_backup_parity(manifest: dict[str, Any], backup_dir: Path) -> None:
    for record in manifest["files"]:
        destination = Path(record["original_path"] if record["kind"] == "source_only" else record["destination_path"])
        if record["existed_before"]:
            if not destination.exists() and not destination.is_symlink():
                raise ValueError(f"rollback parity missing: {record['id']}")
            if file_type(destination) != record["file_type"] or sha256(destination) != record["sha256"]:
                raise ValueError(f"rollback parity mismatch: {record['id']}")
        elif destination.exists() or destination.is_symlink():
                raise ValueError(f"rollback left new managed file: {record['id']}")


def receipt_payload(backup_dir: Path, copied: list[str], source_hashes: dict[str, str], runtime_hashes: dict[str, str]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source_version": "0.17.6",
        "tools": 35,
        "toolsets": 18,
        "source_root": str(REPO_ROOT),
        "runtime_root": expand("${AOTA_HERMES_HOME_HOST}"),
        "files": copied,
        "source_hashes": source_hashes,
        "runtime_hashes": runtime_hashes,
        "compose_changed": False,
        "restart_executed": False,
        "live_smoke_executed": False,
        "backup_path": str(backup_dir),
    }


def rollback_package(backup_dir: Path, quiet: bool = False) -> None:
    manifest_path = backup_dir / "backup-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for record in manifest["files"]:
        if not record.get("managed"):
            raise ValueError(f"unmanaged rollback record: {record['id']}")
        restore_file(record, backup_dir)
    verify_backup_parity(manifest, backup_dir)
    if not quiet:
        print(f"ROLLBACK_VERIFIED {backup_dir}")


def source_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def readiness(manifest_path: Path = MANIFEST_PATH, quiet: bool = False) -> int:
    errors: list[str] = []
    try:
        manifest = load_manifest(manifest_path)
        entries = build_entries(manifest)
    except Exception as exc:  # noqa: BLE001 - readiness must report one stable verdict
        print("READINESS_BLOCKED")
        print(f"manifest: {type(exc).__name__}")
        return 1

    version_path = REPO_ROOT / "VERSION"
    plugin_yaml = REPO_ROOT / "plugin" / "aota-tools" / "plugin.yaml"
    if version_path.read_text(encoding="utf-8").strip() != "0.17.6":
        errors.append("VERSION")
    try:
        plugin_data = yaml.safe_load(source_text(plugin_yaml))
    except Exception:
        plugin_data = None
        errors.append("plugin.yaml.parse")
    if not isinstance(plugin_data, dict) or str(plugin_data.get("version")) != "0.17.6":
        errors.append("plugin.yaml.version")
    if not isinstance(plugin_data, dict) or len(plugin_data.get("provides_tools", [])) != 35:
        errors.append("tools=35")
    init_text = source_text(REPO_ROOT / "plugin" / "aota-tools" / "__init__.py")
    if len(re.findall(r"^TOOLSET_[A-Z0-9_]+\s*=", init_text, re.MULTILINE)) != 18:
        errors.append("toolsets=18")

    for entry in entries:
        if entry["required"] and not entry["source"].is_file():
            errors.append(f"missing:{entry['id']}")
        if entry["kind"] == "copy":
            try:
                safe_target(entry["destination"], entry["root"])
            except ValueError:
                errors.append(f"target:{entry['id']}")
    for entry in entries:
        if not entry["source"].is_file():
            continue
        try:
            if entry["file_type"] == "python":
                compile(source_text(entry["source"]), str(entry["source"]), "exec")
            elif entry["file_type"] in {"yaml", "compose"}:
                yaml.safe_load(source_text(entry["source"]))
            elif entry["file_type"] == "shell":
                result = subprocess.run(["bash", "-n", str(entry["source"])], capture_output=True, text=True)
                if result.returncode:
                    errors.append(f"shell:{entry['id']}")
        except Exception:
            errors.append(f"parse:{entry['id']}")

    start = REPO_ROOT / "plugin" / "aota-tools" / "_profile_task_start.py"
    start_text = source_text(start)
    if sum(start_text.count(key) for key in TRUSTED_KEYS) < 6:
        errors.append("trusted-keys")
    if "for key in _TRUSTED_ORCHESTRATOR_ENV_KEYS:" not in start_text or "child_env.pop(key, None)" not in start_text:
        errors.append("worker-sanitizer")
    if "child_env = dict(parent_env)" not in start_text:
        errors.append("parent-env-copy")
    for profile in WORKER_PROFILES:
        try:
            config = yaml.safe_load(source_text(REPO_ROOT / "profiles" / profile / "config.yaml"))
            disabled = set(config.get("agent", {}).get("disabled_toolsets", []))
            if not set(PLAN_TOOLSETS).issubset(disabled):
                errors.append(f"worker-deny:{profile}")
        except Exception:
            errors.append(f"worker-profile:{profile}")
    try:
        task_main = yaml.safe_load(source_text(REPO_ROOT / "profiles" / "task-main" / "config.yaml"))
        available = set(task_main.get("toolsets", []))
        if not set(PLAN_TOOLSETS).issubset(available):
            errors.append("task-main-retain")
    except Exception:
        errors.append("task-main")
    security_text = source_text(REPO_ROOT / "plugin" / "aota-tools" / "_orchestrator_security_context.py")
    mutation_text = source_text(REPO_ROOT / "plugin" / "aota-tools" / "_plan_mutation.py")
    if "PLAN_WRITE_FORBIDDEN_FOR_WORKER" not in security_text + mutation_text:
        errors.append("worker-marker-defense")

    deploy_text = source_text(REPO_ROOT / "scripts" / "deploy.sh")
    if "aota-forge-plan-files.yaml" not in deploy_text or "aota_forge_plan_package.py" not in deploy_text:
        errors.append("deploy-manifest")
    for required_script in ("deploy.sh", "backup-aota-forge-plan-activation.sh", "rollback-aota-forge-plan-activation.sh"):
        if not (REPO_ROOT / "scripts" / required_script).is_file():
            errors.append(f"script:{required_script}")

    compose_path = Path(expand("${AOTA_HERMES_STACK_ROOT}")) / "docker-compose.yml"
    try:
        compose = yaml.safe_load(source_text(compose_path))
        services = compose.get("services", {})
        agent_env = services.get("hermes-agent", {}).get("environment", {})
        webui_env = services.get("hermes-webui", {}).get("environment", {})
        for key in TRUSTED_KEYS:
            if key not in agent_env:
                errors.append(f"compose-agent:{key}")
            if key in webui_env:
                errors.append(f"compose-webui:{key}")
        if not isinstance(agent_env, dict) or not isinstance(webui_env, dict):
            errors.append("compose-environment-shape")
    except Exception:
        errors.append("compose.parse")

    errors.extend(secret_scan_errors())
    status = "READINESS_BLOCKED" if errors else "READINESS_PASS"
    if not errors:
        try:
            dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.strip())
        except subprocess.SubprocessError:
            dirty = True
        if dirty:
            status = "READINESS_PASS_WITH_UNCOMMITTED_CHANGES"
    print(status)
    if errors and not quiet:
        for error in sorted(set(errors)):
            print(error)
    return 1 if errors else 0


def secret_scan_errors() -> list[str]:
    errors: list[str] = []
    roots = [REPO_ROOT / "deploy", REPO_ROOT / "scripts", REPO_ROOT / "docs" / "aota-forge-plan", REPO_ROOT / "README.md"]
    paths: list[Path] = []
    for root in roots:
        if root.is_file():
            paths.append(root)
        elif root.exists():
            paths.extend(p for p in root.rglob("*") if p.is_file())
    patterns = [
        re.compile(r"sk-[A-Za-z0-9]{16,}"),
        re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}", re.IGNORECASE),
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        re.compile(r"\b(?:OPENAI_API_KEY|HERMES_API_SERVER_KEY|DATABASE_URL|PASSWORD)\s*=\s*(?!$|#|\$\{|replace-with|your[-_]|<)[^\s]+"),
        re.compile(r"\bAOTA_TRUSTED_(?:PRINCIPAL|AUTHORITIES|WORKSPACE_ID)\s*=\s*(?!$|#|\$\{|replace-with|absent|<)[^\s]+"),
    ]
    for path in paths:
        if path.name == ".env" or ".deploy-backups" in path.parts or ".deploy-receipts" in path.parts:
            continue
        try:
            text = source_text(path)
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            if any(pattern.search(line) for pattern in patterns):
                errors.append(f"{path.relative_to(REPO_ROOT)}:{line_no}")
    return errors


def deploy_package(manifest_path: Path = MANIFEST_PATH) -> int:
    if readiness(manifest_path, quiet=True):
        return 1
    entries = build_entries(load_manifest(manifest_path))
    backup_dir = backup_package(entries, REPO_ROOT / ".deploy-backups", quiet=True)
    source_hashes: dict[str, str] = {}
    runtime_hashes: dict[str, str] = {}
    copied: list[str] = []
    for entry in entries:
        if entry["kind"] != "copy":
            continue
        if not entry["source"].is_file():
            raise ValueError(f"source disappeared during deploy: {entry['id']}")
        safe_target(entry["destination"], entry["root"])
        if entry["destination"].is_symlink():
            raise ValueError(f"refusing symlink deployment target: {entry['id']}")
        copy_preserving(entry["source"], entry["destination"])
        source_hashes[entry["id"]] = sha256(entry["source"])
        runtime_hashes[entry["id"]] = sha256(entry["destination"])
        copied.append(entry["id"])
    receipt_dir = REPO_ROOT / ".deploy-receipts" / "aota-forge-plan"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    receipt_dir = receipt_dir / timestamp
    receipt_dir.mkdir(parents=True, exist_ok=False)
    receipt = receipt_payload(backup_dir, copied, source_hashes, runtime_hashes)
    (receipt_dir / "deployment.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("ACTION_REQUIRED")
    print("Human: recreate hermes-agent only after PF-WI-07-4 approval")
    print(f"backup: {backup_dir}")
    print(f"receipt: {receipt_dir / 'deployment.json'}")
    return 0


def verify_deployment(manifest_path: Path = MANIFEST_PATH) -> int:
    entries = build_entries(load_manifest(manifest_path))
    mismatches = []
    for entry in entries:
        if entry["kind"] != "copy":
            continue
        if not entry["source"].is_file() or not entry["destination"].is_file() or sha256(entry["source"]) != sha256(entry["destination"]):
            mismatches.append(entry["id"])
    if mismatches:
        print("DEPLOY_VERIFY_BLOCKED")
        for item in mismatches:
            print(item)
        return 1
    print("DEPLOY_VERIFY_PASS")
    return 0


def fixture() -> int:
    with tempfile.TemporaryDirectory(prefix="aota-forge-plan-fixture-") as temp:
        root = Path(temp)
        source = root / "source"
        runtime = root / "runtime"
        (source / "plugin").mkdir(parents=True)
        (source / "scripts").mkdir(parents=True)
        (runtime / "plugin").mkdir(parents=True)
        (source / "plugin" / "new.py").write_text("new\n", encoding="utf-8")
        (source / "plugin" / "new2.py").write_text("new2\n", encoding="utf-8")
        (source / "scripts" / "deploy.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (runtime / "plugin" / "new.py").write_text("old\n", encoding="utf-8")
        manifest = {
            "schema_version": 1,
            "groups": {
                "plugin": {"kind": "copy", "source_root": str(source / "plugin"), "runtime_root": str(runtime / "plugin"), "files": [{"pattern": "*.py", "destination": "{relative_path}", "file_type": "python", "managed": True}]},
                "scripts": {"kind": "source_only", "source_root": str(source / "scripts"), "runtime_root": str(source / "scripts"), "files": [{"source": "deploy.sh", "destination": "deploy.sh", "file_type": "shell", "managed": True}]},
            },
        }
        entries = build_entries(manifest)
        backup_dir = backup_package(entries, root / "backups", quiet=True)
        (runtime / "plugin" / "new2.py").write_text("created-after-backup\n", encoding="utf-8")
        for entry in entries:
            if entry["kind"] == "copy":
                copy_preserving(entry["source"], entry["destination"])
        rollback_package(backup_dir, quiet=True)
        assert (runtime / "plugin" / "new.py").read_text(encoding="utf-8") == "old\n"
        assert not (runtime / "plugin" / "new2.py").exists()
        assert (backup_dir / "checksums.sha256").is_file()
        receipt_path = root / "deployment.json"
        receipt = receipt_payload(backup_dir, ["plugin/new.py"], {"plugin/new.py": sha256(source / "plugin" / "new.py")}, {"plugin/new.py": sha256(runtime / "plugin" / "new.py")})
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        loaded_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        assert loaded_receipt["schema_version"] == 1
        assert not any(key in loaded_receipt for key in ("env", "api_key", "raw_logs"))
        try:
            bad = dict(manifest)
            bad["groups"] = {"bad": {"kind": "copy", "source_root": str(source), "runtime_root": str(runtime), "files": [{"source": "../escape", "destination": "../escape", "managed": True}]}}
            build_entries(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("path traversal fixture was accepted")
        outside = root / "outside"
        outside.write_text("outside\n", encoding="utf-8")
        link = runtime / "plugin" / "escape.py"
        link.symlink_to(outside)
        try:
            safe_target(link, runtime / "plugin")
        except ValueError:
            pass
        else:
            raise AssertionError("symlink escape fixture was accepted")
        bad_backup = root / "bad-backup"
        shutil.copytree(backup_dir, bad_backup)
        bad_manifest_path = bad_backup / "backup-manifest.json"
        bad_manifest = json.loads(bad_manifest_path.read_text(encoding="utf-8"))
        bad_manifest["files"][0]["destination_path"] = str(outside)
        bad_manifest_path.write_text(json.dumps(bad_manifest), encoding="utf-8")
        try:
            rollback_package(bad_backup, quiet=True)
        except ValueError:
            pass
        else:
            raise AssertionError("rollback manifest-root escape was accepted")
    print("FIXTURE_PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("readiness", "backup", "deploy", "rollback", "verify", "fixture"))
    parser.add_argument("path", nargs="?", help="backup directory for rollback")
    args = parser.parse_args()
    try:
        if args.command == "readiness":
            return readiness()
        if args.command == "backup":
            entries = build_entries(load_manifest())
            backup_package(entries, REPO_ROOT / ".deploy-backups")
            return 0
        if args.command == "deploy":
            return deploy_package()
        if args.command == "verify":
            return verify_deployment()
        if args.command == "fixture":
            return fixture()
        backup_root = REPO_ROOT / ".deploy-backups"
        backup_dir = Path(args.path) if args.path else max(backup_root.iterdir(), key=lambda p: p.name)
        rollback_package(backup_dir)
        print("ACTION_REQUIRED")
        print("Human: recreate hermes-agent only after PF-WI-07-4 approval")
        return 0
    except Exception as exc:  # noqa: BLE001 - shell package gets stable non-secret failure
        print(f"PACKAGE_BLOCKED: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
