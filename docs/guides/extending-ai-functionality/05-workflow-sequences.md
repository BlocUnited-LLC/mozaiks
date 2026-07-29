# Workflow Sequences

`workflows/extended_orchestration/extension_registry.json` declares workflow
sequences the Refinement Engine can re-enter.

The Refinement Engine chooses a route. The workflow registry owns what that route
means.

Apps can use the packaged factory workflow registry directly or extend it with
an app-local overlay:

```json
{
  "pack_name": "MyAppOverlay",
  "version": 3,
  "extends": "mozaiks.default_workflow_registry",
  "workflows": [
    { "id": "ProductWorkflow", "description": "App-owned product workflow" }
  ],
  "entrypoints": [
    { "id": "create_app", "remove": true },
    {
      "id": "product_workflow",
      "workflow": "ProductWorkflow",
      "path": "/product-workflow",
      "label": "Product Workflow"
    }
  ],
  "workflow_sequences": [
    {
      "id": "product_revision",
      "affected_declarative_families": ["product_artifact"],
      "steps": [{ "workflows": ["ProductWorkflow"] }]
    }
  ],
  "artifact_dependency_graph": {
    "product_artifact": []
  }
}
```

Use the overlay form when an app wants default Factory/Studio sequences and
app-owned product workflows in the same effective registry. Lists merge by
`id`; `{ "id": "...", "remove": true }` hides a packaged entry such as the
default create route.

When a registry extends `mozaiks.default_workflow_registry`, workflow folder
resolution also searches the packaged factory workflow root. App-local workflow
folders remain in the app `workflows/` directory and override packaged folders
with the same id. Without `extends`, the app registry is explicit and only the
selected app workflow root is searched.

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
