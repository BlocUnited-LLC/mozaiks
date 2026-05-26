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

## Start The Console Again

`quickstart` creates a `scripts/run-console.ps1` in your workspace. Use it to
start the Console again any time you come back later.

This just means: if you close your terminal, restart your computer, or stop
Mozaiks for the day, run the launch script again to open the same local Console
workspace.

From your workspace root:

```powershell
cd .\mozaiks-workspace
.\.venv\Scripts\Activate.ps1
.\scripts\run-console.ps1
```

## Which Tool To Use

The browser product is the **Mozaiks Console**. Use it to create apps, continue
builds, review generated artifacts, and manage app workspaces.

If you see the word `Studio`, treat it as an internal technical name for the
server behind the Console. It is not a second app you need to learn.

The CLI is just how you set things up locally. It creates the workspace, starts
the local processes, and opens the Console. The normal product experience
happens in the Console, not in terminal commands.

## Your Workspace vs Your App

There are two things Mozaiks creates, and they are not the same:

1. First, `quickstart` creates a local workspace folder such as `./mozaiks-workspace`.
2. Then, inside the Console, you create the actual app you want to build.

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
creating. The app itself is created later from inside the Console after you
click `Create App`.

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
