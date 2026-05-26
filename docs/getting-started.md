# Getting Started

Install Mozaiks, open the Console, and create your first app.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install mozaiks
mozaiks quickstart --dir .\mozaiks-workspace
```

Open the Console:

```text
http://localhost:3000/apps
```

## Reopen the Console

`quickstart` creates a `scripts/run-console.ps1` in your workspace. Use it to
start the Console on any subsequent run:

```powershell
cd .\mozaiks-workspace
.\.venv\Scripts\Activate.ps1
.\scripts\run-console.ps1
```

## Console, Studio, And CLI

The browser product is the **Mozaiks Console**. Use it to create apps, continue
builds, review generated artifacts, and manage app workspaces.

`Studio` is the internal host name for the management server that powers the
Console. You may still see `studio` in architecture docs or host flags such as
`--host studio`; it is not a separate user-facing app.

The CLI is only the local developer entrypoint. It creates workspaces, starts
processes, runs diagnostics, and opens the Console. Product workflows belong in
the Console, not in terminal commands.

## Two-Step Mental Model

There are two separate setup steps:

1. The CLI creates and runs the local workspace shell.
2. The Console creates and manages apps inside that workspace.

Think of `./mozaiks-workspace` as the local container for Console state,
generated artifacts, config, and launch scripts. It is not the app itself. The
actual app is created later when you click `Create App` in the Console.

## Create Your First App

The Console is the normal Mozaiks starting point.

1. Open `Apps`.
2. Click `Create App`.
3. Describe what you want to build.
4. Follow the build workflow in the chat UI.
5. Review the generated app before promotion.

Draft and in-progress apps stay visible in `Apps`, so you can return later and
use `Continue Build`.

The `--dir` value is your local Mozaiks workspace. It is not the app you are
creating. The actual app is created from the Console after you click
`Create App`.

## What The Console Does

`Create App` starts the builder workflow sequence. The workflow handles planning,
requirements, generated UI, modules, integrations, review, and refinement inside
the chat experience.

The Console remains the management surface for:

- app records and build status
- continuing drafts
- opening app consoles
- reviewing generated artifacts before promotion

## Configuration

If `quickstart` asks for configuration, set one model key and MongoDB:

```powershell
$env:OPENAI_API_KEY="sk-..."
$env:MONGO_URI="mongodb://localhost:27017/mozaiks"
```

Use `ANTHROPIC_API_KEY` instead of `OPENAI_API_KEY` if that is your provider.

See [User Configuration](user-configuration.md) for the short configuration
reference.

## Contributing

Want to contribute? See the [Contributing guide](https://docs.mozaiks.ai/contributing/).
