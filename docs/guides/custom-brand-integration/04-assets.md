# Step 4 — Assets & Icons

> **Guide:** Customizing Your Frontend · Step 4 of 5

All brand assets live in `brand/public/assets/` — served statically from the app root.

---

!!! tip "New to Development?"

    **Let AI set up your assets!** Copy this prompt into Claude Code:

    ```
    I want to add brand assets to my Mozaiks app.

    Please read the instruction prompt at:
    docs/instruction-prompts/custom-brand-integration/04-assets.md

    I have these files ready: [list your logo, icons, fonts]
    ```

---

## File layout

```
brand/public/
├── brand.json
├── ui.json
├── navigation.json
├── assets/
│   ├── logo.svg                 →  /assets/logo.svg
│   ├── wordmark.png             →  /assets/wordmark.png
│   ├── favicon.png              →  /assets/favicon.png
│   ├── chat_bg.png              →  /assets/chat_bg.png
│   ├── sparkle.svg              →  /assets/sparkle.svg
│   ├── settings.svg             →  /assets/settings.svg
│   ├── logout.svg               →  /assets/logout.svg
│   ├── notifications.svg        →  /assets/notifications.svg
│   └── profile.svg              →  /assets/profile.svg
└── fonts/
    └── Fagrak Inline.otf        →  /fonts/Fagrak Inline.otf
```

In `brand.json` and `ui.json`, reference **filenames only** (e.g. `"logo.svg"`, not `"/assets/logo.svg"`). The runtime prepends the path.

---

## ActionIcon filename rule

The `ActionIcon` component applies a strict check: the icon value **must contain a dot**.

| ✓ Valid | ✗ Invalid — logs warning, renders null |
|---------|----------------------------------------|
| `"sparkle.svg"` | `"sparkle"` |
| `"logo.png"` | `"SparkleIcon"` |
| `"settings.svg"` | `"icons/sparkle"` |

This rule applies to every icon field in `ui.json`: header actions, profile menu items, profile button icon, notifications icon.

---

## SVG format requirements

1. **Use `fill="currentColor"`** — not a hardcoded hex. Inherits CSS `color` from the parent.
2. **Set `viewBox="0 0 24 24"`** — remove fixed `width`/`height` attributes.
3. **Strip editor metadata** — remove `xmlns:xlink`, Illustrator comments, embedded rasters.
4. **No `fill="none"` on outer elements** if you want solid fills.

```xml
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
  <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>
</svg>
```

---

## Starter asset checklist

| Filename | Used in | Format |
|----------|---------|--------|
| `logo.svg` | Loading screen, header | SVG currentColor |
| `wordmark.png` | Header expanded | PNG transparent, max 200×40 |
| `favicon.png` | Browser tab | PNG 32×32 or 64×64 |
| `chat_bg.png` | Chat background | PNG/WebP, 1400px+ |
| `profile.svg` | Profile fallback avatar | SVG currentColor 24×24 |
| `notifications.svg` | Notification button | SVG currentColor 24×24 |
| `settings.svg` | Profile menu items | SVG currentColor 24×24 |
| `logout.svg` | Sign out menu item | SVG currentColor 24×24 |
| `sparkle.svg` | Action button | SVG currentColor 24×24 |

---

## PNG and WebP assets

Non-vector assets (wordmarks, backgrounds, favicons) are standard `<img>` tags — no SVG injection, no color theming. Use fully composed images with alpha transparency where needed.

For chat backgrounds: WebP at ~80% quality, 1400×900px source is sufficient for most viewports.

---

**Prev:** [Step 3 — ui.json](03-ui-json.md)  
**Next:** [Step 5 — Wiring & Backend](05-wiring.md)
