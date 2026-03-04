# Step 2 — brand.json

> **Guide:** Customizing Your Frontend · Step 2 of 5

File location: `brand/public/brand.json` — served at `/brand.json`

Visual identity only. UI structure, menus, and action icons belong in `ui.json`.

---

!!! tip "New to Development?"

    **Let AI configure your brand.json!** Copy this prompt into Claude Code:

    ```
    I want to configure brand.json for my Mozaiks app.

    Please read the instruction prompt at:
    docs/instruction-prompts/custom-brand-integration/02-brand-json.md

    My brand colors are: [describe your colors]
    My logo file is: [filename]
    ```

---

## Full example

```json
{
  "name": "YourApp",
  "tagline": "AI-Powered Everything",

  "assets": {
    "logo":            "logo.svg",
    "wordmark":        "wordmark.png",
    "favicon":         "favicon.png",
    "backgroundImage": "chat_bg.png"
  },

  "fonts": {
    "body": {
      "family":        "Rajdhani",
      "fallbacks":     "ui-sans-serif, system-ui, sans-serif",
      "googleFont":    "https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;600&display=swap",
      "tailwindClass": "font-sans"
    },
    "heading": {
      "family":        "Orbitron",
      "fallbacks":     "Rajdhani, sans-serif",
      "googleFont":    "https://fonts.googleapis.com/css2?family=Orbitron:wght@700&display=swap",
      "tailwindClass": "font-heading"
    },
    "logo": {
      "family":        "Fagrak Inline",
      "localFont":     true,
      "src":           "/fonts/Fagrak Inline.otf",
      "tailwindClass": "font-logo"
    }
  },

  "colors": {
    "primary":    { "main": "#06b6d4", "light": "#67e8f9", "dark": "#0e7490",  "name": "cyan"    },
    "secondary":  { "main": "#8b5cf6", "light": "#a78bfa", "dark": "#6d28d9",  "name": "violet"  },
    "accent":     { "main": "#f59e0b", "light": "#fbbf24", "dark": "#d97706",  "name": "amber"   },
    "success":    { "main": "#10b981", "light": "#34d399", "dark": "#059669",  "name": "emerald" },
    "warning":    { "main": "#f59e0b", "light": "#fbbf24", "dark": "#d97706",  "name": "amber"   },
    "error":      { "main": "#ef4444", "light": "#f87171", "dark": "#dc2626",  "name": "red"     },
    "background": { "base": "#0b1220", "surface": "#0f1724", "elevated": "#131d33", "overlay": "rgba(13,23,42,0.72)" },
    "border":     { "subtle": "#1e293b", "strong": "#334155", "accent": "#06b6d4" },
    "text":       { "primary": "#e6eef8", "secondary": "#94a3b8", "muted": "#64748b", "onAccent": "#f8fafc" }
  },

  "shadows": {
    "primary":   "0 20px 45px rgba(6,182,212,0.24)",
    "secondary": "0 20px 45px rgba(139,92,246,0.24)",
    "elevated":  "0 24px 60px rgba(11,18,32,0.55)",
    "focus":     "0 0 0 3px rgba(8,145,178,0.55)"
  }
}
```

---

## Field reference

### name & tagline

| Field | Type | Notes |
|-------|------|-------|
| `name` | string required | App display name — used in titles and aria labels |
| `tagline` | string optional | Short tagline for splash/login contexts |

### assets

All values are filenames resolved from `/assets/`.

| Field | Type | Notes |
|-------|------|-------|
| `assets.logo` | string required | Primary logomark SVG or PNG |
| `assets.wordmark` | string optional | Full logotype image |
| `assets.favicon` | string optional | Injected as `<link rel="icon">` |
| `assets.backgroundImage` | string optional | Chat area bg — falls back to `chat_bg_template.png` |

### fonts

Three slots: `body`, `heading`, `logo`. Set `localFont: true` + `src` for self-hosted fonts  
(place files in `brands/public/fonts/` → served at `/fonts/<file>`).

| Field | Notes |
|-------|-------|
| `*.family` | CSS font-family name |
| `*.fallbacks` | Comma-separated CSS fallbacks |
| `*.googleFont` | Full Google Fonts URL — injected as `<link>` |
| `*.localFont` | Set `true` for self-hosted |
| `*.src` | Self-hosted path, e.g. `"/fonts/MyFont.otf"` |
| `*.tailwindClass` | e.g. `"font-heading"` |

### colors

Each semantic token (`primary`, `secondary`, `accent`, `success`, `warning`, `error`) uses:

```json
{ "main": "#hex", "light": "#hex", "dark": "#hex", "name": "label" }
```

Applied as CSS custom properties: `--color-primary`, `--color-primary-light`, etc.

Structural tokens: `background` (`base`, `surface`, `elevated`, `overlay`) · `border` (`subtle`, `strong`, `accent`) · `text` (`primary`, `secondary`, `muted`, `onAccent`)

### shadows

Named `box-shadow` strings → CSS custom properties (`--shadow-primary`, etc.).

---

**Prev:** [Step 1 — Overview](01-overview.md)  
**Next:** [Step 3 — ui.json](03-ui-json.md)
