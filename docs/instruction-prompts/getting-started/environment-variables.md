# Instruction Prompt: Environment Variables Setup

**Task:** Help the user set up their `.env` file for MozaiksAI

**Complexity:** Medium (involves understanding what each variable does and where to get values)

---

## Context for AI Agent

You are helping a user set up environment variables for MozaiksAI, an AI-powered application framework. The user may be new to development and intimidated by `.env` files. Your job is to:

1. Make this as painless as possible
2. Explain what each variable does in plain English
3. Help them get the values they need
4. Verify the setup is correct

### Project Structure Context

```
mozaiks/                          # Root of the repo
├── .env.example                  # Template with all variables (READ THIS)
├── .env                          # User's actual config (CREATE THIS)
├── run_server.py                 # Starts the backend
├── shared_app.py                 # FastAPI application
├── app/                          # Frontend app
├── workflows/                    # Backend workflow definitions
├── mozaiksai/                    # Core runtime (don't modify)
└── infra/
    └── compose/
        └── docker-compose.yml    # Docker services config
```

---

## Step 1: Create the .env File

First, check if `.env` already exists:

```bash
# Check if .env exists
ls -la .env 2>/dev/null || echo ".env does not exist"
```

If it doesn't exist, create it from the template:

```bash
# Windows PowerShell
Copy-Item .env.example .env

# macOS/Linux
cp .env.example .env
```

**Verification:** The file `.env` should now exist in the repo root.

---

## Step 2: Understand the Variables

Read the `.env.example` file to understand what variables exist. Here's what each one does:

### Required Variables (You MUST set these)

| Variable | What it is | Where to get it |
|----------|-----------|-----------------|
| `OPENAI_API_KEY` | Your OpenAI API key for LLM calls | https://platform.openai.com/api-keys |

### Optional Variables (Have sensible defaults)

| Variable | Default | What it does |
|----------|---------|--------------|
| `MONGO_URI` | `mongodb://localhost:27017` | MongoDB connection string. Leave default if using Docker. |
| `MONGO_DB_NAME` | `MozaiksAI` | Database name. Usually no need to change. |
| `AUTH_ENABLED` | `true` (Docker) / `false` (local) | Enable Keycloak authentication. |
| `KC_ADMIN_USER` | `admin` | Keycloak admin console username. |
| `KC_ADMIN_PASSWORD` | `admin` | Keycloak admin console password. Change in production! |

### Advanced Variables (Only if you know what you're doing)

| Variable | Default | When to change |
|----------|---------|----------------|
| `MOZAIKS_OIDC_AUTHORITY` | `http://localhost:8080/realms/mozaiks` | Only if using external Keycloak |
| `AUTH_AUDIENCE` | `mozaiks-app` | Only if you renamed the Keycloak client |
| `AG2_OTEL_ENABLED` | `false` | Set `true` to enable OpenTelemetry tracing |

---

## Step 3: Set the OpenAI API Key

This is the only variable you MUST set manually.

### If the user doesn't have an OpenAI API key:

1. Go to https://platform.openai.com/api-keys
2. Sign in or create an account
3. Click "Create new secret key"
4. Copy the key (starts with `sk-`)
5. **Important:** You can only see this key once. Save it somewhere safe.

### Set the key in .env:

Open the `.env` file and find this line:
```
OPENAI_API_KEY=
```

Change it to:
```
OPENAI_API_KEY=sk-your-actual-key-here
```

**Security Note:** Never commit `.env` to git. It's already in `.gitignore`.

---

## Step 4: Decide on Database Setup

Ask the user: **"Are you using Docker for MongoDB, or do you have your own MongoDB (like Atlas)?"**

### Option A: Using Docker (Recommended for local dev)

Leave the default:
```
MONGO_URI=mongodb://localhost:27017
```

Docker will start MongoDB for you when you run `docker compose up`.

### Option B: Using MongoDB Atlas or external MongoDB

Set your connection string:
```
MONGO_URI=mongodb+srv://username:password@cluster.mongodb.net/MozaiksAI?retryWrites=true&w=majority
```

**Getting an Atlas connection string:**
1. Go to https://cloud.mongodb.com
2. Create a cluster (free tier is fine)
3. Click "Connect" → "Connect your application"
4. Copy the connection string
5. Replace `<password>` with your actual password

---

## Step 5: Decide on Authentication

Ask the user: **"Do you want to use authentication (login/logout), or skip it for local development?"**

### Option A: Use Authentication (Default, recommended)

Leave defaults:
```
AUTH_ENABLED=true
KC_ADMIN_USER=admin
KC_ADMIN_PASSWORD=admin
```

Keycloak will start with Docker and provide login functionality.

Default test user: `dev` / `dev`

### Option B: Skip Authentication (Quick local testing)

Set:
```
AUTH_ENABLED=false
```

All routes will allow unauthenticated access. User ID will be "anonymous".

**Warning:** Never deploy without authentication enabled.

---

## Step 6: Verify the Configuration

After setting up `.env`, verify it's correct:

```bash
# Check that required variables are set
grep "OPENAI_API_KEY=" .env | head -1
grep "MONGO_URI=" .env | head -1
```

The output should show your actual values (not empty).

### Quick validation checklist:

- [ ] `.env` file exists in repo root
- [ ] `OPENAI_API_KEY` is set to a real key (starts with `sk-`)
- [ ] `MONGO_URI` is either default (localhost) or a valid connection string
- [ ] `AUTH_ENABLED` is set to your preference

---

## Step 7: Test the Configuration

Start the backend to verify environment is loaded correctly:

```bash
# Activate virtual environment first
# Windows:
.\.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

# Start the server
python run_server.py
```

**Expected output:**
- No errors about missing environment variables
- Server starts on http://localhost:8000
- Health check works: `curl http://localhost:8000/api/health`

---

## Troubleshooting

### "OPENAI_API_KEY not found" error

**Cause:** The key isn't set or the `.env` file isn't being loaded.

**Fix:**
1. Check `.env` exists in the repo root (not in a subdirectory)
2. Check the key is set: `grep OPENAI_API_KEY .env`
3. Make sure there are no spaces around the `=`: `OPENAI_API_KEY=sk-xxx` (correct)

### "MongoDB connection failed" error

**Cause:** MongoDB isn't running or connection string is wrong.

**Fix:**
1. If using Docker: `docker compose up -d mongo`
2. Check MongoDB is running: `docker ps | grep mongo`
3. If using Atlas: verify the connection string and that your IP is whitelisted

### "Authentication failed" / "Keycloak unreachable"

**Cause:** Keycloak isn't running.

**Fix:**
1. Start Keycloak: `docker compose up -d keycloak`
2. Wait ~30 seconds for it to initialize
3. Or disable auth: set `AUTH_ENABLED=false` in `.env`

### "I changed .env but nothing changed"

**Cause:** The server needs to be restarted to pick up changes.

**Fix:** Stop the server (Ctrl+C) and start it again.

---

## Production Deployment Notes

When deploying to production, you'll need to:

1. **Use secrets management** instead of `.env` files
   - Azure: Key Vault
   - AWS: Secrets Manager
   - GCP: Secret Manager

2. **Change default passwords:**
   ```
   KC_ADMIN_PASSWORD=<strong-random-password>
   KC_DB_PASSWORD=<strong-random-password>
   ```

3. **Set production URLs:**
   ```
   MOZAIKS_OIDC_AUTHORITY=https://auth.yourdomain.com/realms/yourapp
   ```

4. **Never use `AUTH_ENABLED=false` in production**

---

## Summary of Common Setups

### Minimal Local Development (Fastest)
```env
OPENAI_API_KEY=sk-your-key
AUTH_ENABLED=false
```

### Full Local Development (With Auth)
```env
OPENAI_API_KEY=sk-your-key
MONGO_URI=mongodb://localhost:27017
AUTH_ENABLED=true
KC_ADMIN_USER=admin
KC_ADMIN_PASSWORD=admin
```

### Using MongoDB Atlas
```env
OPENAI_API_KEY=sk-your-key
MONGO_URI=mongodb+srv://user:pass@cluster.mongodb.net/MozaiksAI
AUTH_ENABLED=false
```
