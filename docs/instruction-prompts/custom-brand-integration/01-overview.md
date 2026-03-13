# Prompt Pack: App Shell And Branding Overview

!!! tip "New to Development?"

    Copy this into Claude Code, Cursor, or Copilot:

    ```
    I want to customize the app shell and branding for my Mozaiks app.

    Please read the instruction prompt at:
    docs/instruction-prompts/custom-brand-integration/01-overview.md
    ```

---

## Task

Help a user customize the Mozaiks app shell using the current declarative config model.

## Current File Model

Read these files first:

- `platform/app.json`
- `platform/config/ai.json`
- `platform/config/theme_config.json`
- `platform/config/navigation_config.json`
- `platform/config/module_registry.json`

If assets are involved, also inspect:

- `platform/brand/assets/`
- `platform/brand/fonts/`
- `platform/brand/login-theme/` when login-theme changes are requested

## What Each File Owns

- `platform/app.json`: app identity, API URLs, auth
- `platform/config/ai.json`: startup mode, workflow entry point
- `platform/config/theme_config.json`: theme tokens and shell chrome
- `platform/config/navigation_config.json`: landing route and shell navigation
- `platform/config/module_registry.json`: canonical module catalog

## What To Ask The User

1. Do you want to change visuals, shell behavior, or both?
2. Should the app open on chat or on a module page?
3. Should chat start in `ask` mode or `workflow` mode?
4. Are you changing logos, icons, or fonts too?
5. Are auth changes part of this request?

## Rules

- Do not invent retired files like `brand.json`, `ui.json`, or `auth.json`.
- Prefer declarative config changes over React code changes.
- Keep module registration in `module_registry.json`.
- Keep workflow startup in `ai.json`.

## Verification

After changes:

1. verify the edited JSON parses
2. verify `/api/theme-config` and `/api/navigation-config` match the intended change
3. verify referenced assets exist under `platform/brand/`
