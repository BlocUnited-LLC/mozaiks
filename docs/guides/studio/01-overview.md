# Use Studio

Studio is the browser-based interface for building, reviewing, and managing your Mozaiks apps.
Studio opens automatically when you run `python -m mozaiks quickstart` or
`python -m mozaiks studio`.

For the product-level page contracts, see the [Studio docs](../../studio/index.md).

## Creating Your First App

1. Click **Create App** in the top navigation or in the empty workspace.
2. Everything happens in the chat — describe your app and the AI walks you through the steps.
3. Review the generated output when the build completes.
4. Promote the app when you're satisfied.

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

## Coming Back to a Build

Studio saves your position automatically. When you reopen Studio, your last active build is
restored. Click the build in **Apps** to continue.

## Promoting a Build

When a build is ready, Studio shows a **Promote** action. Promoting moves the generated
artifacts from `generated/` into the active app workspace so the runtime loads them.
