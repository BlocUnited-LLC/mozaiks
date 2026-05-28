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

## Theme

Edit `app/brand/theme_config.json` to set your color palette, font family, and
border radius. These tokens flow into the shell and all generated UI surfaces.

## Assets

Place logos and icons under `app/brand/assets/`. Reference them by filename in
`shell.json`. Custom fonts go under `app/brand/fonts/`.

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
