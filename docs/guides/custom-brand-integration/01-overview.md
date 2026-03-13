# App Shell And Branding

> **Guide:** Configure the Mozaiks shell without changing core runtime code

Mozaiks apps ship with a shared shell. You customize that shell through declarative files in `platform/`, not by editing the runtime.

!!! tip "Using an AI coding agent?"

    Start with the prompt packs in:

    `docs/instruction-prompts/custom-brand-integration/`

    Use the guide first to understand the file model, then hand the matching prompt pack to Claude Code, Cursor, or Copilot.

## What You Customize

| Concern | Primary file |
|---|---|
| App identity, API URLs, auth | `platform/app.json` |
| AI startup behavior | `platform/config/ai.json` |
| Theme, logo, colors, header, profile, notifications, footer | `platform/config/theme_config.json` |
| Landing route and extra pages | `platform/config/navigation_config.json` |
| Registered modules | `platform/config/module_registry.json` |
| Public assets and fonts | `platform/brand/assets/`, `platform/brand/fonts/` |
| Keycloak login theme assets | `platform/brand/login-theme/` |

## How The Shell Actually Boots

```text
app/App.jsx
    -> reads platform/app.json
    -> mounts the shared chat-ui shell
    -> chat-ui loads theme_config.json and navigation_config.json from the backend
    -> ai.json decides startup_mode + workflow entry point
    -> module_registry.json provides the durable module catalog
```

That means:

- visual identity lives in `theme_config.json`
- shell routing lives in `navigation_config.json`
- workflow startup lives in `ai.json`
- module registration lives in `module_registry.json`

## Recommended Editing Order

1. Set `platform/app.json`
2. Set `platform/config/ai.json`
3. Customize `platform/config/theme_config.json`
4. Customize `platform/config/navigation_config.json`
5. Add or edit assets under `platform/brand/`
6. Register modules in `platform/config/module_registry.json`

## Guide Structure

| Step | What it covers |
|---|---|
| [Step 2 — Theme Config](02-brand-json.md) | Identity, colors, fonts, header/profile/notifications/footer |
| [Step 3 — Navigation Config](03-ui-json.md) | Landing route, static pages, discover/default nav, module nav |
| [Step 4 — Assets And Fonts](04-assets.md) | Public asset locations and runtime resolution rules |
| [Step 5 — Wiring](05-wiring.md) | How the shell actually reads and applies the configuration |
| [Step 6 — Auth In app.json](06-auth-json.md) | Keycloak and mobile auth settings |

## Current Reality

The old `brand.json`, `ui.json`, and `navigation.json` split is retired in this repo.

Today:

- `theme_config.json` combines branding and shell chrome
- `navigation_config.json` controls landing and page navigation
- `module_registry.json` is the canonical module catalog

Treat those files as the current app-shell contract.
