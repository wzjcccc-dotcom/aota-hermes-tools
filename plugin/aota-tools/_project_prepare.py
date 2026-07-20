"""Read-only Unified Project Brief projection for PCF-WI-04."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from ._project_common import MAX_CARD_BYTES, ProjectError, json_result, load_observed, load_project, project_card
from ._project_registry import REGISTRY_FILE, _read_existing, _safe_registry_path
from ._workspace import WorkspaceError, resolve_workspace
from ._codegraph_readonly import CodegraphError, resolve_codegraph_executable
from ._codegraph_lifecycle import derive_codegraph_lifecycle

TOOL_NAME = "aota_project_prepare"
TOOLSET_NAME = "aota_project_readonly"
MAX_BRIEF_BYTES = 32 * 1024
MAX_HARD_BRIEF_BYTES = 64 * 1024
MAX_WARNINGS = 20
_PROJECT_ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

SCHEMA = {
    "name": TOOL_NAME,
    "description": "Build a bounded, deterministic Unified Project Brief from one registered workspace and exact project ID. Revalidates the canonical manifest; never executes commands, refreshes Registry, or writes files.",
    "parameters": {
        "type": "object",
        "properties": {
            "workspace_id": {"type": "string"},
            "project_id": {"type": "string"},
            "include_commands": {"type": "boolean", "default": True},
            "include_constraints": {"type": "boolean", "default": True},
            "include_plan_reference": {"type": "boolean", "default": True},
            "max_warnings": {"type": "integer", "minimum": 0, "maximum": MAX_WARNINGS, "default": MAX_WARNINGS},
        },
        "required": ["workspace_id", "project_id"],
        "additionalProperties": False,
    },
}


def _error(code: str, workspace_id: str = "", project_id: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {"status": "error", "error_code": code}
    if workspace_id:
        result["workspace_id"] = workspace_id
    if project_id:
        result["project_id"] = project_id[:96]
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 512 or "\x00" in value:
        return None
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts or "\\" in value:
        return None
    return candidate.as_posix()


def _resolve_file(root: Path, relative: str) -> Path | None:
    candidate = root / relative
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    if candidate.is_symlink() or not resolved.is_file():
        return None
    return resolved


def _has_symlink_component(root: Path, relative: str) -> bool:
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            return True
        if not current.exists():
            return False
    return False


def _registry_record(root: Path, workspace_id: str, project_id: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    try:
        path = _safe_registry_path(root)
        existing, error = _read_existing(path)
    except (WorkspaceError, OSError):
        return None, {"status": "invalid", "revision": None, "source_fingerprint": None}
    if error or existing is None:
        return None, {"status": "missing" if error == "registry_missing" else "invalid", "revision": None, "source_fingerprint": None}
    if existing.get("workspace_id") != workspace_id:
        return None, {"status": "invalid", "revision": existing.get("registry_revision"), "source_fingerprint": existing.get("source_fingerprint")}
    record = next((item for item in existing["projects"] if item.get("project_id") == project_id), None)
    return record, {"status": "fresh" if record else "stale", "revision": existing["registry_revision"], "source_fingerprint": existing["source_fingerprint"]}


def _manifest_from_record(root: Path, record: dict[str, Any] | None) -> Path | None:
    relative = _safe_relative(record.get("manifest_path")) if record else ".aota/project.yaml"
    if relative is None or not relative.endswith("/.aota/project.yaml") and relative != ".aota/project.yaml":
        return None
    return _resolve_file(root, relative)


def _path_projection(project_root: Path, paths: dict[str, Any]) -> tuple[dict[str, list[str]], list[str]]:
    result: dict[str, list[str]] = {}
    warnings: list[str] = []
    for category, value in paths.items():
        values = [value] if category == "source_root" else value
        projected: list[str] = []
        for item in values:
            if _has_symlink_component(project_root, item):
                warnings.append(f"declared_path_invalid:{category}:{item}")
            else:
                projected.append(item)
                if not (project_root / item).exists():
                    warnings.append(f"declared_path_missing:{category}:{item}")
        result[category] = projected
    return result, warnings


def _commands(data: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    return {
        category: [
            {"value": value, "source": "declared", "execution_status": "not_executed"}
            for value in values
        ]
        for category, values in data["commands"].items()
    }


def _plan(data: dict[str, Any], observed: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    declared = data["plan"]["active_plan_id"]
    observed_plan = observed.get("plan") if observed else None
    observed_id = observed_plan.get("active_plan_id") if isinstance(observed_plan, dict) else None
    warnings: list[str] = []
    if declared is None and observed_id is None:
        status = "not_linked"
    elif observed is None:
        status = "declared" if declared else "reference_not_verified"
    elif declared == observed_id:
        status = "declared_observed_match"
    else:
        status = "declared_observed_conflict"
        warnings.append("observed_active_plan_conflict")
    return {
        "active_plan_id": declared,
        "observed_revision": observed_plan.get("revision") if isinstance(observed_plan, dict) else None,
        "observed_sha256": observed_plan.get("sha256") if isinstance(observed_plan, dict) else None,
        "status": status,
    }, warnings


def _trim(payload: dict[str, Any]) -> dict[str, Any]:
    def encoded() -> int:
        return len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    if encoded() <= MAX_BRIEF_BYTES:
        return payload
    payload["truncated"] = True
    warnings = payload["warnings"]
    while warnings and encoded() > MAX_BRIEF_BYTES:
        warnings.pop()
    commands = payload.get("commands", {})
    for values in commands.values():
        while values and encoded() > MAX_BRIEF_BYTES:
            values.pop()
    for values in payload["relevant_paths"].values():
        while values and encoded() > MAX_BRIEF_BYTES:
            values.pop()
    if encoded() > MAX_HARD_BRIEF_BYTES:
        raise ProjectError("brief_generation_failed", "brief exceeds hard cap")
    return payload


def _do_prepare(args: dict[str, Any]) -> dict[str, Any]:
    workspace_id = args.get("workspace_id", "")
    project_id = args.get("project_id", "")
    if not isinstance(workspace_id, str) or not workspace_id:
        return _error("workspace_not_found")
    if not isinstance(project_id, str) or not _PROJECT_ID_RE.fullmatch(project_id):
        return _error("project_not_found", workspace_id, project_id if isinstance(project_id, str) else "")
    optional = {key: args.get(key, True) for key in ("include_commands", "include_constraints", "include_plan_reference")}
    if not all(isinstance(value, bool) for value in optional.values()):
        return _error("brief_generation_failed", workspace_id, project_id)
    max_warnings = args.get("max_warnings", MAX_WARNINGS)
    if not isinstance(max_warnings, int) or isinstance(max_warnings, bool) or not 0 <= max_warnings <= MAX_WARNINGS:
        return _error("brief_generation_failed", workspace_id, project_id)
    try:
        root = resolve_workspace(workspace_id)
        record, registry = _registry_record(root, workspace_id, project_id)
        manifest = _manifest_from_record(root, record)
        if manifest is None:
            return _error("project_not_found" if record is None else "manifest_missing", workspace_id, project_id)
        project_root, data = load_project(manifest)
        if data["project"]["id"] != project_id:
            return _error("project_not_found", workspace_id, project_id)
        manifest_sha = _sha256(manifest)
        warnings: list[str] = []
        if record is not None and record.get("manifest_sha256") != manifest_sha:
            registry["status"] = "stale"
            warnings.append("registry_manifest_mismatch")
        observed = None
        observed_status = "missing"
        observed_path = project_root / ".aota" / "observed.json"
        if observed_path.exists():
            try:
                observed = load_observed(observed_path)
                observed_status = "valid"
            except ProjectError:
                observed_status = "invalid"
                warnings.append("observed_invalid")
        card = project_card(project_root, data, observed)
        relevant_paths, path_warnings = _path_projection(project_root, data["paths"])
        plan, plan_warnings = _plan(data, observed)
        warnings.extend(card["warnings"] + path_warnings + plan_warnings)
        if observed is not None and observed["project_id"] != project_id:
            warnings.append("observed_project_id_conflict")
        manifest_relative = manifest.relative_to(root).as_posix()
        root_relative = project_root.relative_to(root).as_posix() if project_root != root else "."
        codegraph_enabled = bool(data["codegraph"]["enabled"])
        index = project_root / data["codegraph"]["index_location"]
        if not codegraph_enabled:
            codegraph_readiness = "not_configured"
        elif not index.is_dir():
            codegraph_readiness = "not_initialized"
        else:
            # Project Brief is static by contract; live lifecycle belongs to the
            # explicit aota_codegraph_lifecycle_status tool.
            codegraph_readiness = "lifecycle_not_observed"
        payload: dict[str, Any] = {
            "status": "ok", "schema_version": 1, "workspace_id": workspace_id, "project_id": project_id,
            "project": {"id": project_id, "name": data["project"]["name"], "kind": data["project"]["kind"], "status": data["project"]["status"], "summary": data["summary"]},
            "identity": {"workspace_id": workspace_id, "root": root_relative, "manifest_path": manifest_relative, "manifest_sha256": manifest_sha},
            "registry": {"status": registry["status"], "revision": registry["revision"], "source_fingerprint": registry["source_fingerprint"], "source": "registry" if record else "canonical_manifest"},
            "capabilities": list(data["capabilities"]), "relevant_paths": relevant_paths,
            "readiness": {"manifest": "ready", "registry": registry["status"], "project_card": "ready", "observed": "ready" if observed_status == "valid" else ("not_observed" if observed_status == "missing" else "invalid"), "git": "observed" if observed else "not_observed", "codegraph": codegraph_readiness, "plan": plan["status"], "deployment": "requires_human_checkpoint" if data["runtime"]["requires_human_checkpoint"] else "declared", "runtime": "not_verified"},
            "warnings": sorted(set(warnings))[:max_warnings],
            "evidence": {"manifest": {"path": manifest_relative, "sha256": manifest_sha}, "registry": {"status": registry["status"], "revision": registry["revision"], "source_fingerprint": registry["source_fingerprint"]}, "project_card": {"schema_version": 1}, "observed": {"present": observed_status != "missing", "validation_status": observed_status}},
            "truncated": False,
        }
        if optional["include_commands"]:
            payload["commands"] = _commands(data)
        if optional["include_constraints"]:
            payload["constraints"] = list(data["constraints"])
        if optional["include_plan_reference"]:
            payload["plan"] = plan
        return _trim(payload)
    except WorkspaceError:
        return _error("workspace_not_found", workspace_id, project_id)
    except ProjectError as exc:
        return _error(exc.code if exc.code in {"manifest_invalid", "observed_invalid", "brief_generation_failed"} else "brief_generation_failed", workspace_id, project_id)
    except (OSError, ValueError):
        return _error("brief_generation_failed", workspace_id, project_id)


def handle(args: dict, **_kwargs: Any) -> str:
    """Return one bounded Unified Project Brief without side effects."""
    return json_result(_do_prepare(args))


def run_isolated_smoke() -> dict[str, str]:
    """Exercise prepare against a temporary registered workspace only."""
    from . import _workspace
    with tempfile.TemporaryDirectory(prefix="pcf-prepare-") as raw:
        root = Path(raw) / "workspace"
        manifest = root / ".aota" / "project.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("""schema_version: 1
project: {id: fixture-project, name: Fixture, kind: hermes-tooling, status: active}
summary: Bounded fixture
capabilities: [hermes-plugin]
paths: {source_root: ., source: [plugin/], docs: [], scripts: [], profiles: [], skills: [], tests: []}
commands: {validate: [scripts/verify.py], deploy: [], verify_deploy: []}
runtime: {deployment_type: files, requires_human_checkpoint: true}
codegraph: {enabled: false, index_location: .codegraph/}
plan: {active_plan_id: null}
constraints: [source-only]
""", encoding="utf-8")
        (root / "plugin").mkdir()
        (root / "scripts").mkdir()
        registry_path = root / REGISTRY_FILE
        registry_path.parent.mkdir(parents=True)
        sha = _sha256(manifest)
        registry_path.write_text(json.dumps({"schema_version": 1, "workspace_id": "fixture", "registry_revision": 1, "generated_at": "2026-07-15T00:00:00Z", "source_fingerprint": "0" * 64, "project_count": 1, "projects": [{"project_id": "fixture-project", "root": ".", "manifest_path": ".aota/project.yaml", "manifest_sha256": sha, "name": "Fixture", "kind": "hermes-tooling", "status": "active", "summary": "Bounded fixture", "capabilities": ["hermes-plugin"], "keywords": [], "relevant_paths": {}, "commands": {}, "active_plan_id": None, "constraints": [], "warnings": []}], "invalid_projects": [], "duplicate_project_ids": [], "scan_warnings": []}), encoding="utf-8")
        previous = _workspace._REGISTRY_PATH
        previous_env = os.environ.get("AOTA_WORKSPACE_REGISTRY_PATH")
        fixture_registry = Path(raw) / "workspaces.json"
        fixture_registry.write_text(json.dumps({"fixture": {"candidates": [str(root)]}}), encoding="utf-8")
        _workspace._REGISTRY_PATH = fixture_registry
        os.environ["AOTA_WORKSPACE_REGISTRY_PATH"] = str(fixture_registry)
        try:
            first = json.loads(handle({"workspace_id": "fixture", "project_id": "fixture-project"}))
            second = json.loads(handle({"workspace_id": "fixture", "project_id": "fixture-project"}))
            assert first == second and first["status"] == "ok"
            assert first["commands"]["validate"][0]["execution_status"] == "not_executed"
            assert first["readiness"]["codegraph"] in {"not_configured", "not_initialized", "lifecycle_not_observed"}
            manifest.write_text(manifest.read_text(encoding="utf-8").replace("Bounded fixture", "Changed fixture"), encoding="utf-8")
            stale = json.loads(handle({"workspace_id": "fixture", "project_id": "fixture-project"}))
            assert stale["registry"]["status"] == "stale" and "registry_manifest_mismatch" in stale["warnings"]
        finally:
            _workspace._REGISTRY_PATH = previous
            if previous_env is None:
                os.environ.pop("AOTA_WORKSPACE_REGISTRY_PATH", None)
            else:
                os.environ["AOTA_WORKSPACE_REGISTRY_PATH"] = previous_env
    return {"status": "PASS", "marker": "PCF_PREPARATION_SMOKE_PASS"}
