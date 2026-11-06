# Admin System

Mozaiks Studio does not expose a separate customer-facing `/admin` page in the
first-party Studio. The Admin Portal entry for Studio routes to `/apps`, which
is the workspace app-management surface.

App Studio keeps these pages visible instead:

```text
/apps/:appId/overview
/apps/:appId/access
/apps/:appId/usage
/apps/:appId/support
```

Generated app AdminPortal routes use `/admin` and `/admin/<page>` paths. The
`/apps/:appId/...` route family is reserved for first-party Studio and hosted
product app operations.

Framework-owned admin composition remains internal and host-owned. Build,
Usage, app access, and support are product surfaces; they are not admin
sections. Deep diagnostic routes such as `/apps/:appId/health`,
`/apps/:appId/activity`, and `/apps/:appId/integrations` may remain routable but
hidden from primary navigation when they are needed for support, build history,
or setup detail. Hosted deployments may provide billing or hosting surfaces
through their own workspace routes or capability packs; the OSS factory Studio
does not hardcode those hosted product routes.

Terminology note:

- use `Operational health` inside Overview for runtime posture, workflow
  reliability, validation, hosting, and integration setup signals
- keep `operations` as an internal admin taxonomy when framework-owned admin
  panels need a bounded section for incidents, logs, or runtime state

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

The admin system has four distinct layers. Keep them separate:

1. `app/app.json` `admins` is the admin bootstrap/access allowlist for the app.
  It matches verified emails only.
2. `app/admin/admin_registry.yaml` declares the app's AdminPortal pages, exact
  page IDs, labels, paths, scope and ordering. The framework owns the shell
  and renders these declarations through its existing route pipeline.
3. `modules/{module}/contracts/admin.yaml` contributes feature-owned panels into those
  pages through an exact `page` reference.
4. An optional connected app backend may contribute app-business panels through
  `GET {app_backend_url}/api/admin/config`.

AdminPortal resolves page and panel rendering from those contracts. Studio
workspace/app navigation is a separate `WorkspaceLayout` concern and must not
be mutated by module admin panels.

## Admin Bootstrap

There is no app-level `admin.json` contract anymore.

Admin bootstrap and access are owned by `app/app.json` `admins` plus the normal
auth role model.

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

## Page Registry and Identity

`admin/admin_registry.yaml` uses `mozaiks.admin.registry.v1`. Its enabled pages
drive AdminPortal navigation and page selection. The shell has built-in content
for `overview`, `access`, `usage`, `operations` and `settings`; additional
registry pages render their module panels through the same shell.

```yaml
schema_version: mozaiks.admin.registry.v1
pages:
  - id: catalog_inventory
    label: Catalog inventory
    path: /admin/catalog
    scope: workspace
```

Page identity is distinct from its URL. A panel referencing `catalog_inventory`
stays on that page; the loader must not convert it to `catalog-inventory`.
The runtime projects the registry ID into `route.meta.adminPage`, and
`AdminPage` uses that value. Existing Studio routes without this metadata keep
their `/apps/:appId/<page>` selection behavior.

Implementation rule:

- runtime/platform code loads the canonical page registry
- frontend admin rendering consumes its exact page identity
- Studio navigation stays deterministic and product-scoped; admin panels do
  not add, rename, or reorder Studio nav items

First-party Studio routes must be declared in
`factory_app/app/ui/route_manifest.json`. The first-party
`factory_app/app/admin/admin_registry.yaml` is reserved for AdminPortal
extension pages and should not duplicate Studio product routes. Generated app
admin registries normally omit `surfaces` and are mounted by the platform host.

## Feature Admin Contract

Feature-owned admin UI lives in `modules/{module}/contracts/admin.yaml`.

Each panel declares `page`, referencing an enabled page in the app registry.
There is no module-panel `section` taxonomy.

The canonical module admin schema is `mozaiks.admin.v2`.

Example:

```yaml
schema_version: mozaiks.admin.v2
panels:
  - id: catalog.inventory
    label: Inventory
    description: Review catalog inventory.
    page: catalog_inventory
    order: 20
    renderer: schema
    layout: full-width
    sections:
      - id: catalog-table
        primitive: DataTable
        config:
          api_endpoint: /api/admin/modules/catalog/list_inventory/read
          data_key: items
          columns:
            - key: name
              label: Item
            - key: status
              label: Status
              type: badge
    permissions: [catalog.read]
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

## Authenticated Automatic Reads

`api_surface: admin_internal` describes exposure, not whether an action may
run automatically. An admin action used by a schema section on mount or refresh
must explicitly declare `read_only: true` in its canonical `module.yaml`:

```yaml
actions:
  - id: list_inventory
    description: Read catalog inventory
    handler_method: list_inventory
    api_surface: admin_internal
    read_only: true
    permissions: [catalog.read]
    input_schema:
      type: object
      additionalProperties: false
    output_schema:
      type: object
      required: [items]
      properties:
        items:
          type: array
          items:
            type: object
            properties:
              name: {type: string}
              status: {type: string}
```

`read_only` is a strict boolean defaulting to false. It is independent of
`ask_context_safe`, which serves a different grounding contract. Declaring
`read_only: true` together with emitted domain events is rejected. This is an
owner-declared eligibility contract; it does not sandbox arbitrary Python or
prove that a falsely declared handler cannot perform a write.

The schema renderer uses the existing authenticated HTTP helper to call:

```http
POST /api/admin/modules/catalog/list_inventory/read
Content-Type: application/json

{}
```

The operation lives in the existing admin module router. It requires the
existing operator and admin-surface checks, registered literal `read_only=True`
metadata, and an empty JSON object. Parameters, context overrides and query
arguments are not part of this first read contract. It then uses the same
module executor with its normal identity, permission, entitlement and audit
checks. A parameterless writer is still ineligible; action-name prefixes do
not grant read admission.

AppGenerator authors the existing typed `config.data_source` pair:

```yaml
data_source:
  module_id: catalog
  action_id: list_inventory
```

Materialization resolves that reference to the `/read` endpoint only when the
action is admin-internal, explicitly read-only and its input schema accepts
`{}`. Data keys must resolve against the declared output schema. Unknown
references, required input parameters and uncontracted response keys fail
validation. Both typed module admin output and authored admin YAML use the
same rules. Runtime admin configuration checks a full module panel against the
active module contracts when it declares automatic `api_endpoint` data in the
`/api/admin/modules` namespace, including malformed read paths. Legacy public
data and explicit-action-only panels retain their existing serving contract;
generated bundles and standalone page schemas remain strictly validated.

Automatic reads and explicit actions remain distinct:

| Binding | HTTP behavior |
| --- | --- |
| Public module data binding | Existing GET `/api/modules/{module}/{action}` |
| Admitted admin data binding | POST `/api/admin/modules/{module}/{action}/read`, fixed `{}` |
| Explicit admin submit/delete button | Existing POST `/api/admin/modules/{module}/{action}` with its declared payload |

The renderer recognizes only the exact canonical admin read path for automatic
POST. There is no page-configurable HTTP method, arbitrary header map or
authority payload. Existing server pagination remains a public GET contract;
this slice does not add privileged query parameters or admin pagination.

Admin config and panel data requests use the shared authenticated fetch helper.
Refresh, cancellation and stale-response handling retain the section lifecycle;
a failed read renders an error and retry state rather than a successful empty
observation. Client role guards remain presentation checks; server enforcement
is authoritative.

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
- page IDs and paths come from the app's canonical admin registry
- module and app-backend panels plug into the host shell through explicit
  contracts
- custom React is a bounded extension point declared by contract, not an ad hoc
  frontend escape hatch

It does not mean that agents may emit arbitrary admin React pages or bypass the
host-owned generated-app admin shell by default.

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

- `app/services/admin_config.py` returns the canonical payload
- `app/services/routes/admin.py` exposes it through
  `mozaiksai.core.admin.build_app_backend_admin_router(...)`
- AppGenerator should treat `ControllerOutput.app_backend_admin_config` as the
  typed source of truth and may regenerate those two files from it during
  assembly/download validation.

This keeps admin rendering deterministic across module-owned and app-backend
surfaces.

The detailed connected app-backend panel contract remains repo-internal
planning material for now. Keep the public contract anchored on this page plus
the host-owned generated-app admin shell behavior described here.

## Generator Rules

- Populate `app/app.json` `admins` for admin bootstrap access.
- Generate `modules/{module}/contracts/admin.yaml` only when the module needs feature-owned admin panels.
- Every generated admin panel must set `page` to an enabled registry page ID.
- Prefer `renderer: schema` with `layout + sections[]`.
- Use `renderer: custom_component` only when the shipped primitive system
  cannot express the panel cleanly.
- Mark admin-only module actions in `module.yaml.actions[]` with
  `api_surface: admin_internal` and their required permissions. Add strict
  `read_only: true` only for actions suitable for automatic reads; retain
  explicit controls for mutations.
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

