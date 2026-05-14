# Admin System

Mozaiks does not currently expose a customer-facing app-admin route family in
the production console.

The production app console keeps these pages visible instead:

```text
/apps/:appId/overview
/apps/:appId/health
/apps/:appId/users
/apps/:appId/usage
/apps/:appId/billing
/apps/:appId/hosting
/apps/:appId/integrations
```

Framework-owned admin composition remains internal and host-owned. Build,
Health, Billing, Hosting, and Integrations are product surfaces; they are not admin
sections.

Terminology note:

- use `Health` as the visible product section name for runtime posture,
  workflow reliability, and app health in the production console
- keep `operations` as an internal admin taxonomy when framework-owned admin
  panels need a bounded section for incidents, logs, or runtime state

## Ownership

| Source | Owner | Declared In | Data/API |
|---|---|---|---|
| Runtime/operator panels | platform host | framework admin contract plus `app/app.json` `admins` for access | same-host `/api/admin/*` |
| Feature panels | module contract | `modules/{module}/admin.yaml` | module actions and optional `backend/admin.py` hooks |
| App-business panels | optional connected app backend | `app_backend_url/api/admin/config` | `app_backend_url/api/admin/*` |

The platform host owns the shell and access model. Modules own feature admin
panels. An app backend may add app-business panels, but it does not replace the
framework-owned admin shell.

## Canonical Model

The admin system has four distinct layers. Keep them separate:

1. `app/app.json` `admins` is the admin bootstrap/access allowlist for the app.
2. The built-in admin section registry is framework-owned and defines the
  semantic section ids, default labels, route paths, and default ordering for
  the app admin route family.
3. `modules/{module}/admin.yaml` contributes feature-owned panels into those
  semantic sections.
4. An optional connected app backend may contribute app-business panels through
  `GET {app_backend_url}/api/admin/config`.

The shell should render a resolved navigation model from those contracts. It
should not invent a second private set of section ids, paths, or drawer items
inside the frontend.

## Admin Bootstrap

There is no app-level `admin.json` contract anymore.

Admin bootstrap and access are owned by `app/app.json` `admins` plus the normal
auth role model.

Runtime/operator panels are framework-owned defaults surfaced by same-host
`/api/admin/*` endpoints. Feature panels and app-business panels use their own
contracts described above.

That means:

- generated apps should set `app/app.json` `admins` for bootstrap access
- generators should not emit `app/config/admin.json`
- runtime/operator panels remain framework-owned
- the workflow-owned build sequence and other product routes remain separate
  product concerns, not admin sections

Example:

```json
{
  "appName": "Builder Workspace",
  "admins": ["builder@example.com"]
}
```

The platform may still mount framework-owned admin panels through
`AdminPortal`, but generators must not create a separate app-admin page, route
family, page schema, or admin React shell for the production console.

## Built-In Section Registry

The built-in admin sections are framework-owned. They provide the semantic
taxonomy for:

- internal admin shell route injection
- sidebar and drawer navigation
- section-level rendering inside `AdminPortal`
- placement of runtime, module, and app-backend panels

The canonical built-in ids are:

```text
overview | users | usage | operations | settings
```

The taxonomy is fixed by the framework today. If a future product genuinely
needs bounded app-level section overrides, add a new explicit contract rather
than reintroducing `app/config/admin.json`.

Implementation rule:

- runtime/platform code owns the canonical section metadata
- frontend admin shells must derive navigation and section routing from that
  canonical metadata or from a payload resolved from it
- do not maintain separate hardcoded copies of section ids, paths, labels, and
  order in multiple frontend files

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

## Custom Admin UI vs Custom Admin Routes

Mozaiks must allow agent-generated custom admin UI, but the default path is to
generate panels inside the host-owned `/admin` shell, not to generate a second
admin shell.

Use this order of preference:

1. `renderer: schema` for panels that can be expressed through the shipped
   primitive system
2. `renderer: custom_component` for bounded React customization when schema is
   not sufficient
3. only introduce standalone generated admin route families through a future,
   explicit contract if the product truly needs them

Do not overload `app/app.json`, `modules/{module}/admin.yaml`, or any other
existing contract into a general admin-page generator.
If Mozaiks later supports agent-generated standalone admin subroutes, that must
be a new contract with explicit ownership, registration, and validation rules.

## Componentization Rule

"Fully componentized" in the admin system means:

- the shell is presentation-first and consumes a resolved navigation model
- built-in section ids and routes come from one canonical registry
- module and app-backend panels plug into those sections through explicit
  contracts
- custom React is a bounded extension point declared by contract, not an ad hoc
  frontend escape hatch

It does not mean that agents may emit arbitrary admin React pages or bypass the
host-owned `/admin` shell by default.

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

- Populate `app/app.json` `admins` for admin bootstrap access.
- Generate `modules/{module}/admin.yaml` for feature-owned admin panels.
- Every generated admin panel must set `section` to one of the semantic admin
  sections listed above.
- Prefer `renderer: schema` with `layout + sections[]`.
- Use `renderer: custom_component` only when the shipped primitive system
  cannot express the panel cleanly.
- Mark admin-only module actions in `module.yaml.actions[]` with admin
  permissions.
- Do not generate admin page schemas, `/app-admin`, standalone admin servers,
  frontend admin shells, or ad hoc admin route families.

## Access

Admin access follows the platform auth rules:

1. JWT role includes `admin`
2. user email matches an entry in `app/app.json` `admins`
3. local dev auth mode allows admin access

The unified UI does not collapse authority:

- runtime/operator panels use same-host admin APIs
- feature panels use module contracts
- app-business panels may use a connected app backend
