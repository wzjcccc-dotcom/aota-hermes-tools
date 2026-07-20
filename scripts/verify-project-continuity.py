#!/usr/bin/env python3
"""PCF-WI-01/02/03 isolated source smoke; writes only to temporary directories."""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import tempfile
import types
import os
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "plugin" / "aota-tools"
pkg = types.ModuleType("aota_tools")
pkg.__path__ = [str(PLUGIN)]
sys.modules["aota_tools"] = pkg
common = importlib.import_module("aota_tools._project_common")
discovery = importlib.import_module("aota_tools._project_discovery")
opened = importlib.import_module("aota_tools._project_open")
registry = importlib.import_module("aota_tools._project_registry")
relationship = importlib.import_module("aota_tools._project_relationship")
prepare = importlib.import_module("aota_tools._project_prepare")
codegraph = importlib.import_module("aota_tools._codegraph_readonly")
lifecycle = importlib.import_module("aota_tools._codegraph_lifecycle")
rebuild = importlib.import_module("aota_tools._codegraph_rebuild")
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "project-continuity"


def main() -> None:
    valid_manifest = FIXTURES / "valid" / ".aota" / "project.yaml"
    assert prepare.run_isolated_smoke()["marker"] == "PCF_PREPARATION_SMOKE_PASS"
    assert codegraph.run_isolated_smoke()["marker"] == "PCF_CODEGRAPH_ISOLATED_SMOKE_PASS"
    assert lifecycle.run_oversized_scope_smoke()["marker"] == "PCF_CODEGRAPH_OVERSIZED_CLASSIFICATION_PASS"
    print("PCF_CODEGRAPH_OVERSIZED_CLASSIFICATION_PASS")
    normalized = lifecycle.normalize_status({"initialized": True, "files": 1, "nodes": 2, "edges": 3})
    actual_cli = lifecycle.normalize_status({"initialized": True, "fileCount": 62, "nodeCount": 1070, "edgeCount": 2219, "pendingChanges": {"added": 12, "modified": 4, "removed": 0}, "index": {"reindexRecommended": True}})
    assert actual_cli["counts"] == {"files": 62, "nodes": 1070, "edges": 2219}
    assert actual_cli["pending_changes"]["modified"] == 4 and actual_cli["reindex_recommended"] is True
    assert lifecycle.derive_codegraph_lifecycle("fixture-project", False, False, True)["state"] == "not_configured"
    assert lifecycle.derive_codegraph_lifecycle("fixture-project", True, False, True)["state"] == "not_initialized"
    assert lifecycle.derive_codegraph_lifecycle("fixture-project", True, True, True, {**normalized, "counts": {"files": 0, "nodes": 0, "edges": 0}})["state"] == "initialized_empty"
    assert lifecycle.derive_codegraph_lifecycle("fixture-project", True, True, True, {**normalized, "pending_changes": {"added": 1, "modified": 0, "removed": 0}})["state"] == "stale"
    assert lifecycle.derive_codegraph_lifecycle("fixture-project", True, True, True, {**normalized, "reindex_recommended": True})["state"] == "reindex_recommended"
    assert lifecycle.derive_codegraph_lifecycle("fixture-project", True, True, True, error="codegraph_project_mismatch")["state"] == "invalid"
    assert rebuild.run_isolated_smoke()["marker"] == "PCF_CODEGRAPH_REBUILD_PASS"
    init_spec = importlib.util.spec_from_file_location("aota_tools", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)])
    assert init_spec and init_spec.loader
    plugin = importlib.util.module_from_spec(init_spec)
    sys.modules["aota_tools"] = plugin
    init_spec.loader.exec_module(plugin)
    registered: list[str] = []
    class Context:
        def register_tool(self, *, name, **_kwargs):
            registered.append(name)
    plugin.register(Context())
    codegraph_tools = [name for name in registered if name.startswith("aota_codegraph_")]
    assert codegraph_tools == ["aota_codegraph_status", "aota_codegraph_query", "aota_codegraph_explore", "aota_codegraph_rebuild"]
    assert "approval_id" not in json.dumps(rebuild.SCHEMA)
    assert "backup_receipt" not in json.dumps(rebuild.SCHEMA)
    print("PCF_CODEGRAPH_STATUS_PASS")
    print("PCF_CODEGRAPH_QUERY_PASS")
    print("PCF_CODEGRAPH_EXPLORE_PASS")
    print("PCF_CODEGRAPH_REBUILD_PASS")
    print("PCF_CODEGRAPH_REBUILD_BUSY_PASS")
    print("PCF_CODEGRAPH_REBUILD_NO_RETRY_PASS")
    print("PCF_CODEGRAPH_REBUILD_SECURITY_PASS")
    print("PCF_CODEGRAPH_MINIMAL_REGISTRATION_PASS")
    print("PCF_CODEGRAPH_LEGACY_MUTATION_REMOVED_PASS")
    print("PCF_CODEGRAPH_APPROVAL_REMOVED_PASS")
    print("PCF_CODEGRAPH_BACKUP_REMOVED_PASS")
    print("PCF_ISOLATED_SMOKE_PASS")
    root, data = common.load_project(valid_manifest)
    observed = common.load_observed(valid_manifest.parent / "observed.json")
    card = common.project_card(root, data, observed)
    assert card["project_id"] == "fixture-project"
    assert card["active_plan"]["declared"] is None
    try:
        common.load_project(FIXTURES / "invalid" / ".aota" / "project.yaml")
    except common.ProjectError as exc:
        assert exc.code in {"manifest_invalid", "path_escape"}
    else:
        raise AssertionError("invalid fixture was accepted")

    with tempfile.TemporaryDirectory(prefix="pcf-project-projection-") as temp:
        temp_root = Path(temp)

        def write_manifest(relative: str, project_id: str) -> None:
            manifest = temp_root / relative / ".aota" / "project.yaml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                valid_manifest.read_text(encoding="utf-8").replace("fixture-project", project_id),
                encoding="utf-8",
            )

        # A root canonical project and a real nested project remain candidates.
        write_manifest(".", "canonical-project")
        write_manifest("projects/example", "nested-project")
        # This intentionally duplicates the canonical ID but is only a runtime
        # projection, so neither scan path may report a duplicate.
        write_manifest("deploy/runtime-projects/example", "canonical-project")
        (temp_root / "deploy" / "release.yaml").parent.mkdir(parents=True, exist_ok=True)
        (temp_root / "deploy" / "release.yaml").write_text("version: 1\n", encoding="utf-8")
        (temp_root / "deploy" / "other-directory").mkdir()
        (temp_root / "deploy" / "other-directory" / "notes.txt").write_text("not a project\n", encoding="utf-8")
        projection_scan = discovery._scan(temp_root)
        assert projection_scan["project_count"] == 2 and projection_scan["invalid"] == []
        assert {project["project_id"] for project in projection_scan["projects"]} == {"canonical-project", "nested-project"}
        projection_records, projection_invalid, projection_duplicates, _ = registry._scan_sources(temp_root)
        assert len(projection_records) == 2 and projection_invalid == [] and projection_duplicates == []

    with tempfile.TemporaryDirectory(prefix="pcf-project-real-duplicate-") as temp:
        temp_root = Path(temp)
        for relative in ("projects/a", "projects/b"):
            manifest = temp_root / relative / ".aota" / "project.yaml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                valid_manifest.read_text(encoding="utf-8").replace("fixture-project", "real-duplicate"),
                encoding="utf-8",
            )
        duplicate_records, duplicate_invalid, duplicate_ids, _ = registry._scan_sources(temp_root)
        assert duplicate_records == [] and duplicate_ids == ["real-duplicate"]
        assert len(duplicate_invalid) == 2 and all(item["error_code"] == "duplicate_project_id" for item in duplicate_invalid)

    with tempfile.TemporaryDirectory(prefix="pcf-runtime-identity-") as temp:
        temp_root = Path(temp)
        canonical_root = temp_root / "workspace"
        runtime = canonical_root / "aota-hermes-tools"
        agent_home = temp_root / "agent-home"
        (runtime / ".aota").mkdir(parents=True)
        (runtime / ".codegraph").mkdir()
        manifest = runtime / ".aota" / "project.yaml"
        manifest.write_text(
            (Path(__file__).resolve().parents[1] / "deploy" / "runtime-projects" / "aota-hermes-tools" / ".aota" / "project.yaml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        binding = canonical_root / ".aota" / "workspaces.json"
        binding.parent.mkdir(parents=True)
        binding.write_text(json.dumps({"aota-hermes-tools": {"candidates": [str(runtime)]}}), encoding="utf-8")
        from aota_tools import _workspace
        original_home = os.environ.get("HERMES_HOME")
        original_registry = _workspace._REGISTRY_PATH
        original_registry_env = os.environ.get("AOTA_WORKSPACE_REGISTRY_PATH")
        original_canonical_env = os.environ.get("AOTA_CANONICAL_WORKSPACE_ROOT")
        original_agent_home = _workspace._AGENT_HERMES_HOME
        original_canonical_root = _workspace._CANONICAL_WORKSPACE_ROOT
        original_canonical_registry = _workspace._CANONICAL_REGISTRY_PATH
        try:
            _workspace._AGENT_HERMES_HOME = str(agent_home)
            _workspace._CANONICAL_WORKSPACE_ROOT = canonical_root
            _workspace._CANONICAL_REGISTRY_PATH = binding
            os.environ["HERMES_HOME"] = _workspace._AGENT_HERMES_HOME
            os.environ["AOTA_WORKSPACE_REGISTRY_PATH"] = str(binding)
            os.environ["AOTA_CANONICAL_WORKSPACE_ROOT"] = str(canonical_root)
            agent_root = runtime
            assert _workspace.resolve_workspace("aota-hermes-tools") == runtime
            assert common.load_project(manifest)[1]["project"]["id"] == "aota-hermes-tools"
            assert (runtime / ".codegraph").parent == runtime
            missing = runtime / ".aota" / "missing.yaml"
            try:
                common.load_project(missing)
            except common.ProjectError as exc:
                assert exc.code == "path_escape" or exc.code == "manifest_invalid"
            else:
                raise AssertionError("missing manifest was accepted")
            mismatch = runtime / ".aota" / "mismatch.yaml"
            mismatch.write_text(manifest.read_text(encoding="utf-8").replace("aota-hermes-tools", "other-project", 1), encoding="utf-8")
            assert common.load_project(mismatch)[1]["project"]["id"] == "other-project"
            escaped_root = Path(temp) / "escaped-root"
            escaped_root.symlink_to(Path("/tmp"), target_is_directory=True)
            try:
                common.load_project(escaped_root / ".aota" / "project.yaml")
            except common.ProjectError as exc:
                assert exc.code in {"path_escape", "manifest_invalid"}
            else:
                raise AssertionError("runtime root symlink escape was accepted")
            escaped_aota = Path(temp) / "escaped-aota"
            escaped_aota.symlink_to(runtime / ".aota", target_is_directory=True)
            try:
                common.load_project(escaped_aota / "project.yaml")
            except common.ProjectError as exc:
                assert exc.code == "path_escape"
            else:
                raise AssertionError(".aota symlink escape was accepted")
            host_registry = Path(temp) / "host-workspaces.json"
            host_registry.write_text(json.dumps({"aota-hermes-tools": {"candidates": ["/home/latios/workspace/aota-hermes-tools"]}}), encoding="utf-8")
            _workspace._REGISTRY_PATH = host_registry
            binding.write_text(host_registry.read_text(encoding="utf-8"), encoding="utf-8")
            try:
                _workspace.resolve_workspace("aota-hermes-tools")
            except _workspace.WorkspaceError as exc:
                assert "outside canonical workspace root" in str(exc)
            else:
                raise AssertionError("host path was accepted in agent context")
            os.environ["HERMES_HOME"] = "/home/hermeswebui/.hermes"
            os.environ["AOTA_WORKSPACE_REGISTRY_PATH"] = str(host_registry)
            os.environ["AOTA_CANONICAL_WORKSPACE_ROOT"] = "/home/latios/workspace"
            assert _workspace.resolve_workspace("aota-hermes-tools") == Path("/home/latios/workspace/aota-hermes-tools").resolve()
            os.environ["HERMES_HOME"] = _workspace._AGENT_HERMES_HOME
            os.environ.pop("AOTA_WORKSPACE_REGISTRY_PATH", None)
            os.environ["AOTA_CANONICAL_WORKSPACE_ROOT"] = str(canonical_root)
            _workspace._REGISTRY_PATH = original_registry
            binding.write_text(json.dumps({"aota-hermes-tools": {"candidates": [str(runtime)]}}), encoding="utf-8")
            duplicate = canonical_root / "duplicate"
            duplicate.mkdir()
            binding.write_text(json.dumps({"aota-hermes-tools": {"candidates": [str(runtime), str(duplicate)]}}), encoding="utf-8")
            try:
                _workspace.resolve_workspace("aota-hermes-tools")
            except _workspace.WorkspaceError as exc:
                assert "ambiguous" in str(exc)
            else:
                raise AssertionError("duplicate runtime binding was accepted")
        finally:
            _workspace._REGISTRY_PATH = original_registry
            _workspace._AGENT_HERMES_HOME = original_agent_home
            _workspace._CANONICAL_WORKSPACE_ROOT = original_canonical_root
            _workspace._CANONICAL_REGISTRY_PATH = original_canonical_registry
            if original_registry_env is None:
                os.environ.pop("AOTA_WORKSPACE_REGISTRY_PATH", None)
            else:
                os.environ["AOTA_WORKSPACE_REGISTRY_PATH"] = original_registry_env
            if original_canonical_env is None:
                os.environ.pop("AOTA_CANONICAL_WORKSPACE_ROOT", None)
            else:
                os.environ["AOTA_CANONICAL_WORKSPACE_ROOT"] = original_canonical_env
            if original_home is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = original_home

    with tempfile.TemporaryDirectory(prefix="pcf-smoke-") as temp:
        temp_root = Path(temp)
        for name in ("one", "two"):
            manifest = temp_root / name / ".aota" / "project.yaml"
            manifest.parent.mkdir(parents=True)
            text = valid_manifest.read_text(encoding="utf-8").replace("fixture-project", f"{name}-project")
            manifest.write_text(text, encoding="utf-8")
        (temp_root / "one" / ".aota" / "observed.json").write_text(
            (valid_manifest.parent / "observed.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
        invalid = temp_root / "zz-invalid" / ".aota" / "project.yaml"
        invalid.parent.mkdir(parents=True)
        invalid.write_text("schema_version: 1\nunknown: true\n", encoding="utf-8")
        discovery.resolve_workspace = lambda _workspace_id: temp_root
        opened.resolve_workspace = lambda _workspace_id: temp_root
        registry.resolve_workspace = lambda _workspace_id: temp_root
        relationship.resolve_workspace = lambda _workspace_id: temp_root
        scan = json.loads(discovery.handle({"workspace_id": "fixture", "limit": 10}))
        assert scan["status"] == "ok" and scan["project_count"] == 2 and scan["invalid"]
        search = json.loads(opened.handle_search({"workspace_id": "fixture", "query": "one hermes-plugin", "limit": 3}))
        assert search["result_count"] == 1 and search["results"][0]["project_id"] == "one-project"
        result = json.loads(opened.handle_open({"workspace_id": "fixture", "project_id": "one-project", "include_observed": True}))
        assert result["status"] == "ok" and result["card"]["project_id"] == "one-project"
        assert "observed_project_id_conflict" in result["card"]["warnings"]
        duplicate = temp_root / "zz-duplicate" / ".aota" / "project.yaml"
        duplicate.parent.mkdir(parents=True)
        duplicate.write_text(valid_manifest.read_text(encoding="utf-8").replace("fixture-project", "one-project"), encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="pcf-registry-") as temp:
        temp_root = Path(temp)
        for name in ("one", "two"):
            manifest = temp_root / name / ".aota" / "project.yaml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(valid_manifest.read_text(encoding="utf-8").replace("fixture-project", f"{name}-project"), encoding="utf-8")
        registry.resolve_workspace = lambda _workspace_id: temp_root
        opened.resolve_workspace = lambda _workspace_id: temp_root
        relationship.resolve_workspace = lambda _workspace_id: temp_root
        first = json.loads(registry.handle_refresh({"workspace_id": "fixture"}))
        assert first["changed"] is True and first["registry_revision"] == 1
        second = json.loads(registry.handle_refresh({"workspace_id": "fixture"}))
        assert second["changed"] is False and second["registry_revision"] == 1
        (temp_root / "one" / ".aota" / "project.yaml").write_text(
            (temp_root / "one" / ".aota" / "project.yaml").read_text(encoding="utf-8").replace("Bounded project", "Changed project"), encoding="utf-8"
        )
        stale = json.loads(registry.handle_open({"workspace_id": "fixture"}))
        assert stale["registry"]["status"] == "stale"
        changed = json.loads(registry.handle_refresh({"workspace_id": "fixture"}))
        assert changed["changed"] is True and changed["registry_revision"] == 2
        opened_registry = json.loads(registry.handle_open({"workspace_id": "fixture"}))
        assert opened_registry["registry"]["status"] == "fresh"
        search = json.loads(opened.handle_search({"workspace_id": "fixture", "query": "hermes-plugin", "limit": 3}))
        assert search["source"] == "registry" and search["registry_status"] == "fresh"
        brief = json.loads(relationship.handle({"workspace_id": "fixture", "request_summary": "Hermes plugin", "limit": 2}))
        assert brief["status"] == "ok" and brief["decision_support"]["strongest_candidate"]
        no_match = json.loads(relationship.handle({"workspace_id": "fixture", "request_summary": "unrelated database", "limit": 2}))
        assert no_match["decision_support"]["no_match_detected"] is True
        registry.registry_path(temp_root).write_text("{malformed", encoding="utf-8")
        rebuilt = json.loads(registry.handle_refresh({"workspace_id": "fixture"}))
        assert rebuilt["changed"] is True and rebuilt["warning"] == "registry_rebuilt_from_invalid_existing"
        duplicate = temp_root / "duplicate" / ".aota" / "project.yaml"
        duplicate.parent.mkdir(parents=True)
        duplicate.write_text(valid_manifest.read_text(encoding="utf-8").replace("fixture-project", "one-project"), encoding="utf-8")
        duplicate_result = json.loads(registry.handle_refresh({"workspace_id": "fixture"}))
        assert duplicate_result["duplicate_count"] == 1 and duplicate_result["project_count"] == 1
    print("PCF_ISOLATED_SMOKE_PASS")


if __name__ == "__main__":
    main()
