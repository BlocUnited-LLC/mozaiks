---
name: add-branding
description: Customize app shell branding - themes, colors, navigation, logos. Works with platform/config/ declarative files.
argument-hint: "[what you want to change - theme, colors, logo, navigation]"
---

Help the user customize the Mozaiks app shell using declarative config.

## Key Files

Read these first:
- `platform/app.json` — App identity, auth, admin emails
- `platform/config/theme_config.json` — Theme tokens and shell chrome
- `platform/config/ai.json` — Startup mode, workflow entry point

For assets:
- `platform/brand/assets/` — Logos, icons
- `platform/brand/fonts/` — Custom fonts

## Questions to Ask

1. Changing visuals, shell behavior, or both?
2. Should app open on chat, a page, or adapter route?
3. Should chat start in `ask` mode or `workflow` mode?
4. Changing logos, icons, or fonts?
5. Any auth changes?

## What Each File Controls

| File | Controls |
|------|----------|
| `platform/app.json` | App name, auth requirement, admin emails |
| `platform/config/theme_config.json` | Colors, fonts, spacing, dark/light mode, shell chrome |
| `platform/config/ai.json` | Entry workflow, startup mode |

## Rules

- Do NOT invent files like `brand.json`, `ui.json`, or `auth.json` — these don't exist
- Prefer declarative config changes over React code changes
- Keep workflow startup settings in `ai.json`

## Verification

After changes:
1. Verify JSON parses correctly
2. Check `/api/theme-config` returns expected values
3. Check `/api/shell-config` returns expected values
4. Verify any referenced assets exist under `platform/brand/`
