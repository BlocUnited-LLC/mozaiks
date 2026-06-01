# Use Studio

Studio is the browser-based interface for building, reviewing, and managing your Mozaiks apps.
Studio opens automatically when you run `python -m mozaiks quickstart` or
`python -m mozaiks studio`.

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
| **Builds** | Run history and artifact review for each app |
| **Settings** | Workspace config, keys, and integration setup |

## Coming Back to a Build

Studio saves your position automatically. When you reopen Studio, your last active build is
restored. Click the build in **Apps** to continue.

## Promoting a Build

When a build is ready, Studio shows a **Promote** action. Promoting moves the generated
artifacts from `generated/` into the active app workspace so the runtime loads them.
