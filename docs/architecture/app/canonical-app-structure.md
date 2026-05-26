# Canonical App Structure

This document defines the app bundle shape Mozaiks should optimize for.

## Core Rule

The bundle should describe the app, not the platform internals.

That means the main authoring folders should focus on:

- what screens exist (`ui/pages/`)
- what workflows exist (`workflows/`, beside `app/`)
- what modules provide business logic (`modules/`)
- what events connect them (declared in `events.yaml` and `orchestrator.yaml`)

## Active App Root Layout

An active app root is the directory read by `mozaiksai/hosts/platform.py`.

The canonical target is a self-contained app workspace whose active root is
`app/`.

```text
workspace/
├── app/
│   ├── app.json
│   ├── config/
│   │   ├── ai.json
│   │   ├── shell.json
│   │   ├── database_intent.json
│   │   ├── secrets.yaml             # optional — names-only secret management contract
│   │   └── shared_persistence.json  # optional — opt-in shared/existing DB contract
│   ├── backend/                    # optional — app-owned support code, not modules
│   │   ├── config.py
│   │   ├── integrations/           # thin external or hosted API clients
│   │   │   └── *_client.py
│   │   ├── adapters/               # provider-specific implementation boundaries
│   │   │   ├── auth/
│   │   │   ├── source_control/
│   │   │   ├── deployment/
│   │   │   ├── dns/
│   │   │   ├── registrar/
│   │   │   ├── cloud/
│   │   │   ├── storage/
│   │   │   ├── secrets/
│   │   │   └── payments/
│   │   ├── security/               # provider-neutral auth/secret helpers
│   │   └── routes/                 # app-level routes only when needed
│   ├── admin/                      # admin portal — pages, registry, and custom admin UI
│   │   ├── admin_registry.yaml     # declares admin portal pages (page ids, paths, scope)
│   │   ├── index.js                # registers admin page components
│   │   └── pages/                  # custom React components for admin portal pages
│   │       └── *.{js,jsx}          # one file per admin page (e.g. OrdersAdminPage.jsx)
│   ├── ui/
│   │   ├── index.js                # registers user-facing custom components; imports admin/index.js
│   │   ├── route_manifest.json     # user-facing full-page React route declarations
│   │   ├── pages/                  # declarative user-facing page schemas
│   │   │   ├── {page_name}.yaml
│   │   │   └── {page_name}/
│   │   │       └── page.yaml
│   │   └── pages/custom/
│   │       └── *.{js,jsx}          # optional custom full-page React routes
│   ├── modules/
│   │   └── {module_name}/
│   │       ├── module.yaml              # required — identity, actions, capabilities
│   │       ├── contracts/               # optional companion manifests
│   │       │   ├── events.yaml          # domain events this module may publish
│   │       │   ├── reactions.yaml       # event reactions owned by this module
│   │       │   ├── notifications.yaml   # notification rules per event
│   │       │   ├── settings.yaml        # user/app settings schema
│   │       │   ├── admin.yaml           # optional feature panels
│   │       │   └── entitlements.yaml    # optional capability entitlements
│   │       ├── runtime_extensions.yaml  # optional — api_router / startup_service
│   │       ├── backend/
│   │       │   ├── __init__.py
│   │       │   ├── handler.py           # required — thin dispatch
│   │       │   ├── service.py           # recommended — business logic/events
│   │       │   ├── repo.py              # recommended — MongoDB access layer
│   │       │   ├── policy.py            # recommended — scoping
│   │       │   ├── schemas.py           # recommended — typed shapes
│   │       │   ├── settings.py          # optional — settings hooks
│   │       │   └── admin.py             # optional — admin panel hooks
│   │       └── ui/                      # optional module-specific UI surfaces
│   │           └── index.js
│   ├── shared_persistence/         # optional helper code for shared_persistence.json
│   │   ├── contracts.py
│   │   ├── persistence.py
│   │   ├── indexes.py
│   │   └── proposals.py
│   └── brand/                      # colocated brand/theme assets
│       ├── assets/
│       ├── fonts/
│       └── theme_config.json
└── workflows/                      # app-local workflow bundles live beside app/
    └── {workflow_name}/
        ├── orchestrator.yaml       # includes triggers (no separate automations/)
        ├── agents.yaml
        ├── handoffs.yaml
        ├── context_variables.yaml
        ├── structured_outputs.yaml
        ├── tools.yaml
        ├── ui_config.yaml
        ├── hooks.yaml
        ├── tools/
        │   └── *.py
        └── ui/                     # optional workflow artifact UI
            ├── index.js
            └── *.{js,jsx}
```

Canonical rule:

- `config/`, `backend/`, `modules/`, `ui/`, `brand/`, and optional
  `shared_persistence/` belong under the active app root
- app-local workflow bundles belong under workspace-root `workflows/`, beside
  `app/`
- generated/customer app workspaces should be self-contained
- sibling `ui/` and `brand/` folders outside the app root are not canonical
- app-owned support code belongs in `backend/integrations/` for external or
  hosted API clients and `backend/adapters/` for provider-specific
  implementation boundaries
- provider-neutral auth/secret helpers belong in `backend/security/`; provider
  secret manager mechanics belong in `backend/adapters/secrets/`
- app-owned runtime secret requirements and vault policy belong in
  `config/secrets.yaml` when needed. This file is a names-only contract and
  must never contain raw secret values.
- app-level backend routes belong in `backend/routes/` only when a module
  runtime extension or platform API contract explicitly needs them
- `backend/` support code does not create runtime actions, own lifecycle state,
  publish events, or own persistence; modules own those behaviors and call
  support code as an implementation detail
- generated app modules use `ctx.persistence` and `config/database_intent.json`
  by default
- `config/shared_persistence.json` and `shared_persistence/` are opt-in only for
  stable shared collections, cross-module aggregate ownership, or existing
  database integration; normal apps should omit them

## Factory App Workspace

`factory_app/app/` is intentionally app-shaped.

It follows the same top-level active app root contract:

- `app.json`
- `config/`
- `ui/`
- optional workspace-root `workflows/` overlay when the app owns local workflows
- `modules/`
- `brand/`

Inside that workspace contract, `<workspace>/workflows/` is reserved for
app-local overlays only. Shared generation-core workflows do not live there;
they live under `factory_app/workflows/`. The first-party `factory_app/app`
bundle should not carry `factory_app/app/workflows/`.

That is deliberate. The factory workspace is the first-party dogfood app for the
builder/control-plane layer, so it should stay close to the same workspace
shape generated or customer apps use.

In practice, that means `factory_app/app/` can be used as a real active app
workspace when dogfooding builder and revision flows.

But it is not the same thing as a generic generated customer app.

It still includes framework-owned responsibilities that a normal generated app
would not own:

- shared builder workflows under `factory_app/workflows/`
- framework-owned Studio management routes declared through `factory_app/app/ui/route_manifest.json` and `factory_app/app/ui/index.js`
- factory control-plane modules such as `factory_app/app/modules/factory_control_plane/`
- framework-owned admin APIs and panel rendering through `AdminPortal`

That means `factory_app/workflows/` is the factory builder layer, while a
separate workspace-root `workflows/` directory is the app-local overlay shape.

Use this distinction when reasoning about changes:

- if the goal is dogfooding the workspace contract, keeping `factory_app/app/`
  app-shaped is correct
- if the goal is defining the canonical customer-app output, generated apps
  should still target the generic `app/` contract without the factory/studio
  exceptions

## Hosted Product Workspace Layout

Hosted product workspaces should ultimately use the same self-contained
app-workspace contract.

Canonical target:

```text
hosted-product/
└── app/                        # active app root read by mozaiksai/hosts/platform.py
    ├── app.json
    ├── config/
    ├── backend/                # optional support code: integrations/adapters/security/routes
    ├── modules/
    ├── ui/
    └── brand/
└── workflows/
    └── extended_orchestration/
        └── extension_registry.json
```

The product workspace's local `workflows/` directory is primarily an overlay
surface. The shared generator implementations the product consumes resolve from
the shared generation core, while
`factory_app/workflows/extended_orchestration/extension_registry.json`
defines the shared build journeys and entrypoints and
`workflows/extended_orchestration/extension_registry.json`
adds product-specific workflow overlays.

The same boundary applies to `factory_app/app/`: it is the app bundle, while
`factory_app/workflows/` is the shared builder workflow layer.

Runtime workflow loading is single-root by default. A running host selects one active
workflow root:

1. Studio defaults to `factory_app/workflows/`
2. app/product hosts use `<workspace>/workflows` when that directory is present
3. `MOZAIKS_WORKFLOWS_PATH` may override the selected root explicitly

The runtime does not auto-merge app and factory workflow roots in normal
platform/studio/hosted-product execution.

For generated OSS-style bundles, bounded frontend customization lives inside the
active app root at `app/ui/index.js`. That file is the app-owned extension
barrel loaded through `@platform/extensions`.

Hosted product modules are hosted-product modules, not generic sample modules:

```text
app/modules/
├── app_registry/               # build records, staged artifact history
├── hosting/                    # hosted deployment intake + lifecycle
├── investor_marketplace/       # listings, investor profiles, investment interest
```

Those modules publish `hosted.*` product events. Generated customer apps should
usually publish `domain.*` events from their own modules instead.
## What Each Family Means

### `app.json`

Small author-facing app manifest.

It should answer:

- what is this app called
- what targets are enabled
- where should the app land when opened
- should people sign in
- who are the default admins

It should not force the user to hand-author platform plumbing.

It should not own shell colors or brand assets.

### `brand/*`

Shell branding assets and login theme files.

Use this family for:

- logos
- icons
- fonts
- Keycloak login-theme assets

Use app shell/theme config to point at those assets.

### `ui/pages/*`

Normal routeable app screens.

Examples:

- `discover` — browse content
- `dashboard` — app home
- Custom pages such as lineup, catalog, settings, etc.

Pages are where most CRUD-style app experience should live.

Admin is not generated as an app page. The platform owns the internal
`/admin` route family and renders framework-owned panels through `AdminPortal`.

Admin remains a framework-owned management surface. In the current architecture,
`AdminPortal` is registered through the Studio composition layer rather than a
separate top-level UI package.

### `ui/pages/custom/*`

Custom full-page React routes are the escape hatch for cases the declarative
page schema cannot express yet.

These routes must be mounted through `ui/route_manifest.json` and should be used
sparingly; declarative `ui/pages/*` remains the default.

Route manifest entries may also declare `meta.surfaces` when a route belongs to
the normal app UI contract but should only appear on a specific shell surface,
such as Studio management routes.

### `workflows/*`

Agentic execution definitions.

Use workflows for:

- reasoning
- orchestration
- review loops
- long-running generation
- HITL

**Event triggers are declared in `orchestrator.yaml`:**

```yaml
# workflows/WritersRoom/orchestrator.yaml
triggers:
  - event: set.brief_confirmed
    action: run
    when:
      payload.status: approved
    message_template: "Start writing for {payload.set_type}."
```

### `modules/*`

Support bundles for shared logic.

Modules should not be the main mental model for app users.

Use them when you need:

- shared page backing logic
- reusable handlers
- shared feature UI helpers
- page-triggered workflow helpers

For generated apps, modules own deterministic business facts and publish
`domain.*` events after state commits. For hosted Mozaiks product features,
modules may publish `hosted.*` events, but those hosted semantics stay above
the runtime kernel.

At runtime, `mozaiksai/hosts/platform.py` registers `ModuleEventRouter` for loaded module
manifests. That router consumes `contracts/reactions.yaml` and `contracts/notifications.yaml`
and derives platform reactions such as `notification.created`.

### `backend/*`

App-level backend support code is optional and deliberately narrower than a
module.

Use:

- `backend/integrations/` for thin clients that call hosted or external APIs
- `backend/adapters/` for provider-specific implementation boundaries, such as
  auth/OIDC/OAuth, source control, deployment, DNS, registrar, cloud, storage,
  secrets, or payment provider adapters
- `backend/security/` for provider-neutral auth and secret helper code
- `backend/routes/` for app-level routes only when explicitly needed

Do not use `backend/` to own product facts. If a behavior has durable state,
user-facing actions, authorization, emitted events, lifecycle transitions, or
persistence authority, put that behavior in a module and have the module call
the integration/adapter.

Generic runtime auth adapters are framework code under `mozaiksai/core/auth/`.
Only app-specific auth provider mechanics belong under `backend/adapters/auth/`.
Provider-specific secret manager mechanics belong under
`backend/adapters/secrets/`; generic secret resolution belongs under
`backend/security/`.

`config/secrets.yaml` is the declarative app-owned secret contract. Use it to
declare provider type, vault/config handles, secret env names, and secret names.
Do not store API keys, tokens, passwords, connection strings, private keys, or
webhook secrets in that file.

### `config/*`

Runtime-facing generated or platform-owned config.

This folder should not be the primary authoring target.

There is no `config/admin.json` contract. Admin bootstrap lives in
`app/app.json` `admins`; optional module feature panels live in
`modules/{module}/contracts/admin.yaml`.

## Practical Authoring Order

For most new apps:

1. Create `app/app.json`
2. Create shell brand config only if the app needs custom identity
3. Create app pages in `app/ui/pages/`
4. Create workflow definitions in `workflows/` (with triggers in `orchestrator.yaml`)
5. Create modules in `app/modules/` (with actions in `module.yaml` and events in `events.yaml`)
6. Add companion manifests under `contracts/` (`reactions.yaml`, `notifications.yaml`,
   `settings.yaml`, `admin.yaml`) as needed
7. Add `backend/integrations/` or `backend/adapters/` only for declared
   non-module support code that modules or workflows call

## CRUD Minimalism

Do not model every database concern up front.

For the current phase, a CRUD-like app should usually start with:

- a page manifest
- a page UI stub
- a thin module handler if the page needs backend reads or actions
- a workflow only when reasoning is actually needed

That is enough to prove the product shape without drowning the user in schema.

## Current Repo Reality

This repo now treats `factory_app/app/` as the first-party Console app bundle served by the Studio host.
Hosted product workspaces are expected to live outside this repo and consume
the same contract.

The canonical target is:

- self-contained app workspaces
- shared generation core outside app workspaces
- hosted product workspaces consuming the same workspace contract

## Module Authoring

The base module structure applies to all modules. Do not introduce a separate
module-type taxonomy. Author the module by defining purpose, actions,
capabilities, permissions, emitted events, optional contracts, and backend
files.

See [Module Authoring Patterns](../modules-systems/module-authoring-patterns.md) for
backend conventions and practical examples.

## Cross References

- [Foundations Overview](../foundations/overview.md)
- [Module Authoring Patterns](../modules-systems/module-authoring-patterns.md)
- [app-bundle-declaratives.md](app-bundle-declaratives.md)
- [../foundations/distribution-and-workspace-model.md](../foundations/distribution-and-workspace-model.md)
