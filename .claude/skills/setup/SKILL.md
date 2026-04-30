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
| **full** | Product builders | + admin portal + subscriptions |

The example Backstage app uses the **full** tier. To create a simpler project:
```bash
mozaiks init chat --name my-chatbot
```

For more info on tiers: `/init-project` or `/add-feature` skills.

## Prerequisites to Check

Run these commands and verify versions:
- Docker 24+ and Compose v2+
- Python 3.11+
- Node 18+, npm 9+

If anything is missing, help them install it first.

## Setup Steps

### 1. Environment Variables
```bash
# Copy .env.example to .env
cp .env.example .env  # or Copy-Item on Windows
```

Set `OPENAI_API_KEY=sk-...` in `.env`. If $ARGUMENTS contains an API key, use it.

### 2. Docker Services
```bash
docker compose -f infra/compose/docker-compose.yml up -d
```

Wait 30-60 seconds for Keycloak to initialize. Verify:
```bash
docker compose -f infra/compose/docker-compose.yml ps
```

### 3. Python Backend
```bash
python -m venv .venv
# Activate: .\.venv\Scripts\Activate.ps1 (Windows) or source .venv/bin/activate (Unix)
pip install -r requirements.txt
mozaiks serve .
```

Keep backend running. Verify: `curl http://localhost:8000/api/health`

### 4. Frontend (new terminal)
```bash
cd app
npm install
npm run dev
```

### 5. Verify
- Open http://localhost:5173
- Login: dev / dev
- Send a message in the chat

## Common Issues

- Port 8080 in use: Another service using Keycloak's port
- Port 27017 in use: Local MongoDB already running
- "OPENAI_API_KEY not found": Check .env is in repo root, no spaces around `=`
- "Authentication Unavailable": Wait for Keycloak, or temporarily set AUTH_ENABLED=false

## What's Running

| Service | URL |
|---------|-----|
| Frontend | http://localhost:5173 |
| Backend | http://localhost:8000 |
| Keycloak | http://localhost:8080 |
| Keycloak Admin | http://localhost:8080/admin (admin/admin) |
| MongoDB | localhost:27017 |
