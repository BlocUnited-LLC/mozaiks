# Getting Started
## Prerequisites

You need:

- Python 3.11+
- Node.js 18+
- MongoDB, either:
  - local MongoDB, or
  - MongoDB Atlas
- one supported LLM provider key, such as:
  - `OPENAI_API_KEY`
  - `ANTHROPIC_API_KEY`

Check your tools:

```bash
python --version
node --version
```

## Step 1: Clone The Repo

```bash
git clone https://github.com/BlocUnited-LLC/mozaiks.git
cd mozaiks
```

## Fastest Path

If you already cloned the repo and want the shortest supported path into the
Console, run the bootstrap script from the repo root.

### PowerShell

```powershell
.\scripts\bootstrap-builder.ps1 -Workspace .\my-first-mozaiks-app
```

### macOS / Linux

```bash
./scripts/bootstrap-builder.sh --workspace ./my-first-mozaiks-app
```

What this does:

- creates `.venv` when missing
- runs `pip install -e .`
- launches `mozaiks quickstart`
- opens the Mozaiks Console

If you want the manual steps instead, keep reading.

## Step 2: Create A Virtual Environment

### PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

### macOS / Linux

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

This installs the `mozaiks` CLI from your local repo checkout.

Important:

- run the commands from the cloned `mozaiks` repo root
- keep the virtual environment activated while you use `mozaiks`
- this is the supported first-time setup path today

## Choose Your Path

If you are new to Mozaiks, use the builder path.

- Builder path
  - You want to build an app through the Console and the shared `factory_app` workflows.
  - Use `mozaiks quickstart`.
- Framework path
  - You are working on the runtime, CLI, platform hosts, workflow generator, or repo internals.
  - Use `mozaiks onboard --full`, `mozaiks studio`, `mozaiks init`, and `mozaiks serve` directly.

## Step 3: Set Your Environment Variables

Before launching the Console, set:

- your LLM key
- your Mongo connection string

If you want the exact breakdown of what is required versus optional, read
[User Configuration](user-configuration.md).

### PowerShell example

```powershell
$env:OPENAI_API_KEY="sk-..."
$env:MONGO_URI="mongodb://localhost:27017/mozaiks"
```

### Bash example

```bash
export OPENAI_API_KEY="sk-..."
export MONGO_URI="mongodb://localhost:27017/mozaiks"
```

If you use Anthropic instead of OpenAI:

```powershell
$env:ANTHROPIC_API_KEY="sk-ant-..."
```

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

If you prefer MongoDB Atlas, set `MONGO_URI` to your Atlas connection string.

## Step 4: Quickstart Into The Console

Run:

```bash
mozaiks quickstart --dir ./my-first-mozaiks-app
```

What this does:

- creates a workspace scaffold if it does not exist
- writes minimal core app config files
- launches the local backend
- launches the frontend shell
- opens the Mozaiks Console in your browser

If you want detailed branding/admin setup later, use:

```bash
mozaiks onboard --dir ./my-first-mozaiks-app --full
```

## Step 5: Open Apps

If the browser did not open automatically, go to:

```text
http://localhost:3000/apps
```

This is the main first-run entrypoint for creating and managing apps.

## Step 6: Start Your First Build

In `Apps`:

1. create a new app
2. choose `Greenfield App`
3. choose your validation strategy
4. paste your build request
5. start the build

Example prompt:

```text
Build me a trading bot app that lets a user describe a trading strategy,
configure risk controls, backtest the strategy, and monitor live signals in a
clean dashboard.
```

At this point, the `factory_app` workflows take over.

The flow is:

1. `ValueEngine`
2. `DesignDocs`
3. `AgentGenerator`
4. `AppGenerator`

You are now using the canonical Mozaiks build path.

## What Happens Behind The Scenes

Mozaiks does **not** immediately overwrite your workspace with generated files.

Instead:

1. the workspace scaffold gives the host something to run
2. the generator workflows create staged artifacts
3. those artifacts are reviewed and then promoted explicitly

Generated output is staged under a path like:

```text
generated/apps/{app_id}/{build_id}/app
```

That means:

- your scaffold is the working shell
- the generated app is a staged build artifact
- promotion is a separate step

If you were expecting Mozaiks to directly fill `app/workflows/` and
`app/modules/` in place during the build, that is **not** the intended
lifecycle.

## The Simplest Mental Model

If you are brand new, think about Mozaiks like this:

- `quickstart`
  - prepares the local workspace and opens the Console
- `studio --open`
  - advanced way to open the builder directly
- `factory_app`
  - does the actual build planning and generation
- `generated/apps/...`
  - holds the staged output

## Daily Commands

Once your workspace exists, these are the main commands you will use:

Builder quickstart:

```bash
mozaiks quickstart --dir ./my-first-mozaiks-app
```

Create or configure a workspace with the lower-level setup command:

```bash
mozaiks onboard --dir ./my-first-mozaiks-app
```

Open the Studio host directly:

```bash
mozaiks studio --dir ./my-first-mozaiks-app --open
```

Inspect workspace status:

```bash
mozaiks studio --dir ./my-first-mozaiks-app --json
```

Use the advanced detailed onboarding flow:

```bash
mozaiks onboard --dir ./my-first-mozaiks-app --full
```

## Troubleshooting

### `pip install mozaiks` did not work

`pip install mozaiks` is the package install path. If it fails, first verify
your Python version, virtual environment, and package index access.

Run:

```bash
pip install mozaiks
mozaiks --version
```

For source-level framework development or local Studio/factory changes, use a
repo checkout instead:

```bash
git clone https://github.com/BlocUnited-LLC/mozaiks.git
cd mozaiks
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
mozaiks --version
```

If `mozaiks` is still not recognized, make sure your virtual environment is
activated and run the module entrypoint directly:

```bash
python -m mozaiks_cli.main --version
```

If you already had an older local install cached, remove it before retrying:

```bash
python -m pip uninstall mozaiks
```

### Console does not open

Check:

- backend: `http://localhost:8000/api/health`
- frontend: `http://localhost:3000`

If the frontend is not running, make sure:

- Node.js is installed
- `web_shell` dependencies can install successfully

### Mongo connection errors

Make sure:

- MongoDB is running locally, or
- your Atlas URI is correct in `MONGO_URI`

### LLM key errors

Make sure you exported the provider key that matches the model/provider you
selected during onboarding.

Examples:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`

### Port already in use

Use different ports:

```bash
mozaiks studio --dir ./my-first-mozaiks-app --open --backend-port 8001 --frontend-port 3001
```

## Advanced: Runtime-Only Path

If you are **not** trying to use the Console and only want the runtime/CLI surfaces,
you can still use the lower-level path:

```bash
mozaiks init chat --name my-app --dir ./my-app
mozaiks serve ./my-app --host platform
```

But for a first-time user who wants to build apps with the generator, the
recommended path is:

```powershell
.\scripts\bootstrap-builder.ps1 -Workspace .\my-first-mozaiks-app
```

```bash
./scripts/bootstrap-builder.sh --workspace ./my-first-mozaiks-app
```
