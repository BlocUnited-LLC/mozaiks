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

The shell compiles JSX in first-party `.js` source files, including normal Vite
source queries. CSS, JSON, and `?raw`/`?url` asset imports use Vite's own loaders.
`npm --prefix web_shell run test:workflow-ui` checks this boundary with the actual
shell configuration and a production Vite build.

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
already running. `run-backend.ps1` and `run-frontend.ps1` bind `127.0.0.1`
(this machine only) unless you pass `-BindHost`; see the note below about
local authentication.

**macOS / Linux (bash or zsh)**

Terminal 1 — infrastructure and backend:

```bash
# Optional: the services run-backend.ps1 starts by default.
# Skip this when MongoDB is already running, or start only `mongo`.
docker compose -f infra/compose/docker-compose.yml up -d mongo keycloak-db keycloak

python -m uvicorn mozaiksai.hosts.studio:app --host 127.0.0.1 --port 8000 --env-file .env
```

Importing `mozaiksai` never reads a `.env`, so `--env-file .env` is how the
backend gets the repo `.env`, as `run-backend.ps1` does. Values already set in
the shell win. Create the file with `cp .env.example .env`, then set
`OPENAI_API_KEY`, and change `LLM_PRIMARY_API_TYPE` to `openai` and
`DEFAULT_LLM_MODEL` to an OpenAI model such as `gpt-5-nano`: builds call OpenAI
only, and `.env.example` ships Gemini values for both.

`.env.example` sets `AUTH_ENABLED=false` and `AUTH_ANON_ROLES=admin,user`,
which run Studio without sign-in and give the anonymous user the admin role.
Without a `.env`, drop the flag and export `MONGO_URI`, `AUTH_ENABLED=false`,
and `AUTH_ANON_ROLES=admin,user` yourself: with no auth setting at all the
backend refuses to start, and without `AUTH_ANON_ROLES` the admin pages are
refused. With auth enabled, startup requires a configured provider and
its audience: `AUTH_AUDIENCE` for the generic JWT/OIDC provider, or
`KEYCLOAK_CLIENT_ID` for the Keycloak provider (see `.env.example`).

Terminal 2 — frontend, once `http://127.0.0.1:8000/api/shell-config` responds:

```bash
npm --prefix web_shell run dev -- --host 127.0.0.1 --port 3000 --strictPort
```

These commands bind to this machine only. With the local auth settings above,
the backend gives the anonymous admin identity only to requests from this
machine (`AUTH_ANON_ACCESS=local`, the default) and refuses every other
request. Open the app at `http://localhost:3000` or `http://127.0.0.1:3000`.
If you bind Vite to `0.0.0.0`, its dev proxy marks requests from other
machines (`X-Forwarded-For`), so the backend refuses them too. To serve other
machines, configure authentication, or set `AUTH_ANON_ACCESS=public`
(anonymous visitors without development access, not for Studio) or
`AUTH_ANON_ACCESS=open` (development access for every client that can
connect, only where the network itself limits who that is).

Either way, that starts:

- the Studio host backend on `http://127.0.0.1:8000`
- the Vite frontend on `http://127.0.0.1:3000/apps`

## Other Dev Modes

Frontend only:

```powershell
# Windows (PowerShell)
.\scripts\run-frontend.ps1
```

```bash
# macOS / Linux, and any shell
npm --prefix web_shell run dev -- --host 127.0.0.1 --port 3000 --strictPort
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
