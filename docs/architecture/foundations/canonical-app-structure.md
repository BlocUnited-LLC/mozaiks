# Canonical App Structure

This document defines the app bundle shape Mozaiks should optimize for.

## Core Rule

The bundle should describe the app, not the platform internals.

That means the main authoring folders should focus on:

- what screens exist (`ui/pages/`)
- what workflows exist (`workflows/`)
- what modules provide business logic (`modules/`)
- what events connect them (declared in `events.yaml` and `orchestrator.yaml`)

## Active App Root Layout

An active app root is the directory read by `mozaiksai/hosts/platform.py`.

The canonical target is a self-contained app workspace whose active root is
`app/`.

```text
app/
├── app.json
├── config/
│   ├── ai.json
│   ├── shell.json
│   └── admin.json
├── ui/
│   ├── index.js                # registers contract-declared custom components
│   ├── route_manifest.json     # custom full-page React route declarations, including surface-gated routes
│   ├── pages/                  # declarative page schemas
│   │   ├── {page_name}.yaml
│   │   └── {page_name}/
│   │       └── page.yaml
│   │   └── custom/
│   │       └── *.{js,jsx}      # optional custom full-page React routes (escape hatch)
│   └── admin/
│       └── *.{js,jsx}          # optional admin custom components
├── workflows/
│   └── {workflow_name}/
│       ├── orchestrator.yaml   # includes triggers (no separate automations/)
│       ├── agents.yaml
│       ├── handoffs.yaml
│       ├── context_variables.yaml
│       ├── structured_outputs.yaml
│       ├── tools.yaml
│       ├── ui_config.yaml
│       ├── hooks.yaml
│       ├── tools/
│       │   └── *.py
│       └── ui/                 # optional, main user-facing UI
│           ├── index.js
│           └── *.{js,jsx}
├── modules/
│   └── {module_name}/
│       ├── module.yaml         # identity, actions, capabilities
│       ├── events.yaml         # domain events this module may publish
│       ├── subscriptions.yaml  # reactions/gates owned by the module
│       ├── notifications.yaml  # notification rules
│       ├── settings.yaml       # user/app settings schema
│       ├── admin.yaml          # admin panels mounted under /admin/*
│       ├── backend/
│       │   ├── __init__.py
│       │   ├── handler.py      # required — thin dispatch, one method per declared action
│       │   ├── service.py      # recommended — all business logic and event emission
│       │   ├── repo.py         # recommended — MongoDB access layer, no logic
│       │   ├── policy.py       # recommended — query scoping for multi-tenancy
│       │   ├── schemas.py      # recommended — typed request/response + document shapes
│       │   ├── settings.py     # optional — settings hook implementations
│       │   ├── subscriptions.py# optional — subscription handler implementations
│       │   ├── notifications.py# optional — notification hook implementations
│       │   └── admin.py        # optional — admin panel implementations
│       └── ui/                 # optional module-specific UI surfaces
│           └── index.js
└── brand/                      # colocated brand/theme assets
    ├── assets/
    ├── fonts/
    └── theme_config.json
```

Canonical rule:

- `config/`, `ui/pages/`, `workflows/`, `modules/`, `ui/`, and `brand/` belong
  together under the active app root
- generated/customer app workspaces should be self-contained
- sibling `ui/` and `brand/` folders outside the app root are transitional,
  not canonical

## Factory App Workspace

`factory_app/app/` is intentionally app-shaped.

It follows the same top-level active app root contract:

- `app.json`
- `config/`
- `ui/`
- `workflows/`
- `modules/`
- `brand/`

Inside that workspace contract, `factory_app/app/workflows/` is now reserved
for app-local overlays only. Shared generation-core workflows no longer live
there; they live under `factory_app/workflows/`.

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
- framework-owned admin shell composition through `AdminPortal`

That means `factory_app/app/workflows/` should be read as an optional overlay
root for the dogfood app workspace, not as the home of shared factory builder
implementations.

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
    ├── modules/
    ├── ui/
    ├── workflows/
    │   └── extended_orchestration/
    │       └── extension_registry.json
    └── brand/
```

The product workspace's local `workflows/` directory is primarily an overlay
surface. The shared generator implementations the product consumes resolve from
the shared generation core, while
`factory_app/workflows/extended_orchestration/extension_registry.json`
defines the shared build journeys and entrypoints and
`app/workflows/extended_orchestration/extension_registry.json`
adds product-specific workflow overlays.

The same boundary applies to `factory_app/app/workflows/`: it is an app-local
overlay surface and may legitimately stay empty when the factory app does not
own any workflow beyond the shared builder layer.

Runtime workflow loading is multi-root. By default the runtime searches:

1. `<active app root>/workflows`
2. the shared generation-core workflow root

If needed, `MOZAIKS_WORKFLOW_ROOTS` can override that order explicitly.
That is what allows a product workspace registry to reference both shared
generation workflows and product-owned workflows such as `AppMarketing` and
`InvestorMarketplace`.

For generated OSS-style bundles, bounded frontend customization lives inside the
active app root at `app/ui/index.js`. That file is the app-owned extension
barrel loaded through `@platform/extensions`.

Hosted product modules are hosted-product modules, not generic sample modules:

```text
app/modules/
├── app_registry/               # build records, staged artifact history
├── hosting/                    # hosted deployment intake + lifecycle
├── investor_marketplace/       # listings, investor profiles, investment interest
└── communications/             # conversations, messages, announcements
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

Admin is not generated as an app page. The platform shell owns the
`/admin` route family and renders the framework-owned `AdminPortal`.

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
# app/workflows/WritersRoom/orchestrator.yaml
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
manifests. That router consumes `subscriptions.yaml` and `notifications.yaml`
and derives platform reactions such as `notification.created`.

### `config/*`

Runtime-facing generated or platform-owned config.

This folder should not be the primary authoring target.

## Practical Authoring Order

For most new apps:

1. Create `app/app.json`
2. Create shell brand config only if the app needs custom identity
3. Create app pages in `app/ui/pages/`
4. Create workflow definitions in `app/workflows/` (with triggers in `orchestrator.yaml`)
5. Create modules in `app/modules/` (with actions in `module.yaml` and events in `events.yaml`)
6. Add `admin.yaml`, `settings.yaml`, `notifications.yaml`, and
   `subscriptions.yaml` as needed

## CRUD Minimalism

Do not model every database concern up front.

For the current phase, a CRUD-like app should usually start with:

- a page manifest
- a page UI stub
- a thin module handler if the page needs backend reads or actions
- a workflow only when reasoning is actually needed

That is enough to prove the product shape without drowning the user in schema.

## Current Repo Reality

This repo now treats `factory_app/app/` as the first-party Studio app bundle.
Hosted product workspaces are expected to live outside this repo and consume
the same contract.

The canonical target is:

- self-contained app workspaces
- shared generation core outside app workspaces
- hosted product workspaces consuming the same workspace contract

## Cross References

- [overview.md](overview.md)
- [app-bundle-declaratives.md](app-bundle-declaratives.md)
- [distribution-and-workspace-model.md](distribution-and-workspace-model.md)
