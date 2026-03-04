# Instruction Prompt: Full Setup from Clone

**Task:** Help the user go from `git clone` to a fully running MozaiksAI app

**Complexity:** High (multiple steps, dependencies, services)

**Time:** 10-15 minutes with AI guidance

---

## Context for AI Agent

You are helping a user set up MozaiksAI for the first time. They've just cloned the repo and need to:

1. Install dependencies (Python, Node)
2. Configure environment variables
3. Start Docker services (MongoDB, Keycloak)
4. Start the backend and frontend
5. Verify everything works

**Be patient and encouraging.** This is often overwhelming for new developers.

### System Requirements

Before starting, verify the user has:
- Docker Desktop (or Docker + Docker Compose)
- Python 3.11+
- Node.js 18+
- npm 9+

If they're missing anything, help them install it first.

---

## Phase 1: Verify Prerequisites

Run these checks and report any issues:

```bash
# Check Docker
docker --version
docker compose version

# Check Python
python --version

# Check Node
node --version
npm --version
```

**Expected:**
- Docker 24+ and Compose v2+
- Python 3.11+
- Node 18+
- npm 9+

### If Docker is missing:

**Windows/Mac:** Download Docker Desktop from https://www.docker.com/products/docker-desktop/

**Linux:**
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in
```

### If Python is missing:

Download from https://www.python.org/downloads/ (get 3.11 or 3.12)

**Important (Windows):** Check "Add Python to PATH" during installation.

### If Node is missing:

Download from https://nodejs.org/ (get LTS version)

Or use nvm:
```bash
# macOS/Linux
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.0/install.sh | bash
nvm install 18

# Windows: use nvm-windows from https://github.com/coreybutler/nvm-windows
```

---

## Phase 2: Set Up Environment Variables

This is the most error-prone step. Take it slowly.

### Step 2.1: Create .env file

```bash
# From the repo root directory

# Windows PowerShell
Copy-Item .env.example .env

# macOS/Linux
cp .env.example .env
```

### Step 2.2: Get an OpenAI API Key

Ask the user: **"Do you have an OpenAI API key? It starts with 'sk-'"**

**If no:**
1. Go to https://platform.openai.com/api-keys
2. Sign in (or create account)
3. Click "Create new secret key"
4. Copy the key immediately (you can only see it once!)

### Step 2.3: Set the API Key

Open `.env` in an editor and find:
```
OPENAI_API_KEY=
```

Change to:
```
OPENAI_API_KEY=sk-your-actual-key-here
```

**Verification:**
```bash
grep "OPENAI_API_KEY=sk-" .env && echo "✓ API key is set" || echo "✗ API key is NOT set"
```

### Step 2.4: Review Other Settings

For first-time setup, the defaults are fine:

```env
MONGO_URI=mongodb://localhost:27017     # Docker will provide MongoDB
MONGO_DB_NAME=MozaiksAI                 # Default database name
AUTH_ENABLED=true                        # Keycloak authentication enabled
```

---

## Phase 3: Start Docker Services

Docker runs three services: MongoDB (database), PostgreSQL (Keycloak's database), and Keycloak (authentication).

### Step 3.1: Start the services

```bash
docker compose -f infra/compose/docker-compose.yml up -d
```

**This will:**
- Pull images if needed (first run takes 2-5 minutes)
- Start MongoDB on port 27017
- Start PostgreSQL (internal, for Keycloak)
- Start Keycloak on port 8080

### Step 3.2: Wait for services to be healthy

```bash
# Check status
docker compose -f infra/compose/docker-compose.yml ps
```

**Expected output:** All services show `healthy` or `running`

**Note:** Keycloak takes 30-60 seconds to fully initialize on first boot. Wait for it.

### Step 3.3: Verify services

```bash
# MongoDB
docker exec -it $(docker ps -qf "name=mongo") mongosh --eval "db.runCommand({ping:1})" 2>/dev/null && echo "✓ MongoDB is running"

# Keycloak (wait 30 seconds after starting)
curl -s http://localhost:8080/health/ready | grep -q "UP" && echo "✓ Keycloak is ready" || echo "⏳ Keycloak still starting..."
```

---

## Phase 4: Set Up Python Backend

### Step 4.1: Create virtual environment

```bash
# Create venv (first time only)
python -m venv .venv
```

### Step 4.2: Activate virtual environment

```bash
# Windows PowerShell
.\.venv\Scripts\Activate.ps1

# Windows CMD
.\.venv\Scripts\activate.bat

# macOS/Linux
source .venv/bin/activate
```

**Verification:** Your prompt should now show `(.venv)` at the beginning.

### Step 4.3: Install Python dependencies

```bash
pip install -r requirements.txt
```

**This takes 2-5 minutes.** If you see errors:
- `Microsoft Visual C++ required`: Install Visual Studio Build Tools
- `pip not found`: Make sure venv is activated

### Step 4.4: Start the backend

```bash
python run_server.py
```

**Expected output:**
```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Application startup complete.
```

**Keep this terminal running.** Open a new terminal for the next steps.

### Step 4.5: Verify backend

In a new terminal:
```bash
curl http://localhost:8000/api/health
```

**Expected:** `{"status":"ok"}` or similar JSON response

```bash
curl http://localhost:8000/api/workflows
```

**Expected:** JSON showing `HelloWorld` workflow

---

## Phase 5: Set Up Frontend

Open a **new terminal** (keep the backend running).

### Step 5.1: Navigate to app directory

```bash
cd app
```

### Step 5.2: Install Node dependencies

```bash
npm install
```

**This takes 1-3 minutes.** Ignore warnings about deprecated packages.

### Step 5.3: Start the frontend

```bash
npm run dev
```

**Expected output:**
```
  VITE v5.x.x  ready in xxx ms

  ➜  Local:   http://localhost:5173/
```

---

## Phase 6: Verify Everything Works

### Step 6.1: Open the app

Open http://localhost:5173 in your browser.

**Expected:** You should see the Keycloak login page.

### Step 6.2: Log in with test user

- **Username:** `dev`
- **Password:** `dev`

**Expected:** After login, you're redirected to the app with a chat interface.

### Step 6.3: Test the HelloWorld workflow

1. You should see "HelloWorld" as the active workflow
2. Type a message like "Hello!"
3. The agent should respond

**If this works, congratulations! Setup is complete.**

---

## Troubleshooting

### "Port 8080 already in use"

Another service is using Keycloak's port.

```bash
# Find what's using port 8080
# Windows:
netstat -ano | findstr :8080
# macOS/Linux:
lsof -i :8080
```

**Fix:** Stop the other service, or change Keycloak's port in `docker-compose.yml`.

### "Port 27017 already in use"

Local MongoDB is already running.

```bash
# Stop local MongoDB
# macOS:
brew services stop mongodb-community
# Linux:
sudo systemctl stop mongod
# Windows:
net stop MongoDB
```

### "OPENAI_API_KEY not found"

The `.env` file isn't being loaded.

**Fix:**
1. Make sure `.env` is in the repo root (same folder as `run_server.py`)
2. Make sure there's no space around `=`: `OPENAI_API_KEY=sk-xxx`
3. Restart the backend after changing `.env`

### "Authentication Unavailable" in browser

Keycloak isn't running or isn't ready yet.

**Fix:**
1. Check Keycloak: `docker ps | grep keycloak`
2. Wait 30-60 seconds for Keycloak to initialize
3. Check Keycloak logs: `docker logs $(docker ps -qf "name=keycloak") --tail 50`

### "Cannot connect to MongoDB"

MongoDB container isn't running.

**Fix:**
```bash
docker compose -f infra/compose/docker-compose.yml up -d mongo
docker ps | grep mongo  # Should show running
```

### Frontend shows blank page

Check browser console (F12 → Console) for errors.

Common issues:
- Backend not running: Start `python run_server.py`
- CORS error: Backend and frontend on different ports is fine, this shouldn't happen
- API URL wrong: Check `app.json` has correct `apiUrl`

---

## Quick Reference: What's Running Where

| Service | URL | Purpose |
|---------|-----|---------|
| Frontend | http://localhost:5173 | React app (what users see) |
| Backend | http://localhost:8000 | Python API + WebSocket |
| Keycloak | http://localhost:8080 | Login/authentication |
| Keycloak Admin | http://localhost:8080/admin | Manage users (admin/admin) |
| MongoDB | localhost:27017 | Database (no web UI) |

---

## Next Steps After Setup

1. **Try the HelloWorld workflow** - Send messages, see how it responds
2. **Customize branding** - Edit `app/brand/public/brand.json`
3. **Create your own workflow** - Copy `workflows/HelloWorld` as a template
4. **Read the docs** - See the full documentation for more features
