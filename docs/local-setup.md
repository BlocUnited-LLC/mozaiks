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
| Repo contributor setup | You are changing Mozaiks itself | `.venv` lives inside the `mozaiks/` repo; run repo scripts like `scripts/run-studio` |
| Standalone workspace setup | A generated app workspace is being developed as its own repo | `.venv` lives inside that app workspace; run that workspace's `scripts/run-studio` |

Do not create a shared `.venv` in the parent folder that contains multiple
repos. Put the environment in the repo or workspace that owns it. For the
public package path, install Mozaiks into the Python environment you normally
use for command-line tools.

Studio requires MongoDB at startup. Docker Desktop is not required. Use
MongoDB Atlas, a native local MongoDB server, or Docker only if you prefer
containerized MongoDB. Set `MONGO_URI` to the reachable database before
launching Studio.

## Prerequisites

- Python 3.11+
- Node.js 18+
- MongoDB Atlas or a local MongoDB server
- one LLM provider key, usually `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`

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

=== "macOS / Linux"

    ```bash
    git clone https://github.com/BlocUnited-LLC/mozaiks.git
    cd mozaiks
    ./scripts/bootstrap-builder.sh --workspace ./mozaiks-workspace
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
    $env:OPENAI_API_KEY="sk-..."
    $env:MONGO_URI="mongodb://localhost:27017/mozaiks"
    ```

=== "macOS / Linux"

    ```bash
    export OPENAI_API_KEY="sk-..."
    export MONGO_URI="mongodb://localhost:27017/mozaiks"
    ```

Use Anthropic instead of OpenAI if preferred:

=== "Windows"

    ```powershell
    $env:ANTHROPIC_API_KEY="sk-ant-..."
    ```

=== "macOS / Linux"

    ```bash
    export ANTHROPIC_API_KEY="sk-ant-..."
    ```

Start the repo development Studio:

=== "Windows"

    ```powershell
    .\scripts\run-studio.ps1
    ```

=== "macOS / Linux"

    ```bash
    ./scripts/run-studio.sh
    ```

Open:

```text
http://localhost:3000/apps
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

Single-command start (opens a backend terminal + runs frontend here):

=== "Windows"

    ```powershell
    .\scripts\run-studio.ps1
    ```

=== "macOS / Linux"

    ```bash
    ./scripts/run-studio.sh
    ```

Or start each service manually in separate terminals:

=== "Windows"

    Terminal 1 — backend:

    ```powershell
    .\.venv\Scripts\Activate.ps1
    .\scripts\run-backend.ps1 -ForceStop
    ```

    Terminal 2 — frontend:

    ```powershell
    .\scripts\run-frontend.ps1 -ForceStop
    ```

=== "macOS / Linux"

    Terminal 1 — backend:

    ```bash
    source .venv/bin/activate
    ./scripts/run-backend.sh --force-stop
    ```

    Terminal 2 — frontend:

    ```bash
    ./scripts/run-frontend.sh --force-stop
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

- backend health: `http://localhost:8000/api/health`
- frontend shell: `http://localhost:3000`
- Node dependencies under `web_shell/`

### Mongo connection errors

Confirm local MongoDB is running or `MONGO_URI` points to a valid Atlas
connection string.

### LLM key errors

Set the provider key matching the model/provider you selected:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`

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
