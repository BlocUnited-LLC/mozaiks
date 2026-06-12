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
admin config, and theme config. When an app needs durable runtime secrets,
`app/config/secrets.yaml` declares the secret provider/vault policy, env
handles, and secret names only. It must never contain raw credential values.

`app/config/subscriptions.yaml` is the canonical generated-app SaaS plan
catalog. Present only in apps that sell their own plans or need end-user
feature gates. It declares each plan_id, its label, and the capability_ids it
grants. When the app also declares `assignment_store`, the platform loads it at
startup and wires the OSS `ConfiguredEntitlementAdapter` into
`ModuleExecutor`; the adapter reads the configured app data alias for active
subscription assignment state. Non-SaaS apps omit this file; all entitlement
gates pass unconditionally via `NoOpEntitlementAdapter`. Schema:
`mozaiks.subscriptions.v1`.

Plans may also declare `usage_limits` for meters such as `ai_tokens`. These
limits are deterministic app intent used by profile, admin, billing, and
MozaiksPay facade surfaces. Token measurements themselves come from runtime
AG2 beta usage middleware and `/api/me/usage` or `/api/admin/usage`; generated
modules must not create a second token ledger.

This file does not control whether the workspace is allowed to use a hosted
operator pack such as MozaiksPay. Hosted pack access is enforced by the hosted
product that provides the pack. Generated app subscription config only controls
the generated app's own users, plans, usage limits, and feature gates.

### `app/ui/pages/`

Schema-driven app pages rendered by the app UI surface.

### `workflows/`

Declarative workflow definitions for agentic execution. A workflow owns its
orchestrator config, agents, handoffs, tools, structured outputs, context
variables, hooks, and optional UI artifacts.

Builder workflows may also exist in the shared generation core, but those are
not app-owned bundle content.

### `app/modules/`

Deterministic CRUD or action surfaces that support workflows and app pages.
Modules are not AI workflows.

Canonical module companion manifests live under
`app/modules/{module}/contracts/`, not at the flat module root.

- `contracts/events.yaml` declares module-emitted event types.
- `contracts/reactions.yaml` is the canonical event-reaction contract.
- `contracts/notifications.yaml` declares notification rules derived from
  events.
- App bundles must not author `contracts/subscriptions.yaml`.
- Persistent module backends use `backend/schemas.py`, not `backend/models.py`.

### `app/brand/`

Brand assets, fonts, and theme inputs used by the shell.

`app/brand/theme_config.json` is the canonical visual identity source. It owns
brand token selection and expanded visual values: `theme.primary`,
`theme.radius`, `theme.font`, `theme.font_heading`, `theme.appearance`,
`theme.density`, plus expanded `fonts`, `colors`, `shadows`, `ui`, and
`primitives` values consumed by shared shell/chat surfaces.

Local font files live under `app/brand/fonts/` and are referenced as
`/fonts/...` from theme config. Generated artifacts must not copy font binaries
outside `brand/`. Google Fonts are declared in theme config and loaded by the
frontend theme loader.

## Rules

- Keep one source of truth for each contract.
- Prefer explicit validation over runtime fallback branches.
- Do not encode app-specific behavior in core runtime modules.
- Do not treat app bundle files as adapters for removed contracts.
- Keep shell/navigation/chrome behavior in `app/config/shell.json`; keep visual
  tokens, typography, radius, density, shadows, and brand assets in
  `app/brand/theme_config.json`.
- Keep secret requirements and vault/provider policy in `app/config/secrets.yaml`
  when needed; generated app bundles must carry names and handles only, never
  raw API keys, tokens, passwords, connection strings, private keys, or webhook
  secrets.

## Related Docs

- [Distribution And Workspace Model](../foundations/distribution-and-workspace-model.md)
- [Canonical App Structure](canonical-app-structure.md)
- [App Manifest and Platform Targets](app-manifest-and-platform-targets.md)
- [Core, Product, and App Bundle Boundary](../foundations/core-product-app-bundle-boundary.md)
- [Workflow Authoring Contracts](../workflows/workflow-authoring-contracts.md)

