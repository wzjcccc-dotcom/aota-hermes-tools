"""Bounded next-tool hints derived from canonical model-facing schemas.

The helper never chooses a strategy or invokes a tool.  It only projects the
required and explicitly useful fields from an existing registered schema so a
model can continue a canonical lifecycle without another ``tool_describe``.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence


_SCHEMA_KEYS = (
    "type",
    "enum",
    "const",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
    "items",
)


def _compact_property(value: Mapping[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in _SCHEMA_KEYS:
        if key not in value:
            continue
        item = value[key]
        if key == "items" and isinstance(item, Mapping):
            item = _compact_property(item)
        compact[key] = copy.deepcopy(item)
    return compact


def minimal_next_tool_schema(
    tool_schema: Mapping[str, Any],
    *,
    include: Sequence[str] = (),
) -> dict[str, Any]:
    """Return a compact parameter schema without duplicating descriptions."""
    parameters = tool_schema.get("parameters")
    if not isinstance(parameters, Mapping):
        return {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    properties = parameters.get("properties")
    if not isinstance(properties, Mapping):
        properties = {}
    required = [item for item in parameters.get("required", []) if isinstance(item, str)]
    names: list[str] = []
    for name in (*required, *include):
        if name in properties and name not in names:
            names.append(name)
    return {
        "type": "object",
        "properties": {
            name: _compact_property(properties[name])
            for name in names
            if isinstance(properties[name], Mapping)
        },
        "required": [name for name in required if name in names],
        "additionalProperties": bool(parameters.get("additionalProperties", False)),
    }


def attach_next_tool_option(
    result: dict[str, Any],
    tool_schema: Mapping[str, Any],
    *,
    arguments: Mapping[str, Any] | None = None,
    include: Sequence[str] = (),
    reason: str,
) -> dict[str, Any]:
    """Attach one protocol-eligible option while leaving the model in control."""
    tool = str(tool_schema.get("name") or "")
    if not tool:
        return result
    schema = minimal_next_tool_schema(tool_schema, include=include)
    option: dict[str, Any] = {
        "tool": tool,
        "schema_version": 1,
        "parameters": schema,
        "reason": reason[:200],
    }
    if arguments is not None:
        option["arguments"] = dict(arguments)
    result.setdefault("allowed_next_tool", tool)
    if arguments is not None:
        result.setdefault("allowed_next_arguments", dict(arguments))
    result.setdefault("allowed_next_tool_schema", schema)
    result.setdefault("next_options", [option])
    return result


__all__ = ["attach_next_tool_option", "minimal_next_tool_schema"]
