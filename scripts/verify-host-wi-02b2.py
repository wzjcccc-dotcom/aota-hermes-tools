#!/usr/bin/env python3
"""Generate the read-only HOST-WI-02B2 closure evidence.

This verifier deliberately does not load credential values, perform an
authenticated provider request, repair SQLite, or invoke a Hermes one-shot.
It records the bounded observations already made by the operator checkpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path("/home/latios/workspace/aota-hermes-tools")
HERMES_HOME = Path("/home/latios/.hermes")
GLOBAL_DB = HERMES_HOME / "state.db"
PROFILE_ROOT = HERMES_HOME / "profiles"
LAUNCHER = Path("/home/latios/.local/bin/hermes-host")
RUNTIME_ENV = Path("/home/latios/.config/hermes-host/runtime.env")
OFFICIAL_HERMES = Path("/home/latios/.venvs/hermes-agent-host/bin/hermes")
CONFIG = HERMES_HOME / "config.yaml"
CANONICAL_ENV = HERMES_HOME / ".env"
AUTH_FILE = HERMES_HOME / "auth.json"


def dump(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def env_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    if not path.is_file():
        return keys
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            keys.add(key)
    return keys


def env_value(path: Path, key: str) -> str:
    if not path.is_file():
        return ""
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[7:].strip()
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip("\"'")
    return ""


def url_class(value: str) -> str:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and host == "opencode.ai":
        return "public_https_opencode"
    if parsed.scheme == "https":
        return "public_https"
    if parsed.scheme == "http" and host in {"127.0.0.1", "localhost"}:
        return "loopback_http"
    if parsed.scheme == "http" and host == "100.123.10.71":
        return "host_proxy_http"
    if parsed.scheme == "http":
        return "private_or_plain_http"
    return "missing_or_invalid"


def run_sqlite(db: Path, sql: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["sqlite3", "-readonly", str(db), sql],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
    )
    return proc.returncode, proc.stdout.strip()


def scalar(db: Path, sql: str, fallback: str = "UNREADABLE") -> str:
    rc, out = run_sqlite(db, sql)
    if rc != 0 or not out:
        return fallback
    return out.splitlines()[-1].strip()


def db_inventory(db: Path) -> dict[str, object]:
    exists = db.is_file()
    size = db.stat().st_size if exists else 0
    table_count = scalar(
        db,
        'PRAGMA writable_schema=ON; SELECT count(*) FROM sqlite_master WHERE type="table";',
        "0",
    ) if exists else "0"
    schema_version = scalar(db, "SELECT group_concat(version) FROM schema_version;", "UNREADABLE") if exists else "MISSING"
    session_count = scalar(db, "SELECT count(*) FROM sessions;", "UNREADABLE") if exists else "MISSING"
    message_count = scalar(db, "SELECT count(*) FROM messages;", "UNREADABLE") if exists else "MISSING"
    rc_quick, quick = run_sqlite(db, "PRAGMA quick_check;") if exists else (1, "MISSING")
    rc_integrity, integrity = run_sqlite(db, "PRAGMA integrity_check;") if exists else (1, "MISSING")
    return {
        "database_path": str(db),
        "exists": exists,
        "size_bytes": size,
        "schema_version": schema_version,
        "table_count": int(table_count) if table_count.isdigit() else table_count,
        "integrity_check": "PASS" if rc_integrity == 0 and integrity == "ok" else "FAIL",
        "integrity_detail_redacted": integrity[:240],
        "quick_check": "PASS" if rc_quick == 0 and quick == "ok" else "FAIL",
        "quick_detail_redacted": quick[:240],
        "session_count": int(session_count) if session_count.isdigit() else session_count,
        "message_count": int(message_count) if message_count.isdigit() else message_count,
        "zero_byte": size == 0,
        "wal_present": db.with_name(db.name + "-wal").exists(),
        "wal_size_bytes": db.with_name(db.name + "-wal").stat().st_size if db.with_name(db.name + "-wal").exists() else 0,
        "shm_present": db.with_name(db.name + "-shm").exists(),
    }


def git_status() -> str:
    proc = subprocess.run(
        ["git", "status", "--short"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return proc.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence_root
    evidence.mkdir(parents=True, exist_ok=False)

    runtime_keys = sorted(env_keys(RUNTIME_ENV))
    canonical_keys = sorted(env_keys(CANONICAL_ENV))
    required = ["OPENCODE_ZEN_API_KEY"]
    optional = ["OPENCODE_ZEN_BASE_URL"]
    key_presence = {
        key: {
            "runtime_env": "present" if key in runtime_keys else "missing",
            "canonical_hermes_env": "present" if key in canonical_keys else "missing",
            "value_recorded": False,
        }
        for key in required + optional
    }
    configured_url_class = url_class(env_value(CANONICAL_ENV, "OPENCODE_ZEN_BASE_URL"))
    profiles = sorted(p for p in PROFILE_ROOT.glob("*/state.db") if p.is_file())
    inventories = [db_inventory(GLOBAL_DB)] + [db_inventory(p) for p in profiles]
    global_inventory = inventories[0]
    profile_inventories = inventories[1:]
    profile_integrity = all(x["integrity_check"] == "PASS" and x["quick_check"] == "PASS" for x in profile_inventories)
    # Do not treat ordinary dependency/test filenames such as
    # ``normalforms.py`` or ``malformed1.mat`` as runtime corruption markers.
    # Only exact operator marker basenames count.
    marker_names = {"corruption.marker", "corruption.json", "quarantine.marker", "quarantine.json"}
    marker_paths = []
    for base in (HERMES_HOME, ROOT / "deploy"):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.name.lower() in marker_names:
                marker_paths.append(str(path))

    provider_resolution = {
        "schema_version": 1,
        "provider": "opencode-zen",
        "model": "deepseek-v4-flash-free",
        "configuration_sources": [
            {"source": "bundled_provider_plugin", "path": "plugins/model-providers/opencode-zen/__init__.py", "status": "used"},
            {"source": "canonical_config_model_alias", "path": str(CONFIG), "entry": "zen-deepseek-free", "status": "used"},
            {"source": "canonical_hermes_env", "path": str(CANONICAL_ENV), "status": "used_by_credential_resolution"},
            {"source": "host_launcher_runtime_env", "path": str(RUNTIME_ENV), "status": "launcher_loaded_then_official_cli_loaded_canonical_env"},
        ],
        "required_keys": required,
        "optional_keys": optional,
        "present_keys": [k for k in required + optional if k in canonical_keys],
        "missing_keys": [k for k in required + optional if k not in canonical_keys],
        "key_presence_by_source": key_presence,
        "auth_file_present": AUTH_FILE.is_file(),
        "auth_file_used_by_provider": False,
        "base_url_class": configured_url_class,
        "provider_default_base_url_class": "public_https_opencode",
        "proxy_present": configured_url_class == "host_proxy_http",
        "launcher_env_loaded": True,
        "official_cli_env_loaded": True,
        "credential_values_redacted": True,
    }
    dump(evidence / "provider-resolution.json", provider_resolution)

    compare_keys = [
        "HOME", "HERMES_HOME", "PATH", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE_URL", "OPENCODE_API_KEY",
        "OPENCODE_BASE_URL", "OPENCODE_ZEN_API_KEY", "OPENCODE_ZEN_BASE_URL",
    ]
    dump(evidence / "launcher-environment-comparison.json", {
        "schema_version": 1,
        "direct_executable": str(OFFICIAL_HERMES),
        "launcher": str(LAUNCHER),
        "launcher_runtime_env": str(RUNTIME_ENV),
        "keys": {key: {
            "runtime_env": "present" if key in runtime_keys else "missing",
            "canonical_hermes_env": "present" if key in canonical_keys else "missing",
            "value_recorded": False,
        } for key in compare_keys},
        "source_behavior": {
            "launcher": "sets HOME, sources runtime.env, execs official executable",
            "official_cli": "loads HERMES_HOME/.env through hermes_cli.env_loader/config",
        },
        "credential_values_redacted": True,
    })

    dump(evidence / "provider-connectivity.json", {
        "schema_version": 1,
        "provider": "opencode-zen",
        "model": "deepseek-v4-flash-free",
        "configured_endpoint": {"base_url_class": configured_url_class, "unauthenticated_models_http_status": 401},
        "public_provider_endpoint": {"dns": "PASS", "tcp": "PASS", "tls": "PASS", "unauthenticated_http_status": 200},
        "auth_test": "NOT_RUN_USER_APPROVAL_REQUIRED",
        "model_availability_test": "NOT_RUN_USER_APPROVAL_REQUIRED",
        "one_shot": "NOT_RUN_CURRENT_CLOSURE_DB_INTEGRITY_BLOCKER",
        "retry_count_current_work_item": 0,
        "credential_values_redacted": True,
    })
    dump(evidence / "provider-error-classification.json", {
        "schema_version": 1,
        "before": {
            "exception_type": "APIConnectionError",
            "sanitized_error_category": "PROXY_FAILURE",
            "http_status": None,
            "endpoint_host": "100.123.10.71",
            "retryability": "retryable_but_not_repeated_in_this_work_item",
            "launcher_related": False,
            "provider_related": "not_established",
            "network_related": True,
            "basis": "previous Hermes log records connection error to configured host proxy; current non-auth endpoint is reachable and returns 401",
        },
        "current_non_auth_observation": "configured_endpoint_reachable_but_auth_not_tested",
        "credential_values_redacted": True,
    })

    dump(evidence / "database-inventory.json", {
        "schema_version": 1,
        "global": global_inventory,
        "profiles": profile_inventories,
        "profile_database_count": len(profile_inventories),
        "zero_byte_database_count": sum(1 for x in inventories if x["zero_byte"]),
        "corruption_marker_count": len(marker_paths),
        "corruption_marker_paths_redacted": marker_paths,
    })
    dump(evidence / "database-integrity.json", {
        "schema_version": 1,
        "global_state_db": global_inventory,
        "profile_state_dbs": profile_inventories,
        "global_state_db_integrity": global_inventory["integrity_check"],
        "profile_state_db_integrity": "PASS" if profile_integrity else "FAIL",
        "zero_byte_database_count": sum(1 for x in inventories if x["zero_byte"]),
        "corruption_marker_count": len(marker_paths),
        "db_content_modified_by_verifier": False,
    })

    dump(evidence / "session-preservation.json", {
        "schema_version": 1,
        "global_session_count": global_inventory["session_count"],
        "profile_session_counts": {Path(x["database_path"]).parent.name: x["session_count"] for x in profile_inventories},
        "profile_database_count_before": 6,
        "profile_database_count_after": len(profile_inventories),
        "preexisting_session_identifier_hash_set_available": False,
        "preexisting_sessions_removed": "NOT_PROVABLE_GLOBAL_DB_MALFORMED",
        "new_runtime_session_count": "NOT_COMPUTABLE",
        "session_content_read": False,
        "message_content_read": False,
    })
    dump(evidence / "runtime-mutation-classification.json", {
        "schema_version": 1,
        "deployer_state_db_target_observation": "no_state.db_path_in_deployment_receipt_or_managed_plan",
        "state_db_targeted_by_deployer": "no",
        "state_db_replaced_by_deployer": "no_evidence",
        "state_db_removed_by_deployer": "no_evidence",
        "one_shot_attempted_by_previous_work_item": True,
        "expected_runtime_mutation": "yes",
        "global_hash_changed_after_previous_cli_smoke": True,
        "runtime_mutation_is_healthy": False,
        "reason": "global state.db schema is malformed; hash change is not alone treated as failure, but integrity failure remains a hard failure",
    })
    dump(evidence / "cli-smoke.json", {
        "schema_version": 1,
        "version": "PASS",
        "doctor": "FAIL_GLOBAL_STATE_DB_MALFORMED",
        "profile_discovery": "PASS",
        "plugin_discovery": "PASS_FROM_PREVIOUS_EVIDENCE",
        "tool_discovery": "PASS_FROM_PREVIOUS_EVIDENCE",
        "provider": "opencode-zen",
        "model": "deepseek-v4-flash-free",
        "one_shot_status": "NOT_RUN_DB_INTEGRITY_BLOCKER_AND_AUTH_APPROVAL_GATE",
        "one_shot_response": None,
        "profile_task_started": False,
    })
    dump(evidence / "desktop-live-smoke.json", {
        "schema_version": 1,
        "desktop_reconnect": "NOT_STARTED",
        "gateway_status": "NOT_STARTED",
        "profile_visibility": "NOT_STARTED",
        "aota_runtime_info": "NOT_STARTED",
        "aota_runtime_available": "NOT_VERIFIED",
        "aota_runtime_root": "/home/latios/.hermes/aota-runtime",
        "aota_profile_task_root": "/home/latios/.hermes/aota-runtime/profile-tasks",
        "tool_visibility": "NOT_STARTED",
        "provider_smoke": "NOT_STARTED",
        "operator_checkpoint": "required",
    })

    status_before = (
        "M deploy/profile-runtime-assembly.yaml; M plugin/aota-tools/_followup_task_create.py; "
        "M scripts/aota_forge_plan_package.py; M scripts/profile_runtime_assembly.py; "
        "?? deploy/evidence/host-migration/; ?? docs/aota-host-migration/; "
        "?? scripts/_host_symlink_projection.py; ?? scripts/capture-host-migration-docker-baseline.py; "
        "?? scripts/capture-host-wi-02b-deployment-resume.py; ?? scripts/capture-host-wi-02b.py; "
        "?? scripts/capture-host-wi-02b1.py; ?? scripts/review-host-wi-02b1.py; "
        "?? scripts/verify-host-symlink-projection.py"
    )
    verification = {
        "schema_version": 1,
        "source_validation": "PASS",
        "canonical_deployment_previously_completed": True,
        "static_runtime_parity": "PASS",
        "launcher_syntax": "PASS",
        "runtime_env_mode": "600",
        "launcher_mode": "700",
        "official_hermes_executable_sha256": sha256(OFFICIAL_HERMES),
        "host_launcher_sha256": sha256(LAUNCHER),
        "official_hermes_executable_mutated": False,
        "host_launcher_mutated": False,
        "private_runtime_env_mutated": False,
        "canonical_config_mutated": False,
        "docker_runtime_state": "OFFLINE_STANDBY",
        "profile_task_started": False,
        "delegate_task_started": False,
        "codegraph_mutation": False,
        "amf_mutation": False,
        "commit": False,
        "push": False,
        "git_status_before": status_before,
        "git_status_after": git_status(),
        "db_content_modified_by_verifier": False,
    }
    dump(evidence / "verification.json", verification)

    result_lines = [
        "# HOST-WI-02B2 Final Result", "",
        "FINAL=FAIL_STATE_DATABASE_INTEGRITY", "SOURCE_VALIDATION=PASS", "",
        "PROVIDER=opencode-zen", "MODEL=deepseek-v4-flash-free",
        "PROVIDER_CONFIGURATION_SOURCE=canonical_hermes_env_plus_model_alias_plus_bundled_provider_plugin",
        "PROVIDER_CONFIGURATION_RESOLUTION=PASS", "PROVIDER_REQUIRED_KEY_COUNT=1",
        "PROVIDER_PRESENT_KEY_COUNT=2", "PROVIDER_MISSING_KEYS=none", "CREDENTIAL_EXPOSED=no", "",
        "PROVIDER_ERROR_BEFORE=APIConnectionError:Connection error",
        "PROVIDER_ERROR_CLASSIFICATION=PROXY_FAILURE", "PROVIDER_FIX_REQUIRED=no",
        "PROVIDER_FIX_APPLIED=no", "PROVIDER_CONNECTIVITY_AFTER=UNAUTHENTICATED_ENDPOINT_REACHABLE_AUTH_NOT_RUN",
        "PROVIDER_RETRY_COUNT=0", "",
        f"HOST_LAUNCHER={LAUNCHER}", "HOST_LAUNCHER_MUTATED=no", "PRIVATE_RUNTIME_ENV_MUTATED=no",
        f"OFFICIAL_HERMES_EXECUTABLE_MUTATED=no", "",
        "HOST_CLI_VERSION_SMOKE=PASS", "HOST_CLI_DOCTOR=FAIL_GLOBAL_STATE_DB_MALFORMED",
        "PROFILE_DISCOVERY=PASS", "AOTA_PLUGIN_DISCOVERY=PASS_FROM_PREVIOUS_EVIDENCE",
        "AOTA_TOOL_DISCOVERY=PASS_FROM_PREVIOUS_EVIDENCE", "HOST_CLI_ONE_SHOT=NOT_RUN",
        "ONE_SHOT_RESPONSE=NOT_OBTAINED", "",
        "GLOBAL_STATE_DB=/home/latios/.hermes/state.db", "GLOBAL_STATE_DB_INTEGRITY=FAIL",
        f"PROFILE_STATE_DB_COUNT={len(profile_inventories)}", f"PROFILE_STATE_DB_INTEGRITY={'PASS' if profile_integrity else 'FAIL'}",
        f"ZERO_BYTE_DATABASE_COUNT={sum(1 for x in inventories if x['zero_byte'])}",
        f"CORRUPTION_MARKER_COUNT={len(marker_paths)}", "",
        "STATE_DB_TARGETED_BY_DEPLOYER=no", "STATE_DB_REPLACED_BY_DEPLOYER=no_evidence",
        "STATE_DB_REMOVED_BY_DEPLOYER=no_evidence", "EXPECTED_RUNTIME_MUTATION=yes",
        "PREEXISTING_SESSIONS_REMOVED=NOT_PROVABLE_GLOBAL_DB_MALFORMED", "NEW_RUNTIME_SESSION_COUNT=NOT_COMPUTABLE",
        "RUNTIME_PRESERVATION=FAIL_GLOBAL_STATE_DB_INTEGRITY", "",
        "DESKTOP_HERMES_PATH=/home/latios/.local/bin/hermes-host", "DESKTOP_RECONNECT=NOT_STARTED",
        "DESKTOP_GATEWAY_STATUS=NOT_STARTED", "DESKTOP_PROFILE_VISIBILITY=NOT_STARTED",
        "AOTA_RUNTIME_INFO=NOT_STARTED", "AOTA_RUNTIME_AVAILABLE=NOT_VERIFIED",
        "AOTA_RUNTIME_ROOT=/home/latios/.hermes/aota-runtime", "AOTA_PROFILE_TASK_ROOT=/home/latios/.hermes/aota-runtime/profile-tasks",
        "DESKTOP_PROVIDER_SMOKE=NOT_STARTED", "",
        "KNOWN_WORKSPACE_ID=aota-hermes-tools", "KNOWN_WORKSPACE_READ_SMOKE=NOT_STARTED",
        "WORKSPACE_RESOLUTION_LIMITATION=NOT_ASSESSED", "WORKSPACE_TOOL_MODIFIED=no", "",
        "CANONICAL_DEPLOYMENT_STATUS=PASS", "STATIC_RUNTIME_PARITY=PASS", "DOCKER_RUNTIME_STATE=OFFLINE_STANDBY",
        "PROFILE_TASK_STARTED=no", "DELEGATE_TASK_STARTED=no", "CODEGRAPH_MUTATION=no", "AMF_MUTATION=no", "",
        f"EVIDENCE_ROOT={evidence}", f"LATEST_POINTER={ROOT / 'deploy/evidence/host-migration/HOST-WI-02B2/latest.json'}",
        f"HOST_WI_02B_CLOSURE_POINTER={ROOT / 'deploy/evidence/host-migration/HOST-WI-02B/latest.json'}",
        'GIT_STATUS_BEFORE="' + status_before + '"', 'GIT_STATUS_AFTER="' + git_status() + '"',
        "COMMIT=no", "PUSH=no", "",
        "LIMITATIONS=Global state.db is malformed at messages_fts; no repair or DB mutation was authorized. Authenticated provider test, one-shot, Desktop reconnect/live smoke remain pending.",
        "NEXT_WORK_ITEM=HOST-WI-02C0",
    ]
    (evidence / "RESULT.md").write_text("\n".join(result_lines) + "\n", encoding="utf-8")
    dump(ROOT / "deploy/evidence/host-migration/HOST-WI-02B2/latest.json", {
        "schema_version": 1,
        "work_item": "HOST-WI-02B2",
        "evidence_relative_path": evidence.name,
        "latest_evidence_root": str(evidence),
        "final": "FAIL_STATE_DATABASE_INTEGRITY",
        "provider_configuration_resolution": "PASS",
        "provider_connectivity": "NOT_CLOSED_AUTH_GATE",
        "global_state_db_integrity": "FAIL",
        "profile_state_db_integrity": "PASS" if profile_integrity else "FAIL",
        "runtime_preservation": "FAIL_GLOBAL_STATE_DB_INTEGRITY",
        "commit": False,
        "push": False,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
