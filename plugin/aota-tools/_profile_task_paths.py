"""Canonical Hermes root/profile path resolution for Profile Tasks."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping


_PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _profile_name(value: object) -> str:
    if not isinstance(value, str) or not _PROFILE_NAME.fullmatch(value):
        raise ValueError("invalid_profile_name")
    return value


def _canonical_profile_home(raw_home: Path) -> tuple[Path, str | None]:
    if not raw_home.is_absolute():
        raise ValueError("hermes_home_must_be_absolute")
    raw_home = Path(os.path.normpath(str(raw_home)))
    canonical_home = raw_home.resolve(strict=False)

    if canonical_home.name == "profiles":
        raise ValueError("hermes_home_cannot_be_profiles_root")

    # Inspect both the supplied layout and the canonical layout.  The former
    # lets us reject a profile symlink that escapes its global tree; the latter
    # prevents a symlink alias from making a profile-local home look global.
    raw_is_profile = raw_home.parent.name == "profiles"
    canonical_is_profile = canonical_home.parent.name == "profiles"
    if raw_is_profile or canonical_is_profile:
        global_root = (
            raw_home.parent.parent.resolve(strict=False)
            if raw_is_profile
            else canonical_home.parent.parent
        )
        if "profiles" in global_root.parts or global_root.name == "profiles":
            raise ValueError("invalid_nested_profile_home")
        profile = _profile_name(raw_home.name if raw_is_profile else canonical_home.name)
        profiles_root = (global_root / "profiles").resolve(strict=False)
        try:
            canonical_home.relative_to(profiles_root)
        except ValueError as exc:
            raise ValueError("profile_home_symlink_escape")
        expected = (profiles_root / profile).resolve(strict=False)
        if canonical_home != expected:
            raise ValueError("profile_home_symlink_escape")
        return global_root, profile

    # A global root must not itself be hidden inside a profile tree.  This
    # rejects profiles/<name>/profiles/<other> using canonical components,
    # rather than string replacement.
    if "profiles" in canonical_home.parts:
        raise ValueError("invalid_nested_profile_home")
    if not canonical_home.is_dir():
        raise ValueError("global_hermes_home_missing")
    return canonical_home, None


def resolve_global_hermes_home(
    *,
    hermes_home: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the canonical global Hermes root, never a profile-local root."""
    env = os.environ if environ is None else environ
    raw = hermes_home
    if raw is None:
        raw = env.get("HERMES_HOME")
    if raw is None or str(raw).strip() == "":
        raw = (Path.home() if home is None else Path(home)) / ".hermes"
    global_root, _profile = _canonical_profile_home(Path(str(raw)).expanduser())
    return global_root


def resolve_profile_home(global_root: str | Path, profile_name: object) -> Path:
    """Resolve a named profile below a canonical global root safely."""
    profile = _profile_name(profile_name)
    root = Path(global_root)
    if not root.is_absolute():
        raise ValueError("global_hermes_home_must_be_absolute")
    root = root.resolve(strict=False)
    if "profiles" in root.parts or root.name == "profiles":
        raise ValueError("invalid_global_hermes_home")
    if not root.is_dir():
        raise ValueError("global_hermes_home_missing")
    profiles_root = (root / "profiles").resolve(strict=False)
    if not profiles_root.is_dir():
        raise ValueError("profiles_root_missing")
    if profiles_root.parent != root:
        raise ValueError("profiles_root_symlink_escape")
    profile_home = (root / "profiles" / profile).resolve(strict=False)
    try:
        profile_home.relative_to(profiles_root)
    except ValueError as exc:
        raise ValueError("profile_home_symlink_escape") from exc
    return profile_home


def build_profile_task_runtime_context(
    *,
    parent_profile: str | None,
    target_profile: str,
    hermes_home: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> dict[str, str]:
    """Build the explicit global/parent/target path authority for a launch."""
    env = os.environ if environ is None else environ
    raw_home = hermes_home if hermes_home is not None else env.get("HERMES_HOME")
    global_root = resolve_global_hermes_home(
        hermes_home=hermes_home, environ=env, home=home
    )
    local_parent = None
    if raw_home:
        try:
            _unused_root, local_parent = _canonical_profile_home(
                Path(str(raw_home)).expanduser()
            )
        except ValueError:
            # resolve_global_hermes_home already provides the authoritative error
            raise
    parent = _profile_name(parent_profile or local_parent or "default")
    target = _profile_name(target_profile)
    parent_home = resolve_profile_home(global_root, parent)
    target_home = resolve_profile_home(global_root, target)
    return {
        "global_hermes_home": str(global_root),
        "parent_profile": parent,
        "parent_profile_home": str(parent_home),
        "target_profile": target,
        "target_profile_home": str(target_home),
    }
