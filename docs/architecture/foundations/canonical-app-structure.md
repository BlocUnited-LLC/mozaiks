# Canonical App Structure

**Last updated:** 2026-03-12  
**Status:** Current architecture reference  
**Audience:** App-bundle authors, generator authors, and core maintainers  
**Prerequisites:** [app-bundle-declaratives.md](app-bundle-declaratives.md), [workflow-architecture.md](workflow-architecture.md)

---

## Purpose

This document defines the canonical file structure Mozaiks Core currently
consumes from an app bundle in this repo.

This is not a generic "any app repo" sketch.
It is the concrete structure the current runtime is built around.

---

## Canonical Bundle Layout

```text
platform/
├── app.json
│
├── config/
│   ├── ai.json
│   ├── navigation_config.json
│   ├── theme_config.json
│   ├── module_registry.json
│   ├── notifications_config.json
│   ├── settings_config.json
│   └── subscription_config.json
│
├── modules/
│   └── {module_name}/
│       ├── module.json
│       ├── handler.py
│       └── ui/
│           ├── index.js
│           └── *.{js,jsx}
│
└── workflows/
    ├── _pack/
    │   └── workflow_graph.json
    └── {workflow_name}/
        ├── orchestrator.yaml
        ├── agents.yaml
        ├── handoffs.yaml
        ├── context_variables.yaml
        ├── structured_outputs.yaml
        ├── tools.yaml
        ├── ui_config.yaml
        ├── hooks.yaml
        ├── _pack/
        │   └── workflow_graph.json
        ├── tools/
        │   └── *.py
        └── ui/
            ├── index.js
            └── *.{js,jsx}
```

This is the current production-facing bundle shape.

Important:

This is the compiled bundle shape the runtime consumes.

It is not the planning model the builder should start with.

The builder should first decompose user intent into typed app concerns, then
compile those into this layout.

See:

- [App Creation Guide](app-creation-guide.md)
- [App Bundle Declaratives](app-bundle-declaratives.md)

---

## File Family Responsibilities

### `platform/app.json`

Deployment and identity manifest.

Owns:

- `appName`
- `appId`
- `apiUrl`
- `wsUrl`
- `platforms`
- `auth`
- `dev`

Does not own:

- workflow entry selection
- chat startup mode
- shell chrome behavior

### `platform/config/ai.json`

AI runtime manifest.

Owns:

- `engine.framework`
- `chat.startup_mode`
- `workflows.entry_point`

This is the app-level AI/chat boot contract.

### `platform/config/navigation_config.json`

Current shell/navigation manifest.

Owns:

- `landing_spot`
- shell nav/default items
- optional static pages
- module navigation projections

Transitional note:

- this file is currently doing more than pure shell configuration
- long-term it should narrow toward shell concerns, while modules stay canonical
  in `module_registry.json`

### `platform/config/theme_config.json`

Visual identity manifest.

Owns:

- app identity
- colors
- fonts
- shell chrome styling
- theme-level UI defaults

### `platform/config/module_registry.json`

Canonical module registry.

Owns:

- what modules exist
- whether they are enabled
- backend handler import paths
- module-level metadata used by the substrate

### `platform/modules/{name}/module.json`

Module declaration for a durable app surface.

Owns:

- module identity
- route metadata
- UI entrypoint path
- display metadata for shell integration

### `platform/modules/{name}/handler.py`

Module backend adapter.

Owns:

- module actions
- module data loading
- integration with persistence or substrate services

### `platform/modules/{name}/ui/*`

Module page UI.

Owns:

- page-level persistent UI beyond chat/workflow surfaces

### `platform/workflows/_pack/workflow_graph.json`

Global workflow-journey graph.

Owns:

- cross-workflow ordering
- sequential/global journey structure

### `platform/workflows/{name}/orchestrator.yaml`

Workflow execution bootstrap.

Owns:

- `workflow_name`
- `max_turns`
- workflow-local `startup_mode` (`AgentDriven`, `UserDriven`, `BackendOnly`)
- `orchestration_pattern`
- `initial_message`
- `initial_agent`

Important:

- this `startup_mode` is workflow-local execution behavior
- it is not the app-level chat startup mode from `platform/config/ai.json`

### `platform/workflows/{name}/agents.yaml`

Agent roster and prompts for the workflow.

### `platform/workflows/{name}/handoffs.yaml`

Agent-to-agent routing rules inside the workflow.

### `platform/workflows/{name}/context_variables.yaml`

Typed workflow/application context bindings.

### `platform/workflows/{name}/structured_outputs.yaml`

Structured output contracts the runtime uses for validation, auto-tool, and MFJ
trigger semantics.

### `platform/workflows/{name}/tools.yaml`

Declared tool bindings and UI tool metadata.

### `platform/workflows/{name}/ui_config.yaml`

Frontend exposure metadata for workflow agents/components.

### `platform/workflows/{name}/hooks.yaml`

Workflow lifecycle hook registration.

### `platform/workflows/{name}/_pack/workflow_graph.json`

Workflow-level MFJ graph.

Owns:

- trigger agent
- fan-out mode
- fan-in resume location

### `platform/workflows/{name}/tools/*.py`

Python tool implementations declared by `tools.yaml`.

### `platform/workflows/{name}/ui/*`

Workflow-specific inline/artifact UI components.

---

## What Is Missing Today

The current bundle structure is strong for workflows and modules, but weak for
general app/data declaratives.

The missing first-class families are:

- `platform/entities/`
- `platform/views/`
- `platform/actions/`
- `platform/policies/`

These are described in [app-bundle-declaratives.md](app-bundle-declaratives.md)
and should be introduced as first-class bundle inputs rather than improvised
per-app code patterns.

---

## Runtime Discovery Anchors

The current runtime discovers and consumes the bundle through these anchors:

- workflows: `platform/workflows/*/orchestrator.yaml`
- global journey graph: `platform/workflows/_pack/workflow_graph.json`
- modules: `platform/config/module_registry.json`
- shell/navigation: `platform/config/navigation_config.json`
- AI bootstrap: `platform/config/ai.json`

---

## Boundary Rules

Do not put these in the app bundle:

- AG2 adapter internals
- transport internals
- event dispatcher internals
- persistence manager internals
- auth middleware internals

Those belong in:

- `mozaiksai/core/`
- `mozaikscore/core/`
- `shared_app.py`
- `chat-ui/src/`

The app bundle is a runtime input, not a runtime implementation layer.

---

## Validation Checklist

- [ ] `platform/app.json` contains only deployment/app identity concerns.
- [ ] `platform/config/ai.json` owns app-level AI/chat startup concerns.
- [ ] `platform/config/module_registry.json` is the canonical module registry.
- [ ] Every active workflow has `orchestrator.yaml`.
- [ ] Every active module has `module.json`, `handler.py`, and a UI entrypoint.
- [ ] Global journey logic and workflow-level MFJ graphs are kept separate.
- [ ] No core runtime internals are reimplemented inside `platform/`.

---

## Bottom Line

Today, the canonical app structure for Mozaiks is:

- `platform/` is the app bundle
- `config/` holds app-wide manifests
- `modules/` holds durable app surfaces
- `workflows/` holds AI orchestration inputs

That is the structure generators should target until the missing app/data
declarative families become first-class.

