# Architecture Foundations

The foundations docs define the stable contracts that app authors and runtime
contributors should share before reading implementation deep dives.

## Reading Path

1. [Distribution And Workspace Model](distribution-and-workspace-model.md)
2. [Framework Capability Classification](framework-capability-classification.md)
3. [Canonical App Structure](canonical-app-structure.md)
4. [App Manifest and Platform Targets](app-manifest-and-platform-targets.md)
5. [App Bundle Declaratives](app-bundle-declaratives.md)
6. [Core, Product, and App Bundle Boundary](core-product-app-bundle-boundary.md)
7. [Account, Admin, and Platform Services](account-admin-and-platform-services.md)
8. [Workflow Architecture](workflow-architecture.md)
9. [Control-Plane Harness Architecture](control-plane-harness-architecture.md)
10. [Orchestration Control Loops](orchestration-control-loops.md)
11. [Workflow Authoring Contracts](workflow-authoring-contracts.md)
12. [Declarative AG2 Mapping](declarative-ag2-mapping.md)
13. [Event System](event-system.md)
14. [Event Contracts](event-contracts.md)
15. [Persistence and Artifact Storage](persistence-and-artifact-storage.md)

## Contract Summary

- Mozaiks framework code lives in `mozaiksai/`, `chat-ui/`, the repo-local web
  shell host, CLI, and the shared generation core.
- Not all first-class framework code is universal app-runtime substrate: the
  runtime, platform host, and core shell primitives are universal, while Studio,
  CLI, and shared generation core are optional framework-owned capabilities.
- `factory_app/app` is the first-party Studio app bundle in this repo, and
  generated/hosted app workspaces follow the same self-contained contract with
  `app/config`, `app/ui/pages`, `app/workflows`, `app/modules`, `app/ui`, and
  `app/brand`.
- Deterministic app behavior is hosted by `mozaiksai/hosts/platform.py` modules or by an
  optional external/generated backend connected through `AppBackendPort`.
- Profile, settings, notifications, subscriptions, and admin are first-class
  deterministic platform services, not workflow-owned product surfaces.
- Workflows are declarative AI runs owned either by an app workspace or by the
  shared generation core.
- Workflow-local AG2 execution, builder-session routing, and scoped refinement
  workers are separate orchestration loops with different limits and resume
  semantics.
- Builder-session free-text routing belongs to a configurable control-plane
  harness layer above workflows, with generic contracts in core and first-party
  implementation in `factory_app`.
- App UI pages are declarative page surfaces owned by the app workspace.
- Deterministic app behavior belongs behind explicit runtime/backend contracts,
  not inside workflow prompts.
- App events and workflow triggers connect ordinary app behavior to AI runs.
- Durable persistence is required for Studio and the builder pipeline, with one
  canonical framework-owned Mongo namespace and explicit separation between
  runtime state, builder artifacts, and app business data.

For the full repository-level architecture, see
[ARCHITECTURE.md](https://github.com/BlocUnited-LLC/mozaiks/blob/main/ARCHITECTURE.md).
