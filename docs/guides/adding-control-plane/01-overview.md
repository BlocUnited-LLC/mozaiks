# Extending AI Functionality

Mozaiks does not just generate apps. You can extend the AI behavior of a
generated app after generation by editing a small set of declarative files.

When you ask for a change, Mozaiks does not treat every request like a blind
code edit. It understands whether you are asking for a tiny patch, a design
adjustment, a new capability, or a concept-level pivot. It routes to the
smallest accurate next step, preserves everything above the change, and only
regenerates what actually needs to move.

This guide shows the control-plane files you edit to extend AI behavior in a
generated app.

In the canonical app workspace contract, `app/config/ai.json` owns runtime
startup for `ask`, `chat`, and `workflows`. Optional control-plane behavior
lives beside the app bundle under `control_plane/config/`.

In this repo's first-party builder workspace, those canonical paths are
dogfooded as:

- `factory_app/app/config/ai.json`
- `factory_app/control_plane/config/runtime.yaml`
- `factory_app/control_plane/config/control_plane.yaml`
- `factory_app/workflows/extended_orchestration/extension_registry.json`

## Schema Shapes

| File | Shape | What it controls | Derived from |
|---|---|---|---|
| `app/config/ai.json` | app runtime startup JSON | `ask`, `chat`, and `workflows` startup defaults only | The app's runtime startup contract |
| `control_plane/config/runtime.yaml` | control-plane runtime policy YAML | `enabled`, `profile`, `llm_profiles`, classifier/coding/contract-surface policy | The app's control-plane runtime policy and model budget |
| `control_plane/config/control_plane.yaml` | control-plane manifest YAML | Harness, checkpoints, routing, prompts, and tool ids | The app's declarative harness pack |
| `workflows/extended_orchestration/extension_registry.json` | workflow registry JSON | The `workflow_sequences` the router may re-enter | The build and revision graph |

## `app/config/ai.json`

This file keeps runtime startup only. It does not carry control-plane policy.

Use it for:

- `ask.ask_mode_prompt`
- `ask.ask_context_variables`
- `chat.chat_startup_mode`
- `workflows.entry_point`
- `workflows.resume_policy`

```json
{
  "ask": {
    "ask_mode_prompt": "You are the Mozaiks assistant. Help users shape, generate, connect, and refine apps in Mozaiks Studio using the shared builder workflows.",
    "ask_context_variables": null
  },
  "chat": {
    "chat_startup_mode": "ask"
  },
  "workflows": {
    "entry_point": "ValueEngine",
    "resume_policy": "last_active_then_oldest_then_entry_point"
  }
}
```

## `control_plane/config/runtime.yaml`

This file is the app-local control-plane runtime policy.

Use it for:

- `enabled`
- `profile`
- `llm_profiles`
- `classifier`
- `coding`
- `contract_surface`

```yaml
schema_version: mozaiks.control_plane.runtime
enabled: true
profile: default
llm_profiles:
  classifier:
    purpose: Change classification for refinement routing.
    expected_behavior: Distinguish patch, design, feature, and core requests.
    llm_config:
      model: gpt-5-nano
      temperature: 0
  codegen:
    purpose: Scoped coding and contract-surface refinement.
    expected_behavior: Produce bounded repair or extension plans.
    llm_config:
      model: gpt-5.2-codex
      temperature: 0.1
classifier:
  enabled: true
  llm_profile: classifier
coding:
  enabled: true
  llm_profile: codegen
contract_surface:
  enabled: true
  llm_profile: codegen
```

## `control_plane/config/control_plane.yaml`

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

## `workflows/extended_orchestration/extension_registry.json`

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

## When To Add A Control Plane

Most apps should not have one.

Omit a control plane when the app is mostly:

- deterministic modules and CRUD
- one known workflow launch
- fixed `workflow_sequences` or transitions with no semantic router above them

Add a control plane when the app needs at least two of these signals, or one
governance-critical signal is dominant:

- semantic request intake across multiple valid execution paths
- checkpointed revision or session continuity
- policy, approval, escalation, or risk gating above workflows/modules
- scoped coding or contract-surface planning after route selection
- artifact routing based on request meaning rather than fixed sequence wiring

Ownership is split deliberately:

- `ValueEngine` may hint that a control-plane surface is needed
- `DesignDocs` decides whether `surface_kind = control_plane` is actually warranted
- `AppGenerator` materializes `control_plane/config/runtime.yaml`, `control_plane/config/control_plane.yaml`, and related files
- `AgentGenerator` stays responsible for workflow bundles that the control plane may route into

## Read Next

- [Context Graph](../platform-intelligence/01-context-graph.md)
- [Harness](../platform-intelligence/02-harness.md)
- [Refinement](../platform-intelligence/03-refinement.md)
