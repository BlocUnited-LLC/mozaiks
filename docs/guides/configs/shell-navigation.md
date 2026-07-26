# Shell And Navigation

`app/config/shell.json` owns app-wide shell behavior: header, navigation,
shortcuts, footer, notifications, and chrome modes.

Use it for:

- header logo and actions
- top-level and local navigation policy
- mobile shortcut policy
- footer visibility
- notification route and empty state
- shell chrome modes such as standard, workspace, focused, immersive, or public

Do not use `shell.json` for page-local layout, business actions, permissions,
branding tokens, or auth provider mechanics.

## Starter

```json
{
  "header": {
    "logo": {
      "src": "logo.svg",
      "alt": "Support Desk",
      "href": "/dashboard"
    },
    "actions": []
  },
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
      "autoFromPages": true
    }
  },
  "chrome": {
    "defaultMode": "standard"
  }
}
```

## How Routes Connect

Shell navigation points at routes. Routes are owned elsewhere:

- schema pages live in `app/ui/pages/*.yaml`
- custom React routes are registered through `app/ui/route_manifest.json` and
  `app/ui/index.js`
- module action routes come from `app/modules/{module_id}/module.yaml`
- workflow routes come from `workflows/{workflow_id}/`

`shell.json` should assemble those surfaces into an app shell. It should not
become a second page registry or a permissions layer.

## Branding Boundary

Visual tokens belong in `app/brand/theme_config.json`, not `shell.json`.
Reference logos and wordmarks from the shell when needed, but keep colors,
fonts, density, radius, shadows, and primitive variants in the brand config.

See also [App Shell and Branding](../custom-brand-integration/01-overview.md).
