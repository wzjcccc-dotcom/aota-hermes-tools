"""Canonical frozen-SPEC scope projection and digest helpers.

WI-09C stores project scope in ``spec.payload``.  This module is deliberately
small so task start, bounded project tools, and scope verification do not grow
their own subtly different SPEC parsers.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
from collections.abc import Mapping
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any


SCOPE_FIELDS = ("read_scope", "write_scope", "forbidden_scope")
SCOPE_SCHEMA_VERSION = 1


class CanonicalScopeError(ValueError):
    """Raised when a frozen SPEC cannot produce a safe canonical scope."""


def _normalize_scope_item(value: object, field: str, index: int) -> str:
    if not isinstance(value, str):
        raise CanonicalScopeError(f"{field}[{index}] must be a string")
    item = value.strip()
    if not item:
        raise CanonicalScopeError(f"{field}[{index}] must not be empty")
    if "\x00" in item:
        raise CanonicalScopeError(f"{field}[{index}] contains NUL")
    if "\\" in item:
        raise CanonicalScopeError(f"{field}[{index}] contains a backslash")
    if item.startswith("/") or PurePosixPath(item).is_absolute() or PureWindowsPath(item).is_absolute():
        raise CanonicalScopeError(f"{field}[{index}] must be relative")
    if any(part == ".." for part in item.split("/")):
        raise CanonicalScopeError(f"{field}[{index}] contains '..' traversal")
    # Keep glob syntax byte-for-byte intact apart from the documented trim.
    # posixpath.normpath is intentionally not used because it can alter glob
    # semantics (for example, repeated separators and wildcard segments).
    return item


def _read_scope_field(container: Mapping[str, Any], field: str) -> list[str]:
    if field not in container:
        return []
    value = container[field]
    if not isinstance(value, list):
        raise CanonicalScopeError(f"{field} must be a list[str]")
    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        normalized = _normalize_scope_item(item, field, index)
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def extract_canonical_scope(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Extract the frozen scope projection from a canonical SPEC.

    Payload keys always win, including an explicit empty list.  A top-level
    field is consulted only when that payload key is absent, which provides a
    bounded, observable compatibility path for historical SPECs.
    """
    if not isinstance(spec, Mapping):
        raise CanonicalScopeError("frozen SPEC must be a mapping")
    payload = spec.get("payload")
    if not isinstance(payload, Mapping):
        raise CanonicalScopeError("frozen SPEC payload must be a mapping")

    source = "payload"
    values: dict[str, list[str]] = {}
    for field in SCOPE_FIELDS:
        if field in payload:
            values[field] = _read_scope_field(payload, field)
        elif field in spec:
            values[field] = _read_scope_field(spec, field)
            source = "legacy_top_level"
        else:
            values[field] = []

    values["scope_source"] = source
    values["scope_schema_version"] = SCOPE_SCHEMA_VERSION
    return values


def compute_scope_digest(scope: Mapping[str, Any]) -> str:
    """Digest only the canonical scope projection and source metadata."""
    value = {field: scope.get(field, []) for field in SCOPE_FIELDS}
    value["scope_source"] = scope.get("scope_source", "payload")
    value["scope_schema_version"] = scope.get("scope_schema_version", SCOPE_SCHEMA_VERSION)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
