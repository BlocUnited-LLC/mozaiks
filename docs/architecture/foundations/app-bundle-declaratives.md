# App Bundle Declaratives

App bundle declaratives are the files under `platform/` that describe an app
without requiring changes to framework runtime code.

## Purpose

Declarative files should describe durable app intent:

- what pages exist
- what workflows exist
- what module actions or backend actions are callable
- what events are emitted or handled
- what shell, theme, and admin defaults apply

Runtime code can then load and validate those files instead of relying on
hardcoded app-specific branches.

## Canonical Families

### `platform/config/`

Runtime-facing app configuration, including AI provider settings, shell config,
admin config, and theme config.

### `platform/pages/`

Schema-driven app pages rendered by the app UI surface.

### `platform/workflows/`

Declarative workflow definitions for agentic execution. A workflow owns its
orchestrator config, agents, handoffs, tools, structured outputs, context
variables, hooks, and optional UI artifacts.

### `platform/modules/`

Deterministic CRUD or action surfaces that support workflows and app pages.
Modules are not AI workflows.

### `platform/brand/`

Brand assets, fonts, and theme inputs used by the shell.

## Rules

- Keep one source of truth for each contract.
- Prefer explicit validation over runtime fallback branches.
- Do not encode app-specific behavior in core runtime modules.
- Do not treat app bundle files as compatibility shims for removed contracts.

## Related Docs

- [Canonical App Structure](canonical-app-structure.md)
- [App Manifest and Platform Targets](app-manifest-and-platform-targets.md)
- [Core, Product, and App Bundle Boundary](core-product-app-bundle-boundary.md)
- [Workflow Authoring Contracts](workflow-authoring-contracts.md)
