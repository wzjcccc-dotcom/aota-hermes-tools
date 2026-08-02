# Hermes Host upgrade note: coder schema-probe projection

## Why this Host change exists

The coder exact-file schema-probe route already has deterministic AOTA tool
names: `aota_search_files` is the first domain call for a named schema probe,
and `aota_read_file` is the only optional narrow follow-up. Hermes Host's
progressive tool-search assembly previously deferred every non-core plugin
tool, so coder paid for `tool_search`/`tool_describe` before reaching tools it
already knew. That was a runtime projection problem, not a new AOTA tool.

## Canonical source change

`/home/latios/workspace/hermes-agent-host/tools/tool_search.py` now accepts the
config key `tools.tool_search.always_visible_tools`. Names in that allowlist
remain direct model-visible schemas; all other plugin tools still go through
the existing `tool_search` / `tool_describe` / `tool_call` bridge. The setting
does not disable progressive disclosure globally and does not grant a tool
that the profile's toolsets did not already expose.

The coder profile owns the runtime setting in:

`profiles/coder/config.yaml`

```yaml
tools:
  tool_search:
    always_visible_tools:
      - aota_read_file
      - aota_search_files
```

The profile Skill/SOUL remains the behavioral authority: the schema-probe
route must not call `tool_search` or `tool_describe` for either projected tool.
The bridge remains available for unrelated coder capabilities so implementation
work is not forced into an all-tools-eager posture.

## Upgrade / rollback procedure

After a Hermes Host update, verify the source and focused tests before managed
deployment:

```bash
cd /home/latios/workspace/hermes-agent-host
rtk python3 -m py_compile tools/tool_search.py
rtk python3 -m pytest -q tests/tools/test_tool_search.py
```

If `always_visible_tools` is absent or the focused test fails, do not claim the
coder projection is active. Restore the Host source change from the matching
AOTA deployment backup or temporarily remove the profile setting; do not turn
off tool-search globally as a workaround.

