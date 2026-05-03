---
name: add-branding
description: Customize app shell branding - themes, colors, navigation, logos. Works with app/brand/ and app/config/ declarative files.
argument-hint: "[what you want to change - theme, colors, logo, navigation]"
---

Help the user customize the Mozaiks app shell using declarative config.

## Key Files

Read these first:
- `app/app.json` — App identity, auth, admin emails
- `app/config/shell.json` — Shell chrome config
- `app/config/ai.json` — Startup mode, workflow entry point
- `app/brand/theme_config.json` — Theme tokens

For assets:
- `app/brand/assets/` — Logos, icons
- `app/brand/fonts/` — Custom fonts

## Questions to Ask

1. Changing visuals, shell behavior, or both?
2. Should app open on chat, a page, or adapter route?
3. Should chat start in `ask` mode or `workflow` mode?
4. Changing logos, icons, or fonts?
5. Any auth changes?

## What Each File Controls

| File | Controls |
|------|----------|
| `app/app.json` | App name, auth requirement, admin emails, startup landing spot |
| `app/brand/theme_config.json` | Colors, fonts, spacing, dark/light mode, shell chrome |
| `app/config/shell.json` | Header, footer, profile, notification chrome |
| `app/config/ai.json` | Entry workflow, startup mode |

## Rules

- Do NOT invent files like `brand.json`, `ui.json`, or `auth.json` — these don't exist
- Prefer declarative config changes over React code changes
- Keep workflow startup settings in `ai.json`
- Shell branding (logos, fonts, login theme) belongs in `app/brand/`
- `app/app.json` is for app identity and startup intent — not colors or shell chrome

## Verification

After changes:
1. Verify JSON parses correctly
2. Check `/api/theme-config` returns expected values
3. Check `/api/shell-config` returns expected values
4. Verify any referenced assets exist under `app/brand/assets/`
