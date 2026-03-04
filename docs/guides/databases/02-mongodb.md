# MongoDB Setup

MongoDB stores all your application data: chat sessions, workflow state, artifacts, and app config.

---

## What MongoDB Stores

| Collection | Contents |
|------------|----------|
| `conversations` | Chat sessions, messages, metadata |
| `workflow_runs` | Workflow execution state, checkpoints |
| `artifacts` | Generated files, code, documents |
| `app_config` | Per-app configuration snapshots |

Collections are created **lazily** — they appear when the runtime first writes to them.

---

## Connection String

Set in `.env`:

```dotenv
MONGO_URI=mongodb://localhost:27017
MONGO_DB_NAME=MozaiksAI
```

| Mode | URI |
|------|-----|
| Docker Compose | `mongodb://mongo:27017/MozaiksAI` |
| Local | `mongodb://localhost:27017/MozaiksAI` |
| Atlas | `mongodb+srv://user:pass@cluster.mongodb.net/MozaiksAI` |

---

## Option A: Docker (Recommended)

Included in `docker-compose.yml`. Starts automatically:

```bash
docker compose -f infra/compose/docker-compose.yml up mongo -d
```

---

## Option B: Local Install

=== "macOS"

    ```bash
    brew tap mongodb/brew
    brew install mongodb-community@7.0
    brew services start mongodb-community@7.0
    ```

=== "Ubuntu/Debian"

    ```bash
    curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc | \
      sudo gpg -o /usr/share/keyrings/mongodb-server-7.0.gpg --dearmor

    echo "deb [ signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/7.0 multiverse" | \
      sudo tee /etc/apt/sources.list.d/mongodb-org-7.0.list

    sudo apt update && sudo apt install -y mongodb-org
    sudo systemctl start mongod
    sudo systemctl enable mongod
    ```

=== "Windows"

    Download from [mongodb.com/try/download/community](https://www.mongodb.com/try/download/community).
    Run the MSI installer. Service starts automatically.

---

## Option C: MongoDB Atlas (Cloud)

1. Create free cluster at [cloud.mongodb.com](https://cloud.mongodb.com)
2. Create a database user
3. Whitelist your IP (or `0.0.0.0/0` for dev)
4. Copy connection string to `.env`:

```dotenv
MONGO_URI=mongodb+srv://myuser:mypassword@cluster0.xxxxx.mongodb.net
MONGO_DB_NAME=MozaiksAI
```

!!! tip "Atlas with Docker"
    Skip local MongoDB container:
    ```bash
    docker compose -f infra/compose/docker-compose.yml up keycloak-db keycloak -d
    ```

---

## GUI Clients

| Client | Free? | Link |
|--------|-------|------|
| MongoDB Compass | Yes | [mongodb.com/products/compass](https://www.mongodb.com/products/compass) |
| mongosh (CLI) | Yes | Included with MongoDB |
| Studio 3T | Free tier | [studio3t.com](https://studio3t.com) |

Connection: `mongodb://localhost:27017/MozaiksAI`

---

## Troubleshooting

??? question "Port 27017 already in use"
    A local MongoDB is already running. Stop it:

    - **macOS**: `brew services stop mongodb-community`
    - **Windows**: Stop "MongoDB" Windows service
    - **Linux**: `sudo systemctl stop mongod`

    Or change port in docker-compose.yml and update `.env`.

??? question "Connection refused"
    1. Check MongoDB is running: `docker ps` or service status
    2. Check firewall isn't blocking port 27017
    3. Verify MONGO_URI in .env
