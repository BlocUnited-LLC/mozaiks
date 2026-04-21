# Platform Authoring

This document defines what should be authored under `platform/` and the key file contracts.

## Core Rule

`platform/` is the app bundle.

It should contain declaratives, assets, and explicit logic stubs. It should not contain framework compiler logic.

The bundle should stay focused on the product-facing pieces:

- app manifest
- pages
- workflows
- modules

Modules exist as support bundles, but they should not dominate the authoring model.

## Primary Families

| Family | Purpose | Path |
| --- | --- | --- |
| App manifest | Small app identity and target manifest | `platform/app.json` |
| Pages | Normal app screens | `platform/pages/*` |
| Workflows | Agentic execution | `platform/workflows/*` |
| Workflow triggers | App event to workflow rules | `platform/workflows/*/orchestrator.yaml` |

## Support Family

| Family | Purpose | Path |
| --- | --- | --- |
| Modules | Shared backing logic and helpers | `platform/modules/*` |

## What Users Should Mostly Author

For most apps, the user or generator should mainly author:

1. app manifest
2. pages
3. workflows
4. modules (when shared support logic appears)

Workflow triggers are declared inline in `orchestrator.yaml`, not as separate files.

## Generator Decision Rules

When the generator needs to add a new capability:

- visible routeable screen -> create a `page`
- shared backend helper -> create a `module`
- agentic execution -> create a `workflow`
- event-triggered workflow handoff -> add `triggers` in `orchestrator.yaml`

## Framework-Owned vs Generator-Owned

### Framework owns:

- manifest resolvers
- config compilers
- registry projections
- runtime loaders
- defaulting logic

Those do not belong in `platform/`.

Example:

- `chat-ui/src/platform/appManifest.js` is framework-owned
- `platform/app.json` is generator-owned

## File Contracts

### `platform/app.json`

Small authoring manifest only.

Expected default shape:

```json
{
  "appName": "My App",
  "targets": {
    "web": true,
    "mobile": false
  },
  "startup": {
    "landing_spot": "/dashboard"
  },
  "authRequired": true,
  "admins": [
    "owner@example.com"
  ]
}
```

The generator should not author low-level runtime plumbing here unless the user explicitly requests advanced overrides.

`platform/app.json` is for app identity, startup route, and product auth intent.

It is not the place for shell colors, login theme files, footer links, or header chrome.

It is also not the place for local development shortcuts such as auto-login.

### `platform/pages/{page}/`

Required:

- `page.json`
- `ui/index.js`

Optional:

- additional `ui/*.{js,jsx}`

Shared page-level UI should live under `platform/pages/_shared/`, not inside a module by default.

### `platform/workflows/{workflow}/`

Required:

- `orchestrator.yaml`
- `agents.yaml`
- `handoffs.yaml`
- `context_variables.yaml`
- `structured_outputs.yaml`
- `tools.yaml`
- `ui_config.yaml`
- `hooks.yaml`

Optional but common:

- `tools/*.py`
- `ui/index.js`
- `ui/*.{js,jsx}`
- `extended_orchestration/mfj_extension.json`

Event routing and workflow triggers are configured via:
- `platform/workflows/{workflow}/orchestrator.yaml` - `triggers` declare which app events start or resume a workflow
- app backends emit domain events through the runtime ingress boundary; there is no separate `platform/automations/event_catalog.json`

### `platform/modules/{module}/`

Required:

- `module.json`
- `handler.py`

Optional:

- `ui/index.js`
- additional `ui/*.{js,jsx}`

Modules are backing capability bundles. The generator should not create module UI unless the module truly exports reusable UI.

Modules do not own page routing by default. Routeable surfaces should be pages.

### `platform/brand/`

Generator-owned when the app needs shell branding.

Use:

- `platform/brand/assets/` for logos and icons
- `platform/brand/fonts/` for local fonts
- `platform/brand/login-theme/` only when auth login theme assets change

Do not use `platform/brand/` for page route logic.

## What Mozaiks Should Default

Mozaiks should default anything that most apps share, including:

- shell behavior
- auth plumbing
- backend URLs
- mobile defaults
- version defaults
- dev defaults
- registry projections

Those should not be the main declarative burden on the user.

## Simple Compiler View

The platform should think in this order:

```text
app idea
  -> small app manifest
  -> pages
  -> workflows (with inline triggers)
  -> app events
  -> support modules if needed
```

That is enough to generate useful apps without turning the config layer into a second programming language.

## Derived or Generated Artifacts

These should not be generator-authored:

- derived module catalog projections
- manifest resolvers
- generated realm exports
- system support files such as silent SSO helpers

Current examples:

- `platform/brand/realm-export.json` is a generated deployment artifact
- `platform/brand/_system/silent-check-sso.html` is framework support

## Anti-Drift Rule

If a file exists only to repeat information already declared elsewhere, the generator should not author it unless the runtime still requires it as a true input.

That means:

- do not duplicate module metadata into a separate catalog file
- do not duplicate app defaults into authored manifests
- do not add compiler helpers to `platform/`

## Cross References

- [canonical-app-structure.md](canonical-app-structure.md)
- [architecture-overview.md](architecture-overview.md)
- [surface-model.md](surface-model.md)
