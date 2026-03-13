# Declarative Runtime System

**Status:** Informational reference  
**Last updated:** 2026-03-12

---

## Purpose

This document summarizes the current declarative runtime pattern in this repo.

It answers a practical question:

"What configuration files does the runtime actually consume today, and which
parts of the system are still declarative versus code-driven?"

This is not the primary contract doc.
Use it as a map of the current implementation shape.

---

## Declarative Families In Use Today

The runtime currently consumes these declarative families under `platform/`:

- `platform/app.json`
- `platform/config/ai.json`
- `platform/config/navigation_config.json`
- `platform/config/theme_config.json`
- `platform/config/module_registry.json`
- `platform/config/notifications_config.json`
- `platform/config/settings_config.json`
- `platform/config/subscription_config.json`
- `platform/workflows/_pack/workflow_graph.json`
- `platform/workflows/{workflow}/**`
- `platform/modules/{module}/module.json`

The most mature declarative surface today is still the workflow layer.

---

## Declarative Runtime Components

### 1. App/config loading

Current config loading is split across:

- `mozaikscore/core/config_loader.py`
- `mozaiksai/core/workflow/workflow_manager.py`

These loaders currently project declarative config into runtime-facing metadata.

Examples:

- `ai.json` defines app-level AI bootstrap
- `workflow_manager` projects `workflows.entry_point` into workflow configs
- `config_loader` projects `chat.startup_mode` into the shell/nav response

### 2. Workflow loading

Current workflow declaratives are loaded from:

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

- `platform/config/module_registry.json`
- `platform/modules/{module}/module.json`

Primary loader:

- `mozaikscore/core/module_manager.py`

### 4. Event-facing declaratives

Substrate-level config still includes:

- `notifications_config.json`
- `settings_config.json`
- `subscription_config.json`

These are consumed by mozaikscore managers and related API routes rather than
the workflow engine itself.

---

## What Is Declarative vs Code-Driven

### Declarative today

- workflow structure
- workflow prompts/agents
- handoffs
- structured outputs
- UI tool metadata
- shell/theme/module registration
- app-level AI startup behavior
- subscription/settings/notification config

### Still code-driven today

- module backend behavior in `handler.py`
- workflow tool implementation in `tools/*.py`
- runtime event dispatch internals
- persistence behavior
- transport behavior
- most CRUD/data modeling concerns

That last point is the important gap:

Mozaiks is declarative-first for workflows, but not yet equally declarative for
entities, views, actions, and policies.

---

## The Current Design Guardrails

1. Keep workflow behavior declarative when it can be expressed in bundle files.
2. Keep runtime internals in code, not in app bundles.
3. Keep shell/app config separate from workflow-local execution config.
4. Do not confuse workflow declaratives with full app-model declaratives.

---

## What This Means Architecturally

The current repo is in a transitional but coherent state:

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

