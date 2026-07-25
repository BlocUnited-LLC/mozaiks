# Workflow Sequences

`workflows/extended_orchestration/extension_registry.json` declares workflow
sequences the refinement engine can re-enter.

The refinement engine chooses a route. The workflow registry owns what that route
means.

Example:

```json
{
  "pack_name": "DefaultPack",
  "version": 3,
  "artifact_dependency_graph": {
    "concept": [],
    "brand": ["concept"],
    "design_docs": ["concept"],
    "experience_spec": ["concept", "design_docs"],
    "workflow_bundle": ["design_docs"],
    "app_bundle": ["design_docs", "experience_spec", "workflow_bundle", "brand"]
  },
  "workflows": [
    { "id": "ValueEngine", "description": "Concept and value decomposition" },
    { "id": "ThemeCapture", "description": "Captures visual identity" },
    { "id": "DesignDocs", "description": "Frontend, backend, database design docs" },
    { "id": "AgentGenerator", "description": "Generates workflow artifacts" },
    { "id": "AppGenerator", "description": "Generates app schema and module files" }
  ],
  "workflow_sequences": [
    {
      "id": "app_revision",
      "affected_declarative_families": ["app_bundle"],
      "steps": [{ "workflows": ["AppGenerator"] }]
    },
    {
      "id": "full_rebuild",
      "affected_declarative_families": ["concept", "design_docs", "workflow_bundle", "app_bundle"],
      "steps": [
        { "workflows": ["ValueEngine"] },
        { "workflows": ["DesignDocs"] },
        { "workflows": ["AgentGenerator", "AppGenerator"] }
      ]
    }
  ]
}
```

Each sequence referenced by `refinement_harness/config/harness.yaml` must
exist here and must declare `affected_declarative_families`.

Keep route impact metadata here, not in `harness.yaml`.
