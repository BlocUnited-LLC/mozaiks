# Mozaiks Control Plane

Mozaiks does not just generate apps. It knows how to change them intelligently
after generation.

When you ask for a change, Mozaiks does not treat every request like a blind
code edit. It understands whether you are asking for a tiny patch, a
design adjustment, a new capability, or a concept-level pivot. It routes to the
smallest accurate next step, preserves everything above the change, and only
regenerates what actually needs to move.

That is the product promise.

The control plane is the system that makes that promise real. The harness is a
single runtime shell inside that system. The control plane decides what should
happen next; the harness is the implementation that executes the checkpoint and
routing flow.

## What The Control Plane Actually Does

When a change request comes in, the control plane:

- classifies the request as `patch`, `design`, `feature`, or `core`
- checks which artifact family is affected and what downstream work that implies
- routes into the smallest valid workflow sequence or coding path
- scopes the change to the relevant contracts and files
- decides whether to auto-apply, ask for confirmation, or clarify first

The control plane depends on persisted revision context, artifact summaries, and
the Context Graph.

The Context Graph tells Mozaiks what exists and how it is connected. The control
plane uses that map to decide what happens next.

## Declarative Pack

The runtime is driven by a first-party declarative pack. Its job is to describe
what the control plane is allowed to do, what runtime implementation should do
it, and which workflow sequences can be re-entered.

| File | Shape | What it controls | Derived from |
|---|---|---|---|
| `factory_app/app/config/ai.json` | app config JSON | Enables the control plane and selects model profiles | The app's chosen control-plane policy and model budget |
| `factory_app/control_plane/config/control_plane.yaml` | control-plane manifest YAML | Declares checkpoints, prompts, routes, tools, and the harness implementation | The first-party control-plane pack for this app workspace |
| `factory_app/workflows/extended_orchestration/extension_registry.json` | workflow registry JSON | Defines the workflow sequences the router can re-enter | The cross-workflow build and revision graph |

So the current system is not one monolithic router hardcoded in Python. It is a
runtime executing a declarative control-plane pack.

## Schema Shapes

These are the current shapes, not target shapes. They are derived from the
runtime loaders and manifest models, so the docs match what the system accepts
today.

### `factory_app/app/config/ai.json`

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

### `factory_app/control_plane/config/control_plane.yaml`

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

### `factory_app/workflows/extended_orchestration/extension_registry.json`

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

## Harness

The harness is the runtime shell inside the broader control plane. It is the
implementation named in `control_plane.yaml`, and it coordinates checkpoints,
tool calls, and routing decisions.

If you want the short version:

- control plane = the full intelligence layer
- harness = the runtime shell that runs the checkpoint flow
- declarative pack = the files that configure that flow

## How A Request Moves Through The System

```text
Your request
  → change classifier assigns patch / design / feature / core
  → route resolver selects a workflow sequence or scoped coding path
  → patch requests go to scope proposal + coding worker
  → feature/design requests go to contract-surface planning + regeneration
  → decision policy decides auto-apply, confirm, clarify, or restart
```

Two examples make that concrete.

**Patch request**

```text
"Fix the broken column header in the projects table"

→ classify: patch
→ query Context Graph for likely page/module scope
→ run scoped coding worker against only those files
→ auto-apply or ask to confirm depending on confidence
```

**Feature request**

```text
"Add export controls to the projects table"

→ classify: feature
→ route: app_revision
→ plan contract surfaces in dependency order
→ regenerate affected surfaces without rerunning the full build
```

See [Refinement Control Plane](./04-refinement-control-plane.md) for the
refinement-specific path inside this system.

---

**Architecture references**

- [Control-Plane Harness Architecture](../../architecture/workflows/control-plane-harness-architecture.md)
- [Context Graph and Code Intelligence](../../architecture/foundations/context-graph-and-code-intelligence.md)
