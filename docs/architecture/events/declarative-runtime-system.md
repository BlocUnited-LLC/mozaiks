# Declarative Runtime System

**Status:** Informational reference  
**Last updated:** 2026-03-12

---

## Purpose

This document summarizes the declarative runtime pattern in this repo.

It answers a practical question:

"What configuration files does the runtime actually consume, and which
parts of the system are declarative versus code-driven?"

This is not the primary contract doc.
Use it as a map of the implementation shape.

---

## Declarative Families In Use

The repo uses these declarative families under `platform/`:

- `platform/app.json`
- `platform/config/ai.json`
- `platform/config/theme_config.json`
- `platform/pages/{page}/page.json`
- `platform/workflows/_pack/workflow_graph.json`
- `platform/workflows/{workflow}/**`
- `platform/modules/{module}/module.json`

The most mature declarative surface is the workflow layer.

---

## Declarative Runtime Components

### 1. App/config loading

App-level config is split between app-shell concerns and workflow-runtime bootstrap.
The workflow runtime consumes the pieces it needs through
`mozaiksai/core/workflow/workflow_manager.py`.

Examples:

- `ai.json` defines app-level AI bootstrap
- `workflow_manager` projects `workflows.entry_point` into workflow configs
- app-shell consumers project chat and navigation settings into frontend state

### 2. Workflow loading

Workflow declaratives are loaded from:

- `platform/workflows/{workflow}/orchestrator.yaml`
- `agents.yaml`
- `handoffs.yaml`
- `context_variables.yaml`
- `structured_outputs.yaml`
- `tools.yaml`
- `ui_config.yaml`
- `hooks.yaml`
- workflow-local `_pack/workflow_graph.json`

Primary loader:

- `mozaiksai/core/workflow/workflow_manager.py`

### 3. Module loading

Modules are declared under:

- `platform/modules/{module}/module.json`

These are app-backend or app-shell surfaces, not core `mozaiksai` runtime
contracts.

### 4. Page loading

Pages are declared under:

- `platform/pages/{page}/page.json`

These are app-shell composition surfaces, not core `mozaiksai` runtime
contracts.

### 5. Event-facing declaratives

Event-facing config is handled by external app backends (greenfield templates)
rather than the runtime itself.

---

## What Is Declarative vs Code-Driven

### Declarative

- workflow structure
- workflow prompts/agents
- handoffs
- structured outputs
- UI tool metadata
- shell/theme/page/adapter registration
- app-level AI startup behavior
- subscription/settings/notification config

### Code-driven

- module backend behavior in `handler.py`
- workflow tool implementation in `tools/*.py`
- runtime event dispatch internals
- persistence behavior
- transport behavior
- most CRUD/data modeling concerns

That last point is the important gap:

Mozaiks is declarative-first for workflows, but not equally declarative for
entities, views, actions, and policies.

---

## Design Guardrails

1. Keep workflow behavior declarative when it can be expressed in bundle files.
2. Keep runtime internals in code, not in app bundles.
3. Keep shell/app config separate from workflow-local execution config.
4. Do not confuse workflow declaratives with full app-model declaratives.

---

## Architecture Notes

The repo is workflow-declarative first:

- strong workflow declaratives
- decent shell/module declaratives
- weak app/data declaratives

That means the next declarative work should focus on:

- entities
- views
- actions
- policies

Those are the missing pieces required for scalable CRUD/basic app generation.

See:

- [app-bundle-declaratives.md](../foundations/app-bundle-declaratives.md)
- [canonical-app-structure.md](../foundations/canonical-app-structure.md)


