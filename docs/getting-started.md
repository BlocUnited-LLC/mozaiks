# Getting Started

## Prerequisites

Install Python 3.11+ and Node.js 18+ before starting.

## Install Mozaiks

Install Mozaiks as a CLI so it stays separate from the workspaces you create.

```powershell
python -m pip install --user pipx
python -m pipx ensurepath
python -m pipx install mozaiks
```

`ensurepath` updates future terminals, so use `python -m pipx` for the install
step above. Then open a new PowerShell before running `mozaiks`.

## Create Your Workspace

Choose where the workspace folder should live on your computer. From that
parent folder, run:

```powershell
mozaiks quickstart --dir .\mozaiks-workspace
```

`quickstart` creates the workspace folder if it does not already exist and
opens the local Console. You do not need to create a `.venv` in the parent
folder or inside the workspace for the normal Console path.

## Start Mozaiks

`quickstart` opens the Console during first setup.

To reopen the same workspace later, run:

```powershell
mozaiks console --dir .\mozaiks-workspace --open
```

For source checkout or contributor setup, use [Local Setup](local-setup.md).

Open the Console at:

```text
http://localhost:3000/
```

## Minimum Config For Real Builds

You need an LLM API key and MongoDB before running real builds.

MongoDB is not required just to open the Console, but builds will fail until
`MONGO_URI` is configured.

PowerShell:

```powershell
$env:OPENAI_API_KEY="sk-..."
$env:MONGO_URI="mongodb://localhost:27017/mozaiks"
```

## Create Your First App

The Console is the normal Mozaiks starting point.

1. Open `Apps`.
2. Click `Create App`.
3. In the chat describe what you want to build.
4. Follow the build workflow in the chat UI.
5. Review the generated app before promotion.

Draft and in-progress apps stay visible in `Apps`, so you can return later and
use `Continue Build`.

## Configuration

You only need a model key and MongoDB for real builds. If you need the short
reference for connector secrets, auth, or deployment-only settings, see [User
Configuration](user-configuration.md).
