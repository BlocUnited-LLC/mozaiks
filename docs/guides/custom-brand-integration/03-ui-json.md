# Step 3 — navigation_config.json

> **Guide:** App Shell And Branding · Step 3 of 5

File location: `platform/config/navigation_config.json`

This file controls how the shell routes and what navigation surfaces it exposes.

## What Belongs Here

Use `navigation_config.json` for:

- `landing_spot`
- extra static pages
- default discover/profile/subscription items
- module nav entries that the shell should expose

Do not use it for:

- startup chat mode
- workflow entry point
- app identity

Those belong in `platform/config/ai.json` and `platform/app.json`.

## How It Relates To ai.json

These settings work together:

- `landing_spot` in `navigation_config.json` picks the route
- `chat.startup_mode` in `ai.json` picks `ask` vs `workflow`
- `workflows.entry_point` in `ai.json` picks the first workflow when workflow mode starts

That means the shell follows this chain:

```text
landing_spot -> chat mode -> workflow entry point
```

## Current Shape

```json
{
  "version": "1.1.0",
  "landing_spot": "/",
  "pages": [],
  "default": [],
  "modules": []
}
```

## Sections

### `landing_spot`

The route the shell opens first.

Examples:

- `"/"` for chat-first
- `"/lineup"` for a module-first app

### `pages`

Optional static or custom pages beyond the built-in shell routes.

### `default`

The built-in top-level shell destinations such as AI, profile, and subscription.

### `modules`

Shell navigation entries for registered modules.

Important:

- `module_registry.json` is still the canonical module catalog
- `navigation_config.json` controls which module routes are surfaced in the shell

## Editing Rules

- Keep `landing_spot` aligned with the actual app experience you want
- If the app is workflow-first, keep `landing_spot: "/"` and set `chat.startup_mode: "workflow"` in `ai.json`
- Prefer registered modules over ad hoc page wiring when the surface is meant to persist

## Verification

After editing:

1. verify `/api/navigation-config` returns the updated config
2. reload the app
3. confirm the landing route and visible shell items match what you changed

## Next

[Step 4 — Assets And Fonts](04-assets.md)
