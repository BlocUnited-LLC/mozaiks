# Architecture Foundations

The foundations docs define the stable contracts that app authors and runtime
contributors should share before reading implementation deep dives.

## Reading Path

1. [Canonical App Structure](canonical-app-structure.md)
2. [App Manifest and Platform Targets](app-manifest-and-platform-targets.md)
3. [App Bundle Declaratives](app-bundle-declaratives.md)
4. [Core, Product, and App Bundle Boundary](core-product-app-bundle-boundary.md)
5. [Workflow Architecture](workflow-architecture.md)
6. [Workflow Authoring Contracts](workflow-authoring-contracts.md)
7. [Declarative AG2 Mapping](declarative-ag2-mapping.md)
8. [Event Taxonomy](event-taxonomy.md)
9. [Event System Architecture](event-system-architecture.md)
10. [Process and Event Map](process-and-event-map.md)

## Contract Summary

- Mozaiks framework code lives in `mozaiksai/`, `chat-ui/`, and the app
  authoring contract under `platform/`.
- App backends are separate deployables and connect through `AppBackendPort`.
- Workflows are declarative AI runs under `platform/workflows/`.
- App UI pages are declarative page surfaces under `platform/pages/`.
- Deterministic app behavior belongs behind explicit runtime/backend contracts,
  not inside workflow prompts.
- App events and workflow triggers connect ordinary app behavior to AI runs.

For the full repository-level architecture, see
[ARCHITECTURE.md](https://github.com/BlocUnited-LLC/mozaiks/blob/main/ARCHITECTURE.md).
