# Custom Brand Integration

> **Guide:** Custom Brand Integration · Overview

---

!!! tip "New to Development?"

    **Let AI customize your branding!** Copy this prompt into Claude Code:

    ```
    I want to customize the branding of my Mozaiks app.

    Please read the instruction prompt at:
    docs/instruction-prompts/custom-brand-integration/01-overview.md

    I want to customize: [colors / logo / fonts / header / all]
    ```

---

## What This Is

When you build an app with Mozaiks, it comes with a default look. But you want it to look like **your** app — your colors, your logo, your style.

**Good news:** You don't need to write any code. Everything is controlled by simple JSON files that you can edit in any text editor.

Think of it like filling out a form:

- *"What's your primary color?"* → `#3B82F6`
- *"What's your app name?"* → `My Awesome App`
- *"Where's your logo?"* → `logo.svg`

The app reads these files and automatically applies your branding everywhere.

---

## What You Can Customize

| What | What it controls |
|------|-----------------|
| **Colors & Fonts** | Your brand colors, typography, visual style |
| **Logo & Images** | Your logo, favicon, background images |
| **Navigation** | Your app's menus, header actions, footer links |
| **Login Page** | The Keycloak login page branding |

---

!!! tip "New to Development?"

    **Let AI guide you through it!** Copy the prompt below into Claude Code (or any AI coding agent):

    ```
    I want to customize my Mozaiks app's branding.

    Please read the instruction prompt at:
    instruction-prompts/custom-brand-integration/colors-and-theme.md

    Then help me set up:
    - My brand colors: [describe your colors, e.g., "blue and purple" or paste hex codes]
    - My app name: [your app name]
    - My logo: [I have a logo file / I need help]

    Walk me through each step and verify the changes work.
    ```

    The AI will read our detailed instructions and guide you through every step.

---

## Want to Get Your Hands Dirty? Here's How It Works

### File Locations

All brand files live in one place:

```
app/brand/public/              ← Everything here is served at /
├── brand.json                 ← Colors, fonts, shadows
├── ui.json                    ← Navigation, menus, footer
├── auth.json                  ← Login page branding
├── navigation.json            ← App navigation structure
├── assets/                    ← Your images
│   ├── logo.svg
│   ├── favicon.png
│   └── ...
└── fonts/                     ← Custom fonts (optional)
```

### How the App Uses These Files

```
App starts
    ↓
Fetches brand.json + ui.json (your source of truth)
    ↓
Queries platform theme API (multi-tenant overrides, if available)
    ↓
Merges overrides on top of brand.json base
    ↓
Applies as CSS variables (--color-primary, etc.)
    ↓
Every component uses these variables
    ↓
Your branding appears everywhere
```

`brand.json` is always the base. If the platform API has no custom theme for your app, it returns 404 and brand.json is used as-is. If a file is missing or has errors, the app uses safe defaults and logs a warning.

---

## Quick Test: Change Something Right Now

Try this to see customization in action:

**1. Open `app/brand/public/brand.json`**

Find this at the top:
```json
{
  "name": "YourApp",
```

**2. Change the name**

```json
{
  "name": "My Cool App",
```

**3. Save and refresh your browser**

Go to http://localhost:5173 — you should see your new app name.

That's it. No restart needed. No code changes. Just edit JSON and refresh.

---

## This Guide's Structure

We've broken customization into focused sections. Each one explains:

1. **What it does** (non-technical)
2. **AI-assisted setup** (for those who want guidance)
3. **Deep dive** (for those who want to understand the details)

| Step | What You'll Do |
|------|---------------|
| [**Step 2: brand.json**](02-brand-json.md) | Set colors, fonts, and visual style |
| [**Step 3: ui.json**](03-ui-json.md) | Configure navigation, menus, and layout |
| [**Step 4: Assets**](04-assets.md) | Add your logo, favicon, and images |
| [**Step 5: Wiring**](05-wiring.md) | Connect navigation to your app's routes |
| [**Step 6: auth.json**](06-auth-json.md) | Brand the login page |

---

## Common Questions

??? question "Do I need to restart the server after changes?"
    No! The frontend hot-reloads. Just save the file and refresh your browser.

??? question "What if I break something?"
    The app has fallback defaults. If your JSON has errors, it'll use safe defaults and show a warning in the browser console (F12 → Console).

??? question "Can I see changes without deploying?"
    Yes, all changes are visible immediately in development mode (`npm run dev`).

??? question "Where do I put my logo file?"
    Put it in `app/brand/public/assets/` and reference it by filename in `brand.json`.

---

**Next:** [Step 2 — brand.json](02-brand-json.md)
