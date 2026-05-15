# Control-Plane Harness Architecture

This document describes the canonical control-plane shape for Mozaiks.

The control plane is the framework layer that sits above workflow-local AG2
execution and decides what should happen when a harnessed request or checkpoint
event arrives.

It is the piece that makes the Mozaiks build and refinement UX feel
intent-aware without turning every workflow into a giant router.

## Purpose

The harness exists for requests that are not well modeled as:

- normal runtime chat
- workflow-local handoffs
- MFJ decomposition
- static `route_to` transitions

Examples:

- "Fix this generated dashboard."
- "Add export controls to the current app."
- "Restart this from concept."
- "This should become investor-facing."

Those requests need:

- persisted session/artifact context
- intent interpretation
- deterministic continuation policy
- optional coding refinement
- clear user-facing decisions

That is the control plane.

## Ownership Model

### `mozaiksai/core/`

Framework-wide primitives that are not specific to the control plane itself.

Examples:

- generic runtime utilities
- session/runtime internals
- workflow/runtime foundations

### `mozaiksai/control_plane/`

The canonical control-plane subsystem.

```text
mozaiksai/control_plane/
  __init__.py
  config.py
  contracts.py
  executor.py
  loader.py
  ports.py
  runtime.py
  schema.py
  implementations/
    change_classifier.py
    refinement_router.py
    harness_decision.py
    scope_proposer.py
    coding_worker.py
    orchestration_control.py
```

This layer owns:

- control-plane runtime
- checkpoint dispatch
- config/schema/loader/contracts
- generic tool execution boundaries
- first-party Mozaiks checkpoint handlers

This is the canonical runtime package.

### `factory_app/control_plane/`

App-zero declaratives and builder-specific tools.

```text
factory_app/control_plane/
  config/
    control_plane.yaml
    tools.yaml
    policies.yaml
  prompts/
    change_classifier_system.yaml
    coding_scope_selection_system.yaml
    coding_refinement_system.yaml
  tools/
    get_revision_context.py
    get_artifact_summary.py
    get_artifact_workspace_catalog.py
    get_artifact_workspace_scope.py
    _artifact_workspace.py
    _shared.py
  ui/
```

`factory_app` is app-zero. It should feel like an authored app surface, not the
owner of the framework runtime.

This layer owns:

- the first-party declarative control-plane pack
- first-party prompt text
- first-party artifact/workspace context tools
- future control-plane UI surfaces

It should not own the runtime engines.

## What The Harness Is Not

The harness is not:

- a workflow
- an AG2 groupchat
- a module handler under `app/modules/*`
- a global prompt wrapped around every message
- a replacement for `extension_registry.json`

The split is:

- control plane
  - interprets checkpoint events
  - decides continuation
- extension graph
  - defines legal workflow movement
- AG2/workflows
  - execute once a workflow is selected

## Pack Model

The app-level switch lives in `app/config/ai.json`:

```json
{
  "control_plane": {
    "enabled": true,
    "classifier": {
      "enabled": true,
      "llm_config": {
        "model": "gpt-4o-mini",
        "temperature": 0.0
      }
    },
    "coding": {
      "enabled": true,
      "llm_config": {
        "model": "gpt-5.2-codex",
        "temperature": 0.1
      }
    }
  }
}
```

That config only enables capabilities and provides model config. It does not
point to Python implementation files.

The declarative pack lives under `factory_app/control_plane/` or an app-local
override at `<workspace>/control_plane/`.

## Declarative Files

### `config/control_plane.yaml`

Declares:

- harness entrypoint
- artifact routing
- checkpoint events
- handler entrypoints
- prompt ids
- tool ids

Example:

```yaml
schema_version: mozaiks.control_plane
profile:
  id: factory_app
  display_name: Factory App Harness
  description: App-zero declarative control-plane pack for the first-party Mozaiks build experience.
harness:
  implementation: mozaiksai.control_plane.implementations.orchestration_control:OrchestrationControlHarness
  supported_trigger_sources:
    - refinement
routing:
  default_artifact_kind: app_bundle
  artifacts:
    - artifact_kind: app_bundle
      label: app bundle
      routes:
        patch:
          workflow_sequence: app_revision
          affected_declarative_families: [app_bundle]
          requires_replanning: false
          requires_rebuild: true
        design:
          workflow_sequence: app_surface_revision
          affected_declarative_families: [design_docs, app_bundle]
        feature:
          workflow_sequence: app_revision
          affected_declarative_families: [app_bundle]
        core:
          workflow_sequence: full_rebuild
          affected_declarative_families: [concept, design_docs, workflow_bundle, app_bundle]
checkpoints:
  - id: request_intake
    event: request_submitted
    entrypoint: mozaiksai.control_plane.implementations.change_classifier:LLMChangeClassifier
    prompt_id: change_classifier_system
    tool_ids:
      - get_revision_context
      - get_artifact_summary

  - id: route
    event: route_requested
    entrypoint: mozaiksai.control_plane.implementations.refinement_router:RefinementTriggerRouteResolver

  - id: decision
    event: decision_requested
    entrypoint: mozaiksai.control_plane.implementations.harness_decision:FirstPartyHarnessDecisionPolicy
```

Route rules:

- `workflow_sequence` is the canonical route target.
- The sequence is resolved from `extension_registry.json`.
- `route_to` is optional and only needed when the route must start at a
  workflow other than the first workflow in the sequence.
- `affected_workflows` is normally derived from the sequence and should not be
  duplicated in the control-plane pack.

### `config/tools.yaml`

Declares harness-owned tools.

Example:

```yaml
tools:
  - id: get_artifact_summary
    kind: context_tool
    description: Load artifact lineage and version metadata.
    entrypoint: factory_app.control_plane.tools.get_artifact_summary:get_artifact_summary
    available_to:
      - request_submitted
      - route_requested
```

### `prompts/*.yaml`

One prompt per file.

Example:

```yaml
id: change_classifier_system
content: |
  You are the authoritative Mozaiks refinement change classifier.
```

### `config/policies.yaml`

Declares deterministic bounds.

Current first use:

- scope size limits
- auto-apply thresholds
- overflow behavior

## Checkpoint Model

The control plane is checkpoint-driven.

Current first-party checkpoints:

- `request_submitted`
- `route_requested`
- `decision_requested`
- `scope_requested`
- `coding_requested`

These are the harness-native units of execution.

### `request_submitted`

LLM-backed interpretation of the request.

Current first-party handler:

- `mozaiksai/control_plane/implementations/change_classifier.py`

### `route_requested`

Deterministic workflow-route selection from typed request intent.

Current first-party handler:

- `mozaiksai/control_plane/implementations/refinement_router.py`

### `decision_requested`

Deterministic user-facing decision shaping.

Examples:

- `workflow_reentry`
- `core_restart`
- `auto_patch`
- `clarify_scope`
- `fallback_workflow`

Current first-party handler:

- `mozaiksai/control_plane/implementations/harness_decision.py`

### `scope_requested`

LLM-backed file-scope proposal when explicit coding scope is missing.

Current first-party handler:

- `mozaiksai/control_plane/implementations/scope_proposer.py`

### `coding_requested`

Scoped coding-worker execution for eligible patch refinements.

Current first-party handler:

- `mozaiksai/control_plane/implementations/coding_worker.py`

## Tool Model

Control-plane tools are leaf capabilities used by checkpoints.

They are not:

- AG2 agent tools
- workflow-local lifecycle tools
- module actions

Examples:

- `get_revision_context`
- `get_artifact_summary`
- `get_artifact_workspace_catalog`
- `get_artifact_workspace_scope`

The current first-party tools live under:

- `factory_app/control_plane/tools/*`

## Runtime Flow

At runtime:

1. the host loads `control_plane` settings from `app/config/ai.json`
2. `mozaiksai/control_plane/loader.py` resolves the active pack
3. `mozaiksai/control_plane/runtime.py` builds a checkpoint runtime
4. the harness entrypoint is instantiated from `harness.implementation`
5. the harness binds and runs the checkpoints it needs

Current Studio refinement flow:

```text
Studio trigger
  -> OrchestrationControlHarness
  -> request_submitted
  -> route_requested
  -> decision_requested
  -> SessionRouter | coding worker | harness decision response
```

If coding is eligible:

```text
... -> scope_requested -> coding_requested
```

## Relation To Workflows And Extensions

The harness depends on the workflow graph, but it is not the graph.

- `extension_registry.json`
  - legal transitions and workflow movement
- control plane
  - semantic interpretation and continuation choice
- workflow runtime
  - actual execution

This is why the harness was required for the Mozaiks build UX. The extension
graph alone cannot interpret `"make this a blockchain marketplace"` or decide
between `clarify_scope`, `run_workflow`, or `restart_upstream`.

## Host Model

Today the first-party harness is mounted by Studio.

That means:

- Studio is the primary harnessed surface
- platform/runtime apps should remain passthrough unless they opt in later

Host-aware gating still matters, but the canonical runtime ownership is now
correct.

## Module Stub

This path still exists:

```text
factory_app/app/modules/factory_control_plane/backend/
```

It is only the dynamic-module stub.

It is not the harness runtime.

## Canonical Paths

Use these paths as source of truth:

- `mozaiksai/control_plane/*`
- `factory_app/control_plane/config/*`
- `factory_app/control_plane/prompts/*`
- `factory_app/control_plane/tools/*`

Do not treat these as canonical:

- `factory_app/app/modules/factory_control_plane/backend/*`
- any removed bridge-era `profiles/default/*` layout
- any removed bridge-era `implementations/default/*` layout
- old root-level `factory_app/control_plane/*.py` runtime handlers

## Guidance

If you are changing framework runtime behavior:

- edit `mozaiksai/control_plane/*`

If you are changing the first-party builder pack:

- edit `factory_app/control_plane/config/*`
- edit `factory_app/control_plane/prompts/*`
- edit `factory_app/control_plane/tools/*`

If you are looking at the module stub under `app/modules/...`, you are not in
the live harness runtime.
