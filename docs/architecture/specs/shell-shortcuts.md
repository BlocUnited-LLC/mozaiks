# Shell Shortcuts

`app/config/shell.json` supports a compact `shortcuts` authoring layer for
common shell chrome. The backend expands shortcuts into the full shell config
returned by `/api/shell-config`, so the frontend still consumes explicit
`header`, `profile`, `notifications`, `footer`, and `mobile` objects.

Shortcuts are not the full navigation model. Page-owned routes should prefer
`ui/pages/*.yaml -> navigation`, and app-wide placement behavior should use
`app/config/shell.json -> navigation.policy`. See
[shell-navigation.md](./shell-navigation.md).
Route-level header/footer/bottom-bar behavior is separate; use
`ui/pages/*.yaml -> shell_mode` and [shell-chrome-modes.md](./shell-chrome-modes.md).

## Example

```json
{
  "header": {
    "logo": {
      "src": "mozaik_logo.svg",
      "wordmark": "mozaik.png",
      "alt": "Mozaiks logo",
      "href": "/marketplace"
    },
    "actions": [
      {
        "id": "discover",
        "label": "Discover",
        "path": "/marketplace",
        "variant": "gradient",
        "visible": true
      }
    ]
  },
  "shortcuts": {
    "header": ["dashboard", "wallet"],
    "profile": ["profile", "wallet", "signout", "signin"],
    "mobile": ["dashboard", "wallet", "create", "profile"],
    "footer": ["legal", "terms", "cookies"],
    "footerHideOnMobile": true
  }
}
```

## Built-In Ids

Common ids include `dashboard`, `wallet`, `create`, `profile`, `messages`,
`notifications`, `settings`, `admin`, `support`, `signout`, `signin`, `legal`,
`terms`, `cookies`, and `privacy`.

The composer also indexes route ids from `ui/route_manifest.json`, declarative
page routes, and custom entries under `shortcuts.items`.

## Custom Items

```json
{
  "shortcuts": {
    "items": {
      "billing": {
        "label": "Billing",
        "path": "/billing",
        "requiresRole": "admin"
      }
    },
    "profile": ["profile", "billing", "signout"],
    "mobile": ["dashboard", "billing", "profile"]
  }
}
```

Use explicit `header`, `profile`, `footer`, or `mobile` arrays only when a
shortcut needs custom labels, icons, role gates, or paths that the catalog cannot
derive. Use `navigation.items` only for entries that are not owned by a page
schema; otherwise put the contribution on the page's `navigation` field.
