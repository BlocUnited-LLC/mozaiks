# Canonical App Structure

This document defines the app bundle shape Mozaiks should optimize for.

## Core Rule

The bundle should describe the app, not the platform internals.

That means the main authoring folders should focus on:

- what screens exist (`pages/`)
- what workflows exist (`workflows/`)
- what modules provide business logic (`modules/`)
- what events connect them (declared in `module.json` and `orchestrator.yaml`)

## Canonical Layout

```text
platform/
├── app.json
├── brand/
│   ├── assets/
│   ├── fonts/
│   └── login-theme/
├── pages/
│   ├── _shared/
│   │   └── ui/
│   │       └── *.{js,jsx}
│   ├── admin/                  # admin dashboard (first-class page)
│   │   └── ui/
│   │       └── AdminPortal.jsx
│   └── {page_name}/
│       ├── page.json
│       └── ui/
│           ├── index.js
│           └── *.{js,jsx}
├── workflows/
│   └── {workflow_name}/
│       ├── orchestrator.yaml   # includes triggers (no separate automations/)
│       ├── admin.yaml          # optional, admin dashboard declarations
│       ├── subscription.yaml   # optional, entitlement requirements
│       ├── agents.yaml
│       ├── handoffs.yaml
│       ├── context_variables.yaml
│       ├── structured_outputs.yaml
│       ├── tools.yaml
│       ├── ui_config.yaml
│       ├── hooks.yaml
│       ├── tools/
│       │   └── *.py
│       ├── admin/              # optional, admin UI components
│       │   └── *AdminPanel.jsx
│       └── ui/                 # optional, main user-facing UI
│           ├── index.js
│           └── *.{js,jsx}
├── operations/
│   └── {operations_name}/
│       ├── operations.json         # includes events.emits, events.handles
│       ├── admin.yaml          # optional, admin dashboard declarations
│       ├── subscription.yaml   # optional, entitlement requirements
│       ├── handler.py
│       ├── admin/              # optional, admin UI components
│       │   └── *AdminPanel.jsx
│       └── ui/                 # optional, main UI components
│           ├── index.js
│           └── *.{js,jsx}
└── config/
    ├── ai.json                 # LLM provider, model, temperature
    └── theme_config.json       # Color schemes, fonts, shell chrome
```
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

- `discover` — Browse content
- `admin` — Admin dashboard (NOTE: currently here, should be first-class)
- Custom pages (lineup, dashboard, settings, etc.)

Pages are where most CRUD-style app experience should live.

Shared page UI belongs under `pages/_shared/`.

**Admin Dashboard Note:**

Currently located at `platform/pages/admin/`. This makes it app-level, but admin should be a **first-class framework component** (like chat-ui).

Think of admin as the "admin user profile" — when an admin logs in, they see a dashboard with system health, token usage, subscription controls, etc. It's not an optional "adapter", it's a core role-based view.

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
3. Create pages (including admin if needed)
4. Create workflow definitions (with triggers in `orchestrator.yaml`)
5. Create modules (with events in `module.json`)
6. Add `admin.yaml` and `subscription.yaml` as needed

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
`platform/modules/*/module.json`, not hand-authored as a separate source of
truth.

## Cross References

- [overview.md](overview.md)
- [page-model.md](page-model.md)
- [app-bundle-declaratives.md](app-bundle-declaratives.md)
