# Getting Started

!!! tip "New to Development?"

    **Let AI do the hard parts!** In Claude Code, just run:

    ```
    /setup
    ```

    The AI will handle prerequisites, environment setup, Docker, and verify everything works.

---

Everything you need to go from clone to a running local Mozaiks app.

Mozaiks uses four canonical host entrypoints:

- `mozaiksai/hosts/runtime.py` - runtime substrate
- `mozaiksai/hosts/platform.py` - headless app host
- `mozaiksai/hosts/studio.py` - local/private Studio management/create host
- `mozaiksai/hosts/mozaiks.py` - hosted product host

`mozaiks serve .` defaults to the local/private Studio host. Use the
direct `run_*.py` wrappers when you want to target one specific layer.

For day-to-day local development, prefer `.\scripts\run-backend.ps1`. That
runner clears previous-run files on startup by default:
`logs/logs/*`, `logs/agent_outputs/*`, and `logs/workflow_converter/*`. The
current run then writes fresh logs and artifacts normally.

---

## What You're Setting Up

Mozaiks is an AI app framework with three main pieces:

| Piece | What it is | Where it runs |
|-------|-----------|---------------|
| **Backend** | Layered Python host selected for local work | `mozaiks serve .` |
| **Frontend** | React app that users interact with | `npm run dev` |
| **Services** | MongoDB (database) + Keycloak (login) | Docker containers |

By the end of this guide, you'll have all three running locally.

---

## Prerequisites

| Tool | Version | Check |
|---|---|---|
| **Docker** & Docker Compose | 24+ / v2+ | `docker --version` |
| **Python** | 3.11+ | `python --version` |
| **Node.js** | 18+ | `node --version` |
| **npm** | 9+ | `npm --version` |

Docker is required — it runs **MongoDB** (app database), **PostgreSQL** (Keycloak user database), and **Keycloak** (identity provider). All three start with a single `docker compose up`.

---

## Fastest path (recommended)

Use the `/setup` skill in Claude Code:

```
/setup
```

This is the best path for first-time developers because it walks you through the
current repo starter layout, `.env`, Docker, and local auth defaults before
startup. The skill will verify everything works.

---

## Repo layout (current repo state)

```
mozaiks/
├── factory_app/                # First-party factory workspace
│   └── app/workflows/
│
├── generated/                  # Staged generated apps/workflows awaiting promotion
│
├── mozaiks-platform/           # App Zero / product workspace
│   ├── app/                    # Active App Zero app root
│   │   └── modules/            # Hosted product modules: marketplace + communications
│
├── web_shell/                  # Local web shell host source
│   ├── App.jsx
│   ├── main.jsx
│   └── vite.config.js
│
├── chat-ui/                    # Shared web UI shell
├── mozaiksai/                  # AI runtime, orchestration, transport
├── mozaiks_cli/                # CLI for local initialization and tooling
├── docs/                       # Architecture and usage documentation
├── mozaiksai/hosts/runtime.py              # Pure runtime FastAPI host
├── mozaiksai/hosts/platform.py             # Runtime + platform shell host
├── mozaiksai/hosts/studio.py               # Runtime + platform + local/private Studio host
├── mozaiksai/hosts/mozaiks.py              # Runtime + platform + Studio + Mozaiks product host
├── mozaiks serve .               # Start the selected host
├── requirements.txt            # Python dependencies
├── .env.example                # Secrets & config template (copy to .env)
│
└── infra/
    ├── compose/                # Docker Compose (Mongo + Keycloak + Postgres + app)
    │   ├── docker-compose.yml      # Development (hot-reload)
    │   └── docker-compose.prod.yml # Production
    └── keycloak/
        └── realm-export.json   # Auto-imported Keycloak realm config
```

    Deterministic app behavior is hosted by the platform host by default. Apps
    may also connect an external/generated backend through `AppBackendPort`.

!!! note "Canonical target architecture"

    The repo layout above is the **current implementation state**, not the
    long-term distribution model. The canonical target is:

    - shared generation core outside app workspaces
    - self-contained app workspaces with `app/config`, `app/ui/pages`,
      `app/workflows`, `app/modules`, `app/ui`, and `app/brand`
    - generated/customer apps as standalone workspaces or repositories

    See
    [Distribution And Workspace Model](architecture/foundations/distribution-and-workspace-model.md)
    for the target architecture.

---

## Step 1 — Configure `.env`

```powershell
# Windows
Copy-Item .env.example .env

# macOS / Linux
cp .env.example .env
```

Open `.env` and set your OpenAI API key:

```dotenv
OPENAI_API_KEY=sk-...
```

That's the only required edit. Everything else has working defaults.

App validation for `AppGenerator` also has one canonical env knob when you want
to override the runtime default:

```dotenv
MOZAIKS_APP_VALIDATION_STRATEGY=local
```

Allowed values are:

- `e2b`
- `local`
- `skip`

If you leave it blank, the runtime resolves the strategy in this order:

1. `e2b` when `E2B_API_KEY` is configured
2. `local` when `npm` is available on the current machine
3. `skip` when neither runtime is available

Studio Create and `mozaiks gen` can also set this explicitly per run, so the
env var is only needed when you want a workspace-wide default.

If you want this repo to run against an app that lives outside the repo, set
one of these optional values in `.env`:

```dotenv
# Either point directly at the app bundle or workspace root
PLATFORM_PATH=C:/repos/customer-app

# Or use the external-workspace alias
MOZAIKS_APP_WORKSPACE_PATH=C:/repos/customer-app
```

Leave both blank to use the repo-local App Zero workspace.

See `.env.example` for the full list with inline comments.

---

## Step 2 — Start the databases + auth

```powershell
docker compose -f infra/compose/docker-compose.yml up -d
```

This starts **three services** automatically:

| Service | Port | What it does |
|---|---|---|
| **MongoDB** | `27017` | App database — chat sessions, workflows, artifacts |
| **PostgreSQL** | (internal) | Keycloak's database — users, realms, sessions, credentials |
| **Keycloak** | `8080` | Identity provider — OIDC login, user management, roles |

!!! info "What about the databases?"
    **You don't need to create any databases, tables, or schemas.** Everything is automatic:

    - **MongoDB**: Collections are created on first write by the runtime. No setup needed.
    - **PostgreSQL**: Keycloak creates and manages its own schema on first boot.
    - **Keycloak realm**: Auto-imported from `infra/keycloak/realm-export.json` on first start — creates the `mozaiks` realm, `mozaiks-app` client, and a `dev` test user.

    Data persists in Docker volumes (`MozaiksAI_mongo_data`, `MozaiksAI_keycloak_db`). Stopping containers does NOT delete data. Only `docker compose down -v` removes volumes.

### Verify databases are healthy

```powershell
docker compose -f infra/compose/docker-compose.yml ps
```

All services should show `healthy`. Keycloak takes ~30 seconds on first boot.

### Keycloak admin console

Once healthy, open [http://localhost:8080/admin](http://localhost:8080/admin):

- **Username:** `admin`
- **Password:** `admin`

From here you can manage users, roles, and login settings. The `mozaiks` realm is pre-configured with:

| Item | Value |
|---|---|
| **Realm** | `mozaiks` |
| **Client** | `mozaiks-app` (public, PKCE, Authorization Code) |
| **Roles** | `user` (default), `admin` |
| **Test user** | username: `dev`, password: `dev` (has both roles) |
| **Redirect URIs** | `localhost:5173`, `localhost:3000`, `localhost:8000` |

---

## Step 3 — Start the app

=== "Local Python + Docker services"

    ```powershell
    # Create a virtual environment (first time only)
    python -m venv .venv

    # Activate it
    # Windows:
    .\.venv\Scripts\Activate.ps1
    # macOS / Linux:
    source .venv/bin/activate

    # Install Python deps
    pip install -r requirements.txt

    # Start backend (recommended local dev runner; clears previous-run logs/artifacts on startup)
    .\scripts\run-backend.ps1

    # Optional: target an external app workspace/repo instead of repo-local App Zero
    .\scripts\run-backend.ps1 -AppWorkspacePath C:/repos/customer-app

    # Optional: target one specific host directly
    python run_runtime.py   # runtime substrate only
    python run_platform.py  # headless app host
    python run_studio.py    # local/private Studio management/create host
    python run_mozaiks.py   # hosted product host
    ```

    To preserve previous-run files when starting the next run, opt out explicitly:

    ```powershell
    .\scripts\run-backend.ps1 -KeepLogsBetweenRuns -KeepRuntimeArtifactsBetweenRuns
    ```

    In a separate terminal:

    ```powershell
    # Start frontend (http://localhost:3000)
    npm --prefix web_shell install   # first time only
    .\scripts\run-frontend.ps1

    # Optional: target the same external app workspace/repo
    .\scripts\run-frontend.ps1 -AppWorkspacePath C:/repos/customer-app
    ```

### Validation strategy in practice

- Studio Create exposes an **App Validation** selector before launching create or
  refinement flows.
- `mozaiks gen` exposes `--validation-strategy e2b|local|skip`.
- Export remains blocked unless validation ends in `passed` or explicit
  `skipped`, and integration checks pass.

=== "Full Docker"

    ```powershell
    docker compose -f infra/compose/docker-compose.yml up --build
    ```

    This starts MongoDB + PostgreSQL + Keycloak + the Python backend together.
    Start the frontend separately:

    ```powershell
    npm --prefix web_shell install   # first time only
    .\scripts\run-frontend.ps1
    ```

---

## Step 4 — Verify

| Check | URL | Expected |
|---|---|---|
| Frontend | [http://localhost:3000](http://localhost:3000) | App loads and auto-signs into the seeded dev user by default |
| Backend health | [http://localhost:8000/api/health](http://localhost:8000/api/health) | Health payload includes `"status": "healthy"` |
| Loaded workflows | [http://localhost:8000/api/workflows](http://localhost:8000/api/workflows) | Shows the active app workflows such as `AppGenerator`, `AgentGenerator`, and `ValueEngine` |
| Keycloak admin | [http://localhost:8080/admin](http://localhost:8080/admin) | Admin console (admin/admin) |

### First login

By default, the frontend uses local dev auto-login. If Keycloak is running, it signs into the seeded dev user automatically.

If you disable auto-login, use the test user:

- **Username:** `dev`
- **Password:** `dev`

After login, you're redirected back to the app with a valid JWT session.

---

## Step 5 — Configure the current starter app root

```json
{
  "appName": "My App",
  "targets": {
    "web": true,
    "mobile": false
  },
  "authRequired": true,
  "admins": ["owner@example.com"]
}
```

`appName` appears in the browser tab and is also used by the mobile shell. `authRequired` says whether the product needs sign-in. `admins` says which signed-in users should count as admins. The active workflow is resolved automatically from backend config, with startup behavior declared in the active app root's `app/config/ai.json`. Backend URLs and local dev auth behavior now live in `.env`.

This file path is part of the repo's current transitional starter layout. The
canonical target for generated/customer apps is a self-contained workspace with
`app/app.json` inside the app repository.

`mozaiks init` now scaffolds that canonical workspace shape directly, so new
customer app repos should use `app/app.json`, `app/config/*`, `app/workflows/*`,
`app/modules/*`, `app/ui/*`, and `app/brand/*`.

For most users, this is the only config file they should touch. The `clients/mobile` directory is the repo-owned native implementation layer.

---

## Database reference

### MongoDB (your app data)

| Collection | Created by | Contents |
|---|---|---|
| `conversations` | Runtime (auto) | Chat sessions and messages |
| `workflow_runs` | Runtime (auto) | Workflow execution state |
| `artifacts` | Runtime (auto) | Generated artifacts |

No migrations needed — collections are created automatically on first use. To inspect data, connect with any MongoDB client:

```
mongodb://localhost:27017/mozaiksai
```

### PostgreSQL (Keycloak's data)

You never interact with this directly. Keycloak manages its own schema (100+ tables for users, realms, sessions, credentials, etc.). It's internal to the `keycloak-db` container and not exposed on a host port.

To reset Keycloak to factory defaults:

```powershell
docker compose -f infra/compose/docker-compose.yml down -v
docker compose -f infra/compose/docker-compose.yml up -d
```

This re-creates all volumes and re-imports the realm from `realm-export.json`.

---

## Troubleshooting

??? question "Keycloak shows `service_unhealthy` on first start"
    Keycloak needs ~30-60 seconds to initialize its database and import the realm. Run `docker compose logs keycloak -f` and wait for `Running the server in development mode`. Then `docker compose up -d` again.

??? question "Port 8080 already in use"
    Another service is using port 8080. Either stop it or change Keycloak's port in `docker-compose.yml` and update `MOZAIKS_OIDC_AUTHORITY` in `.env` to match.

??? question "Port 27017 already in use"
    A local MongoDB is already running. Either stop it (`brew services stop mongodb-community` or stop the Windows service) or change the port mapping in `docker-compose.yml`.

??? question "Frontend shows 'Authentication Unavailable'"
    Keycloak isn't running or isn't reachable. Check `docker compose ps` — Keycloak should be `healthy`. If you want temporary fallback mode, set `AUTH_ENABLED=false` in `.env` and restart backend/frontend.

??? question "I want to skip auth during development"
    For frontend-only work, set `VITE_MOCK_MODE=true` in `.env`. If you need backend auth bypass too, set `AUTH_ENABLED=false`. Keep both off for production-parity testing.

??? question "How do I connect to MongoDB Atlas instead of local?"
    Set `MONGO_URI` in `.env` to your Atlas connection string. You can then skip starting the local mongo container: `docker compose up keycloak-db keycloak -d`

---

## Next steps

Use Claude Code skills to build on top of Mozaiks:

| Task | Skill | What it does |
|------|-------|--------------|
| **Add a workflow** | `/create-workflow` | Creates a new AI workflow with agents, tools, and UI |
| **Customize branding** | `/add-branding` | Configure themes, colors, navigation, and logos |

Or explore the architecture:

- [Keycloak Auth Architecture](architecture/keycloak-auth.md) — How auth, databases, and the runtime fit together
- [Workflow Architecture](architecture/foundations/workflow-architecture.md) — How workflows, tools, and agents work
- [Event System](architecture/foundations/event-system.md) — The event-driven architecture

