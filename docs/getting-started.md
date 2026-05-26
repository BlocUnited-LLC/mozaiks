# Getting Started

## Prerequisites

Install these before starting:

- Python 3.11+
- Node.js 18+
- MongoDB running locally, or a MongoDB Atlas connection string

## Install Mozaiks

Install Mozaiks as a CLI so it stays separate from the workspaces you create.

```powershell
python -m pip install --user pipx
python -m pipx ensurepath
python -m pipx install mozaiks
```

Use `python -m pipx` for the install step above so the current terminal does
not need the `pipx` command on PATH yet.

## Create Your Workspace

Mozaiks does not start MongoDB for you. If you use local MongoDB, start it
before running `quickstart`; the generated workspace uses this default URI:

```powershell
$env:MONGO_URI="mongodb://localhost:27017/mozaiks"
```

If you use Atlas instead, set `MONGO_URI` to your Atlas connection string.

Choose where the workspace folder should live on your computer. From that
parent folder, run:

```powershell
mozaiks quickstart --dir .\mozaiks-workspace
```

If Windows says `mozaiks` is not recognized, open a new PowerShell and retry
the same `mozaiks quickstart` command. That just means PowerShell has not loaded
the PATH update from `ensurepath` yet.

`quickstart` creates the workspace folder if it does not already exist and
opens the local Console. You do not need to create a `.venv` in the parent
folder or inside the workspace for the normal Console path.

## Start Mozaiks

`quickstart` opens the Console during first setup.

Open the Console at:

```text
http://localhost:3000/
```

To reopen the same workspace later, run:

```powershell
mozaiks console --dir .\mozaiks-workspace --open
```

For source checkout or contributor setup, use [Local Setup](local-setup.md).

## Minimum Config For Real Builds

You need MongoDB to open the Console. You also need an LLM API key before
running real builds.

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

After MongoDB is configured, you usually only need a model key for real builds.
If you need the short reference for connector secrets, auth, or deployment-only
settings, see [User Configuration](user-configuration.md).
