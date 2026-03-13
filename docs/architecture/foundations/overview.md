# Architecture Foundations

This section contains the core architecture references for Mozaiks.

These docs explain the stable model behind:

- the modular runtime
- the app bundle under `platform/`
- workflow execution and orchestration
- the event system
- the relationship between core capabilities and first-party product behavior

If you need the high-level architecture, start here before opening deep dives or older notes.

## Recommended Reading Order

1. [Canonical App Structure](canonical-app-structure.md)
2. [App Bundle Declaratives](app-bundle-declaratives.md)
3. [App Creation Guide](app-creation-guide.md)
4. [App Planning Contracts](app-planning-contracts.md)
5. [Workflow Architecture](workflow-architecture.md)
6. [UI Surface and Layout Architecture](ui-surface-and-layout-architecture.md)
7. [Core vs Product vs App Bundle](core-product-app-bundle-boundary.md)
8. [Event Taxonomy](event-taxonomy.md)
9. [Event System Architecture](event-system-architecture.md)
10. [Process and Event Map](process-and-event-map.md)

Use the builder-focused documents after that if you are working on `mozaiks.ai` as a first-party product.

## The Foundations

| Document | What it answers |
|---|---|
| [canonical-app-structure.md](canonical-app-structure.md) | What the live repo and app bundle structure look like today |
| [app-bundle-declaratives.md](app-bundle-declaratives.md) | Which declarative files make up a Mozaiks app bundle |
| [app-creation-guide.md](app-creation-guide.md) | How intent becomes a structured plan before app-bundle generation |
| [app-planning-contracts.md](app-planning-contracts.md) | Typed planning schemas and validation rules for decomposition outputs |
| [workflow-architecture.md](workflow-architecture.md) | How workflows, modules, execution modes, and the runtime fit together |
| [workflow-authoring-contracts.md](workflow-authoring-contracts.md) | How to author workflows that use pauses, UI tools, handoffs, and MFJ correctly |
| [ui-surface-and-layout-architecture.md](ui-surface-and-layout-architecture.md) | How ask/workflow/view surfaces and layouts behave |
| [core-product-app-bundle-boundary.md](core-product-app-bundle-boundary.md) | What belongs in core runtime, the first-party product, and the app bundle |
| [event-taxonomy.md](event-taxonomy.md) | Event families, naming rules, and payload expectations |
| [event-system-architecture.md](event-system-architecture.md) | Event channels, dispatch responsibilities, and runtime ownership |
| [process-and-event-map.md](process-and-event-map.md) | Runtime processes and how they map onto events and transports |
| [graph-injection-contract.md](graph-injection-contract.md) | How graph mutation and injection are represented |
| [learning-loop-architecture.md](learning-loop-architecture.md) | Telemetry and feedback-loop boundaries |

## First-Party Builder Docs

These docs are still important, but they describe the first-party `mozaiks.ai` builder on top of Mozaiks rather than the core platform itself.

| Document | What it answers |
|---|---|
| [runtime-state-and-control-events.md](runtime-state-and-control-events.md) | Generic runtime state and typed control-event contracts in core |
| [builder-orchestration-taxonomy.md](builder-orchestration-taxonomy.md) | Canonical nouns and states used in builder execution docs |
| [app-builder-state-and-routing.md](app-builder-state-and-routing.md) | How visible builder state, routing, and orchestration fit together |
| [builder-execution-model.md](builder-execution-model.md) | How decomposition, task graphs, MFJ waves, and code context fit together |
| [app-builder-architecture.md](app-builder-architecture.md) | The first-party builder user experience and workflow map |

## Use This Section With

- [Architecture Overview](../index.md)
- [Reference Deep Dives](../../reference/deep-dives/index.md)
- [Prompt Packs](../../instruction-prompts/prompt-packs.md)
