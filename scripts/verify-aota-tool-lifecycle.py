#!/usr/bin/env python3
"""Static governance checks for AOTA plugin-tool lifecycle declarations."""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "deploy" / "aota-lifecycle-inventory.yaml"
PLUGIN = ROOT / "plugin" / "aota-tools"
PROFILES = ROOT / "profiles"
MANIFEST = ROOT / "deploy" / "aota-forge-plan-files.yaml"
PROFILE_IDS = {"task-main", "architect", "reviewer", "coder", "debugger", "project-steward"}
MUTATIONS = {"readonly", "bounded_project_mutation", "bounded_runtime_mutation", "control_plane_mutation", "dangerous/not_allowed"}
PATHS = {"none", "aota_runtime", "canonical_workspace", "codegraph_index", "webui_attachments", "network_read", "AOTA_WORKSPACE_REGISTRY_PATH"}


def load_yaml(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"mapping required: {path}")
    return value


def manifest_mentions(relative: str) -> bool:
    return relative in MANIFEST.read_text(encoding="utf-8")


def errors_for(root: Path = ROOT) -> list[str]:
    inventory_path = root / "deploy" / "aota-lifecycle-inventory.yaml"
    plugin_path = root / "plugin" / "aota-tools"
    profile_root = root / "profiles"
    manifest_path = root / "deploy" / "aota-forge-plan-files.yaml"
    errors: list[str] = []
    try:
        inventory = load_yaml(inventory_path)
    except Exception as exc:  # stable diagnostic without source dump
        return [f"inventory:{type(exc).__name__}"]
    if inventory.get("schema_version") != 1:
        errors.append("inventory-schema")
    topology = inventory.get("runtime_topology", {})
    if topology.get("active_webui_transport") != "legacy_in_process":
        errors.append("topology-transport")
    if set(topology.get("plugin_import_processes", [])) != {"hermes-agent", "hermes-webui"}:
        errors.append("topology-processes")
    if set(topology.get("required_activation_targets", [])) != {"hermes-agent", "hermes-webui", "new-session"}:
        errors.append("topology-activation")
    tools = inventory.get("tools")
    if not isinstance(tools, dict) or not tools:
        return errors + ["tools-mapping"]
    try:
        declared = set(load_yaml(plugin_path / "plugin.yaml").get("provides_tools", []))
    except Exception:
        declared = set()
        errors.append("plugin-yaml")
    if set(tools) != declared:
        errors.append("inventory-plugin-tool-set")
    init_text = (plugin_path / "__init__.py").read_text(encoding="utf-8")
    register_text = init_text.split("def register", 1)[-1]
    if "register_tool" not in register_text:
        errors.append("init-registration-function")
    for tool_id, item in tools.items():
        if not isinstance(item, dict):
            errors.append(f"tool-shape:{tool_id}")
            continue
        required = {"module", "toolset", "mutation_class", "allowed_profiles", "denied_profiles", "transports", "path_dependencies", "activation_targets", "exact_dispatch_required"}
        if not required.issubset(item):
            errors.append(f"tool-fields:{tool_id}")
            continue
        module = str(item["module"])
        module_path = plugin_path / f"{module}.py"
        if not module_path.is_file():
            errors.append(f"module:{tool_id}")
            continue
        module_text = module_path.read_text(encoding="utf-8")
        if tool_id not in module_text:
            errors.append(f"module-tool-id:{tool_id}")
        if module != "__init__" and f"from .{module}" not in init_text:
            errors.append(f"init-import:{tool_id}")
        if item["toolset"] not in module_text and item["toolset"] not in init_text:
            errors.append(f"toolset-source:{tool_id}")
        if item["mutation_class"] not in MUTATIONS:
            errors.append(f"mutation:{tool_id}")
        allowed, denied = set(item["allowed_profiles"]), set(item["denied_profiles"])
        if allowed & denied or (allowed | denied) != PROFILE_IDS:
            errors.append(f"profile-policy:{tool_id}")
        if not set(item["transports"]).issubset(set(topology.get("known_transports", []))):
            errors.append(f"transport:{tool_id}")
        if not set(item["path_dependencies"]).issubset(PATHS):
            errors.append(f"path:{tool_id}")
        if set(item["activation_targets"]) != set(topology.get("required_activation_targets", [])):
            errors.append(f"activation:{tool_id}")
        for profile in allowed | denied:
            config_path = profile_root / profile / "config.yaml"
            if not config_path.is_file():
                errors.append(f"profile-missing:{profile}")
                continue
            config = load_yaml(config_path)
            enabled = set(config.get("toolsets", []))
            disabled = set(config.get("agent", {}).get("disabled_toolsets", []))
            toolset = item["toolset"]
            if profile in allowed and toolset not in enabled:
                errors.append(f"profile-enable:{tool_id}:{profile}")
            if profile in denied and toolset not in disabled:
                errors.append(f"profile-deny:{tool_id}:{profile}")
            for transport in item["transports"]:
                configured = set(config.get("platform_toolsets", {}).get(transport, []))
                if profile in allowed and toolset not in configured:
                    errors.append(f"profile-transport:{tool_id}:{profile}:{transport}")
    manifest_text = manifest_path.read_text(encoding="utf-8") if manifest_path.is_file() else ""
    if "aota-lifecycle-inventory.yaml" not in manifest_text:
        errors.append("managed-inventory")
    if 'pattern: "*.py"' not in manifest_text:
        errors.append("managed-plugin-module")
    if 'pattern: "*/SKILL.md"' not in manifest_text:
        errors.append("managed-tool-skill")
    if "terminal" not in set(load_yaml(profile_root / "coder" / "config.yaml").get("agent", {}).get("disabled_toolsets", [])):
        errors.append("coder-terminal")
    if "aota_project_steward" not in set(load_yaml(profile_root / "task-main" / "config.yaml").get("agent", {}).get("disabled_toolsets", [])):
        errors.append("steward-source-write")
    return errors


def fixture() -> int:
    def mutate(path: Path, change) -> None:
        data = load_yaml(path)
        change(data)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def run_case(name: str, change, expected: str) -> None:
        with tempfile.TemporaryDirectory(prefix="aota-tool-lifecycle-") as temp:
            fixture_root = Path(temp) / "repo"
            shutil.copytree(ROOT, fixture_root, ignore=shutil.ignore_patterns(".git", "__pycache__", ".deploy-backups", ".deploy-receipts"))
            change(fixture_root)
            observed = errors_for(fixture_root)
            if not any(value.startswith(expected) for value in observed):
                raise AssertionError(f"{name}: expected {expected}, got {observed}")

    run_case("missing-plugin-yaml", lambda root: mutate(root / "plugin/aota-tools/plugin.yaml", lambda data: data.update(provides_tools=[])), "inventory-plugin-tool-set")
    run_case("toolset-mismatch", lambda root: mutate(root / "deploy/aota-lifecycle-inventory.yaml", lambda data: data["tools"]["aota_path_info"].update(toolset="unknown")), "toolset-source:aota_path_info")
    run_case("unknown-profile", lambda root: mutate(root / "deploy/aota-lifecycle-inventory.yaml", lambda data: data["tools"]["aota_path_info"].update(allowed_profiles=["unknown"], denied_profiles=list(PROFILE_IDS - {"unknown"}))), "profile-policy:aota_path_info")
    run_case("overlap", lambda root: mutate(root / "deploy/aota-lifecycle-inventory.yaml", lambda data: data["tools"]["aota_path_info"].update(allowed_profiles=["task-main"], denied_profiles=["task-main"])) , "profile-policy:aota_path_info")
    run_case("missing-attachment-reader-deny", lambda root: mutate(root / "deploy/aota-lifecycle-inventory.yaml", lambda data: data["tools"]["aota_webui_attachment_read"].update(denied_profiles=["architect", "reviewer", "coder"])), "profile-policy:aota_webui_attachment_read")
    run_case("missing-project-steward-allow", lambda root: mutate(root / "deploy/aota-lifecycle-inventory.yaml", lambda data: data["tools"]["aota_webui_attachment_read"].update(allowed_profiles=["task-main"], denied_profiles=["architect", "reviewer", "coder", "debugger", "project-steward"])), "profile-deny:aota_webui_attachment_read:project-steward")
    run_case("invalid-transport", lambda root: mutate(root / "deploy/aota-lifecycle-inventory.yaml", lambda data: data["tools"]["aota_path_info"].update(transports=["mcp"])), "transport:aota_path_info")
    run_case("missing-activation", lambda root: mutate(root / "deploy/aota-lifecycle-inventory.yaml", lambda data: data["tools"]["aota_path_info"].update(activation_targets=[])), "activation:aota_path_info")
    run_case("unknown-path", lambda root: mutate(root / "deploy/aota-lifecycle-inventory.yaml", lambda data: data["tools"]["aota_path_info"].update(path_dependencies=["/arbitrary/host/path"])), "path:aota_path_info")
    run_case("undeployed-module", lambda root: (root / "deploy/aota-forge-plan-files.yaml").write_text((root / "deploy/aota-forge-plan-files.yaml").read_text(encoding="utf-8").replace('pattern: "*.py"', 'pattern: "*.pyo"', 1), encoding="utf-8"), "managed-plugin-module")
    run_case("missing-init-registration", lambda root: (root / "plugin/aota-tools/__init__.py").write_text((root / "plugin/aota-tools/__init__.py").read_text(encoding="utf-8").replace("from ._path_info", "from ._pathinfo", 1), encoding="utf-8"), "init-import:aota_path_info")
    print("AOTA_TOOL_LIFECYCLE_ISOLATED_SMOKE_PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", action="store_true")
    args = parser.parse_args()
    if args.fixture:
        return fixture()
    errors = errors_for()
    if errors:
        print("AOTA_TOOL_LIFECYCLE_SOURCE_FAIL")
        for error in sorted(set(errors)):
            print(error)
        return 1
    for marker in (
        "AOTA_TOOL_INVENTORY_PASS", "AOTA_TOOL_REGISTRATION_PASS", "AOTA_TOOL_TOOLSET_PASS",
        "AOTA_TOOL_PROFILE_EXPOSURE_PASS", "AOTA_TOOL_TRANSPORT_PASS", "AOTA_TOOL_PATH_DECLARATION_PASS",
        "AOTA_TOOL_MANAGED_MANIFEST_PASS", "AOTA_TOOL_ACTIVATION_TARGET_PASS", "AOTA_TOOL_LIFECYCLE_SOURCE_PASS",
    ):
        print(marker)
    print("RUNTIME_REGISTRY_CHECK_REQUIRED")
    print("FINAL_MODEL_TOOL_LIST_CHECK_REQUIRED")
    print("EXACT_DISPATCH_CHECK_REQUIRED")
    print("PROCESS_RECREATE_REQUIRED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
