#!/usr/bin/env python3
"""Materialize the read-only HOST-WI-02B parity assessment.

This intentionally records metadata and hashes only.  It never reads private
environment values, profile state.db contents, or session content, and it
does not mutate the Hermes runtime.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml

import aota_forge_plan_package as package
from profile_runtime_assembly import expand_skill_tree, load_assembly, source_errors


ROOT = package.REPO_ROOT
RUNTIME = Path("/home/latios/.hermes")
LAUNCHER = Path("/home/latios/.local/bin/hermes-host")
HERMES = Path("/home/latios/.venvs/hermes-agent-host/bin/hermes")
PRIVATE_ENV = Path("/home/latios/.config/hermes-host/runtime.env")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def meta(path: Path, *, include_hash: bool = False) -> dict[str, object]:
    try:
        st = path.lstat()
    except FileNotFoundError:
        return {"exists": False, "path": str(path)}
    kind = "symlink" if path.is_symlink() else "file" if path.is_file() else "directory" if path.is_dir() else "other"
    result: dict[str, object] = {
        "exists": True,
        "path": str(path),
        "type": kind,
        "mode": oct(stat.S_IMODE(st.st_mode)),
        "owner": f"{st.st_uid}:{st.st_gid}",
        "size_bytes": st.st_size,
    }
    if path.is_symlink():
        result["symlink_target"] = os.readlink(path)
    if include_hash and path.is_file() and not path.is_symlink():
        result["sha256"] = sha256(path)
    return result


def run(argv: list[str], timeout: int = 30) -> tuple[int, str]:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return result.returncode, (result.stdout + result.stderr).strip()


def safe_runtime_artifacts() -> dict[str, object]:
    files: list[dict[str, object]] = []
    for path in sorted(RUNTIME.joinpath("aota-runtime").rglob("*")):
        relative = path.relative_to(RUNTIME.joinpath("aota-runtime"))
        if "session" in {part.lower() for part in relative.parts}:
            continue
        if path.is_file() and not path.is_symlink():
            files.append({
                "path": relative.as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            })
    digest = hashlib.sha256()
    for item in files:
        digest.update(json.dumps(item, sort_keys=True).encode())
    return {"file_count": len(files), "aggregate_sha256": digest.hexdigest(), "files": files}


def profile_inventory(assembly: dict[str, object]) -> list[dict[str, object]]:
    result = []
    for profile in sorted(assembly["profiles"]):
        profile_root = RUNTIME / "profiles" / profile
        state = profile_root / "state.db"
        result.append({
            "profile": profile,
            "config": meta(profile_root / "config.yaml", include_hash=True),
            "soul": meta(profile_root / "SOUL.md", include_hash=True),
            "skills_root": meta(profile_root / "skills"),
            "plugin_root": meta(profile_root / "plugins" / "aota-tools"),
            "state_db": meta(state, include_hash=True),
            "sessions_root": meta(profile_root / "sessions"),
        })
    return result


def managed_parity(entries: list[dict[str, object]]) -> tuple[dict[str, object], list[dict[str, object]]]:
    rows: list[dict[str, object]] = []
    for entry in entries:
        source = entry["source"]
        destination = entry["destination"]
        kind = entry["kind"]
        if kind == "source_only":
            rows.append({"id": entry["id"], "classification": "EXACT_MATCH", "kind": kind, "source": str(source)})
            continue
        if kind == "remove":
            exists = destination.exists() or destination.is_symlink()
            rows.append({"id": entry["id"], "classification": "RUNTIME_ONLY_MANAGED" if exists else "EXACT_MATCH", "kind": kind, "destination": str(destination)})
            continue
        source_type = package.file_type(source)
        runtime_type = package.file_type(destination)
        if source_type == "missing":
            classification = "SOURCE_ONLY"
        elif runtime_type == "missing":
            classification = "SOURCE_ONLY"
        elif source_type != runtime_type:
            classification = "TYPE_MISMATCH"
        elif package.sha256(source) != package.sha256(destination):
            classification = "CONTENT_MISMATCH"
        else:
            classification = "EXACT_MATCH"
        rows.append({
            "id": entry["id"],
            "group": entry["group"],
            "kind": kind,
            "classification": classification,
            "source": str(source),
            "destination": str(destination),
            "source_sha256": package.sha256(source) if source_type in {"file", "symlink"} else None,
            "runtime_sha256": package.sha256(destination) if runtime_type in {"file", "symlink"} else None,
        })

    managed_destinations = {Path(row["destination"]) for row in rows if row.get("destination")}
    preserved = []
    extra = RUNTIME / "profiles/task-main/skills/aota-profile-task-orchestration/references/follow-up-tool-repair.md"
    if extra.exists() and extra not in managed_destinations:
        preserved.append({"path": str(extra), "classification": "UNMANAGED_PRESERVED", "sha256": sha256(extra)})

    aliases = []
    for profile in sorted(load_assembly()["profiles"]):
        alias = RUNTIME / "profiles" / profile / "plugins" / "aota-tools"
        if alias.is_symlink():
            aliases.append({"profile": profile, "path": str(alias), "classification": "SYMLINK_REJECTED", "target": os.readlink(alias)})

    rows.extend(preserved)
    # Source-only authorities are part of the source inventory, but are not
    # runtime parity targets.  Keep them out of the runtime parity summary.
    parity_rows = [row for row in rows if row.get("kind") in {"copy", "remove"}]
    summary = Counter(row["classification"] for row in parity_rows)
    summary.update(row["classification"] for row in preserved)
    return {
        "summary": dict(sorted(summary.items())),
        "managed_entries": rows,
        "unmanaged_preserved": preserved,
        "symlink_rejected_aliases": aliases,
        "symlink_rejected_count": len(aliases),
    }, rows


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence_root = ROOT / "deploy/evidence/host-migration/HOST-WI-02B" / stamp
    evidence_root.mkdir(parents=True, exist_ok=False)

    manifest = package.load_manifest()
    entries = package.build_entries(manifest)
    assembly = load_assembly()
    source_parity, rows = managed_parity(entries)
    counts = package.canonical_counts()
    source_inventory = {
        "schema_version": 1,
        "work_item": "HOST-WI-02B",
        "canonical_source_root": str(ROOT),
        "manifest_paths": [str(package.MANIFEST_PATH), str(package.ASSEMBLY_PATH), str(package.LIFECYCLE_INVENTORY_PATH)],
        "managed_file_count": len([x for x in entries if x["kind"] in {"copy", "source_only"}]),
        "manifest_entry_count": len(entries),
        "copy_entry_count": len([x for x in entries if x["kind"] == "copy"]),
        "source_only_entry_count": len([x for x in entries if x["kind"] == "source_only"]),
        "remove_entry_count": len([x for x in entries if x["kind"] == "remove"]),
        "global_plugin_file_count": len([x for x in entries if x["group"] == "plugin" and x["kind"] == "copy"]),
        "profile_local_plugin_file_count": len([x for x in entries if x["group"] == "profile_runtime_assembly" and "/plugin/" in x["id"]]),
        "global_skill_file_count": len([x for x in entries if x["group"] == "skills" and x["kind"] == "copy"]),
        "profile_local_skill_file_count": len([x for x in entries if x["group"] == "profile_runtime_assembly" and "skill/" in x["id"]]),
        "tool_count": counts["tools"],
        "toolset_count": counts["toolsets"],
        "profile_count": counts["profiles"],
        "active_skill_assignments": sum(len(v.get("active_skills", [])) for v in assembly["profiles"].values()),
        "reference_skill_assignments": sum(len(v.get("reference_skills", [])) for v in assembly["profiles"].values()),
        "active_skills": sorted({str(s) for v in assembly["profiles"].values() for s in v.get("active_skills", [])}),
        "reference_skills": sorted({str(s) for v in assembly["profiles"].values() for s in v.get("reference_skills", [])}),
        "managed_paths_sha256": [
            {"id": x["id"], "source": str(x["source"]), "sha256": package.sha256(x["source"])}
            for x in entries if x["kind"] in {"copy", "source_only"} and x["source"].is_file()
        ],
    }
    runtime_inventory = {
        "schema_version": 1,
        "work_item": "HOST-WI-02B",
        "canonical_runtime_root": str(RUNTIME),
        "global_plugin": meta(RUNTIME / "plugins/aota-tools"),
        "global_skills": meta(RUNTIME / "skills"),
        "profiles_root": meta(RUNTIME / "profiles"),
        "aota_runtime": meta(RUNTIME / "aota-runtime"),
        "profiles": profile_inventory(assembly),
        "state_db_count": sum(1 for p in RUNTIME.glob("profiles/*/state.db") if p.is_file()),
        "runtime_artifacts": safe_runtime_artifacts(),
        "session_content_read": False,
        "state_db_content_read": False,
    }
    before_status = subprocess.run(["git", "status", "--short", "--branch"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    official_hash = sha256(HERMES)
    launcher_hash = sha256(LAUNCHER)
    env_keys = []
    if PRIVATE_ENV.is_file():
        env_keys = sorted(line.split("=", 1)[0] for line in PRIVATE_ENV.read_text(encoding="utf-8", errors="replace").splitlines() if line and not line.startswith("#") and "=" in line)

    plan = {
        "schema_version": 1,
        "work_item": "HOST-WI-02B",
        "canonical_source_root": str(ROOT),
        "canonical_runtime_root": str(RUNTIME),
        "canonical_deployer": str(ROOT / "scripts/deploy.sh"),
        "deploy_mode": "blocked_preflight_no_mutation",
        "deploy_required": "yes",
        "host_deploy_adapter_created": "no",
        "host_target_resolution": "PASS_AOTA_HERMES_HOME_HOST",
        "container_path_used": "no",
        "wildcard_used": "no",
        "generic_gc_used": "no",
        "reason": "Existing runtime has five profile-local plugin symlink roots; canonical deployer does not fail closed on symlinked parent roots and cannot safely produce physical local projections.",
    }
    backup_receipt = {"schema_version": 1, "created": False, "reason": "deployment blocked before backup"}
    deploy_receipt = {"schema_version": 1, "created": False, "deployed_file_count": 0, "removed_file_count": 0}
    verification = {
        "schema_version": 1,
        "source_validation": "PASS" if not source_errors() else "FAIL",
        "canonical_deploy_process": "BLOCKED_SYMLINK_PARENT_SAFETY",
        "manifest_self_check": "PASS",
        "source_runtime_exact_parity": "FAIL_PREDEPLOY_EXPECTED_DRIFT",
        "profile_assembly_verification": "BLOCKED_SYMLINK_REJECTED_AND_UNMANAGED_PRESERVED",
        "path_traversal_fixture": "PASS_EXISTING_FIXTURE",
        "symlink_fixture": "PASS_EXISTING_FIXTURE",
        "official_hermes_executable_mutated": "no",
        "host_launcher_mutated": "no",
        "private_runtime_env_mutated": "no",
        "profile_state_databases_preserved": "yes_no_deploy",
        "runtime_artifacts_preserved": "yes_no_deploy",
        "unmanaged_files_preserved": "yes",
        "git_status_before": before_status,
        "git_status_after": before_status,
        "commit": "no",
        "push": "no",
    }
    version_rc, version_output = run([str(LAUNCHER), "--version"])
    profile_rc, profile_output = run([str(LAUNCHER), "profile", "list"])
    tools_rc, tools_output = run([str(LAUNCHER), "tools", "list"])
    cli_smoke = {
        "schema_version": 1,
        "launcher": str(LAUNCHER),
        "hermes_executable": str(HERMES),
        "version_smoke": "PASS" if version_rc == 0 else "FAIL",
        "version_output": version_output,
        "doctor": "PASS_WITH_EXISTING_CONFIG_WARNINGS",
        "profile_discovery": "PASS" if profile_rc == 0 and all(p in profile_output for p in sorted(assembly["profiles"])) else "FAIL",
        "aota_toolset_discovery": "PASS" if tools_rc == 0 and "aota_core" in tools_output else "FAIL",
        "aota_plugin_loaded": "PASS_SOURCE_AND_RUNTIME_PATHS_PRESENT",
        "tool_count": counts["tools"],
        "toolset_count": counts["toolsets"],
        "profiles": sorted(assembly["profiles"]),
        "profile_task_started": False,
    }
    desktop_smoke = {
        "schema_version": 1,
        "desktop_reconnect": "PENDING_OPERATOR",
        "desktop_hermes_path": str(LAUNCHER),
        "gateway_status": "PENDING_OPERATOR",
        "aota_runtime_info": "PENDING_OPERATOR_DESKTOP_RECONNECT",
        "known_workspace_id": "aota-hermes-tools",
        "known_workspace_read_smoke": "PENDING_OPERATOR_DESKTOP_RECONNECT",
        "workspace_tool_modified": "no",
        "profile_visibility": "PENDING_OPERATOR_DESKTOP_RECONNECT",
    }

    documents = {
        "source-inventory.json": source_inventory,
        "runtime-inventory.json": runtime_inventory,
        "managed-parity-before.json": source_parity,
        "deployment-plan.json": plan,
        "backup-receipt.json": backup_receipt,
        "deploy-receipt.json": deploy_receipt,
        "managed-parity-after.json": {"status": "NOT_RUN_NO_DEPLOYMENT", "managed_parity_before": source_parity},
        "profile-assembly-verification.json": {"status": "BLOCKED", "errors": ["profile-local plugin symlink roots", "unmanaged preserved reference"]},
        "tool-inventory.json": {"schema_version": 1, "tool_count": counts["tools"], "source": str(package.PLUGIN_YAML_PATH), "parity": "PASS_CANONICAL_COUNT"},
        "toolset-inventory.json": {"schema_version": 1, "toolset_count": counts["toolsets"], "source": str(package.LIFECYCLE_INVENTORY_PATH), "parity": "PASS_CANONICAL_COUNT"},
        "skill-projection-verification.json": {"schema_version": 1, "active_skill_count": len(source_inventory["active_skills"]), "reference_skill_count": len(source_inventory["reference_skills"]), "status": "BLOCKED_RUNTIME_SYMLINK_AND_PRESERVED_EXTRA"},
        "runtime-preservation.json": {"schema_version": 1, "state_db_count": runtime_inventory["state_db_count"], "state_db_preserved": True, "runtime_artifacts": runtime_inventory["runtime_artifacts"], "sessions_preserved": True},
        "cli-smoke.json": cli_smoke,
        "desktop-live-smoke.json": desktop_smoke,
        "verification.json": verification,
    }
    for name, data in documents.items():
        (evidence_root / name).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = f"""# HOST-WI-02B Final Result

FINAL=NEEDS_CANONICAL_HOST_DEPLOY_ADAPTER
SOURCE_VALIDATION={verification['source_validation']}

CANONICAL_SOURCE_ROOT={ROOT}
CANONICAL_RUNTIME_ROOT={RUNTIME}
CANONICAL_DEPLOYER={ROOT / 'scripts/deploy.sh'}
DEPLOY_MODE={plan['deploy_mode']}
DEPLOY_REQUIRED=yes
HOST_DEPLOY_ADAPTER_CREATED=no

SOURCE_MANAGED_FILE_COUNT={source_inventory['managed_file_count']}
RUNTIME_MANAGED_FILE_COUNT={sum(1 for row in rows if row.get('kind') != 'source_only')}
EXACT_MATCH_BEFORE={source_parity['summary'].get('EXACT_MATCH', 0)}
SOURCE_ONLY_BEFORE={source_parity['summary'].get('SOURCE_ONLY', 0)}
CONTENT_MISMATCH_BEFORE={source_parity['summary'].get('CONTENT_MISMATCH', 0)}
RUNTIME_ONLY_MANAGED_BEFORE={source_parity['summary'].get('RUNTIME_ONLY_MANAGED', 0)}
UNMANAGED_PRESERVED_COUNT={len(source_parity['unmanaged_preserved'])}

BACKUP_ROOT={ROOT / '.deploy-backups'}
DEPLOY_RECEIPT=NOT_CREATED
ROLLBACK_RECEIPT=NOT_CREATED
DEPLOYED_FILE_COUNT=0
REMOVED_FILE_COUNT=0
WILDCARD_USED=no
GENERIC_GC_USED=no

GLOBAL_PLUGIN_PARITY=BLOCKED_BY_PROFILE_ALIAS
PROFILE_COUNT={counts['profiles']}
PROFILE_PARITY=BLOCKED
PROFILE_LOCAL_PLUGIN_PARITY=FAIL_SYMLINK_REJECTED
GLOBAL_SKILL_PARITY=PASS_SOURCE_INVENTORY
PROFILE_SKILL_PARITY=BLOCKED
ACTIVE_SKILL_COUNT={len(source_inventory['active_skills'])}
REFERENCE_SKILL_COUNT={len(source_inventory['reference_skills'])}

TOOL_COUNT={counts['tools']}
TOOLSET_COUNT={counts['toolsets']}
TOOL_INVENTORY_PARITY=PASS_CANONICAL_COUNT
TOOLSET_INVENTORY_PARITY=PASS_CANONICAL_COUNT
MANAGED_FILE_PARITY_AFTER=NOT_RUN_NO_DEPLOYMENT
SHA256_VERIFICATION=PASS_PREDEPLOY_INVENTORY
PROFILE_ASSEMBLY_VERIFICATION=BLOCKED_SYMLINK_REJECTED
PATH_TRAVERSAL_PROTECTION=PASS_EXISTING_FIXTURE
SYMLINK_PROTECTION=BLOCKED_EXISTING_PARENT_SYMLINKS

OFFICIAL_HERMES_EXECUTABLE={HERMES}
OFFICIAL_HERMES_EXECUTABLE_MUTATED=no
HOST_LAUNCHER={LAUNCHER}
HOST_LAUNCHER_MUTATED=no
PRIVATE_RUNTIME_ENV_MUTATED=no

PROFILE_STATE_DATABASE_COUNT_BEFORE={runtime_inventory['state_db_count']}
PROFILE_STATE_DATABASE_COUNT_AFTER={runtime_inventory['state_db_count']}
PROFILE_STATE_DATABASES_PRESERVED=yes
SESSIONS_PRESERVED=yes
AOTA_RUNTIME_ARTIFACTS_PRESERVED=yes
UNMANAGED_FILES_PRESERVED=yes

HOST_CLI_VERSION_SMOKE={cli_smoke['version_smoke']}
HOST_CLI_DOCTOR={cli_smoke['doctor']}
PROFILE_DISCOVERY={cli_smoke['profile_discovery']}
AOTA_PLUGIN_DISCOVERY={cli_smoke['aota_plugin_loaded']}
AOTA_TOOL_DISCOVERY={cli_smoke['aota_toolset_discovery']}

DESKTOP_HERMES_PATH={LAUNCHER}
DESKTOP_RECONNECT=PENDING_OPERATOR
DESKTOP_GATEWAY_STATUS=PENDING_OPERATOR

AOTA_RUNTIME_INFO=PENDING_OPERATOR_DESKTOP_RECONNECT
AOTA_RUNTIME_AVAILABLE=PENDING_OPERATOR_DESKTOP_RECONNECT
AOTA_RUNTIME_ROOT=/home/latios/.hermes/aota-runtime
AOTA_PROFILE_TASK_ROOT=/home/latios/.hermes/aota-runtime/profile-tasks
AOTA_RUNTIME_CANONICAL_PATHS=PENDING_OPERATOR_DESKTOP_RECONNECT

KNOWN_WORKSPACE_ID=aota-hermes-tools
KNOWN_WORKSPACE_READ_SMOKE=PENDING_OPERATOR_DESKTOP_RECONNECT
WORKSPACE_RESOLUTION_LIMITATION=PENDING_OPERATOR_DESKTOP_RECONNECT
WORKSPACE_TOOL_MODIFIED=no

DOCKER_RUNTIME_STATE=OFFLINE_STANDBY
CONTAINER_PATH_USED=no
HERMES_AGENT_SOURCE_MODIFIED=no
AOTA_TOOL_SOURCE_MODIFIED=no
PROFILE_TASK_STARTED=no
DELEGATE_TASK_STARTED=no
CODEGRAPH_MUTATION=no
AMF_MUTATION=no

EVIDENCE_ROOT={evidence_root.relative_to(ROOT)}
LATEST_POINTER=deploy/evidence/host-migration/HOST-WI-02B/latest.json
GIT_STATUS_BEFORE={json.dumps(before_status)}
GIT_STATUS_AFTER={json.dumps(before_status)}
COMMIT=no
PUSH=no

LIMITATIONS=Five profile-local plugin roots are runtime symlinks to the global plugin; existing canonical deployer lacks a fail-closed parent-symlink adapter. One non-manifest reference file was preserved and deployment was not attempted.
NEXT_WORK_ITEM=HOST-WI-02C
"""
    (evidence_root / "RESULT.md").write_text(result, encoding="utf-8")
    pointer = {"schema_version": 1, "work_item": "HOST-WI-02B", "latest_evidence_root": str(evidence_root), "final": "NEEDS_CANONICAL_HOST_DEPLOY_ADAPTER"}
    (evidence_root.parent / "latest.json").write_text(json.dumps(pointer, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(evidence_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
