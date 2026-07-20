# CodeGraph Pending Scope

## Projection fields

`aota_codegraph_lifecycle_status` preserves the raw CodeGraph observation and
adds a bounded projection:

```text
raw_pending
 effective_pending
 known_exclusions.oversized_source
 oversized_sources[]
 semantic_coverage
 coverage_complete
 warnings
```

`oversized_sources[]` contains only project-relative `relative_path`, actual
`size_bytes`, `limit_bytes`, extension/language, fixed reason
`codegraph_max_file_size`, and `semantic_impact=true`. It never returns source
contents and is capped at 200 items with 512-character paths.

## Eligibility contract

A pending item is an `oversized_source_exclusion` only if every condition is
verified:

1. the path is relative to the registered canonical project root;
2. every path component is non-symlink and the target is a readable regular file;
3. the extension is supported by the CodeGraph compatibility set;
4. no effective ignore rule matches;
5. the actual file size is greater than the authoritative CodeGraph limit;
6. the item explicitly has no index membership;
7. the pending reason is `added` or equivalent missing membership;
8. no parse, permission, path, or corruption error is present.

Exact-limit files, one-byte-over files with missing evidence, ignored files,
unsupported files, symlinks, escapes, unreadable files, and already-indexed
files remain effective pending. File metadata supplied by a status payload is
not enough to bypass root containment or filesystem checks.

## Fail-closed behavior

The limit source order is status-configured value, then the explicit `1.1.1`
compatibility profile (`1048576` bytes). Unknown CodeGraph versions without a
limit authority emit `oversized_classification_unavailable` and do not exclude
anything. A missing pending-item list likewise leaves raw pending effective.

## Regression fixture

The isolated fixture models the WebUI observation: 95 files, 6850 nodes,
27404 edges, raw added=2, and the two source paths
`api/routes.py` (1056777 bytes) and `static/i18n.js` (1645929 bytes). With
complete evidence it expects effective added=0, two oversized exclusions,
`ready`, `semantic_ready=true`, `semantic_coverage=partial`, and
`coverage_complete=false`.

The fixture is temporary and does not read or write a real `.codegraph` index.
