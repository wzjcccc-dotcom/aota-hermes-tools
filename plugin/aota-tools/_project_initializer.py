"""PCF-WI-NEW-PROJECT-INITIALIZATION-CORE: bounded, idempotent, fail-closed
project initialization core tool.

Creates the canonical project scaffold (.aota/project.yaml, .aota/observed.json,
source/docs/scripts directories), registers the project in the workspace
registry, and produces an initialized_core observation receipt.  Does NOT
perform Git init/commit, CodeGraph init/index, deploy, or runtime activation.

Follows the trusted steward pattern from _project_steward_mutation.py: env-based
task binding, frozen SPEC validation, profile=project-steward enforcement.
Receipt authority remains trusted_finalizer; the steward only produces
observations, never self-declares authoritative success.
"""

from __future__ import annotations

import datetime as _dt
import fcntl
import json
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from ._project_common import (
    MAX_SUMMARY,
    PROJECT_ID_RE,
    ProjectError,
    load_observed,
    load_project,
    validate_project,
)
from ._project_lifecycle_contract import (
    INITIALIZED_CORE,
    validate_initialization_receipt,
)
from ._project_registry import (
    LOCK_FILE,
    _atomic_write as _registry_atomic_write,
    _build_registry,
    _read_existing,
    _safe_registry_path,
    _scan_sources,
)
from ._spec_contract import ContractError, canonical_hash, validate_spec
from ._workspace import WorkspaceError, resolve_workspace

TOOL_NAME = "aota_project_initialize_core"
TOOLSET_NAME = "aota_project_steward"

_SCAFFOLD_DIRS = ("docs", "scripts", "profiles", "skills", "tests")

INITIALIZE_SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Trusted Project Steward-only bounded project initialization core. "
        "Creates scaffold, project.yaml, observed.json, and registers in "
        "workspace registry.  No Git/CodeGraph/deploy/runtime activation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {"type": "string", "maxLength": 128},
            "project_id": {"type": "string", "maxLength": 96},
            "project_name": {"type": "string", "maxLength": 200},
            "project_kind": {"type": "string", "maxLength": 96},
            "project_root": {"type": "string", "maxLength": 512},
            "summary": {"type": "string", "maxLength": 4000},
            "capabilities": {
                "type": "array",
                "items": {"type": "string", "maxLength": 96},
                "maxItems": 32,
            },
            "source_root": {"type": "string", "maxLength": 512},
        },
        "required": [
            "workspace_id",
            "project_id",
            "project_name",
            "project_kind",
            "project_root",
            "summary",
        ],
        "additionalProperties": False,
    },
}


def _safe_text(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise ValueError(f"invalid {name}")
    return value


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _trusted_steward_init(workspace_id: str, project_id: str) -> tuple[Path, dict[str, Any]]:
    """Validate trusted steward binding for project initialization.

    Follows the pattern from _project_steward_mutation._trusted_steward but
    adapted for initialization (project does not exist yet, so we cannot
    look it up in the registry or load its manifest).
    """
    if os.environ.get("AOTA_PROFILE_TASK_PROFILE") != "project-steward":
        raise PermissionError("profile_not_allowed")
    task_id = os.environ.get("AOTA_PROFILE_TASK_ID", "")
    start_id = os.environ.get("AOTA_PROFILE_TASK_START_ID", "")
    trusted_workspace = os.environ.get("AOTA_PROFILE_TASK_WORKSPACE_ID", "")
    if not task_id or not start_id or workspace_id != trusted_workspace:
        raise PermissionError("trusted_task_context_mismatch")
    task_root = Path(os.environ.get("AOTA_PROFILE_TASK_ROOT", "/aota-runtime/profile-tasks"))
    try:
        meta = json.loads((task_root / workspace_id / task_id / "meta.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("task_meta_unavailable") from exc
    spec = meta.get("spec", {})
    if meta.get("contract_version") == 1:
        try:
            validate_spec(spec, frozen=True)
        except ContractError as exc:
            raise PermissionError("invalid_stewardship_spec") from exc
        if meta.get("spec_hash") != spec.get("spec_hash") or canonical_hash(spec) != spec.get("spec_hash"):
            raise PermissionError("stewardship_spec_hash_mismatch")
    else:
        raise PermissionError("invalid_stewardship_spec")
    if meta.get("status") != "running":
        raise PermissionError("stewardship_spec_binding_mismatch")
    if meta.get("spec_kind", meta.get("task_kind")) != "stewardship":
        raise PermissionError("stewardship_spec_binding_mismatch")
    if meta.get("resolved_profile", "project-steward") != "project-steward":
        raise PermissionError("profile_not_allowed")
    if meta.get("execution", {}).get("start_id") != start_id:
        raise PermissionError("stewardship_spec_binding_mismatch")
    if spec.get("project_id") != project_id:
        raise PermissionError("stewardship_spec_binding_mismatch")
    root = resolve_workspace(workspace_id)
    return root, spec


def _resolve_project_root(workspace_root: Path, project_root: str) -> Path:
    """Resolve and validate the project root path within the workspace."""
    if not isinstance(project_root, str) or not project_root or "\\" in project_root or "\x00" in project_root:
        raise ValueError("invalid_project_root")
    path = PurePosixPath(project_root)
    if path.is_absolute() or ".." in path.parts or path == PurePosixPath("."):
        raise ValueError("invalid_project_root")
    target = workspace_root.joinpath(*path.parts)
    if target.is_symlink():
        raise ValueError("symlink_rejected")
    try:
        target.resolve(strict=False).relative_to(workspace_root.resolve())
    except ValueError as exc:
        raise ValueError("path_escape") from exc
    return target


def _check_root_state(project_root: Path) -> str:
    """Check the state of the project root.

    Returns one of:
    - "not_exists": root does not exist (or is an empty directory)
    - "initialized_core": root has complete scaffold with valid project.yaml and observed.json
    - "partial_scaffold": root exists but has incomplete scaffold
    - "non_empty": root exists with non-scaffold content
    - "symlink_rejected": root is a symlink
    """
    if not project_root.exists():
        return "not_exists"
    if project_root.is_symlink():
        return "symlink_rejected"
    if not project_root.is_dir():
        return "non_empty"

    has_project_yaml = (project_root / ".aota" / "project.yaml").is_file()
    has_observed_json = (project_root / ".aota" / "observed.json").is_file()

    if has_project_yaml and has_observed_json:
        try:
            _, manifest = load_project(project_root / ".aota" / "project.yaml")
            load_observed(project_root / ".aota" / "observed.json")
            for dir_name in _SCAFFOLD_DIRS:
                if not (project_root / dir_name).is_dir():
                    return "partial_scaffold"
            source_root = manifest["paths"]["source_root"]
            if not (project_root / source_root).is_dir():
                return "partial_scaffold"
            return "initialized_core"
        except ProjectError:
            return "partial_scaffold"

    has_aota = (project_root / ".aota").is_dir()
    has_any_scaffold_dir = any((project_root / d).is_dir() for d in _SCAFFOLD_DIRS)

    if has_aota or has_any_scaffold_dir:
        return "partial_scaffold"

    try:
        entries = list(project_root.iterdir())
    except OSError:
        return "non_empty"

    if not entries:
        return "not_exists"

    return "non_empty"


def _generate_project_yaml(
    project_id: str,
    project_name: str,
    project_kind: str,
    summary: str,
    capabilities: list[str],
    source_root: str,
) -> str:
    """Generate the project.yaml content."""
    data = {
        "schema_version": 1,
        "project": {
            "id": project_id,
            "name": project_name,
            "kind": project_kind,
            "status": "planned",
        },
        "summary": summary,
        "capabilities": capabilities,
        "paths": {
            "source_root": source_root,
            "source": [source_root],
            "docs": ["docs"],
            "scripts": ["scripts"],
            "profiles": ["profiles"],
            "skills": ["skills"],
            "tests": ["tests"],
        },
        "commands": {
            "validate": ["scripts/validate.py"],
            "deploy": ["scripts/deploy.py"],
            "verify_deploy": ["scripts/verify-deploy.py"],
        },
        "runtime": {
            "deployment_type": "manual",
            "requires_human_checkpoint": True,
        },
        "codegraph": {
            "enabled": False,
            "index_location": ".codegraph",
        },
        "plan": {
            "active_plan_id": None,
        },
        "constraints": [],
    }
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)


def _generate_observed_json(project_id: str) -> str:
    """Generate the observed.json content."""
    data = {
        "schema_version": 1,
        "project_id": project_id,
        "observed_at": _now(),
        "filesystem": {
            "root_exists": True,
            "manifest_exists": True,
        },
        "git": {
            "available": False,
            "branch": "",
            "head": "",
            "dirty": False,
        },
        "codegraph": {
            "configured": False,
            "index_present": False,
            "runtime_verified": False,
        },
        "plan": {
            "active_plan_id": None,
            "revision": None,
            "sha256": None,
        },
    }
    return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _create_scaffold(
    project_root: Path,
    project_id: str,
    project_name: str,
    project_kind: str,
    summary: str,
    capabilities: list[str],
    source_root: str,
) -> list[str]:
    """Create the canonical project scaffold.

    Returns a list of created relative paths (relative to project_root).
    """
    changed_paths: list[str] = []

    aota_dir = project_root / ".aota"
    aota_dir.mkdir(parents=True, exist_ok=True)

    for dir_name in _SCAFFOLD_DIRS:
        dir_path = project_root / dir_name
        if not dir_path.exists():
            dir_path.mkdir(parents=True, exist_ok=True)
            changed_paths.append(dir_name)

    source_dir = project_root / source_root
    if not source_dir.exists():
        source_dir.mkdir(parents=True, exist_ok=True)
        changed_paths.append(source_root)

    project_yaml_content = _generate_project_yaml(
        project_id, project_name, project_kind, summary, capabilities, source_root
    )
    project_yaml_path = project_root / ".aota" / "project.yaml"
    _atomic_write(project_yaml_path, project_yaml_content)
    changed_paths.append(".aota/project.yaml")

    # Validate project.yaml using existing validator
    _, manifest = load_project(project_yaml_path)
    validate_project(manifest, project_root)

    observed_json_content = _generate_observed_json(project_id)
    observed_json_path = project_root / ".aota" / "observed.json"
    _atomic_write(observed_json_path, observed_json_content)
    changed_paths.append(".aota/observed.json")

    # Validate observed.json using existing validator
    load_observed(observed_json_path)

    return changed_paths


def _register_in_registry(
    workspace_id: str,
    workspace_root: Path,
    project_id: str,
    project_root: Path,
) -> dict[str, Any]:
    """Register the project in the workspace registry.

    Reuses existing _project_registry.py registry writer.  Performs atomic
    write with lock, duplicate detection, and exact-match verification.
    """
    reg_path = _safe_registry_path(workspace_root)
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = workspace_root / LOCK_FILE

    with lock_path.open("a+") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ProjectError("registry_lock_timeout")

        previous, _previous_error = _read_existing(reg_path)
        registry = _build_registry(workspace_id, workspace_root, previous)

        # Duplicate detection
        if project_id in registry.get("duplicate_project_ids", []):
            raise ValueError("duplicate_project_id")

        # Find the new project in the registry
        record = next(
            (item for item in registry.get("projects", []) if item.get("project_id") == project_id),
            None,
        )
        if record is None:
            raise ProjectError("registry_registration_failed")

        # Exact-match verification
        relative_root = project_root.relative_to(workspace_root).as_posix()
        if record["root"] != relative_root:
            raise ProjectError("registry_exact_match_failure")

        manifest_path = project_root / ".aota" / "project.yaml"
        expected_manifest_path = (workspace_root / record["manifest_path"]).resolve()
        actual_manifest_path = manifest_path.resolve()
        if expected_manifest_path != actual_manifest_path:
            raise ProjectError("registry_exact_match_failure")

        # Atomic write with lock
        _registry_atomic_write(reg_path, registry)

    return registry


def _build_receipt(project_id: str, workspace_id: str) -> dict[str, Any]:
    """Build an initialization receipt observation.

    The receipt authority is trusted_finalizer; the steward only produces
    observations, never self-declares authoritative success.
    """
    receipt = {
        "schema_version": 1,
        "project_id": project_id,
        "workspace_id": workspace_id,
        "state": INITIALIZED_CORE,
        "git_summary": {"present": False, "status": "not_requested"},
        "codegraph_summary": {"present": False, "status": "not_requested"},
        "authority": "trusted_finalizer",
        "completed_at": _now(),
    }
    errors = validate_initialization_receipt(receipt)
    if errors:
        raise ProjectError("receipt_validation_failed", ", ".join(errors))
    return receipt


def handle_initialize_core(args: dict, **_kwargs: Any) -> str:
    """Handle aota_project_initialize_core tool invocation."""
    try:
        if not set(args).issubset(INITIALIZE_SCHEMA["parameters"]["properties"]):
            raise ValueError("unknown_field")
        workspace_id = _safe_text(args.get("workspace_id"), "workspace_id", 128)
        project_id = _safe_text(args.get("project_id"), "project_id", 96)
        if not PROJECT_ID_RE.fullmatch(project_id):
            raise ValueError("invalid_project_id")
        project_name = _safe_text(args.get("project_name"), "project_name", 200)
        project_kind = _safe_text(args.get("project_kind"), "project_kind", 96)
        if not PROJECT_ID_RE.fullmatch(project_kind):
            raise ValueError("invalid_project_kind")
        project_root = _safe_text(args.get("project_root"), "project_root", 512)
        summary = _safe_text(args.get("summary"), "summary", MAX_SUMMARY)
        capabilities = args.get("capabilities", [])
        if not isinstance(capabilities, list) or len(capabilities) > 32:
            raise ValueError("invalid_capabilities")
        for cap in capabilities:
            if not isinstance(cap, str) or not PROJECT_ID_RE.fullmatch(cap) or len(cap) > 96:
                raise ValueError("invalid_capability")
        source_root = args.get("source_root", "src")
        if not isinstance(source_root, str) or not source_root:
            raise ValueError("invalid_source_root")
        source_path = PurePosixPath(source_root)
        if source_path.is_absolute() or ".." in source_path.parts:
            raise ValueError("invalid_source_root")

        # Trusted steward binding (env-based, frozen SPEC, profile enforcement)
        workspace_root, _spec = _trusted_steward_init(workspace_id, project_id)

        # Resolve and validate project root
        project_path = _resolve_project_root(workspace_root, project_root)

        # Check root state
        state = _check_root_state(project_path)

        if state == "initialized_core":
            # Check if project_id matches existing project
            _, manifest = load_project(project_path / ".aota" / "project.yaml")
            existing_project_id = manifest["project"]["id"]
            if existing_project_id != project_id:
                raise ValueError("duplicate_root")

            # Verify project is in registry
            try:
                reg_path = _safe_registry_path(workspace_root)
                previous, reg_error = _read_existing(reg_path)
                if reg_error is not None or previous is None:
                    raise ValueError("project_not_in_registry")
                record = next(
                    (item for item in previous.get("projects", []) if item.get("project_id") == project_id),
                    None,
                )
                if record is None:
                    raise ValueError("project_not_in_registry")
                relative_root = project_path.relative_to(workspace_root).as_posix()
                if record["root"] != relative_root:
                    raise ValueError("registry_exact_match_failure")
            except ProjectError:
                raise ValueError("project_not_in_registry")

            # Idempotent success
            receipt = _build_receipt(project_id, workspace_id)
            return json.dumps({
                "status": "idempotent_success",
                "project_id": project_id,
                "workspace_id": workspace_id,
                "project_root": project_root,
                "changed_paths": [],
                "final_state": INITIALIZED_CORE,
                "git": {"status": "not_requested"},
                "codegraph": {"status": "not_requested"},
                "receipt": receipt,
                "authority": "trusted_finalizer",
            }, sort_keys=True)

        if state == "symlink_rejected":
            raise ValueError("symlink_rejected")
        if state == "partial_scaffold":
            raise ValueError("partial_scaffold")
        if state == "non_empty":
            raise ValueError("non_empty_root")
        if state != "not_exists":
            raise ValueError(f"invalid_root_state:{state}")

        # Pre-check: scan for duplicate project IDs before creating scaffold
        records, _invalid, duplicates, _ = _scan_sources(workspace_root)
        if project_id in duplicates:
            raise ValueError("duplicate_project_id")
        for record in records:
            if record.get("project_id") == project_id:
                raise ValueError("duplicate_project_id")

        # Create scaffold
        changed_paths = _create_scaffold(
            project_path,
            project_id,
            project_name,
            project_kind,
            summary,
            capabilities,
            source_root,
        )

        # Register in workspace registry (atomic write with lock, duplicate detection, exact-match)
        _register_in_registry(workspace_id, workspace_root, project_id, project_path)

        # Build receipt (observation only, authority=trusted_finalizer)
        receipt = _build_receipt(project_id, workspace_id)

        return json.dumps({
            "status": "initialized_core",
            "project_id": project_id,
            "workspace_id": workspace_id,
            "project_root": project_root,
            "changed_paths": changed_paths,
            "final_state": INITIALIZED_CORE,
            "git": {"status": "not_requested"},
            "codegraph": {"status": "not_requested"},
            "receipt": receipt,
            "authority": "trusted_finalizer",
        }, sort_keys=True)

    except (PermissionError, WorkspaceError, ProjectError, ValueError) as exc:
        return json.dumps({"status": "rejected", "error": str(exc)}, sort_keys=True)
    except Exception as exc:
        return json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True)