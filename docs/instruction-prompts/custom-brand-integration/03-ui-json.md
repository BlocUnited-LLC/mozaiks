# Prompt Pack: navigation_config.json

!!! tip "New to Development?"

    Copy this into Claude Code, Cursor, or Copilot:

    ```
    I want to configure the navigation for my Mozaiks app.

    Please read the instruction prompt at:
    docs/instruction-prompts/custom-brand-integration/03-ui-json.md

    Changes I want: [Describe your navigation changes — landing page, routes, menu items, etc.]
    ```

---

## Task

Help a user edit `platform/config/navigation_config.json`.

## Read First

- `platform/config/navigation_config.json`
- `platform/config/ai.json`
- `platform/config/module_registry.json`
- `docs/guides/custom-brand-integration/03-ui-json.md`

## What This File Controls

- `landing_spot`
- static `pages`
- default shell destinations
- surfaced module nav items

## What To Ask The User

1. Where should the app land first?
2. Should chat start in `ask` or `workflow` mode?
3. Should any module pages appear in the shell?
4. Are there extra static pages to add?

## Rules

- Keep `landing_spot` in `navigation_config.json`.
- Keep `startup_mode` and `entry_point` in `platform/config/ai.json`.
- Keep module registration in `platform/config/module_registry.json`.

## Verification

1. JSON parses
2. `/api/navigation-config` returns the change
3. shell nav and landing route behave as expected
