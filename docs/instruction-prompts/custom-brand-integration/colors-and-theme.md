# Prompt Pack: Quick Theme Update

!!! tip "New to Development?"

    Copy this into Claude Code, Cursor, or Copilot:

    ```
    I want to do a quick visual refresh for my Mozaiks app.

    Please read the instruction prompt at:
    docs/instruction-prompts/custom-brand-integration/colors-and-theme.md

    Changes I want: [Describe — e.g. new color palette, font, accent color]
    ```

---

## Task

Make a fast visual refresh for a Mozaiks app.

## Read First

- `platform/config/theme_config.json`

## Fast Path

Edit only:

- `identity`
- `assets`
- `fonts`
- `colors`
- `shadows`

Avoid touching routing, modules, or auth unless the user asks for it.

## Verification

1. JSON parses
2. theme values are reflected by `/api/theme-config`
3. the shell looks correct after reload
