# Prompt Pack: theme_config.json

## Task

Help a user edit `platform/config/theme_config.json`.

## Read First

- `platform/config/theme_config.json`
- `docs/guides/custom-brand-integration/02-brand-json.md`

## What This File Controls

- app identity text
- logos and background image
- fonts
- color tokens
- header, profile, notifications, and footer shell UI

## Implementation Steps

1. Read the current `platform/config/theme_config.json`.
2. Ask the user what should change.
3. Edit only the sections needed:
   - `identity`
   - `assets`
   - `fonts`
   - `colors`
   - `shadows`
   - `ui`
4. If an asset or font is referenced, verify the file exists under:
   - `platform/brand/assets/`
   - `platform/brand/fonts/`
5. Keep icon values as real filenames like `"sparkle.svg"`.

## Do Not Do

- Do not create `brand.json`.
- Do not put landing behavior here.
- Do not change auth here.

## Verification

1. JSON parses
2. asset filenames resolve
3. `/api/theme-config` returns the updated values
