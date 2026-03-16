# Architecture

This section explains how Mozaiks is put together.

Use it when you need to understand:

- what belongs in `mozaiks core`
- what belongs in the app bundle under `platform/`
- how workflows, modules, UI tools, and events fit together
- how the runtime executes AG2-backed workflows without hardcoding product behavior

## Architecture Sections

### Foundations

These are the most stable architecture references in the repo. Start here if you need the current model, boundaries, and contracts.

- [Architecture Foundations](foundations/overview.md)

### Frontend

These docs explain the shared shell, surface model, conversation modes, layout behavior, and event-driven state in the web UI.

- [Frontend Overview](frontend/index.md)

### Events

These docs explain the current event system, inventories, and implementation notes around dispatch, runtime events, and UI bridging.

- [Event Architecture Notes](events/overview.md)

### Development

These docs are development-stage planning specs focused on how the builder
derives non-AI CRUD, UI page surfaces, and event-route policy from typed
contracts.

- [Development Specs](development/overview.md)

## Suggested Reading Paths

### I want to understand the platform quickly

1. [Canonical App Structure](foundations/canonical-app-structure.md)
2. [App Bundle Declaratives](foundations/app-bundle-declaratives.md)
3. [Workflow Architecture](foundations/workflow-architecture.md)
4. [Core vs Product vs App Bundle](foundations/core-product-app-bundle-boundary.md)

### I am building workflows

1. [Workflow Architecture](foundations/workflow-architecture.md)
2. [Workflow Authoring Contracts](foundations/workflow-authoring-contracts.md)
3. [Orchestration and Decomposition](orchestration-and-decomposition.md)
4. [Deep Dives](../reference/deep-dives/index.md)

### I am working on runtime or orchestration code

1. [Process and Event Map](foundations/process-and-event-map.md)
2. [Event Taxonomy](foundations/event-taxonomy.md)
3. [Event System Architecture](foundations/event-system-architecture.md)
4. [Runtime State and Control Events](foundations/runtime-state-and-control-events.md)

### I am building or maintaining the first-party app builder

1. [App Builder Architecture](foundations/app-builder-architecture.md)
2. [App Builder State and Routing](foundations/app-builder-state-and-routing.md)
3. [Builder Execution Model](foundations/builder-execution-model.md)
4. [Builder Orchestration Taxonomy](foundations/builder-orchestration-taxonomy.md)

## Related Docs

- [Reference](../reference/index.md)
- [Prompt Packs](../instruction-prompts/prompt-packs.md)
- [Getting Started](../getting-started.md)
