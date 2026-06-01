# App Shell & Branding

Customize the visible identity of your Mozaiks app using declarative config files —
no runtime code changes needed.

## Key Files

| File | Controls |
|------|----------|
| `app/brand/theme_config.json` | Colors, fonts, spacing, dark/light mode |
| `app/brand/assets/` | Logos and icons |
| `app/brand/fonts/` | Custom local fonts |
| `app/config/shell.json` | Header, navigation, shortcuts, chrome modes, notifications |
| `app/app.json` | App name, startup route, auth intent |

---

## theme_config.json

`app/brand/theme_config.json` drives all visual tokens for the shell, pages, and
chat surfaces. It is served by `/api/theme-config` and read by `themeProvider.js`.
It has seven top-level sections.

### theme

High-level presets. Most apps only need to touch these.

```json
{
  "theme": {
    "primary": "cyan",
    "variant": "modern",
    "radius": "medium",
    "font": "rajdhani",
    "font_heading": "orbitron",
    "appearance": "dark",
    "density": "comfortable",
    "branding": {
      "app_name": "My App",
      "logo_url": "/assets/my_logo.svg",
      "favicon_url": "/favicon.ico"
    }
  }
}
```

| Field | Options |
|-------|---------|
| `primary` | Named color — e.g. `cyan`, `violet`, `emerald`, `amber` |
| `variant` | `modern`, `flat`, `glass` |
| `radius` | `none`, `small`, `medium`, `large`, `full` |
| `font` / `font_heading` | Font key matching an entry in `fonts` |
| `appearance` | `dark`, `light`, `system` |
| `density` | `compact`, `comfortable`, `spacious` |

### identity

App name and tagline shown in the UI and metadata.

```json
{
  "identity": {
    "name": "My Company",
    "tagline": "Do great things",
    "app_name": "My App"
  }
}
```

### assets

Asset filenames served from `app/brand/assets/`.

```json
{
  "assets": {
    "logo": "my_logo.svg",
    "wordmark": "my_wordmark.png",
    "favicon": "/favicon.ico",
    "chatbackgroundImage": "chat_bg.png"
  }
}
```

- `logo` and `wordmark` are referenced by `shell.json → header.logo`
- `chatbackgroundImage` is the subtle texture behind the chat feed (optional)

### fonts

Define body, heading, and logo fonts. Use `googleFont` for web fonts or
`localFont: true` with a `src` path for self-hosted fonts.

```json
{
  "fonts": {
    "body": {
      "family": "Inter",
      "fallbacks": "ui-sans-serif, system-ui, sans-serif",
      "googleFont": "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap",
      "tailwindClass": "font-sans"
    },
    "heading": {
      "family": "Sora",
      "fallbacks": "Inter, ui-sans-serif, sans-serif",
      "googleFont": "https://fonts.googleapis.com/css2?family=Sora:wght@600;700&display=swap",
      "tailwindClass": "font-heading"
    }
  }
}
```

For a local font, use:

```json
{
  "localFont": true,
  "src": "/fonts/MyFont.otf"
}
```

### colors

Full color palette. Each entry has `main`, `light`, and `dark` hex values.

```json
{
  "colors": {
    "primary":    { "main": "#06b6d4", "light": "#67e8f9", "dark": "#0e7490", "name": "cyan" },
    "secondary":  { "main": "#8b5cf6", "light": "#a78bfa", "dark": "#6d28d9", "name": "violet" },
    "accent":     { "main": "#f59e0b", "light": "#fbbf24", "dark": "#d97706", "name": "amber" },
    "background": { "base": "#0b1220", "surface": "#0f1724", "elevated": "#131d33" },
    "text":       { "primary": "#e6eef8", "secondary": "#94a3b8", "muted": "#64748b" }
  }
}
```

Also include `success`, `warning`, `error`, `border`, and `text.onAccent` as needed.

### shadows

Color-matched glow shadows used by elevated surfaces and focused elements.
Keys match the color palette: `primary`, `secondary`, `accent`, `success`,
`warning`, `error`, `elevated`, `focus`.

```json
{
  "shadows": {
    "primary": "0 20px 45px rgba(6, 182, 212, 0.24)",
    "focus":   "0 0 0 3px rgba(8, 145, 178, 0.55)"
  }
}
```

### primitives and ui

`primitives` sets border radius, max-width measures, and spacing scale.
`ui` sets pixel-level spacing for the shell header/footer, page layouts, and
chat feed. These rarely need changing — adjust only when the default density
or layout proportions don't match your design.

```json
{
  "primitives": {
    "radius":  { "surface": "1rem", "control": "0.5rem", "bubble": "18px" },
    "measure": { "shell": "1440px", "chat_feed": "1040px" },
    "spacing": { "tight": "0.5rem", "base": "1rem", "loose": "1.5rem" }
  }
}
```

---

## shell.json

`app/config/shell.json` is the main config for how the shell looks and behaves.
It has five top-level sections.

### header

Controls the logo, wordmark, and header action buttons.

```json
{
  "header": {
    "logo": {
      "src": "my_logo.svg",
      "wordmark": "my_wordmark.png",
      "alt": "My App",
      "href": "/dashboard"
    },
    "actions": [
      {
        "id": "create",
        "label": "Create",
        "path": "/create",
        "variant": "primary",
        "visible": true
      }
    ]
  }
}
```

- `logo.src` and `logo.wordmark` reference files in `app/brand/assets/`
- `logo.href` is where clicking the logo navigates
- `actions` are the buttons shown in the top-right of the header
- Each action supports `variant` (`primary` or `secondary`), `visible`, and
  optional `path_by_role` for role-based routing

### shortcuts

Declares which built-in shortcut ids appear in each shell zone. The backend
expands these into the full config returned by `/api/shell-config`.

```json
{
  "shortcuts": {
    "profile": ["profile", "settings", "signout"],
    "mobile": ["create", "profile"],
    "footer": ["legal", "terms", "cookies"],
    "footerHideOnMobile": true
  }
}
```

| Zone | Where it renders |
|------|-----------------|
| `profile` | Profile/avatar dropdown menu |
| `mobile` | Bottom bar on mobile |
| `footer` | Footer links |
| `header` | Header icon strip (optional) |

Common shortcut ids: `dashboard`, `create`, `profile`, `settings`, `signout`,
`signin`, `notifications`, `wallet`, `admin`, `support`, `legal`, `terms`,
`cookies`, `privacy`.

### navigation

Controls where global and local navigation are placed on desktop and mobile.

```json
{
  "navigation": {
    "policy": {
      "desktop": {
        "global": "header",
        "local": "sidebar",
        "footer": "visible"
      },
      "mobile": {
        "global": "bottomBar",
        "local": "sheet",
        "footer": "hidden"
      },
      "maxMobileItems": 5,
      "autoFromPages": false
    }
  }
}
```

- `global` — where primary nav destinations appear (`header` or `bottomBar`)
- `local` — where section/module nav appears (`sidebar` or `sheet`)
- `footer` — `visible` or `hidden`
- `maxMobileItems` — cap on bottom bar items before overflow
- `autoFromPages` — keep `false`; add navigation explicitly in page YAML

### chrome

Defines which shell surfaces (header, footer, bottom bar, local nav) are visible
per `shell_mode` on desktop and mobile. Pages set their own `shell_mode` — see
[Add a Page](../adding-pages/01-overview.md#shell-mode). Only override here when
the app-wide default for a mode needs changing.

```json
{
  "chrome": {
    "defaultMode": "standard",
    "modes": {
      "standard": {
        "desktop": { "header": true, "footer": true, "bottomBar": false, "localNav": true },
        "mobile":  { "header": true, "footer": false, "bottomBar": true,  "localNav": "sheet" }
      },
      "conversation": {
        "desktop": { "header": true, "footer": false, "bottomBar": false, "localNav": false },
        "mobile":  { "header": true, "footer": false, "bottomBar": false, "localNav": false }
      }
    }
  }
}
```

Available modes: `standard`, `workspace`, `conversation`, `focused`, `immersive`, `public`.

### notifications

Controls the notification bell in the header.

```json
{
  "notifications": {
    "show": true,
    "path": "/notifications",
    "emptyText": "No unread notifications"
  }
}
```

Set `"show": false` to hide the bell entirely.

---

## Startup Route

Set the landing page in `app/app.json`:

```json
{
  "startupRoute": "/dashboard"
}
```

## What Branding Does Not Control

Branding controls visual tokens and shell chrome. It does not control admin ownership and behavior.

- **Admin pages and panels** — declared in `app/admin/admin_registry.yaml` and registered in `app/admin/index.js`. Branding does not affect admin page routing or component registration.
- **Module behavior** — business logic and actions live in `app/modules/`. Theme changes do not alter module contracts.
- **Page content** — page structure and data bindings live in `app/ui/pages/*.yaml`. Branding sets visual style only.
- **Navigation structure** — top-level nav items and routes come from `app/config/shell.json` and `app/ui/route_manifest.json`, not from theme tokens.
