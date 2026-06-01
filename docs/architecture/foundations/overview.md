# Architecture Foundations

Foundations are the cross-cutting concepts that explain how the rest of the
architecture is organized. They are not app-specific, module-specific, or
workflow-specific implementation docs.

Use this page first when you need the canonical mental model. Then move into
the peer architecture sections for details:

- [App Architecture](../mozaiksai/index.md)
- [Module Systems](../mozaiksai/index.md)
- [Workflows](../mozaiksai/index.md)
- [Frontend Architecture](../mozaiksai/index.md)
- [Builder and Generation](../builder/app-builder-architecture.md)

## Foundational Contracts

| Doc | Scope |
| --- | --- |
| [Distribution and Workspace Model](distribution-and-workspace-model.md) | Package/source/runtime workspace boundaries |
| [Platform Terminology and Brand Language](platform-terminology-and-brand-language.md) | Customer-facing and internal vocabulary |
| [Platform Information Architecture](platform-information-architecture.md) | Studio and app-level IA contracts |
| [Core, Product, and App Bundle Boundary](core-product-app-bundle-boundary.md) | Ownership boundaries between runtime, product, and app bundle |
| [Graph Authority Boundaries](graph-authority-boundaries.md) | Source-of-truth boundaries for config, runtime, DB, and derived graph indexes |
| [Context Graph and Code Intelligence](context-graph-and-code-intelligence.md) | Context Graph contract, deterministic code extraction, contract mapping, and advisory semantic annotations |
| [App Context and Brownfield Adoption](app-context-and-brownfield-adoption.md) | Unified app context, brownfield onboarding, and control-plane ownership boundaries |

## Events and Data

Events and persistence are foundational because every layer touches them:
modules publish domain facts, workflows stream runtime events, generated
artifacts are staged before promotion, and app business data stays separate
from builder state.

| Doc | Scope |
| --- | --- |
| [Event System](events-and-data/event-system.md) | Event namespaces and routing ownership |
| [Event Contracts](events-and-data/event-contracts.md) | Event envelope schema and naming conventions |
| [Persistence and Artifact Storage](events-and-data/persistence-and-artifact-storage.md) | Mongo/artifact namespaces and generated output staging |
| [Learning Loop Architecture](events-and-data/learning-loop-architecture.md) | Feedback and learning-loop boundaries |

## Contract Summary

- Mozaiks framework code lives in `mozaiksai/`, `chat-ui/`, the repo-local web
  shell host, CLI, and shared generation core.
- App workspaces are self-contained and keep `app/config`, `app/modules`,
  `app/ui`, `app/brand`, workspace-root `services`, and workspace-root
  `workflows` together.
- Domains are planning context. Capability packs are build-time generation
  recipes. Modules are deterministic runtime units.
- Workflows are declarative AI runs owned either by an app workspace or by the
  shared generation core.
- App UI pages are app-owned declarative surfaces. Workflow-owned UI remains
  separate from persistent app pages.
- App events and workflow triggers connect deterministic app behavior to AI
  runs without hardcoding workflow names in module code.
- Durable persistence separates runtime state, builder artifacts, and app
  business data.

For the full repository-level architecture, see
[ARCHITECTURE.md](https://github.com/BlocUnited-LLC/mozaiks/blob/main/ARCHITECTURE.md).
