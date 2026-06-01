# Control Plane Schemas

These are the current schema shapes for the control-plane pack. They are
derived from the runtime loader and manifest models, so the docs match what the
system accepts today.

## `factory_app/app/config/ai.json`

This file is an app config JSON object with a `control_plane` section that
enables the control plane and selects model profiles.

```json
{
  "ask": {
    "ask_mode_prompt": "...",
    "ask_context_variables": null
  },
  "chat": {
    "chat_startup_mode": "ask"
  },
  "workflows": {
    "entry_point": "ValueEngine",
    "resume_policy": "last_active_then_oldest_then_entry_point"
  },
  "control_plane": {
    "enabled": true,
    "profile": "default",
    "llm_profiles": {
      "classifier": {
        "purpose": "...",
        "expected_behavior": "...",
        "llm_config": { "model": "gpt-5-nano", "temperature": 0 }
      },
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

Derived from `mozaiksai.control_plane.config.ControlPlaneConfig` and
`ControlPlaneCapabilityConfig`.

## `factory_app/control_plane/config/control_plane.yaml`

This file is the manifest for the first-party control-plane pack.

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
      label: app bundle
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

Derived from `mozaiksai.control_plane.schema.ControlPlaneManifest`,
`ControlPlaneRoutingManifest`, and `ControlPlaneCheckpointManifest`.

## `factory_app/workflows/extended_orchestration/extension_registry.json`

This file is the cross-workflow registry that defines the sequences the control
plane may re-enter.

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
  "workflows": [{ "id": "ValueEngine" }, { "id": "ThemeCapture" }, { "id": "DesignDocs" }, { "id": "AgentGenerator" }, { "id": "AppGenerator" }],
  "workflow_sequences": [{ "id": "build" }, { "id": "full_rebuild" }, { "id": "app_revision" }, { "id": "design_revision" }]
}
```

Derived from the workflow graph and the current build/revision path definitions.