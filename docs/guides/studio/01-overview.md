# Use Studio

Studio is the browser-based interface for building, reviewing, and managing your Mozaiks apps.
Studio opens automatically when you run `python -m mozaiks quickstart` or
`python -m mozaiks studio`.

For the product-level page contracts, see the [Studio docs](../../studio/index.md).

## Creating Your First App

1. Click **Create App** in the top navigation or in the empty workspace.
2. Everything happens in the chat — describe your app and the AI walks you through the steps.
3. Review the generated output when the build completes. It is saved as a draft.
4. Accept the draft when you're satisfied, then promote it.

In-progress builds stay in **Apps** so you can return any time.

## Navigating Studio

| Section | Purpose |
|---------|---------|
| **Apps** | All your app workspaces and their build state |
| **Usage** | Workspace-level model usage, token volume, and cost estimates |
| **Integrations** | Shared provider setup and safe credential-presence status |
| **App Dashboard** | Single-app portals for Overview, Building, Branding, Launch, Growth, Users, Usage, Support, and Settings |
| **Access** | Single-app account access, plan assignment, and access blockers |
| **App Usage** | Single-app chats, workflows, tokens, and cost detail |

Dashboard structure comes from `app/dashboard/dashboard.yaml`. When that file is
missing, Studio uses the OSS default dashboard manifest. CI can validate that
enabled dashboard portals resolve to mounted routes and that visible Studio
navigation routes are represented in the dashboard manifest.
Manifest-backed portals can use the factory `DashboardPortalPage` route
component, starting with the canonical `/apps/:appId/building` Building lane.

## Coming Back to a Build

Studio saves your position automatically. When you reopen Studio, your last active build is
restored. Click the build in **Apps** to continue.

## Accepting and Promoting a Build

Every generated build, your app's first build included, is saved as a draft. Generating
and validating it does not accept it. When its checks pass, Studio's review offers
**Accept this draft**: accepting makes it the app's current build and records who
accepted it and when. Only an accepted build can be promoted, and later refinements
start from it; until you accept the first build, a change to app-owned capability code
(such as a workspace handler) is refused and asks you to accept it first.

Once a build is accepted, Studio shows a **Promote** action (**Activate this version** in
the review). Promoting moves the generated artifacts from `generated/` into the active app
workspace so the runtime loads them.
