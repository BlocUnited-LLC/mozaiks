# AI-Assisted Setup Instructions for Mozaiks

> **This file is for AI coding agents to read and follow.**
> If you're a human reading this, go to https://docs.mozaiks.ai/setup-prompt and copy the prompt there.

---

## Overview

You are helping a non-technical founder set up their Mozaiks agentic application stack. Your job is to:

1. **Detect their platform** (Windows, macOS, or Linux)
2. **Check prerequisites** and help install anything missing
3. **Clone the repo** (if not already done)
4. **Configure environment** (.env file)
5. **Start all services** (Docker containers + backend + frontend)
6. **Verify everything works**
7. **Explain next steps**

Be patient, friendly, and explain things in plain English. Avoid jargon. If something fails, help them troubleshoot step by step.

---

## Phase 1: Platform Detection & Prerequisites

### Step 1.1: Detect Platform

Run a command to detect the operating system. Based on results:
- **Windows**: Use PowerShell commands
- **macOS**: Use zsh/bash commands
- **Linux**: Use bash commands

### Step 1.2: Check Prerequisites

Check if each tool is installed. For any missing tool, provide installation instructions.

**Required Tools:**

| Tool | Check Command | Why Needed |
|------|---------------|------------|
| **Docker Desktop** | `docker --version` | Runs MongoDB, PostgreSQL, and Keycloak |
| **Python 3.11+** | `python --version` or `python3 --version` | Runs the backend server |
| **Node.js 18+** | `node --version` | Runs the frontend |
| **npm 9+** | `npm --version` | Installs frontend packages |
| **Git** | `git --version` | Clones the repository |

**Installation Instructions by Platform:**

#### Windows
- **Docker Desktop**: Download from https://www.docker.com/products/docker-desktop/ - Run installer, restart computer
- **Python**: Download from https://www.python.org/downloads/ - CHECK "Add to PATH" during install
- **Node.js**: Download from https://nodejs.org/ (LTS version) - Includes npm
- **Git**: Download from https://git-scm.com/download/win

#### macOS
- **Docker Desktop**: Download from https://www.docker.com/products/docker-desktop/
- **Homebrew** (recommended): `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`
- Then: `brew install python@3.11 node git`

#### Linux (Ubuntu/Debian)
```bash
# Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in

# Python, Node, Git
sudo apt update
sudo apt install python3.11 python3.11-venv nodejs npm git
```

### Step 1.3: Verify Docker is Running

Docker Desktop must be **running** (not just installed). Check with:
```bash
docker info
```

If it fails, instruct user to:
- **Windows/macOS**: Open Docker Desktop app from Start Menu/Applications
- **Linux**: Run `sudo systemctl start docker`

---

## Phase 2: Clone Repository

### Step 2.1: Ask User for Project Location

Ask: "Where would you like to create your project? For example: Desktop, Documents, or a custom folder path."

Common locations:
- Windows: `C:\Users\{username}\Desktop` or `C:\Projects`
- macOS: `~/Desktop` or `~/Projects`
- Linux: `~/projects`

### Step 2.2: Clone the Repo

```bash
cd {chosen_location}
git clone https://github.com/BlocUnited-LLC/mozaiks.git
cd mozaiks
```

If repo already exists (user already cloned), just `cd` into it.

---

## Phase 3: Environment Configuration

### Step 3.1: Copy .env.example

```bash
# Windows PowerShell
Copy-Item .env.example .env

# macOS / Linux
cp .env.example .env
```

### Step 3.2: Get OpenAI API Key

**Ask the user:**
> "Do you have an OpenAI API key? You'll need one for the AI features to work."

**If NO:**
1. Go to https://platform.openai.com/signup
2. Create an account (or sign in)
3. Go to https://platform.openai.com/api-keys
4. Click "Create new secret key"
5. Copy the key (starts with `sk-`)
6. IMPORTANT: Save it somewhere safe - you can't see it again!

**If YES:**
Ask them to provide it.

### Step 3.3: Update .env File

Edit the .env file and set ONLY the required value:

```bash
OPENAI_API_KEY=sk-{their_actual_key}
```

All other values have sensible defaults. Don't change anything else for initial setup.

---

## Phase 4: Start Services

### Step 4.1: Start Docker Containers

This starts MongoDB (database), PostgreSQL (auth database), and Keycloak (login system):

```bash
docker compose -f infra/compose/docker-compose.yml up -d
```

**Wait for services to be healthy:**
```bash
docker compose -f infra/compose/docker-compose.yml ps
```

Keycloak takes 30-60 seconds on first start. All services should show "healthy" or "running".

**Troubleshooting:**
- If port 8080 is busy: Another app is using it. Stop that app or we can change Keycloak's port.
- If port 27017 is busy: Local MongoDB is running. Stop it first.
- If Keycloak stays unhealthy: Run `docker compose logs keycloak` to see errors.

### Step 4.2: Start Backend Server

**Terminal 1 - Backend:**

```bash
# Create virtual environment (first time only)
python -m venv .venv

# Activate it
# Windows:
.\.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start the server
python run_server.py
```

The backend runs at http://localhost:8000

**Troubleshooting:**
- "python not found": Try `python3` instead
- "pip not found": Try `pip3` instead
- "permission denied on .ps1": Run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

### Step 4.3: Start Frontend

**Terminal 2 - Frontend (new terminal window):**

```bash
cd app
npm install
npm run dev
```

The frontend runs at http://localhost:5173

---

## Phase 5: Verification

### Step 5.1: Test Each Component

| Check | URL | Expected Result |
|-------|-----|-----------------|
| Frontend | http://localhost:5173 | Shows Keycloak login page |
| Backend Health | http://localhost:8000/api/health | Shows `{"status": "ok"}` |
| Keycloak Admin | http://localhost:8080/admin | Shows login page |

### Step 5.2: First Login

When the user opens http://localhost:5173, they'll see a login page.

**Test credentials (pre-configured):**
- Username: `dev`
- Password: `dev`

After login, they're in the app!

### Step 5.3: Test the HelloWorld Workflow

After logging in, they should see a chat interface. They can type "Hello" to test that the AI is responding.

---

## Phase 6: Explain What's Running

Give the user a simple explanation:

> **You now have a complete AI application stack running:**
>
> - **Frontend** (localhost:5173): Your app's user interface - what users see
> - **Backend** (localhost:8000): The AI brain - processes messages and talks to OpenAI
> - **MongoDB** (localhost:27017): Your database - stores chats and user data
> - **Keycloak** (localhost:8080): Your login system - handles user accounts
>
> Everything is running on your computer right now. To stop it:
> - Press Ctrl+C in the backend terminal
> - Press Ctrl+C in the frontend terminal
> - Run: `docker compose -f infra/compose/docker-compose.yml down`
>
> To start again later, just run the docker compose command and start backend/frontend.

---

## Phase 7: Next Steps

Share these next steps with the user:

### Customize Your App

1. **Change your app name**: Edit `app.json` - set `appName` to your product name
2. **Change colors/branding**: Edit `app/brand/public/brand.json`
3. **Add your logo**: Replace images in `app/brand/public/assets/`

### Read the Docs

- Full documentation: https://docs.mozaiks.ai
- Add new AI workflows: https://docs.mozaiks.ai/guides/adding-a-workflow/
- Customize the UI: https://docs.mozaiks.ai/guides/customizing-frontend/01-overview/

### Get Help

- GitHub Issues: https://github.com/BlocUnited-LLC/mozaiks/issues

---

## Troubleshooting Reference

### Common Issues

**"Docker is not running"**
- Open Docker Desktop application
- Wait for it to fully start (whale icon stops animating)
- Try the command again

**"Port already in use"**
- Another application is using that port
- Find and stop the other application, OR
- We can change the port in docker-compose.yml

**"OPENAI_API_KEY not set"**
- Make sure .env file exists
- Make sure the key is on its own line: `OPENAI_API_KEY=sk-...`
- Make sure there are no extra spaces

**"npm install fails"**
- Try deleting `node_modules` folder and `package-lock.json`, then run again
- Check Node.js version is 18+

**"pip install fails"**
- Make sure virtual environment is activated
- Try: `pip install --upgrade pip` first
- Check Python version is 3.11+

**"Frontend shows blank page"**
- Open browser dev tools (F12) and check Console for errors
- Make sure backend is running
- Try hard refresh: Ctrl+Shift+R

**"Login fails / redirect loop"**
- Make sure Keycloak is healthy: `docker compose ps`
- Try: `docker compose restart keycloak`
- Clear browser cookies for localhost

---

## Quick Reference Commands

**Start everything:**
```bash
# Terminal 1: Docker services
docker compose -f infra/compose/docker-compose.yml up -d

# Terminal 2: Backend (activate venv first)
python run_server.py

# Terminal 3: Frontend
cd app && npm run dev
```

**Stop everything:**
```bash
# Stop frontend: Ctrl+C
# Stop backend: Ctrl+C
docker compose -f infra/compose/docker-compose.yml down
```

**View logs:**
```bash
# Docker services
docker compose -f infra/compose/docker-compose.yml logs -f

# Just Keycloak
docker compose -f infra/compose/docker-compose.yml logs keycloak -f
```

**Reset everything (nuclear option):**
```bash
docker compose -f infra/compose/docker-compose.yml down -v
# This deletes all data! Fresh start.
```
