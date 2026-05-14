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

## Developing Mozaiks Itself

Use [Local Setup](local-setup.md) only if you are changing the framework,
factory workflows, or Console source code.
