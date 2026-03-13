# Step 5 — Wiring

!!! tip "Using an AI coding agent?"

    Hand this prompt pack to Claude Code, Cursor, or Copilot:

    ```
    Help me understand or update how the Mozaiks shell is wired together.

    Please read the instruction prompt at:
    docs/instruction-prompts/custom-brand-integration/05-wiring.md
    ```

---

> **Guide:** App Shell And Branding · Step 5 of 5

This page explains how the app shell reads and applies the declarative config.

## The Runtime Flow

```text
platform/app.json
    -> app/App.jsx boot config

platform/config/ai.json
    -> startup mode + workflow entry point

platform/config/theme_config.json
    -> theme, assets, header, profile, notifications, footer

platform/config/navigation_config.json
    -> landing route + shell pages + module nav

platform/config/module_registry.json
    -> canonical module catalog

platform/brand/
    -> public assets, fonts, login theme files
```

## File Responsibilities

| File | What it controls |
|---|---|
| `platform/app.json` | App identity, API/WS URLs, auth |
| `platform/config/ai.json` | Engine, startup mode, workflow entry point |
| `platform/config/theme_config.json` | Theme tokens and shell chrome |
| `platform/config/navigation_config.json` | Landing route and shell navigation |
| `platform/config/module_registry.json` | Registered modules |

## Web Shell Boot Path

### 1. `app/App.jsx`

The app shell reads `platform/app.json` for core boot configuration.

### 2. `app/vite.config.js`

The web app exposes `platform/brand/` as the public directory, which is why `/assets/...` and `/fonts/...` resolve from that folder.

### 3. `mozaikscore` config routes

The backend serves the declarative config files through API endpoints such as:

- `/api/theme-config`
- `/api/navigation-config`

### 4. `chat-ui` providers

The shared UI layer loads those configs and applies them to:

- theme variables
- shell actions
- header and profile controls
- landing behavior

### 5. `ai.json`

When the landing route is chat, `ai.json` decides:

- whether chat starts in `ask` or `workflow`
- which workflow starts first in workflow mode

## Current Practical Rule

If you want to change how the app looks or how the shell behaves, start by changing declarative config before touching React code.

Edit code only when:

- the shell needs a new UI capability
- the current config model cannot express the behavior
- you are building a new module or workflow UI component

## Verification Checklist

- `platform/app.json` points at the correct backend URLs
- `platform/config/ai.json` has the intended startup mode and workflow entry point
- `platform/config/theme_config.json` returns from `/api/theme-config`
- `platform/config/navigation_config.json` returns from `/api/navigation-config`
- referenced assets exist under `platform/brand/assets/` or `platform/brand/fonts/`
- registered modules exist in `platform/config/module_registry.json`

## Next

[Step 6 — Auth In app.json](06-auth-json.md)
