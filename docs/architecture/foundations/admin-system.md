# Admin System

Mozaiks has one visible admin route:

```text
/admin
```

The route is rendered by the framework-owned `AdminPortal` component. The
visible UX is unified, but authority remains separated by panel source.

## Panel Sources

| Source | Owner | Declared In | Data/API |
|---|---|---|---|
| App panels | app backend | app backend `/api/admin/config` | `app_backend_url/api/admin/*` |
| Module panels | module contract | `modules/{module}/admin.yaml` | module actions / optional module admin hooks |
| Runtime panels | platform/runtime host | `platform/config/admin.json` | same-host `/api/admin/*` |

## Runtime Config

`platform/config/admin.json` controls admin access and runtime/operator panels:

```json
{
  "enabled": true,
  "admin_emails": ["builder@example.com"],
  "panels": {
    "app": [
      { "id": "stats", "label": "App Overview" },
      { "id": "users", "label": "Users" }
    ],
    "modules": [],
    "runtime": [
      { "id": "stats", "label": "Runtime Stats" },
      { "id": "runs", "label": "Active Runs" },
      { "id": "sessions", "label": "Recent Sessions" }
    ]
  },
  "roles": ["admin"],
  "features": {
    "user_management": false,
    "billing": false,
    "audit_log": false
  }
}
```

When this file exists and `enabled` is true, the platform shell injects `/admin`
with the `AdminPortal` component. Generators must not create a separate admin
page, `/app-admin` route, page schema, or admin React shell.

## Module Admin Contract

Modules contribute app-owner panels through `modules/{module}/admin.yaml`:

```yaml
schema_version: mozaiks.admin.v1
panels:
  - id: campaigns.overview
    label: Campaigns
    description: Review and manage campaign activity.
    order: 20
    renderer: schema
    component: null
    data_source: module:campaigns:list_campaigns
    actions: [pause_campaign, archive_campaign]
    permissions: [campaigns.write]
hooks: []
```

Use `renderer: schema` for normal panels. Use `renderer: custom_component` only
when a developer provides a registered React component. Optional Python support
for complex panel data belongs in `modules/{module}/backend/admin.py`.

## Generator Rules

- Generate `platform/config/admin.json` for admin access and runtime/operator
  panel visibility.
- Generate `modules/{module}/admin.yaml` for module-owned app admin panels.
- Mark admin-only module actions in `module.yaml.actions[]` with admin
  permissions.
- Do not generate admin page schemas, `/app-admin`, standalone admin servers,
  or frontend admin shells.

## Access

Admin access follows the platform auth rules:

1. JWT role includes `admin`
2. user email matches `admin_emails` in `platform/config/admin.json`
3. local dev auth mode allows admin access

The unified UI does not collapse authority: app panels still call the app
backend, module panels use module contracts, and runtime panels use same-host
runtime admin APIs.
