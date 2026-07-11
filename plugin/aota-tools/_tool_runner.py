#!/usr/bin/env python3
"""Helper entrypoint inside aota-tools package that routes calls to the right tool handler.

Usage: python3 /path/to/_tool_runner.py <TOOL_NAME> '<JSON_ARGS>'

Available tools: task_spec_create, profile_task_approve, profile_task_start, profile_task_status, handoff_list, handoff_open, handoff_ack
"""
import json
import os
import sys

# We're inside the aota-tools package dir, so relative imports work
TOOLS = {}

def reg(name, module, handler_name="handle"):
    import importlib
    mod = importlib.import_module(module)
    TOOLS[name] = getattr(mod, handler_name)

reg("task_spec_create", "_task_spec_create")
reg("profile_task_approve", "_profile_task_approve")
reg("profile_task_start", "_profile_task_start")
reg("profile_task_status", "_profile_task_status")
reg("handoff_list", "_handoff_list")
reg("handoff_open", "_handoff_open")
reg("handoff_ack", "_handoff_ack")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(json.dumps({"status": "failed", "error": "Usage: tool_runner.py <tool_name> '<json_args>'"}), file=sys.stderr)
        sys.exit(1)

    tool_name = sys.argv[1]
    args = json.loads(sys.argv[2])

    handler = TOOLS.get(tool_name)
    if not handler:
        print(json.dumps({"status": "failed", "error": f"Unknown tool: {tool_name}"}), file=sys.stderr)
        sys.exit(1)

    result = handler(args)
    # Print result to stdout for capture
    print(result)
