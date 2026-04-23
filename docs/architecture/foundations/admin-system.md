# Admin System

Mozaiks has one visible admin route family:

```text
/admin
/admin/users
/admin/billing
/admin/usage
/admin/activity
/admin/settings
/admin/integrations
/admin/support
```

The routes are rendered by the framework-owned `AdminPortal` component. The
visible UX is app-owner oriented: users, billing, usage, activity, settings,
integrations, and support. Builder surfaces such as Studio and Build are
separate product routes, not admin sections. Authority remains separated by
panel source.

## Panel Sources

| Source | Owner | Declared In | Data/API |
|---|---|---|---|
| App panels | app backend | app backend `/api/admin/config` | `app_backend_url/api/admin/*` |
| Feature panels | module contract | `modules/{module}/admin.yaml` | module actions / optional module admin hooks |
| Usage/health panels | platform/runtime host | `platform/config/admin.json` | same-host `/api/admin/*` |

## Runtime Config

`platform/config/admin.json` controls admin access and runtime/operator panels:

```json
{
  "enabled": true,
  "admin_emails": ["builder@example.com"],
  "panels": {
    "app": [
      { "id": "stats", "label": "App Overview", "section": "overview" },
      { "id": "users", "label": "Users", "section": "users" }
    ],
    "modules": [],
    "runtime": [
      { "id": "stats", "label": "Usage Stats", "section": "usage" },
      { "id": "runs", "label": "Active Runs", "section": "usage" },
      { "id": "sessions", "label": "Recent Sessions", "section": "activity" }
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

When this file exists and `enabled` is true, the platform shell injects the
`/admin` route family with the `AdminPortal` component. Generators must not
create a separate admin page, `/app-admin` route, page schema, or admin React
shell.

## Feature Admin Contract

Modules contribute app-owner panels through `modules/{module}/admin.yaml`, but
the UI does not expose "modules" as a user-facing admin section. Each panel must
declare a semantic section:

```text
overview | users | billing | usage | activity | settings | integrations | support
```

Example:

```yaml
schema_version: mozaiks.admin.v1
panels:
  - id: campaigns.overview
    label: Campaigns
    description: Review and manage campaign activity.
    section: usage
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

Do not use `modules`, `plugins`, `operations`, `tools`, `studio`, `build`, or
manifest terms as admin sections. Those concepts belong in builder surfaces or
developer documentation.

## Generator Rules

- Generate `platform/config/admin.json` for admin access and runtime/operator
  panel visibility.
- Generate `modules/{module}/admin.yaml` for feature-owned app admin panels.
- Every generated admin panel must set `section` to one of the semantic admin
  sections listed above.
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
backend, feature panels use module contracts, and usage/health panels use
same-host runtime admin APIs.
