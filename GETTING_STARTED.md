# Getting Started

This repo **is** the template. Clone it, configure the files below, add your workflows, and run.

---

## Repo Layout

```
mozaiks/
├── app/                        # Frontend app (Vite + React) — customize this
│   ├── brand/public/           # Branding: logo, fonts, colors, navigation
│   ├── App.jsx                 # App shell — wire auth/routing here
│   ├── main.jsx                # Entry point
│   └── vite.config.js          # Paths pre-wired — update proxy when backend is live
│
├── app.json                    # App name + API URL — edit this first
│
├── chat-ui/                    # UI library — do not modify
│   └── src/
│       └── workflows/          # Workflow UI component registry
│           └── HelloWorld/     # Example — duplicate for your own
│
├── workflows/                  # Backend workflow definitions — add yours here
│   └── HelloWorld/             # Example AG2 workflow
│
├── mozaiksai/                  # Runtime engine — do not modify
├── shared_app.py               # FastAPI server
├── run_server.py               # Entry point
├── requirements.txt            # Python dependencies
├── .env                        # Secrets & config — fill this in
└── infra/compose/              # Docker Compose (Mongo + app)
```

---

## Files to Customize

### 1. `.env` — Required before first run

| Key | What to set |
|-----|-------------|
| `OPENAI_API_KEY` | Your OpenAI key |
| `MONGO_URI` | `mongodb://localhost:27017` (local) or your Atlas URI |
| `ENVIRONMENT` | `development` or `production` |
| `AUTH_ENABLED` | `false` for local dev, `true` with real OIDC in prod |
| `MOZAIKS_OIDC_AUTHORITY` | Your OIDC provider URL (when `AUTH_ENABLED=true`) |
| `AUTH_AUDIENCE` | JWT audience claim |

All other keys have sensible defaults. See `.env` comments for the full list.

---

### 2. `app.json` — App identity

```json
{
  "appName": "My App",
  "apiUrl": "http://localhost:8000"
}
```

Change `appName` (shown in the browser tab) and `apiUrl` to match your deployed backend URL.

---

### 3. `app/brand/public/brand.json` — Colors, typography, logo

```json
{
  "appName": "My App",
  "logoUrl": "/assets/my_logo.png",
  "colors": { "primary": "#6366f1", ... },
  ...
}
```

Replace values to match your brand. The frontend loads this at startup.

---

### 4. `app/brand/public/navigation.json` — Sidebar / nav items

Defines which workflows appear in the navigation and their display names/icons.

---

### 5. `app/brand/public/ui.json` — Layout & feature flags

Controls feature toggles, panel layout, and UI behaviour.

---

### 6. `app/brand/public/assets/` — Logo & icons

Drop your own files here and update the paths in `brand.json`.

---

### 7. `app/brand/public/fonts/` — Custom fonts

Add `.ttf`/`.woff` files here and reference them in your CSS / `brand.json`.

---

## Adding a Workflow

A workflow has two parts: **backend config** and **frontend UI components**.

### Backend — `workflows/<YourWorkflow>/`

Copy `workflows/HelloWorld/` and rename the folder. Key files to edit:

| File | What it controls |
|------|-----------------|
| `orchestrator.yaml` | `workflow_name`, `max_turns`, `startup_mode`, `initial_message`, `initial_agent` |
| `agents.yaml` | Agent names, system prompts, `max_consecutive_auto_reply` |
| `handoffs.yaml` | Which agent hands off to which, and under what condition |
| `tools.yaml` | Tools each agent can call, and whether they render UI |
| `context_variables.yaml` | Shared state across agents (DB-backed or computed) |
| `structured_outputs.yaml` | Pydantic-style output schemas for agents |
| `hooks.yaml` | Lifecycle hooks (`update_agent_state`, `process_message_before_send`) |
| `tools/<fn>.py` | Python tool implementations |

The workflow is **auto-discovered** at server startup — no registration needed.

### Frontend — `chat-ui/src/workflows/<YourWorkflow>/`

Copy `chat-ui/src/workflows/HelloWorld/` and rename the folder. Edit:

| File | What it controls |
|------|-----------------|
| `components/index.js` | Exports `{ ComponentName: ReactComponent }` — keys must match `ui.component` in `tools.yaml` |
| `components/<Name>.js` | React component rendered when the tool fires |
| `theme_config.json` | Per-workflow color overrides (optional) |

Then register it in `chat-ui/src/workflows/index.js`:

```js
import MyWorkflowComponents from './MyWorkflow/components';

const WORKFLOW_REGISTRY = {
  HelloWorld: { components: HelloWorldComponents },
  MyWorkflow: { components: MyWorkflowComponents }, // ← add this line
};
```

---

## Running Locally

```powershell
# 1. Start MongoDB
docker compose -f infra/compose/docker-compose.yml up mongo -d

# 2. Install Python deps (first time only)
.\.venv\Scripts\pip.exe install -r requirements.txt

# 3. Start backend
.\.venv\Scripts\python.exe run_server.py

# 4. Start frontend (separate terminal)
cd app
npm install   # first time only
npm run dev
```

Backend runs on `http://localhost:8000`.  
Frontend runs on `http://localhost:3000`.

### Or run everything in Docker

```powershell
docker compose -f infra/compose/docker-compose.yml up --build
```

---

## WebSocket Session URL

```
ws://localhost:8000/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}
```

The frontend connects here automatically once a chat session is started via `POST /api/chats/{app_id}/{workflow_name}/start`.
