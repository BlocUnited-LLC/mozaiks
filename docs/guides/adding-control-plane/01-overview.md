# Add the Control Plane

Add the control plane when an app needs to understand a change request, choose
the right next path, and refine existing app artifacts without treating every
edit like a full rebuild.

Use this guide when you want Mozaiks to:

- classify change requests as `patch`, `design`, `feature`, or `core`
- route a request into the right workflow sequence or scoped coding path
- scope the work to the smallest relevant contracts and files
- preserve the current build state while still allowing targeted refinement

The control plane is not one file. In the current first-party builder app, it is
defined by three declarative surfaces:

## File Structure

```text
factory_app/app/config/ai.json
factory_app/control_plane/config/control_plane.yaml
factory_app/workflows/extended_orchestration/extension_registry.json
```

## Schema Shapes

| File | Shape | What it controls | Derived from |
|---|---|---|---|
| `factory_app/app/config/ai.json` | app config JSON with `control_plane` settings | Enables the control plane and selects model profiles | The app's control-plane policy and model budget |
| `factory_app/control_plane/config/control_plane.yaml` | control-plane manifest YAML | Declares checkpoints, prompts, routes, tools, and the harness implementation | The first-party pack for this app workspace |
| `factory_app/workflows/extended_orchestration/extension_registry.json` | workflow registry JSON | Defines the workflow sequences the router can re-enter | The build and revision graph |

## `factory_app/app/config/ai.json`

This file turns the control plane on and selects the model profiles it uses.

```json
{
  "control_plane": {
    "enabled": true,
    "profile": "default",
    "llm_profiles": {
      "classifier": { "purpose": "...", "expected_behavior": "...", "llm_config": { "model": "gpt-5-nano", "temperature": 0 } },
      "impact_analyzer": { "purpose": "...", "expected_behavior": "..." },
      "architecture": { "purpose": "...", "expected_behavior": "..." },
      "planner_replanner": { "purpose": "...", "expected_behavior": "..." },
      "codegen": { "purpose": "...", "expected_behavior": "..." },
      "reviewer_validator": { "purpose": "...", "expected_behavior": "..." }
    },
    "classifier": { "enabled": true, "llm_profile": "classifier" },
    "coding": { "enabled": true, "llm_profile": "codegen" },
    "contract_surface": { "enabled": true, "llm_profile": "codegen" }
  }
}
```

## `factory_app/control_plane/config/control_plane.yaml`

This file declares the actual control-plane manifest: the harness, routing,
checkpoints, prompts, and tool bindings.

```yaml
schema_version: mozaiks.control_plane
profile:
  id: factory_app
  display_name: Factory App Harness
  description: First-party declarative control-plane pack for the Mozaiks builder/reference app workspace.
harness:
  implementation: mozaiksai.control_plane.implementations.orchestration_control:OrchestrationControlHarness
routing:
  default_artifact_kind: app_bundle
  artifacts:
    - artifact_kind: app_bundle
      routes:
        patch:
          workflow_sequence: app_revision
        design:
          workflow_sequence: app_surface_revision
        feature:
          workflow_sequence: app_revision
        core:
          workflow_sequence: full_rebuild
checkpoints:
  - id: request_intake
    event: request_submitted
    entrypoint: mozaiksai.control_plane.implementations.change_classifier:LLMChangeClassifier
    prompt_id: change_classifier_system
    tool_ids: [get_revision_context, get_artifact_summary, get_stale_artifact_families]
```

## `factory_app/workflows/extended_orchestration/extension_registry.json`

This file defines the workflow sequences the control plane can re-enter.

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
    { "id": "ValueEngine", "description": "Concept & value decomposition" },
    { "id": "ThemeCapture", "description": "Captures visual identity and produces theme_config.json" },
    { "id": "DesignDocs", "description": "Frontend, backend, database design docs" },
    { "id": "AgentGenerator", "description": "Generates workflow artifacts and agent specs" },
    { "id": "AppGenerator", "description": "Generates app schema and module files" }
  ],
  "workflow_sequences": [
    { "id": "build" },
    { "id": "full_rebuild" },
    { "id": "app_revision" },
    { "id": "design_revision" }
  ]
}
```

## Read Next

- [Context Graph](../platform-intelligence/01-context-graph.md)
- [Harness](../platform-intelligence/02-harness.md)
- [Refinement](../platform-intelligence/03-refinement.md)
