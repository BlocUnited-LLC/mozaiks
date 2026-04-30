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

These routes are rendered by the framework-owned `AdminPortal` component. The
shell is fixed and semantic. Studio, Build, and other builder surfaces are
separate product routes, not admin sections.

## Ownership

| Source | Owner | Declared In | Data/API |
|---|---|---|---|
| Runtime/operator panels | platform host | `app/config/admin.json` | same-host `/api/admin/*` |
| Feature panels | module contract | `modules/{module}/admin.yaml` | module actions and optional `backend/admin.py` hooks |
| App-business panels | optional connected app backend | `app_backend_url/api/admin/config` | `app_backend_url/api/admin/*` |

The platform host owns the shell and access model. Modules own feature admin
panels. An app backend may add app-business panels, but it does not replace the
host-owned `/admin` shell.

## Host Config

`app/config/admin.json` is host-owned. It controls:

- whether admin is enabled
- admin email allowlist
- section visibility and ordering
- runtime/operator panel visibility

It does not declare feature panels.

Example:

```json
{
  "schema_version": "mozaiks.admin.host.v1",
  "enabled": true,
  "admin_emails": ["builder@example.com"],
  "sections": {
    "overview": { "label": "Overview", "enabled": true, "order": 999 },
    "users": { "label": "Users", "enabled": true, "order": 1000 },
    "billing": { "label": "Billing", "enabled": false, "order": 1001 },
    "usage": { "label": "Usage", "enabled": true, "order": 1002 },
    "activity": { "label": "Activity", "enabled": true, "order": 1003 },
    "settings": { "label": "Settings", "enabled": true, "order": 1004 },
    "integrations": { "label": "Integrations", "enabled": true, "order": 1005 },
    "support": { "label": "Support", "enabled": true, "order": 1006 }
  },
  "runtime_panels": [
    { "id": "stats", "label": "Usage Stats", "section": "usage" },
    { "id": "runs", "label": "Active Runs", "section": "usage" },
    { "id": "sessions", "label": "Recent Sessions", "section": "activity" }
  ]
}
```

When this file exists and `enabled` is true, the platform shell injects the
`/admin` route family with the `AdminPortal` component. Generators must not
create a separate admin page, `/app-admin` route, page schema, or admin React
shell.

## Feature Admin Contract

Feature-owned admin UI lives in `modules/{module}/admin.yaml`.

Each panel must declare one semantic section:

```text
overview | users | billing | usage | activity | settings | integrations | support
```

The canonical module admin schema is `mozaiks.admin.v2`.

Example:

```yaml
schema_version: mozaiks.admin.v2
panels:
  - id: campaigns.overview
    label: Campaigns
    description: Review and manage campaign activity.
    section: usage
    order: 20
    renderer: schema
    layout: full-width
    sections:
      - id: campaigns-table
        primitive: DataTable
        config:
          api_endpoint: /api/modules/campaigns/list_campaigns
          columns:
            - key: name
              label: Campaign
            - key: status
              label: Status
              type: badge
    permissions: [campaigns.write]
hooks: []
```

Use `renderer: schema` for the normal path. Schema panels reuse the same
primitive section system as `ui/pages/*.yaml`, but they render inside the
host-owned `/admin` shell rather than becoming standalone routes.

Use `renderer: custom_component` only when the shipped primitive system cannot
express the panel cleanly. In that case:

- `admin.yaml` declares `renderer: custom_component` and a stable `component`
  registry key
- `module_contract.js_stubs` declares the required React stub file
- the app-level `ui/index.js` registration barrel registers that component

Optional Python support for complex panel data belongs in
`modules/{module}/backend/admin.py`.

## App-Backend Panels

A connected app backend may contribute app-business admin panels through
`GET {app_backend_url}/api/admin/config`.

Those panels must declare:

- `schema_version: mozaiks.admin.app_backend.v1`
- `panels[]`
- `renderer: builtin | schema | custom_component`
- `builtin_panel` for builtin panels
- `layout + sections[]` for schema panels
- `component` for bounded custom components

Recommended generated file ownership for split backends:

- `backend/admin_config.py` returns the canonical payload
- `backend/routes/admin.py` exposes it through
  `mozaiksai.core.admin.build_app_backend_admin_router(...)`
- AppGenerator should treat `ControllerOutput.app_backend_admin_config` as the
  typed source of truth and may regenerate those two files from it during
  assembly/download validation.

This keeps admin rendering deterministic across module-owned and app-backend
surfaces.

The detailed connected app-backend panel contract remains repo-internal
planning material for now. Keep the public contract anchored on this page plus
the host-owned `/admin` shell behavior described here.

## Generator Rules

- Generate `app/config/admin.json` for admin access and runtime/operator panel
  visibility only.
- Generate `modules/{module}/admin.yaml` for feature-owned admin panels.
- Every generated admin panel must set `section` to one of the semantic admin
  sections listed above.
- Prefer `renderer: schema` with `layout + sections[]`.
- Use `renderer: custom_component` only when the shipped primitive system
  cannot express the panel cleanly.
- Mark admin-only module actions in `module.yaml.actions[]` with admin
  permissions.
- Do not generate admin page schemas, `/app-admin`, standalone admin servers,
  or frontend admin shells.

## Access

Admin access follows the platform auth rules:

1. JWT role includes `admin`
2. user email matches `admin_emails` in `app/config/admin.json`
3. local dev auth mode allows admin access

The unified UI does not collapse authority:

- runtime/operator panels use same-host admin APIs
- feature panels use module contracts
- app-business panels may use a connected app backend
