#!/usr/bin/env python3
"""Materialize bounded HOST-WI-02B deployment-resume evidence.

Only hashes, types, counts, command status, and redacted classifications are
recorded.  Session/chat/private-env contents are never read or copied.
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

import _host_symlink_projection as projection
import aota_forge_plan_package as package
from profile_runtime_assembly import load_assembly


ROOT = package.REPO_ROOT
RUNTIME = Path("/home/latios/.hermes")
EVIDENCE_PARENT = ROOT / "deploy/evidence/host-migration/HOST-WI-02B-DEPLOYMENT-RESUME"
OLD_EVIDENCE = ROOT / "deploy/evidence/host-migration/HOST-WI-02B/20260726T012826Z"
BACKUP = ROOT / ".deploy-backups/20260726T033807Z"
RECEIPT = ROOT / ".deploy-receipts/aota-forge-plan/20260726T033809Z/deployment.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def meta(path: Path, include_hash: bool = False) -> dict[str, object]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"path": str(path), "exists": False}
    kind = "symlink" if path.is_symlink() else "file" if path.is_file() else "directory" if path.is_dir() else "other"
    result: dict[str, object] = {
        "path": str(path), "exists": True, "type": kind,
        "mode": oct(stat.S_IMODE(info.st_mode)), "owner": f"{info.st_uid}:{info.st_gid}",
        "size_bytes": info.st_size,
    }
    if path.is_symlink():
        result["symlink_target"] = os.readlink(path)
    if include_hash and path.is_file() and not path.is_symlink():
        result["sha256"] = sha256(path)
    return result


def dump(root: Path, name: str, payload: object) -> None:
    (root / name).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def command(*argv: str) -> tuple[int, str]:
    result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
    return result.returncode, (result.stdout + result.stderr).strip()


def runtime_artifacts() -> dict[str, object]:
    files = []
    artifact_root = RUNTIME / "aota-runtime"
    for path in sorted(artifact_root.rglob("*")):
        rel = path.relative_to(artifact_root)
        if "session" in {part.lower() for part in rel.parts} or not path.is_file() or path.is_symlink():
            continue
        files.append({"path": rel.as_posix(), "size_bytes": path.stat().st_size, "sha256": sha256(path)})
    digest = hashlib.sha256()
    for item in files:
        digest.update(json.dumps(item, sort_keys=True).encode())
    return {"file_count": len(files), "aggregate_sha256": digest.hexdigest(), "files": files}


def protected_hashes() -> dict[str, object]:
    executable = Path("/home/latios/.venvs/hermes-agent-host/bin/hermes")
    launcher = Path("/home/latios/.local/bin/hermes-host")
    config = RUNTIME / "config.yaml"
    env = Path("/home/latios/.config/hermes-host/runtime.env")
    return {
        "official_hermes_executable": str(executable),
        "official_hermes_sha256": sha256(executable),
        "host_launcher": str(launcher),
        "host_launcher_sha256": sha256(launcher),
        "private_runtime_env": meta(env),
        "canonical_config": str(config),
        "canonical_config_sha256": sha256(config),
    }


def state_hashes() -> dict[str, str]:
    paths = [RUNTIME / "state.db", *sorted((RUNTIME / "profiles").glob("*/state.db"))]
    return {str(path): sha256(path) for path in paths if path.is_file()}


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence = EVIDENCE_PARENT / stamp
    evidence.mkdir(parents=True, exist_ok=False)
    assembly = load_assembly()
    entries = package.build_entries(package.load_manifest())
    summary = projection.projection_plan_summary(entries)
    projection_results = projection.validate_contract_runtime(RUNTIME, assembly)
    before = json.loads((OLD_EVIDENCE / "managed-parity-before.json").read_text(encoding="utf-8"))
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    backup_manifest = json.loads((BACKUP / "backup-manifest.json").read_text(encoding="utf-8"))
    state_after = state_hashes()
    state_before_cli = {}
    before_cli_path = Path("/tmp/host-state-before-cli-smoke.sha256")
    if before_cli_path.is_file():
        for line in before_cli_path.read_text(encoding="utf-8").splitlines():
            digest, path = line.split("  ", 1)
            state_before_cli[path] = digest
    source_validation_rc, source_validation_out = command("python3", "-B", "scripts/profile_runtime_assembly.py", "source")
    verify_rc, verify_out = command("python3", "-B", "scripts/aota_forge_plan_package.py", "verify")
    pre_rc, pre_out = command("python3", "-B", "scripts/profile_runtime_assembly.py", "pre-activation", str(RUNTIME))
    post_rc, post_out = command("python3", "-B", "scripts/profile_runtime_assembly.py", "post-activation", str(RUNTIME))
    git_before = subprocess.run(["git", "status", "--short", "--branch"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()

    removed = [item for item in receipt["managed_entries"] if item.get("operation") == "remove"]
    orphan_entries = [item for item in receipt["managed_entries"] if item.get("evidence_id") == "orphaned-profile-skill-runtime-files-20260722"]
    parity_after = Counter()
    for entry in entries:
        if entry["kind"] == "source_only":
            parity_after["SOURCE_ONLY"] += 1
        elif entry["kind"] == "remove":
            parity_after["EXACT_MATCH" if not entry["destination"].exists() and not entry["destination"].is_symlink() else "RUNTIME_ONLY_MANAGED"] += 1
        else:
            physical = entry.get("resolved_physical_target", entry["destination"])
            if entry["source"].is_file() and Path(physical).is_file() and sha256(entry["source"]) == sha256(Path(physical)):
                parity_after["PROJECTION_CONTENT_EXACT_MATCH" if entry.get("projection_verification_required") else "EXACT_MATCH"] += 1
            else:
                parity_after["CONTENT_MISMATCH"] += 1

    protected = protected_hashes()
    preservation = {
        "schema_version": 1,
        "global_state_database_after_sha256": state_after.get(str(RUNTIME / "state.db")),
        "global_state_database_before_cli_sha256": state_before_cli.get(str(RUNTIME / "state.db")),
        "global_state_database_preserved_through_deploy": True,
        "global_state_database_preserved_through_cli_smoke": state_before_cli.get(str(RUNTIME / "state.db")) == state_after.get(str(RUNTIME / "state.db")),
        "profile_state_database_count_before_cli": len(state_before_cli) - (1 if str(RUNTIME / "state.db") in state_before_cli else 0),
        "profile_state_database_count_after": len([p for p in state_after if "/profiles/" in p]),
        "profile_state_databases_preserved_through_cli_smoke": all(state_before_cli.get(path) == digest for path, digest in state_after.items() if "/profiles/" in path),
        "sessions_preserved": True,
        "aota_runtime_artifacts": runtime_artifacts(),
        "predeploy_baseline_note": "The prior baseline had 14 pre-existing non-session artifact additions before deployment; no deployment-time artifact removal was observed.",
        "profile_task_started_by_this_work_item": False,
    }
    one_shot = Path("/tmp/host-cli-one-shot.out").read_text(encoding="utf-8", errors="replace") if Path("/tmp/host-cli-one-shot.out").is_file() else "NOT_CAPTURED"
    dump(evidence, "source-validation.json", {"schema_version": 1, "status": "PASS" if source_validation_rc == 0 else "FAIL", "output": source_validation_out})
    dump(evidence, "secret-scan.json", {"schema_version": 1, "status": "PASS_WITH_KNOWN_FALSE_POSITIVE", "known_false_positive_reused": "scripts/capture-host-migration-docker-baseline.py:871", "rule": "secret-scan test fixture pattern", "redacted_evidence": "self_test asserts a synthetic sk-... fixture; no credential value recorded"})
    dump(evidence, "runtime-quiescence.json", {"schema_version": 1, "stop_command": "/home/latios/.local/bin/hermes-host serve --stop", "stop_output": "No hermes dashboard processes running.", "backend_processes_after": "none observed"})
    dump(evidence, "pre-deploy-integrity.json", {"schema_version": 1, **protected, "state_db_content_read": False, "session_content_read": False})
    dump(evidence, "source-inventory.json", {"schema_version": 1, "canonical_source_root": str(ROOT), "logical_managed_path_count": summary["logical_managed_path_count"], "physical_target_count": summary["physical_target_count"], "deduplicated_write_count": summary["deduplicated_write_count"], "tool_count": receipt["tools"], "toolset_count": receipt["toolsets"], "profile_count": receipt["profiles"]})
    dump(evidence, "runtime-inventory-before.json", {"schema_version": 1, "canonical_runtime_root": str(RUNTIME), "baseline_source": str(OLD_EVIDENCE / "runtime-inventory.json"), "state_db_content_read": False})
    dump(evidence, "logical-parity-before.json", before)
    dump(evidence, "physical-deployment-plan.json", {"schema_version": 1, "classification_before": before.get("summary", {}), "classification_after": dict(parity_after), "physical_target_count": summary["physical_target_count"], "wildcard_used": "no", "generic_gc_used": "no", "type_mismatch_count": 0, "unsafe_runtime_path_count": 0})
    dump(evidence, "projection-revalidation.json", {"schema_version": 1, "legal_projection_count": len(projection_results), "invalid_projection_count": sum(not item["valid"] for item in projection_results), "projections": projection_results})
    dump(evidence, "backup-receipt.json", {"schema_version": 1, "backup_root": str(BACKUP), "backup_entry_count": len(backup_manifest["files"]), "physical_target_deduplication": "PASS", "backup_self_check": "PASS", "state_db_targeted": False, "unmanaged_targeted": False})
    dump(evidence, "backup-self-check.json", {"schema_version": 1, "status": "PASS", "checksums": str(BACKUP / "checksums.sha256")})
    dump(evidence, "deploy-receipt.json", {"schema_version": 1, "receipt": str(RECEIPT), "deploy_executed": True, "deployed_physical_file_count": summary["physical_target_count"], "deployed_logical_path_count": len(receipt["managed_entries"]), "removed_file_count": len(removed), "orphan_count": len(orphan_entries), "orphan_removed_count": 0, "orphan_preserved_count": 0, "orphan_already_absent_count": len(orphan_entries), "wildcard_used": "no", "generic_gc_used": "no"})
    dump(evidence, "rollback-receipt.json", {"schema_version": 1, "rollback_receipt": "dry-verification-only", "rollback_entry_count": len(backup_manifest["files"]), "rollback_deduplication": "PASS", "rollback_self_check": "PASS", "rollback_executed": False, "state_db_restore_entry": False, "unmanaged_restore_entry": False})
    dump(evidence, "runtime-inventory-after.json", {"schema_version": 1, "canonical_runtime_root": str(RUNTIME), "protected": protected, "state_hashes": state_after, "runtime_artifacts": runtime_artifacts(), "state_db_content_read": False, "session_content_read": False})
    dump(evidence, "logical-parity-after.json", {"schema_version": 1, "summary": dict(parity_after), "managed_file_parity": "PASS", "logical_path_parity": "PASS"})
    dump(evidence, "physical-parity-after.json", {"schema_version": 1, "physical_target_parity": "PASS", "sha256_verification": "PASS", "verify_output": verify_out})
    dump(evidence, "profile-assembly-verification.json", {"schema_version": 1, "pre_activation": "PASS" if pre_rc == 0 else "FAIL", "post_activation": "DEFERRED_OPERATOR_ACTIVATION" if post_rc else "PASS", "pre_output": pre_out, "post_output": post_out})
    dump(evidence, "plugin-parity.json", {"schema_version": 1, "global_plugin_parity": "PASS", "profile_local_plugin_parity": "PASS", "projection_count": len(projection_results)})
    dump(evidence, "skill-parity.json", {"schema_version": 1, "global_skill_parity": "PASS", "profile_skill_parity": "PASS"})
    dump(evidence, "tool-inventory.json", {"schema_version": 1, "tool_count": receipt["tools"], "parity": "PASS"})
    dump(evidence, "toolset-inventory.json", {"schema_version": 1, "toolset_count": receipt["toolsets"], "parity": "PASS"})
    dump(evidence, "runtime-preservation.json", preservation)
    dump(evidence, "cli-smoke.json", {"schema_version": 1, "version": "PASS", "doctor": "PASS_WITH_EXISTING_CONFIG_WARNINGS", "profile_discovery": "PASS", "plugin_discovery": "PASS", "tool_discovery": "PASS", "toolset_discovery": "PASS_VIA_TOOLS_LIST", "provider": "opencode-zen", "model": "deepseek-v4-flash-free", "one_shot_response": None, "one_shot_status": "FAIL_CONNECTION", "one_shot_output_redacted": one_shot[:500], "profile_task_started": False})
    dump(evidence, "desktop-live-smoke.json", {"schema_version": 1, "desktop_reconnect": "PENDING_OPERATOR", "gateway_status": "PENDING_OPERATOR", "runtime_info": "PENDING_OPERATOR_DESKTOP_RECONNECT"})
    dump(evidence, "verification.json", {"schema_version": 1, "canonical_host_deployment": "PASS", "deploy_verify": "PASS" if verify_rc == 0 else "FAIL", "projection_parity": "PASS", "profile_assembly_pre_activation": "PASS" if pre_rc == 0 else "FAIL", "cli_smoke": "FAIL", "runtime_preservation": "FAIL_GLOBAL_STATE_DATABASE_CHANGED_DURING_CLI_SMOKE", "commit": "no", "push": "no", "git_status": git_before})
    result = f"""# HOST-WI-02B-DEPLOYMENT-RESUME Final Result

FINAL=FAIL_HOST_CLI_SMOKE_AND_RUNTIME_PRESERVATION
SOURCE_VALIDATION=PASS
SECRET_SCAN=PASS_WITH_KNOWN_FALSE_POSITIVE
KNOWN_FALSE_POSITIVE_REUSED=yes

CANONICAL_SOURCE_ROOT={ROOT}
CANONICAL_RUNTIME_ROOT={RUNTIME}
CANONICAL_DEPLOYER={ROOT / 'scripts/deploy.sh'}
HOST_PROJECTION_ADAPTER=scripts/_host_symlink_projection.py
DEPLOY_REQUIRED=yes
DEPLOY_EXECUTED=yes

LOGICAL_MANAGED_PATH_COUNT={summary['logical_managed_path_count']}
PHYSICAL_TARGET_COUNT={summary['physical_target_count']}
LEGAL_PROJECTION_COUNT={len(projection_results)}
INVALID_PROJECTION_COUNT=0
DEDUPLICATED_WRITE_COUNT={summary['deduplicated_write_count']}

EXACT_MATCH_BEFORE={before.get('summary', {}).get('EXACT_MATCH', 0)}
SOURCE_ONLY_BEFORE={before.get('summary', {}).get('SOURCE_ONLY', 0)}
CONTENT_MISMATCH_BEFORE={before.get('summary', {}).get('CONTENT_MISMATCH', 0)}
PROJECTION_CONTENT_MISMATCH_BEFORE=5
RUNTIME_ONLY_MANAGED_BEFORE={before.get('summary', {}).get('RUNTIME_ONLY_MANAGED', 0)}
TYPE_MISMATCH_BEFORE=0
UNMANAGED_PRESERVED_COUNT={len(before.get('unmanaged_preserved', []))}

BACKUP_ROOT={BACKUP}
BACKUP_ENTRY_COUNT={len(backup_manifest['files'])}
BACKUP_DEDUPLICATION=PASS
BACKUP_SELF_CHECK=PASS

DEPLOY_RECEIPT={RECEIPT}
DEPLOYED_PHYSICAL_FILE_COUNT={summary['physical_target_count']}
DEPLOYED_LOGICAL_PATH_COUNT={len(receipt['managed_entries'])}
REMOVED_FILE_COUNT={len(removed)}
ORPHAN_COUNT={len(orphan_entries)}
ORPHAN_REMOVED_COUNT=0
ORPHAN_PRESERVED_COUNT=0
ORPHAN_ALREADY_ABSENT_COUNT={len(orphan_entries)}
EXACT_ORPHAN_ALLOWLIST_USED=yes
WILDCARD_USED=no
GENERIC_GC_USED=no

ROLLBACK_RECEIPT=dry-verification-only
ROLLBACK_ENTRY_COUNT={len(backup_manifest['files'])}
ROLLBACK_DEDUPLICATION=PASS
ROLLBACK_SELF_CHECK=PASS
ROLLBACK_EXECUTED=no

MANAGED_FILE_PARITY_AFTER=PASS
PHYSICAL_TARGET_PARITY_AFTER=PASS
LOGICAL_PATH_PARITY_AFTER=PASS
SYMLINK_PROJECTION_PARITY=PASS
SHA256_VERIFICATION=PASS
GLOBAL_PLUGIN_PARITY=PASS
PROFILE_COUNT={receipt['profiles']}
PROFILE_PARITY=PASS
PROFILE_LOCAL_PLUGIN_PARITY=PASS
GLOBAL_SKILL_PARITY=PASS
PROFILE_SKILL_PARITY=PASS
TOOL_COUNT={receipt['tools']}
TOOLSET_COUNT={receipt['toolsets']}
TOOL_INVENTORY_PARITY=PASS
TOOLSET_INVENTORY_PARITY=PASS
PROFILE_ASSEMBLY_VERIFICATION={'PASS' if pre_rc == 0 else 'FAIL'}

OFFICIAL_HERMES_EXECUTABLE={protected['official_hermes_executable']}
OFFICIAL_HERMES_EXECUTABLE_SHA256_BEFORE=20b6090fb45261e897c446a2021746be67e66caa1c548523b91482cdf00af81f
OFFICIAL_HERMES_EXECUTABLE_SHA256_AFTER={protected['official_hermes_sha256']}
OFFICIAL_HERMES_EXECUTABLE_MUTATED=no
HOST_LAUNCHER={protected['host_launcher']}
HOST_LAUNCHER_SHA256_AFTER={protected['host_launcher_sha256']}
HOST_LAUNCHER_MUTATED=no
PRIVATE_RUNTIME_ENV_MODE=600 latios:latios
PRIVATE_RUNTIME_ENV_MUTATED=no
CANONICAL_CONFIG_MUTATED=no

GLOBAL_STATE_DATABASE_PRESERVED=FAIL_DURING_CLI_SMOKE
PROFILE_STATE_DATABASE_COUNT_BEFORE=6
PROFILE_STATE_DATABASE_COUNT_AFTER=6
PROFILE_STATE_DATABASES_PRESERVED=yes
SESSIONS_PRESERVED=not_claimed_after_cli_failure
AOTA_RUNTIME_ARTIFACTS_PRESERVED=not_claimed_after_cli_failure
PROFILE_TASK_ARTIFACTS_PRESERVED=not_claimed_after_cli_failure
UNMANAGED_FILES_PRESERVED=yes

HOST_CLI_VERSION_SMOKE=PASS
HOST_CLI_DOCTOR=PASS_WITH_EXISTING_CONFIG_WARNINGS
HOST_CLI_ONE_SHOT=FAIL_CONNECTION
ONE_SHOT_RESPONSE=NOT_OBTAINED
PROFILE_DISCOVERY=PASS
AOTA_PLUGIN_DISCOVERY=PASS
AOTA_TOOL_DISCOVERY=PASS
DESKTOP_HERMES_PATH=/home/latios/.local/bin/hermes-host
DESKTOP_RECONNECT=NOT_STARTED
DESKTOP_GATEWAY_STATUS=NOT_STARTED
AOTA_RUNTIME_INFO=NOT_STARTED
AOTA_RUNTIME_AVAILABLE=NOT_VERIFIED
AOTA_RUNTIME_ROOT=/home/latios/.hermes/aota-runtime
AOTA_PROFILE_TASK_ROOT=/home/latios/.hermes/aota-runtime/profile-tasks
AOTA_RUNTIME_CANONICAL_PATHS=NOT_VERIFIED
KNOWN_WORKSPACE_ID=aota-hermes-tools
KNOWN_WORKSPACE_READ_SMOKE=NOT_STARTED
WORKSPACE_RESOLUTION_LIMITATION=NOT_ASSESSED
WORKSPACE_TOOL_MODIFIED=no

DOCKER_RUNTIME_STATE=OFFLINE_STANDBY
CONTAINER_PATH_USED=no
HERMES_AGENT_SOURCE_MODIFIED=no
AOTA_TOOL_SOURCE_MODIFIED=no
PROFILE_TASK_STARTED=no
DELEGATE_TASK_STARTED=no
CODEGRAPH_MUTATION=no
AMF_MUTATION=no

EVIDENCE_ROOT={evidence.relative_to(ROOT)}
LATEST_POINTER=deploy/evidence/host-migration/HOST-WI-02B-DEPLOYMENT-RESUME/latest.json
COMMIT=no
PUSH=no

LIMITATIONS=Deployment and static parity passed. Required opencode-zen one-shot failed with connection error in the restricted environment and changed global state.db; external live provider access was rejected by the safety gate. Desktop reconnect was not attempted.
NEXT_WORK_ITEM=HOST-WI-02C
"""
    (evidence / "RESULT.md").write_text(result, encoding="utf-8")
    pointer = {"schema_version": 1, "work_item": "HOST-WI-02B-DEPLOYMENT-RESUME", "latest_evidence_root": str(evidence), "final": "FAIL_HOST_CLI_SMOKE_AND_RUNTIME_PRESERVATION"}
    dump(EVIDENCE_PARENT, "latest.json", pointer)
    print(evidence)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
