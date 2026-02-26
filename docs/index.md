&nbsp;

<div align="center">
  <img src="assets/mozaik_logo.svg" alt="Mozaiks" width="160"/>
  <h1 style="margin-top: 1rem; font-size: 2.4rem; font-weight: 800; letter-spacing: -0.5px;">Mozaiks</h1>
  <p style="font-size: 1.15rem; color: #94a3b8; max-width: 560px; margin: 0 auto 1.5rem;">
    Open-source runtime, orchestration, and contracts for AI-native applications.
    Drop in a fully branded chat interface in minutes.
  </p>
</div>

---

## What you can build

Mozaiks gives you a production-ready AI chat interface that's completely yours to brand and extend.

| | |
|---|---|
| 💬 **Conversational AI** | Full chat UI with streaming, history, and agent responses |
| 🎨 **Full Brand Control** | Colors, fonts, logos, icons — all from JSON files, no code changes |
| ⚡ **Workflow Mode** | Split-screen artifact view for structured AI workflows |
| 🧩 **Embeddable Widget** | Drop a floating assistant into any existing app |
| 🔌 **Bring Your Own Backend** | Wire to any FastAPI/REST backend with a single config |

---

## Frontend Customization Guide

Everything you need to take the template from default → fully branded.

<div class="grid cards" markdown>

-   :fontawesome-solid-rocket: **1 — Overview & Quick Start**

    ---

    Understand the template structure, file layout, and get running in under 5 minutes.

    [:octicons-arrow-right-24: Start here](guides/customizing-frontend/01-overview.md)

-   :fontawesome-solid-palette: **2 — Brand JSON**

    ---

    Set your colors, typography, gradients, and shadows from a single JSON file.

    [:octicons-arrow-right-24: Customize brand](guides/customizing-frontend/02-brand-json.md)

-   :fontawesome-solid-sliders: **3 — UI JSON**

    ---

    Configure header actions, profile menu, notifications, and footer links.

    [:octicons-arrow-right-24: Configure UI](guides/customizing-frontend/03-ui-json.md)

-   :fontawesome-solid-images: **4 — Assets**

    ---

    Add your logo, background images, custom fonts, and icon SVGs.

    [:octicons-arrow-right-24: Add assets](guides/customizing-frontend/04-assets.md)

-   :fontawesome-solid-plug: **5 — Wiring**

    ---

    Connect your frontend to the Mozaiks runtime and go live.

    [:octicons-arrow-right-24: Wire it up](guides/customizing-frontend/05-wiring.md)

</div>

---

## How it works

```
templates/
  app.json          ← appName, appId, apiUrl, defaultWorkflow
  frontend/
    brand/public/
      brand.json    ← colors, fonts, shadows
      ui.json       ← icons, header actions, nav
      assets/       ← logo, background, fonts
```

At startup `themeProvider.js` fetches `brand.json` and `ui.json`, merges them into a theme object, and stamps CSS custom properties across the entire app. Change the JSON → change the look. No rebuilds needed for brand updates.

---

!!! tip "New here?"
    Jump straight to [Step 1 — Overview & Quick Start](guides/customizing-frontend/01-overview.md) to get a live branded app running in minutes.
