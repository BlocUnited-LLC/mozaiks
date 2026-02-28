# Getting Started

Everything you need to go from clone → running authenticated app in minutes.

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

## Repo layout

```
mozaiks/
├── app/                        # Frontend app (Vite + React) — brand & customize this
│   ├── brand/public/           # brand.json, ui.json, navigation.json, auth.json, assets, fonts
│   ├── App.jsx                 # App shell (Keycloak auth built-in)
│   ├── main.jsx                # Entry point
│   └── vite.config.js          # Pre-wired — update proxy once backend is live
│
├── app.json                    # App name + API URL
│
├── chat-ui/src/                # UI library — do not modify
│   └── workflows/              # Workflow frontend component registry
│       └── HelloWorld/         # Example — copy for your own workflows
│
├── workflows/                  # Backend AG2 workflow definitions
│   └── HelloWorld/             # Example workflow — copy for your own
│
├── mozaiksai/                  # Runtime engine — do not modify
├── shared_app.py               # FastAPI server entry
├── run_server.py               # Start the server
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

That's the only required edit. Everything else has working defaults:

| Key | Required | Default | Description |
|---|---|---|---|
| `OPENAI_API_KEY` | **Yes** | — | Your OpenAI API key |
| `MONGO_URI` | — | `mongodb://localhost:27017` | MongoDB connection string |
| `MONGO_DB_NAME` | — | `MozaiksAI` | App database name |
| `AUTH_ENABLED` | — | `true` (Docker) / `false` (local) | Enable Keycloak JWT validation |
| `KC_ADMIN_USER` | — | `admin` | Keycloak admin console username |
| `KC_ADMIN_PASSWORD` | — | `admin` | Keycloak admin console password |
| `KC_DB_PASSWORD` | — | `keycloak` | Keycloak Postgres password |
| `MOZAIKS_OIDC_AUTHORITY` | — | `http://localhost:8080/realms/mozaiks` | Keycloak realm URL |
| `AUTH_AUDIENCE` | — | `mozaiks-app` | JWT audience (matches Keycloak client ID) |

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

    Data persists in Docker volumes (`mozaiksai_mongo_data`, `mozaiksai_keycloak_db`). Stopping containers does NOT delete data. Only `docker compose down -v` removes volumes.

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

    # Start backend (http://localhost:8000)
    python run_server.py
    ```

    In a separate terminal:

    ```powershell
    # Start frontend (http://localhost:5173)
    cd app
    npm install   # first time only
    npm run dev
    ```

=== "Full Docker"

    ```powershell
    docker compose -f infra/compose/docker-compose.yml up --build
    ```

    This starts MongoDB + PostgreSQL + Keycloak + the Python backend together.
    Start the frontend separately:

    ```powershell
    cd app
    npm install   # first time only
    npm run dev
    ```

---

## Step 4 — Verify

| Check | URL | Expected |
|---|---|---|
| Frontend | [http://localhost:5173](http://localhost:5173) | Keycloak login page (redirects automatically) |
| Backend health | [http://localhost:8000/api/health](http://localhost:8000/api/health) | `{"status": "ok"}` |
| Loaded workflows | [http://localhost:8000/api/workflows](http://localhost:8000/api/workflows) | Shows `HelloWorld` |
| Keycloak admin | [http://localhost:8080/admin](http://localhost:8080/admin) | Admin console (admin/admin) |

### First login

When you open the frontend, you'll be redirected to Keycloak's login page. Use the test user:

- **Username:** `dev`
- **Password:** `dev`

After login, you're redirected back to the app with a valid JWT session.

---

## Step 5 — Configure `app.json`

```json
{
  "appName": "My App",
  "appId": "my-app",
  "defaultWorkflow": "HelloWorld",
  "defaultUserId": "local-dev-user",
  "apiUrl": "http://localhost:8000",
  "wsUrl": "ws://localhost:8000"
}
```

`appName` appears in the browser tab. Set `apiUrl` and `wsUrl` to your deployed backend URL when going to production.

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
mongodb://localhost:27017/MozaiksAI
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
    Another service is using port 8080. Either stop it or change Keycloak's port in `docker-compose.yml` and update `authority` in `app/brand/public/auth.json` to match.

??? question "Port 27017 already in use"
    A local MongoDB is already running. Either stop it (`brew services stop mongodb-community` or stop the Windows service) or change the port mapping in `docker-compose.yml`.

??? question "Frontend shows 'Authentication Unavailable'"
    Keycloak isn't running or isn't reachable. Check `docker compose ps` — Keycloak should be `healthy`. If you want to skip auth for local dev, set `AUTH_ENABLED=false` in `.env` and remove `app/brand/public/auth.json`.

??? question "I want to skip auth during development"
    Set `AUTH_ENABLED=false` in `.env` to disable backend JWT validation. The frontend will still try to reach Keycloak — remove or rename `app/brand/public/auth.json` to skip that too. Remember: auth is the default for a reason.

??? question "How do I connect to MongoDB Atlas instead of local?"
    Set `MONGO_URI` in `.env` to your Atlas connection string. You can then skip starting the local mongo container: `docker compose up keycloak-db keycloak -d`

---

## Next steps

<div class="grid cards" markdown>

-   :fontawesome-solid-sitemap: **Add a workflow**

    ---

    Create your own backend YAML config + frontend components.

    [:octicons-arrow-right-24: Adding a Workflow](guides/adding-a-workflow.md)

-   :fontawesome-solid-palette: **Brand your app**

    ---

    Set colors, fonts, logo, and nav from JSON files — no code changes.

    [:octicons-arrow-right-24: Customize Frontend](guides/customizing-frontend/01-overview.md)

-   :fontawesome-solid-lock: **Configure auth**

    ---

    Customize Keycloak login, roles, branding, and social providers.

    [:octicons-arrow-right-24: Auth JSON](guides/customizing-frontend/06-auth-json.md)

-   :fontawesome-solid-server: **Architecture**

    ---

    How auth, databases, and the runtime fit together.

    [:octicons-arrow-right-24: Keycloak Auth Architecture](architecture/keycloak-auth.md)

</div>
