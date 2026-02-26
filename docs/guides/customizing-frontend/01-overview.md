# Step 1 — Overview & Quick Start

> **Guide:** Customizing Your Frontend · Step 1 of 5  
> **Live:** https://docs.mozaiks.ai/guides/custom-frontend/

---

## How it works

The `chat-ui` template reads all brand and layout configuration from two static JSON files
served from the app's public directory. At startup, `themeProvider.js` fetches both files
in parallel, merges them into a single theme object, and applies CSS custom properties
across the entire app.

| File | Controls |
|------|----------|
| `brand.json` | Colors, typography, shadows, asset filenames — what it *looks like* |
| `ui.json` | Header actions, profile menu, notifications, footer links — what *appears* |

If a config file or individual key is missing, the runtime falls back to `BARE_FALLBACK_THEME`
and logs a `console.warn`. The app always renders.

---

## File layout

```
templates/
  app.json                         ← shared config (appName, appId, apiUrl, defaultWorkflow)
  frontend/
    vite.config.js                 # publicDir → "brand/public"
    package.json
    App.jsx
    main.jsx
    brand/
      public/                      ← Vite publicDir — served at /
        brand.json
        ui.json
        navigation.json
        assets/
          mozaik_logo.svg
          mozaik.png
          chat_bg_template.png
          profile.svg
          notifications.svg
          sparkle.svg
          settings.svg
          logout.svg
        fonts/
          Fagrak Inline.otf
    workflows/
      index.js                     # auto-discovers workflow folders
      hello_world/
        index.js
        HelloWorldArtifact.jsx
  backend/
    main.py
    workflows/
      hello_world/__init__.py
```

---

## Quick start

1. **Copy the template** — copy `templates/frontend/` and `templates/backend/` into your app root.
2. **Set your app identity** — edit `templates/app.json` (`appName`, `appId`, `defaultWorkflow`, `apiUrl`).
3. **Install dependencies**: `cd templates/frontend && npm install`
4. **Edit `brand/public/brand.json`** — your colors, fonts, assets. → [Step 2](02-brand-json.md)
5. **Edit `brand/public/ui.json`** — your header, profile menu, footer. → [Step 3](03-ui-json.md)
6. **Drop your assets** into `brand/public/assets/`. → [Step 4](04-assets.md)
7. **Wire navigation and auth** via `onAction`. → [Step 5](05-wiring.md)

---

## Start the dev server

```bash
cd templates/frontend
npm run dev
# → http://localhost:3000
```

> ⚠️ **Icon values must be filenames.** Values like `"bell"` or `"sparkle"` are not
> built-in tokens. Always use filenames: `"bell.svg"`, `"sparkle.svg"`.

---

**Next:** [Step 2 — brand.json](02-brand-json.md)
