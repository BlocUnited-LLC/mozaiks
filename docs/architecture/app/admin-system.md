# Admin System

Mozaiks Studio does not expose a separate customer-facing `/admin` page in the
first-party Studio. The Admin Portal entry for Studio routes to `/apps`, which
is the workspace app-management surface.

App Studio keeps these pages visible instead:

```text
/apps/:appId/overview
/apps/:appId/usage
/apps/:appId/health
/apps/:appId/support
```

The full Studio menus are listed in
[Studio Product Model](../builder/studio-product-model.md).

Generated app AdminPortal routes use `/admin` and `/admin/<page>` paths. The
`/apps/:appId/...` route family is reserved for first-party Studio and hosted
product app operations.

Framework-owned admin composition remains internal and host-owned. Build,
Usage, health, and support are product surfaces; they are not admin
sections. `/apps/:appId/activity` (build review) remains routable but hidden
from primary navigation; other pages link to it. The app owner manages
connections from Integrations in the selected app’s Studio navigation. Hosted
deployments may provide billing, hosting, or account-access surfaces through
their own workspace routes or capability packs; the OSS factory Studio does not
hardcode those hosted product routes, and it mounts no page it has no data
for.

Terminology note:

- `operations` is the admin page for runtime state: what is running and how
  recent sessions ended
- `activity` is the admin page for a feature's own history, such as an audit
  trail

## Ownership

| Source | Owner | Declared In | Data/API |
|---|---|---|---|
| Runtime/operator panels | platform host | framework admin contract plus `app/app.json` `admins` for access | same-host `/api/admin/*` |
| Feature panels | module contract | `modules/{module}/contracts/admin.yaml` | module actions and optional `backend/admin.py` hooks |
| App-business panels | optional connected app backend | `app_backend_url/api/admin/config` | `app_backend_url/api/admin/*` |

The platform host owns the shell and access model. Modules own feature admin
panels. An app backend may add app-business panels, but it does not replace the
framework-owned admin shell.

## Canonical Model

The admin system has five distinct layers. Keep them separate:

1. `app/app.json` `admins` is the admin bootstrap/access allowlist for the app.
  It matches verified emails only.
2. The standard admin page catalog is framework-owned. It defines the page ids,
  labels, route paths, icons, and ordering for the app admin route family.
3. `app/admin/admin_registry.yaml` declares which pages this app's portal has.
  A page that is not declared has no route and no navigation entry.
4. `modules/{module}/contracts/admin.yaml` contributes feature-owned panels to
  those pages.
5. An optional connected app backend may contribute app-business panels through
  `GET {app_backend_url}/api/admin/config`.

AdminPortal resolves page rendering from those contracts. Studio
workspace/app navigation is a separate `WorkspaceLayout` concern and must not
be mutated by module admin panels.

## Admin Bootstrap

There is no app-level `admin.json` contract anymore.

Admin bootstrap and access are owned by `app/app.json` `admins` plus the normal
auth role model.

For ordinary module actions, `config/auth.yaml` (`mozaiks.auth.v2`) grants
permissions through `authorization.roles`. A matching `app.json` admin email
contributes only the reserved `admin` role's declared permissions, after the
runtime verifies the email or admits local development access. Matching rejects
non-ASCII addresses and is case-insensitive. The allowlist belongs to the loaded
app. It does not grant unrelated roles, bypass module permission checks or data
ownership, or confer the separate platform-operator authority required by
`/api/admin/modules/*`.

Runtime/operator panels are framework-owned defaults surfaced by same-host
`/api/admin/*` endpoints. Feature panels and app-business panels use their own
contracts described above. The same-host `/api/admin/*` endpoints are internal
framework APIs; Studio pages may consume their data, but customer-facing copy
should not expose "Admin" as a product section.

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

Generated app hosts may still mount framework-owned admin panels through
`AdminPortal`, but generators must not create a separate app-admin page, route
family, page schema, or admin React shell for Studio.

## Standard Admin Pages

The standard admin pages are framework-owned. They are the one vocabulary for:

- the pages an `admin/admin_registry.yaml` normally declares
- sidebar and drawer navigation
- placement of runtime, module, and app-backend panels

The standard page ids are:

```text
overview | access | usage | billing | activity | operations | settings | integrations | support
```

The catalog lives in `mozaiksai/core/admin/contract.py`
(`STANDARD_ADMIN_PAGES`), with each page's label, `/admin` path, icon, and
order. `tests/test_admin_page_catalog.py` keeps the generator contract, the
generator prompts, the frontend copy (`chat-ui/src/admin/contracts/adminPages.js`),
and these docs equal to it.

What each page shows:

| Page | Framework content | Filled by panels |
|---|---|---|
| `overview` | conversation counts, token and cost totals | yes |
| `usage` | AI usage totals, recent runs with tokens and cost | yes |
| `operations` | recent sessions and how each ended | yes |
| `settings` | the app's name, id, and connection details | yes |
| `access`, `billing`, `activity`, `integrations`, `support` | none | only |

A page with no framework content and no panel shows an empty state, so a
registry should declare `access`, `billing`, `activity`, `integrations`, or
`support` only when a panel references it. `usage` and `operations` are worth
declaring for any app that runs AI workflows.

A registry may also declare its own page id (a capability pack's `commerce`
page, for example). It renders the panels that reference it under the registry
label.

The framework panels are declared in the same file (`DEFAULT_RUNTIME_PANELS`):
`stats` and `runs` on `usage`, `sessions` on `operations`. Token and cost
numbers come only from `GET /api/admin/usage`; `GET /api/admin/stats`,
`/api/admin/runs`, and `/api/admin/sessions` describe conversation lifecycle.

If a future product genuinely needs bounded app-level page overrides, add a
new explicit contract rather than reintroducing `app/config/admin.json`.

Implementation rule:

- runtime/platform code owns the standard page metadata
- frontend admin rendering resolves the active page from the route the
  registry declared, and its wording from the standard page catalog
- Studio navigation stays deterministic and product-scoped; admin panels do
  not add, rename, or reorder Studio nav items

First-party Studio routes must be declared in
`factory_app/app/ui/route_manifest.json`. The first-party
`factory_app/app/admin/admin_registry.yaml` is reserved for AdminPortal
extension pages and should not duplicate Studio product routes. Generated app
admin registries normally omit `surfaces` and are mounted by the platform host.

## Feature Admin Contract

Feature-owned admin UI lives in `modules/{module}/contracts/admin.yaml`.

Each panel names the admin page it appears on with `page`: a page id declared
in the app's `admin/admin_registry.yaml`, normally one of the standard pages
above. A panel whose page is not declared has nowhere to render, and the
generated-bundle scanner rejects the bundle
(`factory_app/workflows/AppGenerator/tools/generated_bundle_scanner.py`,
"missing enabled admin page"). Panels on one page are shown in ascending
`order`.

The canonical module admin schema is `mozaiks.admin.v2`.

Example:

```yaml
schema_version: mozaiks.admin.v2
panels:
  - id: campaigns.overview
    label: Campaigns
    description: Review and manage campaign activity.
    page: usage
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
host-owned generated-app admin shell rather than becoming standalone routes.

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
generate panels inside the host-owned generated-app admin shell, not to generate
a second admin shell.

Use this order of preference:

1. `renderer: schema` for panels that can be expressed through the shipped
   primitive system
2. `renderer: custom_component` for bounded React customization when schema is
   not sufficient
3. only introduce standalone generated admin route families through a future,
   explicit contract if the product truly needs them

Do not overload `app/app.json`, `modules/{module}/contracts/admin.yaml`, or any other
existing contract into a general admin-page generator.
If Mozaiks later supports agent-generated standalone admin subroutes, that must
be a new contract with explicit ownership, registration, and validation rules.

## Componentization Rule

"Fully componentized" in the admin system means:

- the shell is presentation-first and consumes a resolved navigation model
- standard page ids and routes come from one canonical catalog
- module and app-backend panels plug into those pages through explicit
  contracts
- custom React is a bounded extension point declared by contract, not an ad hoc
  frontend escape hatch

It does not mean that agents may emit arbitrary admin React pages or bypass the
host-owned generated-app admin shell by default.

## App-Backend Panels

A connected app backend may contribute app-business admin panels through
`GET {app_backend_url}/api/admin/config`.

The config declares `schema_version: mozaiks.admin.app_backend.v1` and
`panels[]`. Each panel declares:

- `section`: the standard admin page it appears on. The shell renders the
  panel on that page, with that page's module panels, when the registry
  declares the page.
- `renderer: schema` with `layout + sections[]`, or `renderer: custom_component`
  with `component`.

A backend with no admin config route (404) contributes no panels; any other
failure is reported on the admin pages.

`renderer: builtin` (`builtin_panel: stats | users | subscriptions`) is also in
the contract, but it is not served end to end: each built-in panel reads its
data from further app-backend routes, and nothing in this repository
implements them. See
[App-Backend Admin Contract](../builder/app-backend-admin-contract.md#renderer-builtin).

Generated file ownership for split backends:

- `app/services/admin_config.py` returns the canonical payload as a plain dict
- `app/services/routes/admin.py` exposes it at `GET /api/admin/config` as a
  self-contained FastAPI router with no Mozaiks import
- AppGenerator treats `ControllerOutput.app_backend_admin_config` as the typed
  source of truth and renders those two files from it.

This keeps admin rendering deterministic across module-owned and app-backend
surfaces.

## Generator Rules

- Populate `app/app.json` `admins` for admin bootstrap access.
- Generate `modules/{module}/contracts/admin.yaml` only when the module needs feature-owned admin panels.
- Every generated module admin panel must set `page` to a page id declared in
  `admin/admin_registry.yaml`, and the registry must declare every page a
  panel references.
- Declare a standard page only when something renders on it.
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
2. user email matches an entry in `app/app.json` `admins` and the identity
   provider marked that email verified (`email_verified` is JSON `true`; see
   [Admin allowlist needs a verified email](../verified/auth-setup.md#admin-allowlist-needs-a-verified-email))
3. with authentication off, `AUTH_ANON_ROLES=admin,user` gives the local
   development identity the admin role. A request with development access
   also still uses the allowlist without verification: an `AUTH_ANON_EMAIL`
   or dev persona `X-Mozaiks-Dev-Email` address on `admins` is promoted,
   because that identity can already take any role (`AUTH_ANON_ROLES`,
   `X-Mozaiks-Dev-Roles`). Public visitors carry no email, so the allowlist
   never applies to them.

The unified UI does not collapse authority:

- runtime/operator panels use same-host admin APIs
- feature panels use module contracts
- app-business panels may use a connected app backend

