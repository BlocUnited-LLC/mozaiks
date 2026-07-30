# App Dashboard

App Dashboard is the canonical owner/operator portal for one Mozaiks app. It is
paired with the Workspace Dashboard, which manages the full app portfolio.

Dashboard structure is declared by the OSS dashboard manifest contract:

```text
app/dashboard/dashboard.yaml
```

See [App Dashboard Contract](../architecture/app/app-dashboard-contract.md) for
the runtime schema, manifest overlay rules, route-alignment validator, and
ownership boundaries.

## Overview

**Route:** `/apps/:appId/overview`

The main snapshot for an app: what it costs, who is using it, and what needs
attention right now. The app header shows the name, logo, tagline, and a single
lifecycle-aware next step — such as continue build, review artifacts, or
configure integrations.

Secondary panels link to deeper diagnostic and history pages, but the primary
action appears only once.

## Portals

The default App Dashboard has these portal lanes:

| Portal | Route | Purpose |
| --- | --- | --- |
| Overview | `/apps/:appId/overview` | App identity, lifecycle state, KPIs, and next step. |
| Building | `/apps/:appId/building` | Build requests, artifact versions, approval queue, and build workflow launches. |
| Branding | `/apps/:appId/branding` | Brand kit, themes, logos, generated media, and promoted assets. |
| Launch | `/apps/:appId/launch` | Landing page, hosting, domains, and deployment readiness. |
| Growth | `/apps/:appId/growth` | Marketing campaigns, landing page improvement, and campaign assets. |
| Users | `/apps/:appId/access` | Users, roles, invitations, access blockers, and policy summaries. |
| Usage | `/apps/:appId/usage` | App chats, workflows, tokens, cost, quotas, and usage limits. |
| Support | `/apps/:appId/support` | App-scoped support threads and follow-up. |
| Settings | `/apps/:appId/settings` | App configuration and management forms. |

Apps may hide or extend portals through the dashboard manifest. Capability packs
can contribute panels without taking over the whole dashboard.

The generic factory `DashboardPortalPage` renders manifest-backed lanes such as
Building. Mount it from `ui/route_manifest.json` with
`component: DashboardPortalPage` and keep portal content in
`dashboard/dashboard.yaml`; do not copy the factory page into an app workspace.

Apps that mount concrete Studio routes should run the dashboard route validator
in CI. Enabled portals must point to registered routes, and visible Studio
navigation routes must be declared as enabled dashboard portals.

The OSS dashboard default is not a collaborative development product surface.
Discussion, voting, proposal workflows, and community moderation belong to
app-owned modules and routes. A host product can expose admin review or
moderation summaries through module or custom dashboard panels without copying
those product semantics into the generic Factory/Studio defaults.

## Support

**Route:** `/apps/:appId/support`

Support conversations for this app, organized by status: **Needs reply**,
**In progress**, and **Resolved**. Open a conversation to reply to a user or
assign it to an operator.

## Access

**Route:** `/apps/:appId/access`

Who can use this app, what role they have, what plan they are on, and whether
anyone is blocked or needs attention. Shows account status, last activity, and
any access flags for each account.

## Usage

**Route:** `/apps/:appId/usage`

Token and cost detail for this app broken down by workflow and chat. Expand a
workflow group to see individual chats. When model pricing is incomplete, a
pricing status notice appears collapsed at the bottom.

## Diagnostic Pages

These pages are linked from Overview or Support when something needs
investigation. They are not listed in primary navigation.

| Page | Route | Opens from |
| --- | --- | --- |
| Health diagnostics | `/apps/:appId/health` | Overview when a runtime or integration issue needs investigation |
| Integration setup | `/apps/:appId/integrations` | Overview when a required service is not connected, or workspace Integrations |
| Build history | `/apps/:appId/activity` | Building when you need the full artifact preservation audit detail |
