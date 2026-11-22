---
title: App Dashboard Contract
status: Authoritative - Pre-Production
created: 2026-07-28
updated: 2026-07-30
depends_on: surface-model.md, platform-navigation-contract.md, ../workflows/workflow-routing-transitions.md
---

# App Dashboard Contract

The App Dashboard is the canonical owner/operator surface every Mozaiks app can
expose. It is not App Zero-specific. App Zero is the richest first consumer, but
the contract belongs in OSS so generated apps, customer workspaces, hosted apps,
and self-hosted apps share the same management model.

## Dashboard Scopes

Mozaiks has two dashboard levels:

| Scope | Canonical route | Purpose |
| --- | --- | --- |
| Workspace Dashboard | `/apps` | Portfolio-level management for all apps owned by a user, team, or workspace. |
| App Dashboard | `/apps/:appId/...` | Focused management for one app. |

The Workspace Dashboard answers cross-app questions: what apps exist, what needs
attention, where cost is coming from, and which shared integrations are ready.
The App Dashboard answers app-specific questions: what is being built, what is
branded, what is launched, who can use it, and what workflows or support threads
need attention.

### Directory Composition

`AppsDirectory`, exported through `@mozaiks/factory-app-ui`, accepts an already
authorized app list and optional `navigationForApp` callbacks. Hosts with a
paged data source can supply:

- `pagination`: `{ page, hasNext, hasPrevious, onNext, onPrevious, onRetry }`;
- controlled `searchValue` / `onSearchChange` and `activeFilter` /
  `onFilterChange` pairs;
- `loading` and `error` for the current request.

With pagination, the host owns querying, cursor state, search and filtering.
The directory renders the supplied page without filtering its rows again,
labels counts as belonging to that page, and provides Previous/Next and retry
controls. Loading preserves the search controls and disables page navigation.
Without pagination, the existing local portfolio search, filters and summary
remain available. The shared component does not define hosted app ownership,
provider calls, or the host's paging API.

## Canonical File

Apps declare dashboard structure in:

```text
app/dashboard/dashboard.yaml
```

At runtime the active app root is already `app/`, so framework loaders resolve:

```text
dashboard/dashboard.yaml
```

The schema version is:

```yaml
schema_version: mozaiks.dashboard.v1
```

When the file is missing, Studio uses the OSS default manifest. App-owned files
can overlay the default with `extends: default` and override portals by id.
The `dashboard/` directory is a canonical app-root plane, and
`app/provenance.yaml` can reference `dashboard/dashboard.yaml` as the app's
dashboard overlay.

## Ownership Boundaries

| File | Owns | Does not own |
| --- | --- | --- |
| `dashboard/dashboard.yaml` | Workspace/App Dashboard portals, panel composition, dashboard actions, workflow launch affordances. | Workflow sequencing, arbitrary route registration, raw React pages, admin module panel contracts. |
| `workflows/extended_orchestration/extension_registry.json` | Workflow registry, workflow sequences, transition routing. | Product navigation, dashboard portals, persistent page IA. |
| `ui/route_manifest.json` | Concrete React route registration. | Canonical dashboard semantics. |
| `admin/admin_registry.yaml` | AdminPortal extension page ids used by module `contracts/admin.yaml`. | First-party Studio/App Dashboard pages. |
| `modules/*/contracts/admin.yaml` | Module-owned admin panels bound to declared module actions. | Cross-app dashboard IA or workflow orchestration. |

The dashboard manifest may reference workflow sequences, but it does not define
them. The sequence must already exist in `extension_registry.json`.

## Manifest Shape

```yaml
schema_version: mozaiks.dashboard.v1
extends: default

workspace:
  portals:
    - id: portfolio
      label: Apps
      route: /apps
      icon: apps
      order: 0
      panels:
        - id: portfolio
          type: app_portfolio_table
          title: Apps

app:
  portals:
    - id: building
      label: Project
      route: /apps/:appId/building
      icon: hammer
      order: 10
      panels:
        - id: project
          type: build_project
          title: Project
```

## Canonical App Portals

The default App Dashboard lanes are:

| Portal | Purpose |
| --- | --- |
| `overview` | App identity, lifecycle, KPIs, next step, and top-level alerts. |
| `building` | The app's Project: where the build stands, its saved build documents, and every version. |
| `branding` | Brand kit, logos, themes, generated media, and promoted brand assets. |
| `launch` | Landing page status, hosting, domains, deployment readiness, and launch workflows. |
| `growth` | Landing-page improvement, marketing campaigns, campaign assets, and growth workflow launch actions. |
| `users` | App users, roles, access blockers, invitations, and policy summaries. |
| `usage` | App-specific chats, workflows, token usage, cost, and usage limits. |
| `support` | App-specific support threads and user follow-up. |
| `settings` | App-level settings and configuration forms. |

An app does not have to show every portal. Capability packs and app-specific
overlays can disable a portal or add panels.

## Determinism Rules

- Portal ids, panel ids, capabilities, and action ids are stable lowercase
  identifiers.
- Portal routes are app-local paths. App-scoped portal routes include `:appId`.
- Panels use known panel types. Arbitrary JSX is only allowed through the
  explicit `custom_component` panel type.
- Workflow launch actions reference existing workflow ids or workflow sequence
  ids; they do not inline routing graphs.
- Module actions use `<module_id>.<action_id>` targets and must bind to actions
  declared in `module.yaml`.
- The manifest is declarative. It contains no secrets, provider credentials,
  payment-provider ids, or hosted-product internals.

## Runtime Primitives

The OSS runtime provides:

- `mozaiksai.core.dashboard.DashboardManifest`
- `load_dashboard_manifest(app_root)`
- `build_default_dashboard_manifest()`
- `build_dashboard_shell_routes(manifest)`
- `validate_dashboard_manifest_routes(manifest, route_pages)`
- `GET /api/studio/dashboard`
- `DashboardPortalPage`, the generic factory UI renderer for
  manifest-declared portal panels

`build_dashboard_shell_routes()` is a migration bridge for clients that still
consume route-manifest-shaped entries. Existing `ui/route_manifest.json` pages
remain valid while the Dashboard Portal renderer is adopted.

`DashboardPortalPage` reads the active `mozaiks.dashboard.v1` manifest through
`/api/studio/dashboard`, matches the current route to an enabled portal, then
renders known panel types against Studio data. The generic renderer currently
covers summary, next step, portal links, the `build_project` Project panel, and
`custom_component` panels. Apps should mount it by
declaring a normal route-manifest entry with `component: DashboardPortalPage`;
they should not copy the factory page into their workspace to activate a
default portal.

### The Project Panel

`build_project` renders an app's Project from records the server already keeps,
read-only:

- the saved build documents, read from `GET /api/studio/build/history` with one
  `build_family` filter per document (history sorts by version number across
  families, so one unfiltered page can hide a document behind internal
  records). Each document shows its latest `current` version, or its newest
  version when none is current. Internal families (`app_context_version`,
  `app_intelligence_snapshot`, `app_context_graph`, `source_context_bundle`,
  `brownfield_registration`) are not shown;
- a document's text, which is its saved `commit_metadata.metadata.summary_payload`
  (the content of its single JSON file), rendered as headings, paragraphs, and
  lists. The App document links to the build review page instead;
- the build steps in the `build` workflow sequence order (Idea, Look & feel,
  Design, Plans & pricing, AI workflows, App). A step is done when a current
  version of its document exists;
- one status sentence and one button. The button opens the app's recorded build
  conversation (`/chat?workflow=…&chat_id=…&app_id=<chat_app_id>`), the build
  review page, or asks `POST /api/workflows/trigger` to start `ValueEngine` (a
  draft with no conversation) or `AppReview` (a built app) bound to the app's
  `build_registry_id`. When the server records no entry the page can open, it
  says so.

My Apps lists up to five in-build apps under "Builds in progress" with the same
sentence and button, using one history request per listed app.

### Host Sections

A `custom_component` panel renders the component registered under its
`component` name in the shell component registry, with props `panel`, `appId`,
`app` (the app's Factory registry entry), and `buildRegistryId`. A host adds a
section to the Project page by overlaying the `building` portal by id; panels
merge by id, so the Project panel stays and the host panel is appended:

```yaml
app:
  portals:
    - id: building
      panels:
        - id: host_section
          type: custom_component
          source: custom
          component: HostProjectSection
          order: 10
```

The OSS Project page has no voting, discussion, or governance; a host product
that offers those renders them in its own registered component.

### App-owned Management Composition

`@mozaiks/factory-app-ui` exports `AppsDirectory` and `DashboardPortalPage` for
app-owned composition around the shared Studio presentation. `AppsDirectory`
accepts `apps`, `loading`, `error`, an optional `deleteApp` callback, and an
optional `navigationForApp(app)` callback returning `primaryAction` and
`dashboardHref`. Without that callback, the standard Studio directory retains
its existing navigation. Without a delete callback, it offers no delete action.

An app whose management record differs from the Factory record must resolve
that relationship through its own authorized data source. Its wrapper can pass
`targetAppId` and `buildRegistryId` to `DashboardPortalPage`: Studio finds that
exact target and verifies its Factory registry association before loading build
data. The route's `appId` remains the management selector for portal navigation;
workflow launches use the resolved Factory record's execution host and build
registry. No identity is rewritten or inferred from another identifier.

Ordinary Studio routes need no wrapper or extra props. Workspace-scoped portal
routes have no `appId` and require no selected-app lookup. Product wrappers must
preserve that workspace behavior and show an unavailable or unlinked state when
their own authorized record cannot resolve a Factory target.

The OSS dashboard contract intentionally stops at build/review management.
Collaborative development product behavior such as proposal discussion, voting,
community moderation, and proprietary approval policy belongs in app-owned
modules and app-specific routes. Apps can surface a compact admin summary
through `module_panel_ref` or `custom_component` panels, but those product
semantics are not generic Factory/Studio defaults.

`validate_dashboard_manifest_routes()` is the CI contract for apps that mount
concrete Studio pages. It validates that every enabled dashboard portal points
to a registered route, every portal intended for navigation is visible in the
matching `workspace-studio` or `app-studio` route group, and every visible
Studio navigation route has a matching enabled dashboard portal. Planned portals
should stay `enabled: false` until their route and product surface exist.

## Multimodal Integration

Generated media is a dashboard capability, not a dashboard architecture.
Branding, Launch, and Growth portals can use the OSS media primitives to show
generated images, promoted assets, campaign media, and landing page visuals. The
user-facing operation is still deterministic: review an asset, promote it to a
target, or launch a workflow sequence.

## Cleanup Direction

The long-term target is:

1. Keep `extension_registry.json` limited to workflow routing and sequences.
2. Keep `admin_registry.yaml` limited to AdminPortal extension pages.
3. Keep `route_manifest.json` as concrete route mounting, not the semantic App
   Dashboard source.
4. Move App Studio portal definitions into `dashboard/dashboard.yaml`.
5. Let generated apps customize dashboards through manifest overlays and
   capability-contributed panels instead of bespoke hardcoded dashboard pages.
