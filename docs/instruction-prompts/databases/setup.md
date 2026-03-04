# Instruction Prompt: Database Setup

**Task:** Set up MongoDB and PostgreSQL/Keycloak databases

**Complexity:** Low (Docker commands and configuration)

---

## Context for AI Agent

You are helping a user set up databases for their MozaiksAI application. Mozaiks uses two databases:
- **MongoDB** — Chat sessions, workflow state, app config (you interact with this)
- **PostgreSQL** — Keycloak's internal database (managed by Keycloak, you don't touch it)

---

## Step 1: Understand Setup Method

Ask the user:

1. **"Are you using Docker or installing databases locally?"**
   - Docker (recommended) — One command starts everything
   - Local installation — Manual setup for MongoDB

2. **"Are you using cloud databases?"**
   - MongoDB Atlas — Cloud MongoDB
   - Managed PostgreSQL — For Keycloak

---

## Step 2: Docker Setup (Recommended)

### Start Everything

```powershell
docker compose -f infra/compose/docker-compose.yml up -d
```

This starts:
- MongoDB 7 on port `27017`
- PostgreSQL 16 (internal only, for Keycloak)
- Keycloak 26 on port `8080`

### Verify Services

```powershell
docker compose -f infra/compose/docker-compose.yml ps
```

All services should show `healthy`. Keycloak takes ~30-60 seconds on first boot.

### Verify MongoDB

```powershell
# Connect to MongoDB shell
docker exec -it mozaiksai-mongo mongosh MozaiksAI

# Should see prompt: MozaiksAI>
```

### Verify Keycloak

Open http://localhost:8080/admin
- Username: `admin`
- Password: `admin`

---

## Step 3: Local MongoDB Installation

If not using Docker for MongoDB:

### macOS

```bash
brew tap mongodb/brew
brew install mongodb-community@7.0
brew services start mongodb-community@7.0
```

### Ubuntu/Debian

```bash
# Import MongoDB GPG key
curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc | \
  sudo gpg -o /usr/share/keyrings/mongodb-server-7.0.gpg --dearmor

# Add repo
echo "deb [ signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/7.0 multiverse" | \
  sudo tee /etc/apt/sources.list.d/mongodb-org-7.0.list

sudo apt update
sudo apt install -y mongodb-org
sudo systemctl start mongod
sudo systemctl enable mongod
```

### Windows

Download from [mongodb.com/try/download/community](https://www.mongodb.com/try/download/community).
Run the MSI installer. The service starts automatically.

---

## Step 4: MongoDB Atlas (Cloud)

If using MongoDB Atlas:

1. Create a free cluster at [cloud.mongodb.com](https://cloud.mongodb.com)
2. Create a database user
3. Whitelist your IP (or `0.0.0.0/0` for dev)
4. Copy the connection string

Update `.env`:
```dotenv
MONGO_URI=mongodb+srv://myuser:mypassword@cluster0.xxxxx.mongodb.net
MONGO_DB_NAME=MozaiksAI
```

If using Atlas, you can skip the local MongoDB container:
```powershell
docker compose -f infra/compose/docker-compose.yml up keycloak-db keycloak -d
```

---

## Step 5: Configure Environment Variables

Add to `.env`:

```dotenv
# MongoDB
MONGO_URI=mongodb://localhost:27017
MONGO_DB_NAME=MozaiksAI

# Keycloak Database Password (change in production!)
KC_DB_PASSWORD=keycloak

# Keycloak Admin (change in production!)
KC_ADMIN_PASSWORD=admin
```

### For Docker Compose Internal Network

If running the app inside Docker:
```dotenv
MONGO_URI=mongodb://mongo:27017
```

---

## Step 6: Verify Connection

### Test MongoDB Connection

```python
from pymongo import MongoClient
import os

client = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.environ.get("MONGO_DB_NAME", "MozaiksAI")]

# Test write
db.test_collection.insert_one({"test": "hello"})

# Test read
result = db.test_collection.find_one({"test": "hello"})
print(f"MongoDB working: {result}")

# Cleanup
db.test_collection.delete_one({"test": "hello"})
```

### Test Keycloak Connection

```powershell
# Should return JSON with realm info
curl http://localhost:8080/realms/mozaiks
```

---

## Step 7: Summary Template

```markdown
## Database Setup Complete

### Services Running
- ✅ MongoDB: [localhost:27017 / Atlas]
- ✅ PostgreSQL: [Docker internal]
- ✅ Keycloak: [localhost:8080]

### Environment Variables
- MONGO_URI=[value]
- MONGO_DB_NAME=MozaiksAI
- KC_DB_PASSWORD=[set]
- KC_ADMIN_PASSWORD=[set]

### Verification
- [ ] MongoDB shell connects
- [ ] Keycloak admin console accessible
- [ ] App connects to MongoDB
```

---

## Troubleshooting

### "MongoDB port 27017 already in use"

Stop the local MongoDB service:
- macOS: `brew services stop mongodb-community`
- Windows: Stop "MongoDB" Windows service
- Linux: `sudo systemctl stop mongod`

Or change the port in docker-compose.yml:
```yaml
ports:
  - "27018:27017"
```
Then update `.env`: `MONGO_URI=mongodb://localhost:27018`

### "Keycloak shows unhealthy"

Keycloak takes 30-60 seconds to start. Check logs:
```powershell
docker compose -f infra/compose/docker-compose.yml logs keycloak -f
```

Wait for "Running the server in development mode".

### "Connection refused to MongoDB"

1. Check MongoDB is running: `docker ps` or `brew services list`
2. Check firewall isn't blocking port 27017
3. Verify MONGO_URI in .env

### "Cannot authenticate to MongoDB Atlas"

1. Check username/password in connection string
2. Verify IP is whitelisted in Atlas dashboard
3. Try `0.0.0.0/0` for development (all IPs)

---

## Production Checklist

| Setting | Dev Default | Production Value |
|---------|-------------|------------------|
| `MONGO_URI` | localhost | MongoDB Atlas or managed |
| `KC_DB_PASSWORD` | keycloak | Strong random password |
| `KC_ADMIN_PASSWORD` | admin | Strong random password |
| `KC_HOSTNAME` | localhost | Your auth domain |

### MongoDB Production

- [ ] Use MongoDB Atlas or managed service
- [ ] Enable authentication
- [ ] Enable TLS/SSL
- [ ] Set up automated backups
- [ ] Configure replica set for HA

### Keycloak Production

- [ ] Strong database password
- [ ] Consider managed PostgreSQL
- [ ] Set up automated backups
