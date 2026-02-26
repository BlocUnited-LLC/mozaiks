# Brands

This folder is the declarative branding layer for template apps.
`brand.json` controls colors, fonts, header behavior, and image assets.

## Folder structure

```text
brands/
├── README.md
└── public/
    ├── brand.json
    ├── assets/              # Served at /assets/*
    └── fonts/               # Served at /fonts/*
```

## URL mapping

| File | URL |
|---|---|
| `public/brand.json` | `/brand.json` |
| `public/assets/logo.svg` | `/assets/logo.svg` |
| `public/fonts/MyFont.otf` | `/fonts/MyFont.otf` |

## Header configuration

`header` in `brand.json` drives the top bar in `src/components/layout/Header.js`.

```json
{
  "header": {
    "logo": {
      "src": "logo.svg",
      "wordmark": "wordmark.png",
      "alt": "My Brand",
      "href": "https://example.com"
    },
    "icons": {
      "profile": "profile.svg",
      "notifications": "notifications.svg"
    },
    "actions": [
      { "id": "discover", "label": "Discover", "icon": "sparkle", "visible": true },
      { "id": "docs", "label": "Docs", "icon": "docs.svg", "visible": true }
    ],
    "showProfile": true,
    "showNotifications": true
  }
}
```

### `header.actions[].icon` values

- Built-in icon tokens: `sparkle`, `discover`, `bell`, `profile`, `settings`, `plus`, `search`
- Custom image file: use a filename like `docs.svg` and place it in `public/assets/`
- Absolute URL/path also works (for example `/assets/docs.svg` or `https://...`)

### `header.icons` values

- `profile`: avatar fallback icon when no user photo exists
- `notifications`: icon used by the notification button
- Use filenames under `public/assets/` or absolute URLs/paths

## Update checklist

1. Add/replace files in `public/assets/` and `public/fonts/`.
2. Update `public/brand.json`.
3. Run the app and verify header logo/actions/icons.
