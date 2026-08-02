#!/usr/bin/env python3
"""Zero-LLM source verifier for retired AOTA parent-wake customization."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOST = Path("/home/latios/workspace/hermes-agent-host")
HOST_LAUNCHER = Path("/home/latios/.local/bin/hermes-host")


def main() -> int:
    adapter = HOST / "gateway" / "aota_parent_wake.py"
    gateway_run = (HOST / "gateway" / "run.py").read_text(encoding="utf-8")
    completion = (
        ROOT / "plugin" / "aota-tools" / "_completion_observation.py"
    ).read_text(encoding="utf-8")
    finalizer = (
        ROOT / "plugin" / "aota-tools" / "_profile_task_finalize.py"
    ).read_text(encoding="utf-8")
    forge_plan = (ROOT / "deploy" / "aota-forge-plan-files.yaml").read_text(
        encoding="utf-8"
    )
    manifest = json.loads(
        (ROOT / "deploy" / "host-parent-wake" / "host-parent-wake.manifest.json").read_text(
            encoding="utf-8"
        )
    )

    assert not adapter.exists(), adapter
    for token in (
        "gateway.aota_parent_wake",
        "_aota_parent_wake",
        "_load_aota_host_runtime_env",
        "AOTA_PARENT_WAKE_ENABLED",
    ):
        assert token not in gateway_run, token
    print("HERMES_PARENT_WAKE_CORE_CUSTOMIZATION_ABSENT=PASS")

    launcher = HOST_LAUNCHER.read_text(encoding="utf-8")
    assert 'RUNTIME_ENV="/home/latios/.config/hermes-host/runtime.env"' in launcher
    assert 'source "$RUNTIME_ENV"' in launcher
    assert 'exec "$REAL_HERMES" "$@"' in launcher
    print("HOST_LAUNCHER_RUNTIME_ENV_AUTHORITY=PASS")

    assert 'raise CompletionTransportContextError("terminal_background_required")' in completion
    resolver = completion[completion.index("def resolve_completion_transport"):completion.index("def _parse_utc")]
    assert "COMPLETION_TRANSPORT_LEGACY_DURABLE" not in resolver
    assert finalizer.count("_legacy_delivery_outbox_required(") >= 3
    print("TERMINAL_BACKGROUND_ONLY_NEW_START=PASS")

    assert "hermes_host_parent_wake:" not in forge_plan
    assert "hermes_host_profile_skill_projection:" in forge_plan
    managed = set(manifest["managed_source_files"])
    assert not managed & {
        "gateway/aota_parent_wake.py",
        "gateway/run.py",
        "gateway/wake.py",
    }
    assert manifest["status"] == "parent-wake-retired-profile-skill-projection-active"
    assert manifest["retired_parent_wake"]["historical_events"] == "preserved_untouched"
    print("FORGE_PARENT_WAKE_RETIRED=PASS")

    required_host_files = {
        "agent/prompt_builder.py",
        "agent/skill_utils.py",
        "tools/skills_tool.py",
        "toolsets.py",
    }
    assert required_host_files <= managed
    assert {"skills.allowlist", "skills.reference_allowlist", "skills-readonly"} <= set(
        manifest["required_contracts"]
    )
    print("PROFILE_SKILL_PROJECTION_PRESERVED=PASS")
    print("AOTA_PARENT_WAKE_RETIREMENT_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
