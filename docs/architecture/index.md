# Architecture

!!! note "This section is for framework contributors"
    If you are building an app with Mozaiks, you do not need this section.
    Start with [Getting Started](../getting-started.md) or the [Guides](../guides/index.md).

This section documents the Mozaiks framework internals: the AI runtime, app
workspace contract, module system, workflow authoring model, event/data model,
and frontend surfaces. It is reference material for contributors working on the
framework itself.

The authoritative OSS north-star software-design document is
[Mozaiks OSS Software Design](MOZAIKS_OSS_SOFTWARE_DESIGN.md).
Use [Architecture Quick Reference](ARCHITECTURE_QUICK_REFERENCE.md) as the
short contributor summary, not as a competing source of authority.

## Start Here

1. [Mozaiks OSS Software Design](MOZAIKS_OSS_SOFTWARE_DESIGN.md)
2. [Architecture Quick Reference](ARCHITECTURE_QUICK_REFERENCE.md)
3. [Architecture Foundations](foundations/overview.md)
4. [App Architecture](app/index.md)
5. [Module Systems](modules-systems/index.md)
6. [Workflows](workflows/index.md)
7. [MozaiksAI Runtime](mozaiksai/index.md)
8. [Frontend Architecture](frontend/index.md)
9. [Builder and Generation](builder/app-builder-architecture.md)

## Foundational Contracts

- [Distribution and Workspace Model](foundations/distribution-and-workspace-model.md)
- [Platform Terminology and Brand Language](foundations/platform-terminology-and-brand-language.md)
- [Platform Information Architecture](foundations/platform-information-architecture.md)
- [Core, Product, and App Bundle Boundary](foundations/core-product-app-bundle-boundary.md)
- [Relationship Provider Contract](foundations/relationship-provider-contract.md)
- [App Intelligence Plane](foundations/app-intelligence-plane.md)
- [App Intelligence User Journey](foundations/app-intelligence-user-journey.md)
- [Graph Authority Boundaries](foundations/graph-authority-boundaries.md)
- [Context Graph and Code Intelligence](foundations/context-graph-and-code-intelligence.md)
- [App Context and Brownfield Adoption](foundations/app-context-and-brownfield-adoption.md)
- [Event System](foundations/events-and-data/event-system.md)
- [Event Contracts](foundations/events-and-data/event-contracts.md)
- [Persistence and Artifact Storage](foundations/events-and-data/persistence-and-artifact-storage.md)

## App Contracts

- [Generated App Lifecycle Model](app/generated-app-lifecycle-model.md)
- [Generated App Functional Acceptance](app/generated-app-functional-acceptance.md)
- [Canonical App Structure](app/canonical-app-structure.md)
- [App Manifest and Platform Targets](app/app-manifest-and-platform-targets.md)
- [App Bundle Declaratives](app/app-bundle-declaratives.md)
- [Platform Authoring](app/platform-authoring.md)
- [Platform Navigation Contract](app/platform-navigation-contract.md)
- [Account, Admin, and Platform Services](app/account-admin-and-platform-services.md)
- [Generated App Deployment Contract](deployment/generated-app-deployment-contract.md)

## Runtime Authoring Contracts

- [Module System](modules-systems/module-system.md)
- [AppGenerator Capability Planning](modules-systems/appgenerator-capability-planning.md)
- [Workflow Architecture](workflows/workflow-architecture.md)
- [Workflow Authoring Contracts](workflows/workflow-authoring-contracts.md)
- [Orchestration Control Loops](workflows/orchestration-control-loops.md)

## Related Sections

- [Frontend UI](frontend/index.md)
- [MozaiksAI Runtime](mozaiksai/index.md)
