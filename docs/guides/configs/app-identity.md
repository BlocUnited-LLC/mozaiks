# App Identity

`app/app.json` is the app's identity and high-level runtime intent. It is the
first file the app loader expects to find in an active app root.

Use `app/app.json` for:

- app name and app id
- optional description and version
- startup route intent
- whether auth is required
- local admin allowlist
- high-level target flags such as web or mobile

Do not use `app/app.json` for module actions, page schemas, navigation chrome,
provider credentials, billing rules, or refinement routing.

## Starter

```json
{
  "appName": "Support Desk",
  "appId": "support-desk",
  "version": "1.0.0",
  "startup": {
    "landing_spot": "/dashboard"
  },
  "targets": {
    "web": true,
    "mobile": false
  },
  "authRequired": true,
  "admins": ["owner@example.com"]
}
```

## Common Fields

| Field | Purpose |
|-------|---------|
| `appName` | Human-readable app name used by Studio and shell summaries |
| `appId` | Stable app id for workspace and generated-artifact references |
| `version` | App bundle version metadata |
| `startup.landing_spot` | Default route after startup or login |
| `targets.web` | Declares that the app has a web surface |
| `targets.mobile` | Declares mobile target intent when present |
| `authRequired` | Enables authenticated app behavior when paired with `app/config/auth.yaml` |
| `admins` | Local admin email allowlist for admin-gated surfaces |

Generated code may also use `name` in small fixtures or lower-level runtime
tests. For real app workspaces, prefer `appName` and `appId` so Studio and
workspace summaries have explicit identity metadata.

## Related Files

- `app/config/ai.json` chooses ask/chat/workflow startup.
- `app/config/shell.json` controls visible navigation.
- `app/config/auth.yaml` carries provider-neutral auth routes and env handles.
- `app/brand/theme_config.json` carries visual identity.
