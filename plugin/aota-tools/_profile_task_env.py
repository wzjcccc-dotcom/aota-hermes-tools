"""Resolve Profile Task credentials from the one global Hermes authority.

This module deliberately does not read a Profile-local ``.env`` or parse
``auth.json``.  API-key credentials are selected from ``<global>/.env`` and
OAuth credentials are discovered by Hermes from ``<global>/auth.json``.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Mapping

try:
    from ._profile_task_paths import resolve_global_hermes_home
except ImportError:  # standalone launcher execution
    import importlib.util as _importlib_util

    _paths = Path(__file__).with_name("_profile_task_paths.py")
    _spec = _importlib_util.spec_from_file_location("_profile_task_paths", _paths)
    if not _spec or not _spec.loader:
        raise ImportError("cannot load profile task path resolver")
    _mod = _importlib_util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    resolve_global_hermes_home = _mod.resolve_global_hermes_home


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# This is the smallest closed provider metadata available to the plugin.  If
# Hermes exposes richer registry metadata in-process, _profile_task_start.py
# passes that explicit auth_type and key mapping here; unknown providers never
# fall back to API-key guessing.
PROVIDER_METADATA: dict[str, dict[str, str]] = {
    "opencode-go": {
        "auth_type": "api_key",
        "key_env": "OPENCODE_GO_API_KEY",
        "base_url_env": "OPENCODE_GO_BASE_URL",
    },
    "openai-codex": {
        "auth_type": "oauth",
        "key_env": "",
        "base_url_env": "",
    },
}


class CredentialResolutionError(RuntimeError):
    """Stable, non-secret credential failure classification."""

    def __init__(self, classification: str, message: str):
        super().__init__(message)
        self.classification = classification


def _safe_summary(root: Path) -> str:
    """Return a bounded authority summary suitable for logs."""
    return str(root)


def _parse_dotenv(path: Path) -> dict[str, str]:
    """Parse deterministic assignments without shell expansion or execution."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise CredentialResolutionError(
            "credential_authority_invalid", "global_env_unreadable"
        ) from exc

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not _ENV_NAME.fullmatch(name):
            continue
        if name in values:
            raise CredentialResolutionError(
                "credential_authority_invalid", f"global_env_duplicate_key={name}"
            )
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            try:
                parsed = ast.literal_eval(value)
                if isinstance(parsed, str):
                    value = parsed
            except (SyntaxError, ValueError) as exc:
                raise CredentialResolutionError(
                    "credential_authority_invalid", "global_env_invalid_quoted_value"
                ) from exc
        # Do not expand $VARS, backticks, or command substitutions.  The value
        # remains literal, as required by the authority contract.
        values[name] = value
    return values


def _provider_metadata(
    provider_name: str,
    *,
    auth_type: str = "",
    key_env: str = "",
    base_url_env: str = "",
) -> dict[str, str]:
    metadata = dict(PROVIDER_METADATA.get(provider_name, {}))
    declared_auth_type = auth_type.strip() if isinstance(auth_type, str) else ""
    if declared_auth_type:
        metadata["auth_type"] = declared_auth_type
    if not metadata or metadata.get("auth_type") not in {"api_key", "oauth", "oauth_external"}:
        raise CredentialResolutionError(
            "credential_provider_unsupported", f"provider={provider_name}"
        )
    if key_env:
        metadata["key_env"] = key_env
    if base_url_env:
        metadata["base_url_env"] = base_url_env
    return metadata


def _check_global_file(path: Path, label: str) -> bool:
    try:
        return path.is_file() and os.access(path, os.R_OK)
    except OSError:
        return False


def resolve_global_provider_credentials(
    *,
    global_hermes_home: str | Path,
    provider_name: str,
    key_env: str = "",
    base_url_env: str = "",
    configured_base_url: str = "",
    provider_auth_type: str = "",
    provider_default_base_url: str = "",
) -> dict[str, object]:
    """Resolve one provider from the canonical global Hermes home.

    The returned ``child_env`` is bounded credential material for a subprocess;
    no secret is printed or included in the log metadata.  For OAuth the
    mapping is empty and Hermes is expected to read global ``auth.json``.
    """
    try:
        global_home = resolve_global_hermes_home(hermes_home=global_hermes_home)
    except (OSError, ValueError) as exc:
        raise CredentialResolutionError(
            "credential_authority_invalid", f"global_root_invalid={exc}"
        ) from exc

    metadata = _provider_metadata(
        provider_name,
        auth_type=provider_auth_type,
        key_env=key_env,
        base_url_env=base_url_env,
    )
    auth_type_value = metadata["auth_type"]
    if auth_type_value in {"oauth", "oauth_external"}:
        auth_path = global_home / "auth.json"
        if not _check_global_file(auth_path, "auth_json"):
            raise CredentialResolutionError(
                "credential_missing",
                f"provider={provider_name} key_env=none credential_source=global_auth_json "
                f"global_root={_safe_summary(global_home)} auth_json_exists=false",
            )
        return {
            "provider_name": provider_name,
            "auth_type": auth_type_value,
            "credential_source": "global_auth_json",
            "global_root": str(global_home),
            "child_env": {},
            "key_present": True,
            "base_url_present": False,
        }

    key_name = metadata.get("key_env", "")
    url_name = metadata.get("base_url_env", "")
    if not _ENV_NAME.fullmatch(key_name):
        raise CredentialResolutionError(
            "credential_authority_invalid", "invalid_key_env"
        )
    if url_name and not _ENV_NAME.fullmatch(url_name):
        raise CredentialResolutionError(
            "credential_authority_invalid", "invalid_base_url_env"
        )
    env_path = global_home / ".env"
    if not _check_global_file(env_path, "global_env"):
        raise CredentialResolutionError(
            "credential_missing",
            f"provider={provider_name} key_env={key_name} credential_source=global_env "
            f"global_root={_safe_summary(global_home)} global_env_exists=false",
        )
    values = _parse_dotenv(env_path)
    key = values.get(key_name, "")
    if not key:
        raise CredentialResolutionError(
            "credential_missing",
            f"provider={provider_name} key_env={key_name} credential_source=global_env "
            f"global_root={_safe_summary(global_home)} global_env_exists=true key_present=false",
        )
    configured = configured_base_url.strip() if isinstance(configured_base_url, str) else ""
    global_url = values.get(url_name, "") if url_name else ""
    selected_url = configured or global_url or provider_default_base_url
    child_env: dict[str, str] = {key_name: key}
    if url_name and selected_url:
        child_env[url_name] = selected_url
    return {
        "provider_name": provider_name,
        "auth_type": auth_type_value,
        "credential_source": "global_env",
        "global_root": str(global_home),
        "child_env": child_env,
        "key_present": True,
        "base_url_present": bool(selected_url),
    }


def _bounded_child_env(
    parent_env: Mapping[str, str],
    *,
    global_home: str,
    credential_env: Mapping[str, str],
) -> dict[str, str]:
    """Build the worker environment without inheriting arbitrary parent keys."""
    exact = {
        "PATH", "HOME", "TERM", "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES",
        "LANGUAGE", "NO_COLOR", "AOTA_RUNTIME_ROOT", "AOTA_PROFILE_TASK_ROOT",
        "AOTA_CANONICAL_WORKSPACE_ROOT", "AOTA_WORKSPACE_REGISTRY_PATH",
        "AOTA_WEBUI_ATTACHMENT_ROOT", "AOTA_WEBUI_ATTACHMENT_REF_ROOT",
        "AOTA_WORKSPACE_ID", "AOTA_PROJECT_ID", "AOTA_WORKSPACE_ROOT",
        "AOTA_PROJECT_ROOT", "AOTA_WORKSPACE_DECISION_ID", "AOTA_SPEC_ID",
        "AOTA_SPEC_REVISION", "AOTA_SPEC_HASH",
    }
    child = {key: value for key, value in parent_env.items() if key in exact or key.startswith("AOTA_PROFILE_TASK_")}
    child["HERMES_HOME"] = global_home
    child.update(credential_env)
    return child


def _render_status(result: Mapping[str, object]) -> str:
    return (
        f"credential_resolved=true provider={result['provider_name']} "
        f"auth_type={result['auth_type']} source={result['credential_source']} "
        f"global_root={result['global_root']} key_present={str(result['key_present']).lower()} "
        f"base_url_present={str(result['base_url_present']).lower()}"
    )


def _resolve_for_command(
    *,
    global_hermes_home: str,
    provider_name: str,
    key_env: str,
    base_url_env: str,
    configured_base_url: str,
    auth_type: str,
) -> tuple[dict[str, object], dict[str, str]]:
    result = resolve_global_provider_credentials(
        global_hermes_home=global_hermes_home,
        provider_name=provider_name,
        key_env=key_env,
        base_url_env=base_url_env,
        configured_base_url=configured_base_url,
        provider_auth_type=auth_type,
    )
    child_env = _bounded_child_env(
        os.environ,
        global_home=str(result["global_root"]),
        credential_env=result["child_env"],  # type: ignore[arg-type]
    )
    return result, child_env


def _write_ready_marker(path: str) -> None:
    marker = Path(path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("credential_ready=true\n", encoding="utf-8")


def emit_exports(
    *,
    key_env: str,
    provider_name: str,
    base_url_env: str,
    configured_base_url: str,
    global_hermes_home: str,
    auth_type: str = "",
) -> int:
    try:
        result = resolve_global_provider_credentials(
            global_hermes_home=global_hermes_home,
            provider_name=provider_name,
            key_env=key_env,
            base_url_env=base_url_env,
            configured_base_url=configured_base_url,
            provider_auth_type=auth_type,
        )
    except CredentialResolutionError as exc:
        print(f"{exc.classification}: {exc}", file=sys.stderr)
        return {"credential_missing": 1, "credential_authority_invalid": 2,
                "credential_provider_unsupported": 3}.get(exc.classification, 2)
    print(_render_status(result))
    return 0


def run_with_credentials(
    *,
    command: str,
    ready_marker: str,
    key_env: str,
    provider_name: str,
    base_url_env: str,
    configured_base_url: str,
    global_hermes_home: str,
    auth_type: str = "",
) -> int:
    try:
        result, child_env = _resolve_for_command(
            global_hermes_home=global_hermes_home,
            provider_name=provider_name,
            key_env=key_env,
            base_url_env=base_url_env,
            configured_base_url=configured_base_url,
            auth_type=auth_type,
        )
    except CredentialResolutionError as exc:
        print(f"{exc.classification}: {exc}", file=sys.stderr)
        return {"credential_missing": 1, "credential_authority_invalid": 2,
                "credential_provider_unsupported": 3}.get(exc.classification, 2)

    try:
        print(_render_status(result))
        _write_ready_marker(ready_marker)
        return subprocess.run(["/bin/bash", "-c", command], env=child_env, check=False).returncode
    except OSError as exc:
        print(f"credential_child_launch_failed: {exc.__class__.__name__}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-env", default="")
    parser.add_argument("--provider-name", required=True)
    parser.add_argument("--base-url-env", default="")
    parser.add_argument("--configured-base-url", default="")
    parser.add_argument("--auth-type", default="")
    parser.add_argument("--global-hermes-home", required=True)
    parser.add_argument("--command", default="")
    parser.add_argument("--ready-marker", default="")
    args = parser.parse_args()
    values = {
        "key_env": args.key_env,
        "provider_name": args.provider_name,
        "base_url_env": args.base_url_env,
        "configured_base_url": args.configured_base_url,
        "global_hermes_home": args.global_hermes_home,
        "auth_type": args.auth_type,
    }
    if args.command:
        if not args.ready_marker:
            print("credential_authority_invalid: ready marker is required", file=sys.stderr)
            return 2
        return run_with_credentials(command=args.command, ready_marker=args.ready_marker, **values)
    return emit_exports(**values)


if __name__ == "__main__":
    raise SystemExit(main())
