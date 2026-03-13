# Mozaiks Dual Runtime Architecture

This note explains the practical split between `mozaiksai` and `mozaikscore`.

It is a reference note, not a product manifesto. Read it when you need to
understand why Mozaiks keeps agent execution and application services as
separate runtimes.

## The Split

Mozaiks has two peer runtimes:

- `mozaiksai`
  - AI workflows
  - AG2 execution
  - chat/runtime transport
  - orchestration, handoffs, MFJ, journey flow
  - UI tool round-trips
- `mozaikscore`
  - modules
  - notifications
  - settings
  - subscriptions
  - admin surfaces
  - application services and state

The important rule is:

- `mozaiksai` owns agentic execution
- `mozaikscore` owns app substrate services

Neither should absorb the other.

## Why This Split Exists

AI workflow execution and application services have different operational
shapes.

Agent workflows are:

- long-running
- stream-oriented
- pause/resume aware
- non-deterministic

Application services are usually:

- request/response
- CRUD-oriented
- permissioned
- stable and transactional

Keeping them separate makes the system easier to scale, debug, and evolve.

## How Users Experience It

The user should not feel the split.

They see one app with:

- chat
- inline UI
- artifact panels
- persistent module pages

Internally, the chat/runtime may be powered by `mozaiksai` while a persistent
page or module action is powered by `mozaikscore`.

## Modules, Not Plugins

The current platform vocabulary is `modules`.

Older docs and code may still mention `plugins`, but new work should use:

- `platform/modules/*`
- `platform/config/module_registry.json`
- `mozaikscore/core/module_manager.py`

Treat `plugin` as legacy naming, not the preferred conceptual model.

## Where The Bridge Happens

The bridge between the two runtimes happens through a few stable surfaces:

- typed config under `platform/`
- shared app identity and tenancy context
- module registration and module routes
- event delivery and websocket bridging
- artifact and UI tool rendering in the chat shell

That means a workflow can trigger or surface app functionality without the core
runtime becoming application-specific.

## What Belongs Where

Use this quick test:

- If it is about running an agent or workflow, it belongs in `mozaiksai`.
- If it is about stable app functionality, it belongs in `mozaikscore`.
- If it is declarative app definition consumed by the runtime, it belongs under
  `platform/`.

Examples:

| Concern | Home |
|---|---|
| AG2 orchestration adapter | `mozaiksai` |
| `use_ui_tool(...)` round-trip handling | `mozaiksai` |
| Notifications preferences | `mozaikscore` |
| Subscription enforcement | `mozaikscore` |
| Workflow definitions | `platform/workflows/` |
| Module definitions | `platform/modules/` + `platform/config/module_registry.json` |
| Theme and shell config | `platform/config/` |

## Why This Matters For Generated Apps

Generated apps should not directly modify runtime internals.

They should emit declarative app-bundle files that the runtimes consume:

- workflows
- modules
- shell config
- theme config
- entities, views, and actions as those contracts mature

That keeps generated apps bounded and keeps the core modular.

## Related Docs

- [Canonical App Structure](../../architecture/foundations/canonical-app-structure.md)
- [App Bundle Declaratives](../../architecture/foundations/app-bundle-declaratives.md)
- [Workflow Architecture](../../architecture/foundations/workflow-architecture.md)
- [Core vs Product vs App Bundle](../../architecture/foundations/core-product-app-bundle-boundary.md)
