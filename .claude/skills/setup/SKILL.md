---
name: setup
description: Set up Mozaiks from scratch. Walks through Docker, Python, Node, environment variables, and verification.
argument-hint: "[optional: your OpenAI API key]"
---

Help the user set up Mozaiks from a fresh clone.

## Tier System

Mozaiks supports four development tiers:

| Tier | Use Case | What's Included |
|------|----------|-----------------|
| **engine** | Headless AI API | Workflows only, no UI |
| **chat** | Chatbot builders | Workflows + chat UI |
| **integrated** | SaaS builders | + modules + event bus + auth |
| **full** | Product builders | + admin portal + full management surfaces |

For more info on tiers: `/init-project` or `/add-feature` skills.

## Prerequisites to Check

Run these commands and verify versions:
- Docker 24+ and Compose v2+
- Python 3.11+
- Node 18+, npm 9+

If anything is missing, help them install it first.

## Canonical Local Contributor Path

For this repo, the default local contributor experience is:

- Studio backend on `http://localhost:8000`
- `web_shell/` frontend on `http://localhost:3000`
- `factory_app/app` as the first-party builder/reference app bundle when no
	external app workspace is selected

## Setup Steps

### 1. Environment Variables
```bash
# Copy .env.example to .env
cp .env.example .env  # or Copy-Item on Windows
```

Set `OPENAI_API_KEY=sk-...` in `.env`. If $ARGUMENTS contains an API key, use it.

### 2. Python + Repo Dependencies
```bash
python -m venv .venv
# Activate: .\.venv\Scripts\Activate.ps1 (Windows) or source .venv/bin/activate (Unix)
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

### 3. Frontend Dependencies
```bash
npm --prefix web_shell install
```

### 4. Start the Default Local Dev Stack

Preferred path in this repo:

```powershell
.\scripts\run-studio.ps1
```

That starts the Studio backend and the `web_shell` frontend together.

### 5. Verify

- Open `http://localhost:3000/apps`
- Check backend health at `http://localhost:8000/api/health`
- Confirm the frontend is loading the first-party builder/reference app bundle
	from `factory_app/app`

### 6. Optional Split Mode

If the user wants separate backend and frontend terminals:

```powershell
.\scripts\run-backend.ps1
.\scripts\run-frontend.ps1
```

If they are developing against an external app workspace, rerun those scripts
with `-AppWorkspacePath <path>`.

## Common Issues

- Port 8080 in use: Another service using Keycloak's port
- Port 27017 in use: Local MongoDB already running
- "OPENAI_API_KEY not found": Check .env is in repo root, no spaces around `=`
- Frontend loads the wrong app bundle: restart with `-AppWorkspacePath <path>` or set `PLATFORM_PATH`
- Port 3000 or 8000 already in use: rerun `run-studio.ps1`, `run-backend.ps1`, or `run-frontend.ps1` with `-ForceStop`

## What's Running

| Service | URL |
|---------|-----|
| Frontend | http://localhost:3000 |
| Backend | http://localhost:8000 |
| Keycloak | http://localhost:8080 |
| Keycloak Admin | http://localhost:8080/admin (admin/admin) |
| MongoDB | localhost:27017 |
