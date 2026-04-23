# Canonical App Structure

This document defines the app bundle shape Mozaiks should optimize for.

## Core Rule

The bundle should describe the app, not the platform internals.

That means the main authoring folders should focus on:

- what screens exist (`pages/`)
- what workflows exist (`workflows/`)
- what modules provide business logic (`modules/`)
- what events connect them (declared in `events.yaml` and `orchestrator.yaml`)

## Active App Root Layout

An active app root is the directory read by `platform_app.py`. In the default
OSS workspace this is `platform/`. In App Zero this is `mozaiks-platform/app/`.

```text
platform/
├── app.json
├── config/
│   ├── ai.json
│   ├── shell.json
│   └── admin.json
├── pages/
│   ├── {page_name}.yaml        # Declarative page schema
│   └── {page_name}/
│       └── page.yaml           # Optional folder form
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
│       │   ├── handler.py      # required deterministic action handler
│       │   ├── settings.py     # optional settings hooks
│       │   ├── subscriptions.py
│       │   ├── notifications.py
│       │   └── admin.py
│       └── ui/                 # optional module-specific UI surfaces
│           └── index.js
└── brand/                      # optional colocated brand/theme assets
    ├── assets/
    ├── fonts/
    └── theme_config.json
```

## Product Workspace Layout

Some workspaces wrap the active app root with product-owned brand and UI
extension folders. App Zero uses this shape:

```text
mozaiks-platform/
├── app/                        # active app root read by platform_app.py
│   ├── app.json
│   ├── config/
│   ├── modules/
│   ├── pages/
│   └── workflows/
├── brand/                      # product brand/theme assets
├── ui/                         # product UI extension
├── generated/                  # generator output, not runtime-loaded
│   ├── apps/{app_id}/{build_id}/app/
│   └── workflows/{app_id}/{build_id}/{workflow_name}/
└── app-builder/                # builder docs/planning, not runtime-loaded
```

The loader resolves `brand/` and `ui/` as siblings of the active app root when
they exist. That is why `mozaiks-platform/app` can be the active app root while
`mozaiks-platform/brand` and `mozaiks-platform/ui` remain product-level assets.
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

Use `platform/config/theme_config.json` to point at those assets.

### `pages/*`

Normal routeable app screens.

Examples:

- `discover` — browse content
- `dashboard` — app home
- Custom pages such as lineup, catalog, settings, etc.

Pages are where most CRUD-style app experience should live.

Admin is not generated as an app page. The platform shell owns the
`/admin` route family and renders the framework-owned `AdminPortal`.

**Future:** Should be promoted to first-class `admin-ui/` directory at repo root (parallel to `chat-ui/`).

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
# platform/workflows/WritersRoom/orchestrator.yaml
triggers:
  - event: set.brief_confirmed
    action: run
    when:
      payload.status: approved
    message_template: "Start writing for {payload.set_type}."
```

### `modules/*`

Support bundles for shared logic.

Modules should not be the main mental model for app authors.

Use them when you need:

- shared page backing logic
- reusable handlers
- shared feature UI helpers
- page-triggered workflow helpers

### `config/*`

Runtime-facing generated or platform-owned config.

This folder should not be the primary authoring target.

## Practical Authoring Order

For most new apps:

1. Create `app.json`
2. Create shell brand config only if the app needs custom identity
3. Create app pages
4. Create workflow definitions (with triggers in `orchestrator.yaml`)
5. Create modules (with actions in `module.yaml` and events in `events.yaml`)
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

The repo still contains older generated and runtime projection files under
`platform/config/*`.

Treat those as implementation outputs, not the ideal authoring model.

The derived module catalog is one example of this. It should be derived from
`platform/modules/*/module.yaml`, not hand-authored as a separate source of
truth.

## Cross References

- [overview.md](overview.md)
- [page-model.md](page-model.md)
- [app-bundle-declaratives.md](app-bundle-declaratives.md)
