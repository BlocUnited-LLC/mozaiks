# Step 4 — Assets And Fonts

!!! tip "Using an AI coding agent?"

    Hand this prompt pack to Claude Code, Cursor, or Copilot:

    ```
    Help me add or replace shell assets and fonts for my Mozaiks app.

    Please read the instruction prompt at:
    docs/instruction-prompts/custom-brand-integration/04-assets.md
    ```

---

> **Guide:** App Shell And Branding · Step 4 of 5

Mozaiks serves shell assets from `platform/brand/`.

The web app uses:

- `platform/brand/assets/` -> `/assets/...`
- `platform/brand/fonts/` -> `/fonts/...`

This works because the app’s Vite config uses `platform/brand` as the public directory.

## Where Files Go

```text
platform/brand/
├── assets/
│   ├── logo.svg
│   ├── wordmark.png
│   ├── favicon.png
│   ├── chat_bg_template.png
│   ├── sparkle.svg
│   ├── settings.svg
│   ├── notifications.svg
│   └── profile.svg
├── fonts/
│   └── Fagrak Inline.otf
└── login-theme/
    └── resources/...
```

## How theme_config.json References Assets

In `platform/config/theme_config.json`:

- image and icon fields should use filenames such as `"logo.svg"`
- local fonts should use `/fonts/...` paths

Examples:

```json
{
  "assets": {
    "logo": "mozaik_logo.svg",
    "backgroundImage": "chat_bg_template.png"
  },
  "fonts": {
    "logo": {
      "localFont": true,
      "src": "/fonts/Fagrak Inline.otf"
    }
  }
}
```

## Asset Rules

- icons should be real filenames with extensions
- SVG icons should ideally use `currentColor`
- background images and wordmarks can be PNG or WebP
- local font files should be placed under `platform/brand/fonts/`

## Login Theme Assets

Keycloak login assets are separate from the in-app shell assets.

Those live under:

- `platform/brand/login-theme/`

Use that folder only for Keycloak login-theme customization, not for normal shell icons.

## Verification

After adding or replacing files:

1. confirm the files exist under `platform/brand/assets/` or `platform/brand/fonts/`
2. verify the app can load them at `/assets/<file>` or `/fonts/<file>`
3. reload the app and confirm the shell resolves the new asset names

## Next

[Step 5 — Wiring](05-wiring.md)
