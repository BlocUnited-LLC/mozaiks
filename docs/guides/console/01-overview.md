# Use The Console

The Mozaiks Console is the normal starting point after install. It is where you
create apps, continue builds, and open app-specific pages and tools.

The CLI or repo bootstrap scripts get you to this point by creating the local
workspace folder and starting the processes. The Console then creates apps
inside that workspace; it does not replace local machine setup.

Open it at:

```text
http://localhost:3000/apps
```

## Main Journey

1. Open `Apps`.
2. Click `Create App`.
3. Describe the product you want to build.
4. Complete the build workflow in the chat UI.
5. Review generated artifacts before promotion.
6. Return to `Apps` to continue drafts or open app consoles.

Most of the planning and build work happens in the chat. You are not expected
to click through a separate page-by-page app builder.

## Apps

`Apps` is the list of apps in your workspace.

Use it to:

- see every app in the workspace
- find drafts, active apps, and apps needing input
- continue incomplete builds
- open a specific app console
- create or import apps

Draft apps appear immediately, so you do not lose work if a build is not
finished in one session.

## Create App

`Create App` starts the app build flow. The workflow asks for the
product intent, gathers requirements, plans the app, and generates staged
artifacts.

Use `Create App` when you want Mozaiks to create a new app from a product idea.

## Continue Build

Use `Continue Build` when an app is still in draft, build, review, configuring,
or needs-revision state.

The workflow resumes from the existing app record and build context rather than
starting from scratch.

## Open App Console

Use `Open App Console` for an app-specific view. The app console is where a
single app exposes its setup, usage, users, deployment, integrations, and admin
pages as those capabilities become available.

## Workspace vs App Scope

The Console is workspace scope:

- many apps
- aggregate activity
- shared workspace configuration
- app creation and continuation

The App Console is app scope:

- one app
- app-specific setup
- app-specific usage and operations
- app-specific settings and admin

## Related Docs

- [Getting Started](../../getting-started.md)
- [App Shell & Branding](../custom-brand-integration/01-overview.md)
