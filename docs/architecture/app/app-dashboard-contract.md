---
title: App Dashboard Contract
status: Authoritative - Pre-Production
created: 2026-07-28
updated: 2026-07-28
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
      label: Building
      route: /apps/:appId/building
      icon: hammer
      order: 10
      capabilities: [build_threads, artifact_versions, approval_votes]
      panels:
        - id: threads
          type: build_threads
          title: Threads
        - id: artifacts
          type: artifact_timeline
          title: Artifacts
        - id: approvals
          type: approval_queue
          title: Approvals
        - id: continue_build
          type: workflow_launcher
          source: workflow
          workflow_id: extended_orchestration
          actions:
            - id: continue_build
              label: Continue Build
              type: workflow_sequence
              target: build
              variant: primary
```

## Canonical App Portals

The default App Dashboard lanes are:

| Portal | Purpose |
| --- | --- |
| `overview` | App identity, lifecycle, KPIs, next step, and top-level alerts. |
| `building` | Build threads, artifact versions, approvals, votes, and build workflow launch actions. |
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
- `GET /api/studio/dashboard`

`build_dashboard_shell_routes()` is a migration bridge for clients that still
consume route-manifest-shaped entries. Existing `ui/route_manifest.json` pages
remain valid while the Dashboard Portal renderer is adopted.

## Multimodal Integration

Generated media is a dashboard capability, not a dashboard architecture.
Branding, Launch, and Growth portals can use the OSS media primitives to show
generated images, promoted assets, campaign media, and landing page visuals. The
user-facing operation is still deterministic: review an asset, promote it to a
target, or launch a workflow sequence.

## Legacy Cleanup Direction

The long-term target is:

1. Keep `extension_registry.json` limited to workflow routing and sequences.
2. Keep `admin_registry.yaml` limited to AdminPortal extension pages.
3. Keep `route_manifest.json` as concrete route mounting, not the semantic App
   Dashboard source.
4. Move App Studio portal definitions into `dashboard/dashboard.yaml`.
5. Let generated apps customize dashboards through manifest overlays and
   capability-contributed panels instead of bespoke hardcoded dashboard pages.
