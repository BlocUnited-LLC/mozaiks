# Local Setup

Use this page when [Getting Started](getting-started.md) is not enough and you
need to debug a local run or work from a repo checkout.

If you only want to create your first app, start with
[Getting Started](getting-started.md) instead.

## Choose The Right Setup Path

Use one setup path at a time:

| Path | Use When | Environment |
| --- | --- | --- |
| Public package install (after the first PyPI release) | You want to create and use local Mozaiks workspaces | Mozaiks is installed into your Python environment; until the release, use an editable install from a checkout. Run `python -m mozaiks quickstart` and `python -m mozaiks studio` |
| Repo contributor setup | You are changing Mozaiks itself | `.venv` lives inside the `mozaiks/` repo; run repo scripts like `scripts\run-studio.ps1` |
| Standalone workspace setup | A generated app workspace is being developed as its own repo | `.venv` lives inside that app workspace; run that workspace's `scripts/run-studio` |

Do not create a shared `.venv` in the parent folder that contains multiple
repos. Put the environment in the repo or workspace that owns it. For the
public package path, install Mozaiks into the Python environment you normally
use for command-line tools. Mozaiks is not on PyPI yet, so that means
`python -m pip install -e <path-to-mozaiks>` from a clone.

Studio requires MongoDB at startup. Docker Desktop is not required when you use
MongoDB Atlas or a native local MongoDB server, but the repo convenience scripts
start Docker Compose infra by default. If you already have MongoDB available,
set `MONGO_URI` and launch repo scripts with `-SkipInfra`.

!!! tip "Want the full stack — MongoDB + Keycloak + app — all in one command?"
    Use the Docker Compose stack in `infra/compose/`. See the
    [Self-Hosting guide](guides/self-hosting.md) for a walkthrough.

## Prerequisites

- Python 3.11+
- Node.js 20.19+ or 22.12+ (required by the Vite 8 frontend toolchain)
- MongoDB Atlas or a local MongoDB server
- a credential and installed SDK for the selected LLM provider; OpenAI is the default

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
    $env:OPENAI_API_KEY="sk-..."
    $env:MONGO_URI="mongodb://localhost:27017/mozaiks"
    $env:AUTH_ENABLED="false"
    $env:AUTH_ANON_ROLES="admin,user"
    ```

=== "macOS / Linux"

    ```bash
    export OPENAI_API_KEY="sk-..."
    export MONGO_URI="mongodb://localhost:27017/mozaiks"
    export AUTH_ENABLED="false"
    export AUTH_ANON_ROLES="admin,user"
    ```

Builds default to OpenAI with `gpt-5-nano`; set `DEFAULT_LLM_MODEL` to use
another OpenAI model. For Anthropic or Gemini, install `mozaiks[anthropic]` or
`mozaiks[gemini]`, set `LLM_PRIMARY_API_TYPE=anthropic` or `google`, and set
`LLM_PRIMARY_MODEL` with the matching provider credential. The two `AUTH_` settings run
Studio without sign-in for requests from this machine and give the anonymous
user the admin role. With no auth setting at all, the backend refuses to start
and names the settings to choose from.

If the repo also has a `.env`, the backend script loads it too; values set in
the shell win. Keep the selected provider, model, and credential consistent.
A `.env` copied from `.env.example` selects Google; override
`LLM_PRIMARY_API_TYPE=openai` and `LLM_PRIMARY_MODEL` with the intended OpenAI
model when using the OpenAI setup above. An explicit primary model takes
precedence over `DEFAULT_LLM_MODEL`.

Install the frontend dependencies once, from the repo root (the same commands
in every shell):

```bash
npm --prefix chat-ui install
npm --prefix web_shell install
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

After Studio starts, run the local smoke from the repo root in a new terminal:

```powershell
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

Generated output is saved as a draft. Accepting the draft in Studio makes it the
app's current build; promotion is the explicit step that then copies the accepted
files into the app.

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

Check `LLM_PRIMARY_API_TYPE`, `LLM_PRIMARY_MODEL`, and the selected provider's
credential together. `openai` uses `OPENAI_API_KEY`, `anthropic` uses
`ANTHROPIC_API_KEY`, and `google` uses `GEMINI_API_KEY` or `GOOGLE_API_KEY`.
Install the corresponding optional SDK (`mozaiks[anthropic]` or
`mozaiks[gemini]`) before selecting it. These settings govern both startup
validation and Factory execution. An existing stored provider configuration
takes precedence unless `MOZAIKS_LLM_CONFIG_SKIP_MONGO=1` selects environment-only
configuration.

`LLM_MAX_TOKENS` optionally sets a positive provider-native `max_tokens` limit;
leave it unset to keep the provider default. Anthropic's AG2 default is 4096,
which may be too small for a full generated design. Choose a limit supported by
the selected model. Gemini uses `max_output_tokens` instead and rejects this
`max_tokens` setting; configure its native field through the stored provider
configuration or explicit LLM config.

### Native schema compilation errors

`LLM_STRUCTURED_OUTPUT_MODE` defaults to `native`. If the selected provider
cannot compile a workflow's schema, explicitly set
`LLM_STRUCTURED_OUTPUT_MODE=prompted` and restart the host. This selects AG2's
prompted schema transport while retaining the same declared model and local
validation before automatic save/tools. Invalid output still fails; Mozaiks
does not switch modes automatically after a provider error.

For example, Anthropic documents a limit of 16 union-typed parameters across
strict schemas, counting both `anyOf` and type arrays. A schema with more
nullable fields can exceed that limit even when the intended answer uses
mostly empty lists. See [Anthropic's schema complexity limits](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
and the [AG2 transport mapping](architecture/workflows/declarative-ag2-mapping.md#structured_outputsyaml).
Prompted mode adds the schema to model input; a successful schema probe alone
does not prove a complete workflow or generated app.

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
