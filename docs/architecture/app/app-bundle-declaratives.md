# App Bundle Declaratives

App bundle declaratives are the files under an app workspace's active app root
that describe an app without requiring changes to framework runtime code.

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

### `app/config/`

Runtime-facing app configuration, including AI provider settings, shell config,
admin config, and theme config.

### `app/ui/pages/`

Schema-driven app pages rendered by the app UI surface.

### `app/workflows/`

Declarative workflow definitions for agentic execution. A workflow owns its
orchestrator config, agents, handoffs, tools, structured outputs, context
variables, hooks, and optional UI artifacts.

Builder workflows may also exist in the shared generation core, but those are
not app-owned bundle content.

### `app/modules/`

Deterministic CRUD or action surfaces that support workflows and app pages.
Modules are not AI workflows.

### `app/brand/`

Brand assets, fonts, and theme inputs used by the shell.

`app/brand/theme_config.json` is the canonical visual identity source. It owns
brand token selection and expanded visual values: `theme.primary`,
`theme.radius`, `theme.font`, `theme.font_heading`, `theme.appearance`,
`theme.density`, plus expanded `fonts`, `colors`, `shadows`, `ui`, and
`primitives` values where older shell/chat compatibility still needs them.

Local font files live under `app/brand/fonts/` and are referenced as
`/fonts/...` from theme config. Generated artifacts must not copy font binaries
outside `brand/`. Google Fonts are declared in theme config and loaded by the
frontend theme loader.

## Rules

- Keep one source of truth for each contract.
- Prefer explicit validation over runtime fallback branches.
- Do not encode app-specific behavior in core runtime modules.
- Do not treat app bundle files as compatibility shims for removed contracts.
- Keep shell/navigation/chrome behavior in `app/config/shell.json`; keep visual
  tokens, typography, radius, density, shadows, and brand assets in
  `app/brand/theme_config.json`.

## Related Docs

- [Distribution And Workspace Model](../foundations/distribution-and-workspace-model.md)
- [Canonical App Structure](canonical-app-structure.md)
- [App Manifest and Platform Targets](app-manifest-and-platform-targets.md)
- [Core, Product, and App Bundle Boundary](../foundations/core-product-app-bundle-boundary.md)
- [Workflow Authoring Contracts](../workflows/workflow-authoring-contracts.md)

