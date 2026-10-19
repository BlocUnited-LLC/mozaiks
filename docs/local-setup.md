# Local Setup

Use this page when [Getting Started](getting-started.md) is not enough and you
need to debug a local run or work from a repo checkout.

If you only want to create your first app, start with
[Getting Started](getting-started.md) instead.

## Choose The Right Setup Path

Use one setup path at a time:

| Path | Use When | Environment |
| --- | --- | --- |
| Public package install | You want to create and use local Mozaiks workspaces | Install with `python -m pip install mozaiks`, then run `python -m mozaiks quickstart` and `python -m mozaiks studio` |
| Repo contributor setup | You are changing Mozaiks itself | `.venv` lives inside the `mozaiks/` repo; run repo scripts like `scripts\run-studio.ps1` |
| Standalone workspace setup | A generated app workspace is being developed as its own repo | `.venv` lives inside that app workspace; run that workspace's `scripts/run-studio` |

Do not create a shared `.venv` in the parent folder that contains multiple
repos. Put the environment in the repo or workspace that owns it. For the
public package path, install Mozaiks into the Python environment you normally
use for command-line tools. If that environment already has a contributor
checkout installed, use a separate workspace environment as described under
[Already satisfied with an editable install](#already-satisfied-with-an-editable-install).

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
- an OpenAI API key (`OPENAI_API_KEY`); builds call OpenAI only today

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

Builds call OpenAI only today. The default model is `gpt-5-nano`; set
`DEFAULT_LLM_MODEL` to use another OpenAI model. The two `AUTH_` settings run
Studio without sign-in for requests from this machine and give the anonymous
user the admin role. With no auth setting at all, the backend refuses to start
and names the settings to choose from.

If the repo also has a `.env`, the backend script loads it too; values set in
the shell win. A `.env` copied from `.env.example` sets
`DEFAULT_LLM_MODEL=gemini-2.0-flash`, and the runtime would send that model
name to OpenAI, so change it to an OpenAI model such as `gpt-5-nano`.

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

These examples assume a workspace in your user folder. If you chose another
location, use that workspace's absolute path for `--dir` instead.

=== "Windows"

    ```powershell
    Set-Location $env:USERPROFILE
    python -m mozaiks quickstart --dir .\mozaiks-workspace
    python -m mozaiks studio --dir .\mozaiks-workspace --open
    python -m mozaiks studio --dir .\mozaiks-workspace --json
    python -m mozaiks onboard --dir .\mozaiks-workspace
    ```

=== "macOS / Linux"

    ```bash
    cd "$HOME"
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

### Access is denied / WinError 5

Relative paths such as `.\mozaiks-workspace` start from the terminal's current
directory. If the prompt is `PS C:\Windows\System32>`, that command tries to
create a workspace inside Windows. Use your user folder instead:

```powershell
Set-Location $env:USERPROFILE
python -m mozaiks quickstart --dir .\mozaiks-workspace
```

Administrator access is not needed. Keep workspaces in a writable folder outside
`C:\Windows` and `C:\Program Files`.

### PowerShell treats the API key as a command

Keep the quotes around the value when setting an environment variable:

```powershell
$env:OPENAI_API_KEY="replace-with-your-openai-api-key"
```

Replace only the placeholder text. An unquoted value can produce
`CommandNotFoundException` and leave the variable unset. A successful assignment
prints nothing. Set the variable in the same terminal used to launch Studio,
and remove credentials before sharing terminal output.

### Already satisfied with an editable install

`Requirement already satisfied: mozaiks ... (0.2.0)` confirms a matching package
version is installed; it does not prove Python is using the published PyPI
files. An editable checkout can have the same version. From your workspace
folder, outside the Mozaiks source checkout, check:

```bash
python -m pip show mozaiks
python -c "import mozaiks_cli; print(mozaiks_cli.__file__)"
```

`Editable project location` or an import path inside a repository/worktree means
you are using that source checkout. To try the public package while keeping the
contributor environment intact, create a separate environment inside your
workspace. The examples below use `.venv-pypi`; choose a new name if it already
contains an editable install.

=== "Windows"

    ```powershell
    Set-Location "$env:USERPROFILE\mozaiks-workspace"
    python -m venv .venv-pypi
    .\.venv-pypi\Scripts\python.exe -m pip install mozaiks
    .\.venv-pypi\Scripts\python.exe -m mozaiks studio --dir . --open
    ```

=== "macOS / Linux"

    ```bash
    cd "$HOME/mozaiks-workspace"
    python -m venv .venv-pypi
    ./.venv-pypi/bin/python -m pip install mozaiks
    ./.venv-pypi/bin/python -m mozaiks studio --dir . --open
    ```

Use your existing workspace's path and set `MONGO_URI` and `OPENAI_API_KEY` in
this terminal, or in the workspace's `.env`, before launching. If the workspace
has not been scaffolded yet, use `quickstart --dir .` instead of
`studio --dir . --open`. Calling the environment's Python directly avoids needing
to activate it or change PowerShell's execution policy.

Installing the package does not rewrite an existing scaffold's app configuration.
If the workspace was created by an older checkout, also check
[Older scaffold configuration](#older-scaffold-configuration).

### Backend failed to start

`Setup complete` confirms that the scaffold was written. A later backend error
means Studio has not started successfully; keep the workspace and inspect the
server error before retrying.

The public launcher records backend output under the workspace's
`logs/studio-backend.log` and includes its last lines in a startup failure:

=== "Windows"

    ```powershell
    Get-Content "$env:USERPROFILE\mozaiks-workspace\logs\studio-backend.log" -Tail 80
    ```

=== "macOS / Linux"

    ```bash
    tail -n 80 "$HOME/mozaiks-workspace/logs/studio-backend.log"
    ```

Use the path printed by the launcher if your workspace is elsewhere. Frontend
startup output is in `logs/studio-frontend.log` after the backend is healthy.
If the error only says "Check the backend terminal for details" and there is no
`studio-backend.log`, check for an older
[editable install](#already-satisfied-with-an-editable-install). That launcher
may have opened a separate terminal instead of saving its output.

To keep a backend error visible in the current terminal, run the Studio backend
in the foreground with the same Python environment and workspace:

=== "Windows"

    ```powershell
    python -m mozaiks serve "$env:USERPROFILE\mozaiks-workspace" --host studio --listen 127.0.0.1
    ```

=== "macOS / Linux"

    ```bash
    python -m mozaiks serve "$HOME/mozaiks-workspace" --host studio --listen 127.0.0.1
    ```

This command starts only the backend. If you use `.venv-pypi`, replace `python`
with its Python path from the previous section. Use `Ctrl+C` to stop this
diagnostic process before reopening Studio with `studio --dir <workspace> --open`.
If port 8000 is already occupied, add `--port 8001` for the diagnostic run.

Use the actual exception to decide the fix: a MongoDB connection error needs a
running database and correct URI; an import error needs the correct Python
installation and dependencies; an address-in-use error needs an available port.
Review logs for credentials before sharing them. `studio --json` shows app
configuration, not a server health check or the backend exception.

### Older scaffold configuration

An older editable checkout can leave configuration that the published package
rejects even after you install it into a clean environment. Keep your workspace
and back up each file before editing it; do not rerun `init` over an existing app.

If the traceback contains `SecretContractError: Invalid names-only secret
contract: missing, model_type`, inspect `app/security/secrets.yaml`. The old
empty starter used `provider: env` and `secrets: []` without a version. For that
empty, environment-backed starter only, replace it with:

```yaml
version: 1
kind: app_secret_contract
provider:
  type: env
secrets: []
```

This file declares secret policy, not credential values. Keep API keys and
`MONGO_URI` in environment variables or the workspace's `.env`. If the contract
already declares secrets or a vault provider, preserve those declarations and
repair their schema instead of replacing them with the empty example.

The same older scaffold may also lack an `appId` in `app/app.json`. If it is
absent, add a stable lowercase slug for your workspace, such as
`"appId": "my-app"`, while preserving the other fields. Do not change an existing
ID: it scopes the app's data and artifacts.

For a trusted, single-user local workspace with `AUTH_ENABLED=false`, Studio
management pages also need the local user's role. Current scaffolds set
`AUTH_ANON_ROLES=admin,user`; if adopting that setting in an older workspace,
set `AUTH_ANON_ACCESS=local` and use the published CLI's default loopback binding
(`127.0.0.1`). This grants administrative access to requests from your computer,
not a sign-in-protected account; do not use it to expose Studio to a network.
Keep authenticated deployments' access settings unchanged.

Stop the Studio processes you launched, then relaunch using the same isolated
Python environment so configuration changes take effect. Prefer the published
`studio --dir <workspace> --open` command over old generated launch scripts,
which may have different bind and environment-file defaults.

### Studio does not open

For a public package install, check:

- backend health: `http://localhost:8000/api/health`
- backend shell config: `http://localhost:8000/api/shell-config`
- frontend shell: `http://localhost:3000/apps`

Adjust the ports if you selected different ones. If startup stops before the
frontend launches, follow [Backend failed to start](#backend-failed-to-start).
For a source checkout, also inspect dependencies under `web_shell/` and run
the repo smoke script: `.\scripts\smoke-studio-local.ps1 -SkipBrowser`.
The repo smoke script checks `http://localhost:8000/health`, which reports
platform startup status; `/api/health` checks runtime database connectivity.

### Mongo connection errors

Confirm local MongoDB is running or `MONGO_URI` points to a valid Atlas
connection string.

Setting `MONGO_URI` does not start MongoDB. If you used the container from
[Getting Started](getting-started.md#2-start-mongodb), start Docker Desktop,
then run `docker start mozaiks-mongo`. Skip Docker commands if you use a native
MongoDB server or an existing remote database.

### LLM key errors

Builds call OpenAI only today, whatever provider is configured elsewhere. Set
`OPENAI_API_KEY`, and make sure `DEFAULT_LLM_MODEL`, when set, names an OpenAI
model (the default is `gpt-5-nano`). Gemini and Anthropic keys are not used for
builds yet.

If the backend log says `GEMINI_API_KEY / GOOGLE_API_KEY / LLM_PRIMARY_API_KEY
is not set`, the environment has `LLM_PRIMARY_API_TYPE=google` (the
`.env.example` value). That setting changes only the startup check and the
Studio integrations summary; set it to `openai` so both report the key builds
actually use.

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
