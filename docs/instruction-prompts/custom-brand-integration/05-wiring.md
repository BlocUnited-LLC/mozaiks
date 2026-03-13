# Prompt Pack: Shell Wiring

## Task

Explain or update how the Mozaiks shell is wired together.

## Read First

- `platform/app.json`
- `platform/config/ai.json`
- `platform/config/theme_config.json`
- `platform/config/navigation_config.json`
- `platform/config/module_registry.json`
- `app/vite.config.js`
- `docs/guides/custom-brand-integration/05-wiring.md`

## What To Explain

- `app.json` boots the shell
- `ai.json` controls startup mode and workflow entry
- `theme_config.json` controls visual shell state
- `navigation_config.json` controls landing route and shell nav
- `module_registry.json` is the module catalog
- `platform/brand/` is the public asset root for the web app

## Rules

- Do not reintroduce retired `brand.json` or `ui.json` files.
- Do not move workflow startup config out of `ai.json`.
- Do not describe module registration as a plugin system.

## Verification

1. point to the exact file a requested change belongs in
2. explain the boot chain clearly
3. only recommend code edits if config cannot express the requirement
