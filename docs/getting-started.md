# Getting Started

Everything you need to go from clone → running server in minutes.

---

## Repo layout

```
mozaiks/
├── app/                        # Frontend app (Vite + React) — brand & customize this
│   ├── brand/public/           # brand.json, ui.json, navigation.json, assets, fonts
│   ├── App.jsx                 # App shell
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
└── infra/compose/              # Docker Compose (Mongo + app container)
```

---

## Step 1 — Configure `.env`

Copy `.env.example` to `.env` and fill in the required values:

```powershell
Copy-Item .env.example .env
```

| Key | Required | Description |
|-----|----------|-------------|
| `OPENAI_API_KEY` | ✅ | Your OpenAI key |
| `MONGO_URI` | ✅ | `mongodb://localhost:27017` for local, or Atlas URI |
| `ENVIRONMENT` | — | `development` (default) or `production` |
| `AUTH_ENABLED` | — | `false` for local dev (default), `true` with real OIDC in prod |
| `MOZAIKS_OIDC_AUTHORITY` | (prod) | OIDC provider URL — required when `AUTH_ENABLED=true` |
| `AUTH_AUDIENCE` | (prod) | JWT audience claim (default: `api://mozaiks-auth`) |
| `MONGO_DB_NAME` | — | Database name (default: `MozaiksAI`) |
| `REACT_DEV_ORIGIN` | — | Frontend origin for CORS (default: `http://localhost:3000`) |

All other keys have safe defaults — see `.env.example` inline comments for the full list.

---

## Step 2 — Configure `app.json`

```json
{
  "appName": "My App",
  "apiUrl": "http://localhost:8000"
}
```

`appName` appears in the browser tab. Set `apiUrl` to your deployed backend URL when going to production.

---

## Step 3 — Run locally

=== "Local Python + Docker Mongo"

    ```powershell
    # Start MongoDB in Docker
    docker compose -f infra/compose/docker-compose.yml up mongo -d

    # Install Python deps (first time only)
    .\.venv\Scripts\pip.exe install -r requirements.txt

    # Start backend (http://localhost:8000)
    .\.venv\Scripts\python.exe run_server.py

    # In a separate terminal — start frontend (http://localhost:3000)
    cd app
    npm install   # first time only
    npm run dev
    ```

=== "Full Docker"

    ```powershell
    docker compose -f infra/compose/docker-compose.yml up --build
    ```

    The compose file starts MongoDB + the Python backend together.  
    Start the frontend separately with `npm run dev` from `app/`.

---

## Step 4 — Verify

Once both are running:

- Frontend: [http://localhost:3000](http://localhost:3000)
- Backend health: [http://localhost:8000/api/health](http://localhost:8000/api/health)
- Loaded workflows: [http://localhost:8000/api/workflows](http://localhost:8000/api/workflows)

The response from `/api/workflows` should show `HelloWorld` — the example workflow included in the repo.

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

</div>
