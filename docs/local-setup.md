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
.\scripts\bootstrap-builder.ps1 -Workspace .\my-first-mozaiks-app
```

On macOS or Linux:

```bash
git clone https://github.com/BlocUnited-LLC/mozaiks.git
cd mozaiks
./scripts/bootstrap-builder.sh --workspace ./my-first-mozaiks-app
```

The bootstrap script creates `.venv` when needed, installs the local package in
editable mode, starts the builder stack, and opens the Mozaiks Console.

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
mozaiks quickstart --dir .\my-first-mozaiks-app
```

Open:

```text
http://localhost:3000/apps
```

## Useful Commands

```powershell
mozaiks quickstart --dir .\my-first-mozaiks-app
mozaiks studio --dir .\my-first-mozaiks-app --open
mozaiks studio --dir .\my-first-mozaiks-app --json
mozaiks onboard --dir .\my-first-mozaiks-app --full
```

`quickstart` is the preferred local command. The lower-level `studio` command is
mainly useful when you need explicit ports, JSON status output, or process
debugging.

## Runtime-Only Path

If you only want the app runtime and not the Console-driven builder:

```powershell
mozaiks init chat --name my-app --dir .\my-app
mozaiks serve .\my-app --host platform
```

Most new users should use the Console path instead.

## Generated Output

Mozaiks stages generated output before it touches an active app workspace:

```text
generated/apps/{app_id}/{build_id}/app
```

Promotion is the explicit step that moves validated artifacts into an active app
root.

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
mozaiks studio --dir .\my-first-mozaiks-app --open --backend-port 8001 --frontend-port 3001
```

Or stop the existing local process before restarting.
