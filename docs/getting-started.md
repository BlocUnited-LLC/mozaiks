# Getting Started

Build and run your first Mozaiks AI app in five steps.

---

## Prerequisites

| Tool | Version | Check |
|------|---------|-------|
| **Python** | 3.11+ | `python --version` |
| **Node.js** | 18+ | `node --version` |
| **MongoDB** | Any | local install or [MongoDB Atlas](https://www.mongodb.com/atlas) free tier |

You need an **OpenAI API key** (or another supported LLM provider).

---

## Step 1 — Install Mozaiks

```bash
pip install mozaiks
```

Verify the install:

```bash
mozaiks --version
```

---

## Step 2 — Create a new app

```bash
mozaiks init my-app
cd my-app
```

This scaffolds a self-contained app workspace:

```
my-app/
├── app/
│   ├── app.json          # app identity and config
│   ├── config/
│   │   └── ai.json       # AI provider and model config
│   ├── workflows/        # your AI workflows live here
│   ├── modules/          # deterministic backend modules
│   └── ui/               # frontend pages and branding
└── .env.example
```

### Choose a preset (optional)

| Preset | What you get |
|--------|-------------|
| `chat` | Chat interface + one AI workflow (default) |
| `engine` | Bare runtime only, no UI |
| `integrated` | Chat + modules + event bus |
| `full` | Everything: chat, modules, admin, auth, event bus |

```bash
mozaiks init my-app full
```

---

## Step 3 — Add your API key

```bash
cp .env.example .env
```

Open `.env` and set your key:

```dotenv
OPENAI_API_KEY=sk-...
MONGO_URI=mongodb://localhost:27017/my-app
```

That's the only required edit.

!!! tip "Using MongoDB Atlas?"
    Replace `MONGO_URI` with your Atlas connection string. No local MongoDB install needed.

---

## Step 4 — Start the app

```bash
mozaiks serve .
```

This starts the platform host on `http://localhost:8000`.

To also get the frontend:

```bash
# In a second terminal
npm --prefix app/ui install
npm --prefix app/ui run dev
```

Frontend runs at `http://localhost:3000`.

### Hot reload during development

```bash
mozaiks serve . --reload
```

---

## Step 5 — Open the app

| What | URL |
|------|-----|
| Frontend | [http://localhost:3000](http://localhost:3000) |
| Backend health | [http://localhost:8000/api/health](http://localhost:8000/api/health) |
| Active workflows | [http://localhost:8000/api/workflows](http://localhost:8000/api/workflows) |

You should see your app's chat interface. Type a message — the default workflow will respond.

---

## What's next

### Add an AI workflow

```bash
mozaiks gen workflow --prompt "a customer support agent that answers FAQs"
```

This generates a full workflow (agents, tools, handoffs) in `app/workflows/`.

### Add a backend module

Use Claude Code:

```
/add-module
```

### Customize branding

```
/add-branding
```

---

## CLI reference

| Command | What it does |
|---------|-------------|
| `mozaiks init <name>` | Create a new app workspace |
| `mozaiks serve .` | Start the runtime for the current workspace |
| `mozaiks serve . --host studio` | Start with the Studio management UI |
| `mozaiks gen workflow` | Generate an AI workflow from a prompt |
| `mozaiks gen app` | Generate a full app from a prompt |
| `mozaiks add <feature>` | Add a feature to an existing app |
| `mozaiks info` | Show current config and available presets |

---

## Troubleshooting

??? question "Port 8000 already in use"
    ```bash
    mozaiks serve . --port 8001
    ```

??? question "MongoDB connection refused"
    Make sure MongoDB is running locally (`mongod`) or that your `MONGO_URI` in `.env` points to a reachable instance.

??? question "OpenAI rate limit errors"
    Check that `OPENAI_API_KEY` in `.env` is valid and has available quota.

??? question "Workflow not showing up"
    Confirm the workflow directory has an `orchestrator.yaml` file. Run `mozaiks info` to see what the runtime has loaded.
