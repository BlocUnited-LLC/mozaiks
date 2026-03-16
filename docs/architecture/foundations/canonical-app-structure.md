# Canonical App Structure

This document defines the target app-bundle structure for Mozaiks.

It reflects the architecture in this directory, not every legacy file layout in
the current repo.

The current `platform/` directory in this repo should still be treated as the
flagship runtime-output example used to test this model.

## Core Rule

The bundle must separate:

- app substrate declaratives
- automation declaratives
- workflow declaratives
- shell declaratives

If those concerns collapse into one `config/` bucket, the generator cannot stay
coherent.

## Canonical Target Layout

```text
platform/
├── app.json
│
├── shell/
│   ├── navigation.json
│   └── theme.json
│
├── data/
│   ├── entities/
│   │   └── *.json
│   ├── views/
│   │   └── *.json
│   ├── actions/
│   │   └── *.json
│   └── policies/
│       └── *.json
│
├── modules/
│   └── {module_name}/
│       ├── module.json
│       └── ui/
│           ├── index.js
│           └── *.{js,jsx}
│
├── automations/
│   ├── event_catalog.json
│   └── routes.json
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

## Stable Versus Transitional

### Stable

The workflow folder contract is intentionally preserved:

- `orchestrator.yaml`
- `agents.yaml`
- `handoffs.yaml`
- `context_variables.yaml`
- `structured_outputs.yaml`
- `tools.yaml`
- `ui_config.yaml`
- `hooks.yaml`
- `tools/*.py`
- `ui/*`
- `_pack/workflow_graph.json`

### Transitional

The current repo still uses:

- `platform/config/navigation_config.json`
- `platform/config/theme_config.json`
- `platform/config/module_registry.json`
- other `platform/config/*.json` files

Treat those as transitional compiled projections of shell or substrate concerns.
They should not be the long-term conceptual source of truth for the generator.

In practice today:

- `platform/config/navigation_config.json` behaves like a shell projection
- `platform/config/theme_config.json` behaves like a shell projection
- `platform/config/module_registry.json` behaves like a derived module index

The builder should understand those paths because the runtime consumes them
today, but it should still plan against the canonical families first.

## Responsibility by Family

### `platform/app.json`

Owns app identity and deployment metadata:

- `app_id`
- `app_name`
- endpoint base URLs
- auth and platform metadata

It does not own workflow routing, domain events, or shell chrome behavior.

### `platform/shell/*`

Owns shell behavior:

- landing surface
- navigation
- semantic header controls
- discover and shell chrome
- theme identity

Shell declaratives do not define entities, actions, or automation.

### `platform/data/*`

Owns durable app substrate behavior:

- `entities`: business objects and schemas
- `views`: list, detail, form, board, dashboard, search
- `actions`: deterministic mutations and service calls
- `policies`: role, plan, tenant, and capability rules

This is the non-AI application model.

### `platform/modules/*`

Owns user-facing composed surfaces.

Modules are where the shell exposes real product areas such as:

- CRM
- Marketplace
- Calendar
- Inbox
- Admin

A module references views, actions, and optionally workflow entrypoints. It is
not the same thing as an entity or a workflow.

### `platform/automations/*`

Owns the event-driven bridge between the substrate and the AI runtime.

- `event_catalog.json` declares the domain events the app emits or consumes
- `routes.json` maps event types and predicates to automation effects

Automation routes belong to the app bundle, but they execute on the AI side.

### `platform/workflows/*`

Owns workflow-local reasoning, orchestration, UI pauses, and tooling.

The workflow directory is not the whole application.

## Module Registry Direction

`module_registry.json` should become derived, not canonical.

Long-term rule:

- `modules/{name}/module.json` is canonical
- shell declarations decide placement and visibility
- any registry file is a compiled index, not the authoring model

## Why This Structure

This separation lets the generator answer four different questions cleanly:

1. What business model does the app have?
2. What shell and surfaces does the app expose?
3. What domain events can cause automation?
4. What workflows exist, and what do they do?

Without that split, CRUD logic and workflow logic collapse into one substrate.

## Cross References

- [app-bundle-declaratives.md](app-bundle-declaratives.md)
- [workflow-architecture.md](workflow-architecture.md)
- [event-system-architecture.md](event-system-architecture.md)
