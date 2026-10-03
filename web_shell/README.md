# web_shell

`web_shell/` is the repo-local Vite shell used to develop and preview Mozaiks
UI surfaces. It is not a generated app workspace, and contributors do not need
any private hosted-product repo to run it.

## What It Loads

- By default, the shell resolves the first-party builder/reference app bundle at
  `factory_app/app`.
- If `PLATFORM_PATH` or `MOZAIKS_APP_WORKSPACE_PATH` is set, the shell resolves
  that selected app bundle or workspace instead.
- Shared build sequencing still comes from
  `factory_app/workflows/extended_orchestration/extension_registry.json` when no
  app-local workflow root overrides it.

## Prerequisites

- Python 3.11+ with Mozaiks installed from this checkout
  (`python -m pip install -e ".[dev]"`)
- Node.js 20.19+ or 22.12+ (required by Vite 8)
- MongoDB reachable at `MONGO_URI`, or Docker to start it

## Install Frontend Dependencies

From the repo root. These commands are the same in every shell:

```bash
npm --prefix chat-ui install
npm --prefix web_shell install
```

`vite.config.js` resolves shared packages from `chat-ui/node_modules` first,
and CI installs both packages in this order.

## Quick Start

The `scripts/*.ps1` launchers are PowerShell scripts. On macOS and Linux, run
the commands they wrap, shown below.

**Windows (PowerShell)**

```powershell
.\scripts\run-studio.ps1
```

`run-studio.ps1` opens the backend in a new terminal, waits for
`http://localhost:8000/api/shell-config`, then runs the frontend in the
current terminal. By default it first starts the Docker Compose `mongo`,
`keycloak-db`, and `keycloak` services; pass `-SkipInfra` when MongoDB is
already running.

**macOS / Linux (bash or zsh)**

Terminal 1 — infrastructure and backend:

```bash
# Optional: the services run-backend.ps1 starts by default.
# Skip this when MongoDB is already running, or start only `mongo`.
docker compose -f infra/compose/docker-compose.yml up -d mongo keycloak-db keycloak

python -m uvicorn mozaiksai.hosts.studio:app --host 0.0.0.0 --port 8000 --env-file .env
```

Importing `mozaiksai` never reads a `.env`, so `--env-file .env` is how the
backend gets the repo `.env`, as `run-backend.ps1` does. Values already set in
the shell win. Drop the flag if the repo has no `.env`. `.env.example` sets
`AUTH_ENABLED=false`; with auth enabled, startup also requires `AUTH_AUDIENCE`
(see `.env.example`).

Terminal 2 — frontend, once `http://localhost:8000/api/shell-config` responds:

```bash
npm --prefix web_shell run dev -- --host 0.0.0.0 --port 3000 --strictPort
```

Either way, that starts:

- the Studio host backend on `http://localhost:8000`
- the Vite frontend on `http://localhost:3000/apps`

## Other Dev Modes

Frontend only:

```powershell
# Windows (PowerShell)
.\scripts\run-frontend.ps1
```

```bash
# macOS / Linux, and any shell
npm --prefix web_shell run dev -- --host 0.0.0.0 --port 3000 --strictPort
```

Split backend/frontend terminals on Windows:

```powershell
.\scripts\run-backend.ps1
.\scripts\run-frontend.ps1
```

On macOS and Linux, the two terminals in [Quick Start](#quick-start) are the
split mode.

## Path Selection

- Leave `PLATFORM_PATH` unset to use `factory_app/app`.
- On Windows, pass `-AppWorkspacePath <path>` to `scripts/run-backend.ps1` or
  `scripts/run-frontend.ps1` to point at an external app workspace.
- On macOS and Linux, set the same variables the scripts set, in both the
  backend and frontend terminals:

  ```bash
  export MOZAIKS_APP_WORKSPACE_PATH=/path/to/workspace
  export PLATFORM_PATH=/path/to/workspace
  ```

- `PLATFORM_PATH` may target either an app bundle directory containing
  `app.json` or a workspace root containing `app/app.json`.

## Tailwind Source Detection

`styles.css` uses Tailwind v4 CSS-first source detection. It intentionally does
not load `tailwind.config.js` through `@config`, because packaged Docker builds
copy `web_shell/` beside dependency trees where automatic or previous JavaScript
configuration source detection can scan the wrong context.

`vite.config.js` creates `web_shell/.mozaiks-tailwind-sources/` at startup with
links to chat UI, Factory UI/workflows, and the active app/workflow roots.
That directory is generated local state and is ignored by git.

## What You Usually Edit

| Path | Role |
|------|------|
| `factory_app/app/` | First-party builder/reference app bundle loaded by default |
| `factory_app/app/ui/` | First-party UI pages, custom React, and route ownership |
| `factory_app/app/config/` | Shell, AI, auth, and other app-level config |
| `factory_app/app/brand/` | Brand assets and theme config |
| `factory_app/workflows/` | Shared builder workflows and extension registry |
| `web_shell/vite.config.js` | Shell path resolution and Vite/runtime integration |

`web_shell/` hosts those surfaces; it is not the canonical place to author app
contracts or builder workflows.
