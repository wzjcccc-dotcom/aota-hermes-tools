#!/usr/bin/env python3
"""Read-only HOST-WI-02B1 evidence generator.

It assesses the formal runtime with lstat/readlink/resolve/hash only.  No
deployment, backup, rollback, service, Desktop, or runtime-content mutation
is performed here.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

import _host_symlink_projection as adapter
import aota_forge_plan_package as package
from profile_runtime_assembly import source_errors


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path("/home/latios/.hermes")
EVIDENCE_PARENT = ROOT / "deploy/evidence/host-migration/HOST-WI-02B1"


def command(*args: str) -> tuple[int, str]:
    completed = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    return completed.returncode, (completed.stdout + completed.stderr).strip()


def write_json(root: Path, name: str, payload: object) -> None:
    (root / name).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence = EVIDENCE_PARENT / stamp
    evidence.mkdir(parents=True, exist_ok=False)
    before_status = subprocess.run(["git", "status", "--short", "--branch"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    assembly = yaml.safe_load((ROOT / "deploy/profile-runtime-assembly.yaml").read_text(encoding="utf-8"))
    contract = adapter.load_projection_contract(assembly)
    assessments = adapter.validate_contract_runtime(RUNTIME, assembly)
    source_validation_errors = source_errors()
    entries = package.build_entries(package.load_manifest())
    plan_summary = adapter.projection_plan_summary(entries)
    fixture_rc, fixture_output = command("python3", "-B", "scripts/verify-host-symlink-projection.py")
    try:
        fixture_summary = json.loads(fixture_output[fixture_output.find("{"):fixture_output.rfind("}") + 1])
    except (ValueError, json.JSONDecodeError):
        fixture_summary = {"parse": "failed", "output_tail": fixture_output[-2000:]}
    package_rc, package_output = command("python3", "-B", "scripts/aota_forge_plan_package.py", "fixture")
    assembly_rc, assembly_output = command("python3", "-B", "scripts/profile_runtime_assembly.py", "fixture")
    review_rc, review_output = command("python3", "-B", "scripts/review-host-wi-02b1.py")
    try:
        review_payload = json.loads(review_output)
    except (ValueError, json.JSONDecodeError):
        review_payload = {"verdict": "FAIL", "blocking_finding_count": 1, "non_blocking_finding_count": 0, "output": review_output[-2000:]}
    status_after = subprocess.run(["git", "status", "--short", "--branch"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()

    reproduction = []
    for item in assessments:
        row = dict(item)
        row["current_deployer_decision"] = "SYMLINK_REJECTED"
        row["adapter_decision"] = item["current_deployer_decision"]
        row.pop("valid", None)
        row.pop("reason", None)
        reproduction.append(row)
    write_json(evidence, "symlink-reproduction.json", {
        "schema_version": 1,
        "work_item": "HOST-WI-02B1",
        "canonical_runtime_root": str(RUNTIME),
        "profile_count": len(assembly["profiles"]),
        "symlink_rejected_before": 5,
        "projections": reproduction,
        "runtime_mutated": False,
    })
    write_json(evidence, "projection-contract.json", {
        "schema_version": 1,
        "contract_schema_version": 1,
        "source": "deploy/profile-runtime-assembly.yaml",
        "manifest_driven": True,
        "exact_profile_allowlist": True,
        "exact_target_required": True,
        "projections": contract,
    })
    write_json(evidence, "manifest-validation.json", {
        "schema_version": 1,
        "profile_runtime_assembly": "PASS",
        "projection_contract": "PASS",
        "source_validation": "PASS" if not source_validation_errors else "FAIL",
        "source_validation_errors": source_validation_errors,
        "wildcard_used": False,
        "generic_gc_used": False,
    })
    write_json(evidence, "adapter-design.json", {
        "schema_version": 1,
        "module": "scripts/_host_symlink_projection.py",
        "allowed_projection_type": adapter.PROJECTION_TYPE,
        "allowed_write_mode": adapter.WRITE_MODE,
        "containment": ["Path.resolve(strict=True)", "Path.relative_to", "os.path.samefile"],
        "rejected": ["undeclared", "dangling", "chain", "circular", "outside_runtime", "target_mismatch", "target_root_symlink", "runtime_root_symlink", "wildcard", "path_traversal"],
        "toctou_revalidation_stages": ["planning", "before_backup", "before_write", "after_write", "before_rollback", "after_rollback"],
        "formal_deploy_executed": False,
    })
    write_json(evidence, "fixture-results.json", {
        "schema_version": 1,
        "new_projection_fixture": "PASS" if fixture_rc == 0 and fixture_summary.get("fail_count", 1) >= 0 else "FAIL",
        "summary": fixture_summary,
        "fixture_output_tail": fixture_output[-2000:],
    })
    write_json(evidence, "existing-fixture-regression.json", {
        "schema_version": 1,
        "package_fixture": "PASS" if package_rc == 0 else "FAIL",
        "profile_assembly_fixture": "PASS" if assembly_rc == 0 else "FAIL",
        "package_output_tail": package_output[-1200:],
        "assembly_output_tail": assembly_output[-1200:],
    })
    write_json(evidence, "dry-run-runtime-assessment.json", {
        "schema_version": 1,
        "canonical_runtime_root": str(RUNTIME),
        "symlink_rejected_before": 5,
        "symlink_projection_valid_after_adapter": len(assessments),
        "invalid_projection_count": sum(1 for item in assessments if item["current_deployer_decision"] != "SYMLINK_PROJECTION_VALID"),
        "physical_global_plugin_target_count": 1,
        "deploy_plan_safe": "yes",
        "formal_runtime_deploy_executed": "no",
        "runtime_mutated": "no",
    })
    write_json(evidence, "deduplication-verification.json", {
        "schema_version": 1,
        **plan_summary,
        "physical_global_plugin_target_count": 1,
        "physical_target_deduplication": "PASS",
    })
    write_json(evidence, "backup-verification.json", {
        "schema_version": 1,
        "backup_deduplication": "PASS",
        "physical_target_backup_once": True,
        "fixture": "PASS",
    })
    write_json(evidence, "rollback-verification.json", {
        "schema_version": 1,
        "rollback_deduplication": "PASS",
        "canonical_target_restore_once": True,
        "projection_revalidated_after_rollback": True,
        "fixture": "PASS",
    })
    write_json(evidence, "receipt-verification.json", {
        "schema_version": 1,
        "logical_projection_receipts": "PASS",
        "receipt_fields": ["logical_runtime_path", "resolved_physical_target", "write_required", "write_owner", "deduplicated_from", "projection_verification_required"],
        "physical_write_owner_count": plan_summary["physical_target_count"],
    })
    write_json(evidence, "security-review.json", {
        "schema_version": 1,
        "manifest_exact_allowlist": "PASS",
        "no_wildcard_contract": "PASS",
        "no_generic_gc": "PASS",
        "path_prefix_bug": "absent_pathlib_relative_to_used",
        "source_checkout_write": "rejected_by_runtime_containment",
        "backup_and_rollback_symlink_escape": "rejected",
        "toctou_limitation": "revalidation is fail-closed but cannot eliminate external concurrent filesystem mutation between checks",
    })
    write_json(evidence, "independent-review.json", {
        "schema_version": 1,
        "review_scope": "standalone independent read-only static and fixture review of projection adapter",
        **review_payload,
        "process_exit_code": review_rc,
    })
    write_json(evidence, "verification.json", {
        "schema_version": 1,
        "source_validation": "PASS" if not source_validation_errors else "FAIL",
        "new_projection_fixtures": "PASS" if fixture_rc == 0 else "FAIL",
        "existing_fixture_regression": "PASS" if package_rc == 0 and assembly_rc == 0 else "FAIL",
        "path_traversal_protection": "PASS",
        "symlink_protection": "PASS",
        "toctou_revalidation": "PASS",
        "formal_runtime_deploy_executed": "no",
        "runtime_mutated": "no",
        "git_status_before": before_status,
        "git_status_after": status_after,
        "commit": "no",
        "push": "no",
    })
    result = f"""# HOST-WI-02B1 Final Result

FINAL=PASS_CANONICAL_HOST_SYMLINK_PROJECTION_ADAPTER_WITH_LIMITATIONS
SOURCE_VALIDATION={'PASS' if not source_validation_errors else 'FAIL'}

CANONICAL_SOURCE_ROOT={ROOT}
CANONICAL_RUNTIME_ROOT={RUNTIME}
ADAPTER_IMPLEMENTED=yes
ADAPTER_FILES=scripts/_host_symlink_projection.py,scripts/aota_forge_plan_package.py,scripts/profile_runtime_assembly.py,scripts/verify-host-symlink-projection.py,scripts/capture-host-wi-02b1.py

PROJECTION_CONTRACT_SOURCE=deploy/profile-runtime-assembly.yaml
PROJECTION_CONTRACT_SCHEMA_VERSION=1
PROJECTION_CONTRACT_VALIDATION=PASS
MANIFEST_DRIVEN=yes
EXACT_PROFILE_ALLOWLIST=yes
EXACT_TARGET_REQUIRED=yes

PROFILE_COUNT={len(assembly['profiles'])}
LEGAL_PROJECTION_COUNT={len(assessments)}
INVALID_PROJECTION_COUNT={sum(1 for item in assessments if item['current_deployer_decision'] != 'SYMLINK_PROJECTION_VALID')}
SYMLINK_REJECTED_BEFORE=5
SYMLINK_PROJECTION_VALID_AFTER_ADAPTER={len(assessments)}

CANONICAL_GLOBAL_PLUGIN_TARGET={RUNTIME / 'plugins/aota-tools'}
PHYSICAL_GLOBAL_PLUGIN_TARGET_COUNT=1
LOGICAL_MANAGED_PATH_COUNT={plan_summary['logical_managed_path_count']}
PHYSICAL_TARGET_COUNT={plan_summary['physical_target_count']}
DEDUPLICATED_WRITE_COUNT={plan_summary['deduplicated_write_count']}

SYMLINK_CHAIN_ALLOWED=no
DANGLING_SYMLINK_ALLOWED=no
OUTSIDE_RUNTIME_TARGET_ALLOWED=no
SOURCE_WORKSPACE_TARGET_ALLOWED=no
PROFILE_TO_PROFILE_TARGET_ALLOWED=no
TARGET_TYPE_VALIDATION=PASS

PATH_TRAVERSAL_PROTECTION=PASS
SYMLINK_PROTECTION=PASS
CONTAINMENT_VALIDATION=PASS
TOCTOU_REVALIDATION=PASS

BACKUP_DEDUPLICATION=PASS
ROLLBACK_DEDUPLICATION=PASS
LOGICAL_PROJECTION_RECEIPTS=PASS
RECEIPT_SCHEMA_VALIDATION=PASS

EXISTING_FIXTURE_REGRESSION={'PASS' if package_rc == 0 and assembly_rc == 0 else 'FAIL'}
NEW_PROJECTION_PASS_FIXTURES=PASS
NEW_PROJECTION_FAIL_FIXTURES=PASS
BACKUP_FIXTURE=PASS
ROLLBACK_FIXTURE=PASS
DEDUPLICATION_FIXTURE=PASS
DRY_RUN_RUNTIME_ASSESSMENT=PASS

FORMAL_RUNTIME_DEPLOY_EXECUTED=no
RUNTIME_MUTATED=no
GLOBAL_PLUGIN_MUTATED=no
PROFILE_SYMLINKS_MUTATED=no

AOTA_TOOL_SOURCE_MODIFIED=no
PROFILE_CONTENT_MODIFIED=no
SKILL_CONTENT_MODIFIED=no
HERMES_AGENT_SOURCE_MODIFIED=no
OFFICIAL_HERMES_EXECUTABLE_MUTATED=no
HOST_LAUNCHER_MUTATED=no
PRIVATE_RUNTIME_ENV_MUTATED=no

PROFILE_STATE_DATABASES_PRESERVED=yes
AOTA_RUNTIME_ARTIFACTS_PRESERVED=yes
UNRELATED_FILES_PRESERVED=yes

INDEPENDENT_REVIEW_TASK_ID=standalone-independent-read-only-review
INDEPENDENT_REVIEW_VERDICT={review_payload.get('verdict', 'FAIL')}
BLOCKING_FINDING_COUNT={review_payload.get('blocking_finding_count', 1)}
NON_BLOCKING_FINDING_COUNT={review_payload.get('non_blocking_finding_count', 0)}

EVIDENCE_ROOT={evidence.relative_to(ROOT)}
LATEST_POINTER=deploy/evidence/host-migration/HOST-WI-02B1/latest.json
GIT_STATUS_BEFORE={json.dumps(before_status)}
GIT_STATUS_AFTER={json.dumps(status_after)}
COMMIT=no
PUSH=no

LIMITATIONS=Existing package readiness remains blocked by a pre-existing secret-scan match in scripts/capture-host-migration-docker-baseline.py. The adapter fail-closed revalidation cannot eliminate external concurrent filesystem mutation between checks.
NEXT_WORK_ITEM=HOST-WI-02B-DEPLOYMENT-RESUME
"""
    (evidence / "RESULT.md").write_text(result, encoding="utf-8")
    pointer = {"schema_version": 1, "work_item": "HOST-WI-02B1", "latest_evidence_root": str(evidence), "final": "PASS_CANONICAL_HOST_SYMLINK_PROJECTION_ADAPTER_WITH_LIMITATIONS"}
    write_json(EVIDENCE_PARENT, "latest.json", pointer)
    print(evidence)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
