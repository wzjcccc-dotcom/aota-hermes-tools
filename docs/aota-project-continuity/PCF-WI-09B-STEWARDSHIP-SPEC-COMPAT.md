# PCF-WI-09B — Temporary Stewardship SPEC Compatibility

SUPERSEDED_BY=PCF-WI-09C

This temporary `task_kind=stewardship` compatibility layer is superseded by
the WI-09C closed `spec_kind` schema. It adds only fixed routing to
`project-steward` and a closed `role_contract`:

```yaml
project_id: <registered project>
allowed_project_artifacts: [readme | changelog | roadmap | project_doc | project_metadata]
operation: intake | context_prepare | docs_update | artifact_link | close
forbidden_actions: []
work_item_id: <optional bounded id>
```

The common existing fields carry objective (`goal`), acceptance criteria,
scope, and evidence. `write_scope` must be empty. The layer rejects arbitrary
profile selection, source write scope, terminal permissions, CodeGraph rebuild
permissions, deployment permissions, unknown operations, and unknown role
fields. Bounded docs/metadata mutations are enforced separately from
`write_scope` by trusted Project Steward tools.
