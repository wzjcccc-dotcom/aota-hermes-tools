"""Host-native runner contract for AOTA Profile Tasks.

The runner is a frozen executable identity, not a shell command or a PATH
lookup.  The canonical launcher owns the Host Hermes environment; the
official Hermes executable is accepted only as an explicit, trusted
diagnostic override.
"""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import stat
from pathlib import Path
from typing import Mapping, Iterable


PROFILE_TASK_RUNNER_CONTRACT_VERSION = 2
PROFILE_TASK_DEFAULT_RUNNER = "/home/latios/.local/bin/hermes-host"
PROFILE_TASK_OFFICIAL_EXECUTABLE = "/home/latios/.venvs/hermes-agent-host/bin/hermes"
PROFILE_TASK_DOCKER_RUNNER_FORBIDDEN = "yes"
_TRUSTED_OVERRIDE_ROOT = Path("/home/latios/.local/bin")
_FORBIDDEN_RUNNER = "/usr/local/bin/hermes"
_FORBIDDEN_TOKENS = re.compile(r"[\x00\r\n;|&`$()]")


class RunnerValidationError(ValueError):
    """Raised when a runner violates the frozen Host execution contract."""


def _owner_name(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def _mode(mode: int) -> str:
    return f"{stat.S_IMODE(mode):04o}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_parent_chain(path: Path) -> None:
    current = path.parent
    trusted_uids = {0, os.getuid(), os.stat("/").st_uid}
    while True:
        try:
            info = current.stat()
        except OSError as exc:
            raise RunnerValidationError("runner_parent_untrusted") from exc
        group_writable_by_unrelated_group = stat.S_IWGRP & info.st_mode and info.st_gid != os.getgid()
        if not stat.S_ISDIR(info.st_mode) or stat.S_IWOTH & info.st_mode or group_writable_by_unrelated_group:
            raise RunnerValidationError("runner_parent_untrusted")
        if info.st_uid not in trusted_uids:
            raise RunnerValidationError("runner_parent_untrusted")
        if current == current.parent:
            return
        current = current.parent


def _normalise_input(value: object) -> Path:
    if not isinstance(value, str) or not value or value != value.strip():
        raise RunnerValidationError("runner_path_invalid")
    if _FORBIDDEN_TOKENS.search(value):
        raise RunnerValidationError("runner_path_invalid")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise RunnerValidationError("runner_path_must_be_absolute")
    return path


def _allowed_override(realpath: Path, allowed_override_paths: Iterable[str] | None) -> bool:
    if allowed_override_paths is not None:
        allowed = {Path(item).resolve(strict=False) for item in allowed_override_paths}
        return realpath in allowed
    try:
        realpath.relative_to(_TRUSTED_OVERRIDE_ROOT)
    except ValueError:
        return False
    return True


def inspect_runner(path: str, *, allowed_override_paths: Iterable[str] | None = None) -> dict[str, object]:
    """Validate *path* and return redaction-safe executable identity metadata."""
    requested = _normalise_input(path)
    if str(requested) == _FORBIDDEN_RUNNER:
        raise RunnerValidationError("legacy_runner_unsupported_for_host_execution")
    try:
        info = requested.lstat()
    except OSError as exc:
        raise RunnerValidationError("runner_unavailable") from exc
    if stat.S_ISDIR(info.st_mode) or not stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode):
        raise RunnerValidationError("runner_not_regular_file")

    realpath = Path(os.path.realpath(requested))
    if str(realpath) == _FORBIDDEN_RUNNER:
        raise RunnerValidationError("legacy_runner_unsupported_for_host_execution")
    try:
        target = realpath.stat()
    except OSError as exc:
        raise RunnerValidationError("runner_unavailable") from exc
    if not stat.S_ISREG(target.st_mode):
        raise RunnerValidationError("runner_not_regular_file")
    if not os.access(realpath, os.X_OK):
        raise RunnerValidationError("runner_not_executable")
    if target.st_mode & (stat.S_IWOTH | stat.S_IWGRP):
        raise RunnerValidationError("runner_world_or_group_writable")
    _check_parent_chain(realpath)

    canonical = realpath == Path(PROFILE_TASK_DEFAULT_RUNNER)
    official = realpath == Path(PROFILE_TASK_OFFICIAL_EXECUTABLE)
    override = _allowed_override(realpath, allowed_override_paths)
    if not (canonical or official or override):
        raise RunnerValidationError("runner_target_untrusted")

    runner_sha256 = _sha256(realpath)
    identity_payload = {
        "runner_realpath": str(realpath),
        "runner_sha256": runner_sha256,
        "runner_owner": _owner_name(target.st_uid),
        "runner_mode": _mode(target.st_mode),
    }
    identity_hash = hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "runner_path": str(requested),
        "runner_realpath": str(realpath),
        "runner_identity_hash": identity_hash,
        "runner_contract_version": PROFILE_TASK_RUNNER_CONTRACT_VERSION,
        "host_mode": True,
        "runner_owner": identity_payload["runner_owner"],
        "runner_mode": identity_payload["runner_mode"],
        "runner_sha256": runner_sha256,
        "launcher_target": str(realpath) if requested != realpath else "",
        "launcher_target_sha256": runner_sha256 if requested != realpath else "",
        "canonical_default": canonical,
        "official_executable": official,
        "legacy_docker_runner_rejected": True,
    }


def resolve_runner(*, environ: Mapping[str, str] | None = None, allowed_override_paths: Iterable[str] | None = None) -> str:
    """Resolve the canonical Host runner with explicit override precedence."""
    env = os.environ if environ is None else environ
    override = str(env.get("AOTA_HERMES_RUNNER", "")).strip()
    if override:
        inspect_runner(override, allowed_override_paths=allowed_override_paths)
        return str(_normalise_input(override))
    inspect_runner(PROFILE_TASK_DEFAULT_RUNNER)
    return PROFILE_TASK_DEFAULT_RUNNER


def runner_contract(path: str, *, allowed_override_paths: Iterable[str] | None = None) -> dict[str, object]:
    """Return the validated identity persisted in a new frozen manifest."""
    return inspect_runner(path, allowed_override_paths=allowed_override_paths)
