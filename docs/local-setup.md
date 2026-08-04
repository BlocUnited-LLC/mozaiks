# Local Setup

Use this page when [Getting Started](getting-started.md) is not enough and you
need to debug a local run or work from a repo checkout.

If you only want to create your first app, start with
[Getting Started](getting-started.md) instead.

## Choose The Right Setup Path

Use one setup path at a time:

| Path | Use When | Environment |
| --- | --- | --- |
| Public package install | You want to create and use local Mozaiks workspaces | Python owns the installed package; run `python -m mozaiks quickstart` and `python -m mozaiks studio` |
| Repo contributor setup | You are changing Mozaiks itself | `.venv` lives inside the `mozaiks/` repo; run repo scripts like `scripts\run-studio.ps1` |
| Standalone workspace setup | A generated app workspace is being developed as its own repo | `.venv` lives inside that app workspace; run that workspace's `scripts/run-studio` |

Do not create a shared `.venv` in the parent folder that contains multiple
repos. Put the environment in the repo or workspace that owns it. For the
public package path, install Mozaiks into the Python environment you normally
use for command-line tools.

Studio requires MongoDB at startup. Docker Desktop is not required when you use
MongoDB Atlas or a native local MongoDB server, but the repo convenience scripts
start Docker Compose infra by default. If you already have MongoDB available,
set `MONGO_URI` and launch repo scripts with `-SkipInfra`.

!!! tip "Want the full stack — MongoDB + Keycloak + app — all in one command?"
    Use the Docker Compose stack in `infra/compose/`. See the
    [Self-Hosting guide](guides/self-hosting.md) for a walkthrough.

## Prerequisites

- Python 3.11+
- Node.js 18+
- MongoDB Atlas or a local MongoDB server
- one LLM provider key — `GEMINI_API_KEY` (free, default), `OPENAI_API_KEY`, or `ANTHROPIC_API_KEY`

Tree-sitter parser packages are installed with Mozaiks itself. They are the
baseline parser path for source-backed App Intelligence; deterministic
fallbacks only cover parser-package failures.

FalkorDB is recommended for production or team-scale App Intelligence querying
and Studio visualization. Local setup can use artifact-backed snapshots until
you need a shared graph service.

Check local tools:

=== "Windows"

    ```powershell
    python --version
    node --version
    ```

=== "macOS / Linux"

    ```bash
    python --version
    node --version
    ```

## Source Checkout Bootstrap

Use this path when you are changing Mozaiks itself. The bootstrap script creates
`.venv` inside the cloned `mozaiks/` repo:

=== "Windows"

    ```powershell
    git clone https://github.com/BlocUnited-LLC/mozaiks.git
    cd mozaiks
    .\scripts\bootstrap-builder.ps1 -Workspace .\mozaiks-workspace
    ```

It installs the local package in editable mode, starts the local Mozaiks
services, and opens Studio.

## Manual Editable Setup

Use this only when you need to run each step yourself from a cloned `mozaiks/`
repo:

=== "Windows"

    ```powershell
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install --upgrade pip
    python -m pip install -e .
    ```

=== "macOS / Linux"

    ```bash
    python -m venv .venv
    source .venv/bin/activate
    python -m pip install --upgrade pip
    python -m pip install -e .
    ```

Set the minimum required environment:

=== "Windows"

    ```powershell
    $env:GEMINI_API_KEY="your-key-here"
    $env:MONGO_URI="mongodb://localhost:27017/mozaiks"
    ```

=== "macOS / Linux"

    ```bash
    export GEMINI_API_KEY="your-key-here"
    export MONGO_URI="mongodb://localhost:27017/mozaiks"
    ```

!!! tip "Free LLM key — Google Gemini"
    The default provider is **Google Gemini** (free tier, no credit card required).
    Get your key at [aistudio.google.com](https://aistudio.google.com) → **Get API key**.

To use OpenAI or Anthropic instead, set the matching key and override the provider:

=== "Windows"

    ```powershell
    $env:OPENAI_API_KEY="sk-..."
    $env:LLM_PRIMARY_API_TYPE="openai"
    # or
    $env:ANTHROPIC_API_KEY="sk-ant-..."
    $env:LLM_PRIMARY_API_TYPE="anthropic"
    ```

=== "macOS / Linux"

    ```bash
    export OPENAI_API_KEY="sk-..."
    export LLM_PRIMARY_API_TYPE="openai"
    # or
    export ANTHROPIC_API_KEY="sk-ant-..."
    export LLM_PRIMARY_API_TYPE="anthropic"
    ```

Start the repo development Studio from Windows PowerShell:

```powershell
.\scripts\run-studio.ps1
```

The script opens the backend in a separate terminal, waits for
`http://localhost:8000/api/shell-config`, then starts the Vite frontend in the
current terminal.

If Docker Desktop is not running because you are using MongoDB another way:

```powershell
.\scripts\run-studio.ps1 -SkipInfra
```

Open:

```text
http://localhost:3000/apps
```

After Studio starts, run the local smoke in a new terminal:

```powershell
cd C:\Repos\BlocUnitedRepo\mozaiks
.\scripts\smoke-studio-local.ps1
```

For a fast HTTP-only check without opening the browser smoke:

```powershell
.\scripts\smoke-studio-local.ps1 -SkipBrowser
```

## Which Tool To Use Here

Use the CLI for local-machine tasks:

- create the local workspace when it does not exist yet
- start or reopen the backend/frontend processes
- inspect status or run diagnostics

Use Studio for product tasks:

- create apps
- continue builds
- review staged artifacts
- open app-specific pages and tools

The CLI gets the local install running. Studio is where you actually use
Mozaiks after that. See the [CLI Reference](cli-reference.md) for all commands.

## Useful Commands

=== "Windows"

    ```powershell
    python -m mozaiks quickstart --dir .\mozaiks-workspace
    python -m mozaiks studio --dir .\mozaiks-workspace --open
    python -m mozaiks studio --dir .\mozaiks-workspace --json
    python -m mozaiks onboard --dir .\mozaiks-workspace
    ```

=== "macOS / Linux"

    ```bash
    python -m mozaiks quickstart --dir ./mozaiks-workspace
    python -m mozaiks studio --dir ./mozaiks-workspace --open
    python -m mozaiks studio --dir ./mozaiks-workspace --json
    python -m mozaiks onboard --dir ./mozaiks-workspace
    ```

`quickstart` is the preferred local command. The lower-level `studio` command is
mainly useful when you need explicit ports, JSON status output, or process
debugging.

## Repo Dev Scripts

The repo scripts are for framework development from a source checkout. They are
not the public package install path.

Single-command start from Windows PowerShell. This opens the backend in a new
terminal, waits for the backend shell config endpoint, then runs the frontend in
the current terminal:

```powershell
.\scripts\run-studio.ps1
```

For a clean testing restart, use:

```powershell
.\scripts\run-studio.ps1 -ForceStop
```

That stops existing backend/frontend listeners and clears prior files under
`logs/logs/`, `logs/agent_outputs/`, and `logs/workflow_converter/` before the
new Studio run starts.

If MongoDB is already running outside Docker, use:

```powershell
.\scripts\run-studio.ps1 -SkipInfra
```

Or start each service manually in separate terminals:

Terminal 1 — backend:

```powershell
.\.venv\Scripts\Activate.ps1
.\scripts\run-backend.ps1 -ForceStop
```

Terminal 2 — frontend:

```powershell
.\scripts\run-frontend.ps1 -ForceStop
```

These scripts use the repo-local `factory_app/app`, `factory_app/workflows`, and
`web_shell/` sources. The backend script can start local Docker Compose infra for
Mongo and Keycloak. Use them when you are changing Mozaiks itself or debugging
the Studio stack.

For the boundary between repo-local `infra/`, the first-party `factory_app/`
workspace, and generated app deployment artifacts, see
`docs/architecture/deployment/oss-infra-and-generated-app-deployment.md`.

## Runtime-Only Path

If you only want the app runtime and not the Studio setup flow:

=== "Windows"

    ```powershell
    mozaiks init chat --name my-app --dir .\my-app
    mozaiks serve .\my-app --host platform
    ```

=== "macOS / Linux"

    ```bash
    mozaiks init chat --name my-app --dir ./my-app
    mozaiks serve ./my-app --host platform
    ```

Most new users should use the Studio path instead.

## Generated Output

Mozaiks writes generated output here before it is copied into an app:

```text
generated/apps/{app_id}/{build_id}/app
```

Promotion is the explicit step that copies approved generated files into the
app.

## Troubleshooting

### Studio does not open

Check:

- backend health: `http://localhost:8000/health`
- backend shell config: `http://localhost:8000/api/shell-config`
- frontend shell: `http://localhost:3000/apps`
- Node dependencies under `web_shell/`
- local smoke: `.\scripts\smoke-studio-local.ps1 -SkipBrowser`

If startup stops before the frontend launches, check the backend terminal first.
Common causes are Docker Desktop not running, MongoDB not reachable, missing
Python dependencies, or missing frontend dependencies under `web_shell/`.

### Mongo connection errors

Confirm local MongoDB is running or `MONGO_URI` points to a valid Atlas
connection string.

### LLM key errors

Set the key matching the provider you are using:

| Provider | Key variable | Notes |
|---|---|---|
| Google Gemini (default) | `GEMINI_API_KEY` | Free tier — get key at [aistudio.google.com](https://aistudio.google.com) |
| OpenAI | `OPENAI_API_KEY` | Also set `LLM_PRIMARY_API_TYPE=openai` |
| Anthropic | `ANTHROPIC_API_KEY` | Also set `LLM_PRIMARY_API_TYPE=anthropic` |

### Port already in use

=== "Windows"

    ```powershell
    python -m mozaiks studio --dir .\mozaiks-workspace --open --backend-port 8001 --frontend-port 3001
    ```

=== "macOS / Linux"

    ```bash
    python -m mozaiks studio --dir ./mozaiks-workspace --open --backend-port 8001 --frontend-port 3001
    ```

Or stop the existing local process before restarting.
