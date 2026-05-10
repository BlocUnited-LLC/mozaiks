# Architecture Foundations

The foundations docs define the stable contracts that app authors and runtime
contributors should share before reading implementation deep dives.

## Reading Path

1. [Distribution And Workspace Model](distribution-and-workspace-model.md)
2. [Framework Capability Classification](framework-capability-classification.md)
3. [Platform Terminology And Brand Language](platform-terminology-and-brand-language.md)
4. [Platform Information Architecture](platform-information-architecture.md)
5. [Generated App Lifecycle Model](generated-app-lifecycle-model.md)
6. [Canonical App Structure](canonical-app-structure.md)
7. [App Manifest and Platform Targets](app-manifest-and-platform-targets.md)
8. [App Bundle Declaratives](app-bundle-declaratives.md)
9. [Core, Product, and App Bundle Boundary](core-product-app-bundle-boundary.md)
10. [Account, Admin, and Platform Services](account-admin-and-platform-services.md)
11. [Workflow Architecture](workflow-architecture.md)
12. [Control-Plane Harness Architecture](control-plane-harness-architecture.md)
13. [Orchestration Control Loops](orchestration-control-loops.md)
14. [Workflow Authoring Contracts](workflow-authoring-contracts.md)
15. [Declarative AG2 Mapping](declarative-ag2-mapping.md)
16. [Event System](event-system.md)
17. [Event Contracts](event-contracts.md)
18. [Persistence and Artifact Storage](persistence-and-artifact-storage.md)

## Contract Summary

- Mozaiks framework code lives in `mozaiksai/`, `chat-ui/`, the repo-local web
  shell host, CLI, and the shared generation core.
- Not all first-class framework code is universal app-runtime substrate: the
  runtime, platform host, and core shell primitives are universal, while Studio,
  CLI, and shared generation core are optional framework-owned capabilities.
- `factory_app/app` is the first-party Console app bundle served by the Studio host in this repo, and
  generated/hosted app workspaces follow the same self-contained contract with
  `app/config`, `app/ui/pages`, `app/workflows`, `app/modules`, `app/ui`, and
  `app/brand`.
- Product terminology, IA, and lifecycle are first-class architecture contracts.
  `factory_app`, `Studio`, and `control_plane` are implementation terms, while
  visible UX should prefer `Apps`, `Build`, `Operations`, `Integrations`, and
  app lifecycle states such as `draft`, `building`, and `active`.
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
- Durable persistence is required for the Studio host, the visible workspace
  console/build surfaces, and the builder pipeline, with one canonical
  framework-owned Mongo namespace and explicit separation between runtime
  state, builder artifacts, and app business data.

For the full repository-level architecture, see
[ARCHITECTURE.md](https://github.com/BlocUnited-LLC/mozaiks/blob/main/ARCHITECTURE.md).
