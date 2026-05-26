# Local Setup

Use this page when [Getting Started](getting-started.md) is not enough and you
need to debug a local run or work from a repo checkout.

If you only want to create your first app, start with
[Getting Started](getting-started.md) instead.

## Prerequisites

- Python 3.11+
- Node.js 18+
- MongoDB locally or MongoDB Atlas
- one LLM provider key, usually `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`

Check local tools:

```powershell
python --version
node --version
```

## Source Checkout Bootstrap

Use this path when you are changing Mozaiks itself:

```powershell
git clone https://github.com/BlocUnited-LLC/mozaiks.git
cd mozaiks
.\scripts\bootstrap-builder.ps1 -Workspace .\mozaiks-workspace
```

On macOS or Linux:

```bash
git clone https://github.com/BlocUnited-LLC/mozaiks.git
cd mozaiks
./scripts/bootstrap-builder.sh --workspace ./mozaiks-workspace
```

The bootstrap script creates `.venv` when needed, installs the local package in
editable mode, starts the local Mozaiks services, and opens the Console.

## Manual Editable Setup

Use this only when you need to run each step yourself:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

Set the minimum required environment:

```powershell
$env:OPENAI_API_KEY="sk-..."
$env:MONGO_URI="mongodb://localhost:27017/mozaiks"
```

Use Anthropic instead of OpenAI if preferred:

```powershell
$env:ANTHROPIC_API_KEY="sk-ant-..."
```

Start the local builder:

```powershell
mozaiks quickstart --dir .\mozaiks-workspace
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

Use the Console for product tasks:

- create apps
- continue builds
- review staged artifacts
- open app-specific pages and tools

The CLI gets the local install running. The Console is where you actually use
Mozaiks after that.

## Useful Commands

```powershell
mozaiks quickstart --dir .\mozaiks-workspace
mozaiks console --dir .\mozaiks-workspace --open
mozaiks console --dir .\mozaiks-workspace --json
mozaiks onboard --dir .\mozaiks-workspace --full
```

`quickstart` is the preferred local command. The lower-level `console` command is
mainly useful when you need explicit ports, JSON status output, or process
debugging.

## Repo Dev Scripts

The repo scripts are for framework development from a source checkout. They are
not the public package install path.

Single-command start (opens a backend terminal + runs frontend here):

```powershell
.\scripts\run-console.ps1
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
the Console stack.

## Runtime-Only Path

If you only want the app runtime and not the Console setup flow:

```powershell
mozaiks init chat --name my-app --dir .\my-app
mozaiks serve .\my-app --host platform
```

Most new users should use the Console path instead.

## Generated Output

Mozaiks writes generated output here before it is copied into an app:

```text
generated/apps/{app_id}/{build_id}/app
```

Promotion is the explicit step that copies approved generated files into the
app.

## Troubleshooting

### Console does not open

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

Use a different backend/frontend port:

```powershell
mozaiks console --dir .\mozaiks-workspace --open --backend-port 8001 --frontend-port 3001
```

Or stop the existing local process before restarting.
