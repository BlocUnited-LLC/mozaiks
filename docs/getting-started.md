# Getting Started

## Install Mozaiks

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install mozaiks
```

## Create Your Workspace

Choose where the workspace folder should live on your computer and run:

```powershell
mozaiks quickstart --dir .\{workspace name}
```

`quickstart` creates the workspace folder if it does not already exist and
opens the local Console.

## Start Mozaiks

`quickstart` opens the Console during first setup.

After that, if you want to start the same workspace again later, run:

```powershell
cd .\{workspace name}
.\.venv\Scripts\Activate.ps1
.\scripts\run-console.ps1
```

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
