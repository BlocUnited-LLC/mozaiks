# App Shell & Branding

Customize the visible identity of your Mozaiks app using declarative config files —
no runtime code changes needed.

## Key Files

| File | Controls |
|------|----------|
| `app/brand/theme_config.json` | Colors, fonts, spacing, dark/light mode |
| `app/brand/assets/` | Logos and icons |
| `app/brand/fonts/` | Custom local fonts |
| `app/config/shell.json` | Navigation, chrome modes, shortcuts, header/footer/profile |
| `app/app.json` | App name, startup route, auth intent |

## Theme

Edit `app/brand/theme_config.json` to set your color palette, font family, and
border radius. These tokens flow into the shell and all generated UI surfaces.

## Assets

Place logos and icons under `app/brand/assets/`. Reference them in
`theme_config.json` or `shell.json`. Custom fonts go under `app/brand/fonts/`.

## Shell Navigation

`app/config/shell.json` controls where navigation appears and which shortcuts
are available. A minimal example:

```json
{
  "navigation": {
    "policy": {
      "desktop": { "global": "header", "local": "sidebar" },
      "mobile": { "global": "bottomBar", "local": "sheet" }
    }
  },
  "shortcuts": {
    "header": ["dashboard"],
    "profile": ["profile", "settings", "signout"],
    "mobile": ["dashboard", "create", "profile"],
    "footer": ["terms", "privacy"]
  }
}
```

## Shell Chrome Modes

Individual pages declare a `shell_mode` in their YAML:

```yaml
shell_mode: workspace     # dense dashboard or admin surface
shell_mode: conversation  # chat or inbox thread
shell_mode: focused       # onboarding, checkout, review flow
shell_mode: immersive     # full-viewport canvas or media
shell_mode: public        # unauthenticated or marketing route
```

Override app-wide mode defaults in `shell.json` only when the per-page
declaration is not enough.

## Startup Route

Set the landing page in `app/app.json`:

```json
{
  "startupRoute": "/dashboard"
}
```
