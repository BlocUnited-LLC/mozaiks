# Step 2 — theme_config.json

!!! tip "Using an AI coding agent?"

    Hand this prompt pack to Claude Code, Cursor, or Copilot:

    ```
    Help me configure theme_config.json for my Mozaiks app.

    Please read the instruction prompt at:
    docs/instruction-prompts/custom-brand-integration/02-brand-json.md
    ```

---

> **Guide:** App Shell And Branding · Step 2 of 5

File location: `platform/config/theme_config.json`

This file controls the visible identity of the web shell:

- app name and tagline
- logos and background image
- fonts
- colors and shadows
- header, profile, notifications, and footer UI

## What Belongs Here

`theme_config.json` combines what older versions split across multiple files.

Use it for:

- brand identity
- visual theme tokens
- shell chrome
- chat mode labels/tints

Do not use it for:

- API URLs
- auth provider selection
- workflow entry points
- module registration

Those belong in `platform/app.json`, `platform/config/ai.json`, and `platform/config/module_registry.json`.

## Top-Level Shape

```json
{
  "identity": {
    "name": "MozaiksAI",
    "tagline": "AI-Powered Workflows",
    "app_name": "Mozaiks"
  },
  "assets": {
    "logo": "mozaik_logo.svg",
    "wordmark": "mozaik.png",
    "favicon": "mozaik.png",
    "backgroundImage": "chat_bg_template.png"
  },
  "fonts": {},
  "colors": {},
  "shadows": {},
  "ui": {
    "chat": {},
    "header": {},
    "profile": {},
    "notifications": {},
    "footer": {}
  }
}
```

## Key Sections

### `identity`

Use this for app-facing names and taglines that the shell should surface.

### `assets`

All asset values are filenames resolved under `/assets/`.

The actual files live in:

- `platform/brand/assets/`

### `fonts`

Self-hosted font files live in:

- `platform/brand/fonts/`

Local fonts use paths such as:

```json
{
  "localFont": true,
  "src": "/fonts/Fagrak Inline.otf"
}
```

### `colors` and `shadows`

These become the shell’s visual tokens and are consumed by the shared UI layer.

### `ui`

This is where the shell chrome lives now:

- `ui.chat`
- `ui.header`
- `ui.profile`
- `ui.notifications`
- `ui.footer`

## Editing Rules

- Use asset filenames such as `"sparkle.svg"` or `"logo.png"`, not React component names.
- Keep icon values as actual filenames with extensions.
- Keep colors as explicit hex values or CSS-safe strings.
- Prefer changing this file over patching UI source.

## Verification

After editing:

1. reload the web app
2. open the browser console if something fails to render
3. verify `/api/theme-config` returns the updated file

## Next

[Step 3 — navigation_config.json](03-ui-json.md)
