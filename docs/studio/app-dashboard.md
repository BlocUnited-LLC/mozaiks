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

The first-party Studio mounts these App Dashboard portals. Each one is a page
in the app's Studio menu:

| Portal | Route | Purpose |
| --- | --- | --- |
| Overview | `/apps/:appId/overview` | App identity, lifecycle state, KPIs, and next step. |
| Revenue | `/apps/:appId/revenue` | Recurring-revenue metrics, trend, and MRR movement. Empty until a billing integration records revenue. |
| Users | `/apps/:appId/audience` | Acquisition, engagement, conversion, and the app's lifecycle funnel. |
| Project | `/apps/:appId/building` | Where the app's build stands with one button to start or continue it, the build steps, the saved build documents, and every version. |
| Token Usage | `/apps/:appId/usage` | App chats, workflows, tokens, and cost. |
| Health | `/apps/:appId/health` | What needs attention: the latest build's validation result, failed runs, and integrations that are missing a credential or failing their check. |
| Integrations | `/apps/:appId/integrations` | The app's connections, credentials, and build requirements. |
| Support | `/apps/:appId/support` | App-scoped support threads and follow-up. |

The default dashboard contract also declares Branding, Launch, Growth, Users
(access), and Settings portals. The first-party Studio has no page for them, so
its `dashboard/dashboard.yaml` disables them; a host that mounts those pages
enables them in its own manifest. A portal is never left enabled without a
page: `tests/test_dashboard_manifest.py` runs the route validator on the real
manifest and route files.

Apps may hide or extend portals through the dashboard manifest. Capability packs
can contribute panels without taking over the whole dashboard.

The generic factory `DashboardPortalPage` renders manifest-backed lanes such as
Project. Mount it from `ui/route_manifest.json` with
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

## Project

**Route:** `/apps/:appId/building`

One page per app, in plain language and read-only:

- **Status and one button.** "This app's build hasn't started." (Start build),
  "First build in progress. Next step: …" (Continue build), "Built. Last
  updated …" (Make a change), or "The last build didn't pass its checks." (See
  what failed, which opens the build review page). The button opens this app's
  build conversation, the build review page, or starts the app's build on the
  server. If the server records no build conversation to reopen, the page says
  so.
- **Build steps** in build order: Idea, Look & feel, Design, Plans & pricing,
  AI workflows, App. A step is done once its document has a current version.
- **Documents.** A card for the latest version of each saved document: Idea &
  value, Look & feel, Design, Plans & pricing, AI workflows, App, and for an
  imported app what discovery found. Each card shows its version, date, and
  whether it is Current, Draft, Replaced, Out of date, or didn't pass checks.
  Opening a card shows the document as headings, paragraphs, and lists, with
  colours as swatches; "Show raw" shows the saved JSON. The App card links to
  the build review page.
- **All versions**, collapsed, one row per saved version.

My Apps shows the same status and button for up to five apps whose build is
not finished, under "Builds in progress", above the app list.

A host product can add its own section to this page with a `custom_component`
panel; see [App Dashboard Contract](../architecture/app/app-dashboard-contract.md#host-sections).

## Support

**Route:** `/apps/:appId/support`

Support conversations for this app, organized by status: **Needs reply**,
**In progress**, and **Resolved**. Open a conversation to reply to a user or
assign it to an operator.

## Usage

**Route:** `/apps/:appId/usage`

Token and cost detail for this app broken down by workflow and chat. Expand a
workflow group to see individual chats. When model pricing is incomplete, a
pricing status notice appears collapsed at the bottom.

## Health

**Route:** `/apps/:appId/health`

A short list of specific things to fix, built only from signals Studio has: the
newest build's validation result, how many of the app's recorded runs failed,
and which connected services still lack a credential. There is no health score
and no uptime figure; this Studio has no source for either.

## Account Access

The first-party Studio has no page listing an app's accounts. It has no account
directory to read: an app's users live in that app's sign-in provider. A host
product that manages accounts mounts its own Access page and enables the
`users` portal in its dashboard manifest.

## Detail Pages

This page is linked from other pages and is not listed in the menu.

| Page | Route | Opens from |
| --- | --- | --- |
| Build review | `/apps/:appId/activity` | Overview and Project, to review a saved build and its version history |
