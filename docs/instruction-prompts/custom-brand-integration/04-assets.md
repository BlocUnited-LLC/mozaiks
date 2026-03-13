# Prompt Pack: Assets And Fonts

!!! tip "New to Development?"

    Copy this into Claude Code, Cursor, or Copilot:

    ```
    I want to add or replace assets and fonts for my Mozaiks app.

    Please read the instruction prompt at:
    docs/instruction-prompts/custom-brand-integration/04-assets.md

    Changes I want: [Describe — e.g. new logo, custom font, background image]
    ```

---

## Task

Help a user add or replace shell assets and fonts for Mozaiks.

## Current Asset Locations

- `platform/brand/assets/`
- `platform/brand/fonts/`
- `platform/brand/login-theme/` for Keycloak login-theme assets only

## Read First

- `platform/config/theme_config.json`
- `docs/guides/custom-brand-integration/04-assets.md`

## Rules

- normal shell image and icon filenames are referenced from `theme_config.json`
- images resolve at `/assets/<file>`
- fonts resolve at `/fonts/<file>`
- login-theme files are separate from shell files

## Implementation Steps

1. Ask what files the user wants to add or replace.
2. Put shell assets in `platform/brand/assets/`.
3. Put fonts in `platform/brand/fonts/`.
4. Update `platform/config/theme_config.json` with the matching filenames.
5. Verify the files are available from the expected public URLs.

## Verification

1. files exist in `platform/brand/`
2. filenames in `theme_config.json` match exactly
3. the app loads the new asset or font
