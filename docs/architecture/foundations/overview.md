# Architecture Foundations

The foundations docs define the stable contracts that app authors and runtime
contributors should share before reading implementation deep dives.

## Reading Path

1. [Canonical App Structure](canonical-app-structure.md)
2. [App Manifest and Platform Targets](app-manifest-and-platform-targets.md)
3. [App Bundle Declaratives](app-bundle-declaratives.md)
4. [Core, Product, and App Bundle Boundary](core-product-app-bundle-boundary.md)
5. [Account, Admin, and Platform Services](account-admin-and-platform-services.md)
6. [Workflow Architecture](workflow-architecture.md)
7. [Workflow Authoring Contracts](workflow-authoring-contracts.md)
8. [Declarative AG2 Mapping](declarative-ag2-mapping.md)
9. [Event System](event-system.md)
10. [Event Contracts](event-contracts.md)

## Contract Summary

- Mozaiks framework code lives in `mozaiksai/`, `chat-ui/`, and the app
  authoring contract under `platform/`.
- `platform/` is the default active app root; App Zero uses
  `mozaiks-platform/app/` as its active app root with sibling `brand/`, `ui/`,
  and `generated/` workspace folders.
- Deterministic app behavior is hosted by `platform_app.py` modules or by an
  optional external/generated backend connected through `AppBackendPort`.
- Profile, settings, notifications, subscriptions, and admin are first-class
  deterministic platform services, not workflow-owned product surfaces.
- Workflows are declarative AI runs under `platform/workflows/`.
- App UI pages are declarative page surfaces under `platform/pages/`.
- Deterministic app behavior belongs behind explicit runtime/backend contracts,
  not inside workflow prompts.
- App events and workflow triggers connect ordinary app behavior to AI runs.

For the full repository-level architecture, see
[ARCHITECTURE.md](https://github.com/BlocUnited-LLC/mozaiks/blob/main/ARCHITECTURE.md).
