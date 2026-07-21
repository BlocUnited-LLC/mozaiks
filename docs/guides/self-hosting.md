# Self-Hosting

Mozaiks can run on your own machine, server, or cloud account. This guide walks
through every option from the simplest single-container start to a scalable
production setup — with plain explanations of the technology at each step.

---

## What You're Running

When you self-host Mozaiks, three things need to run:

| What | Plain English | Required? |
| --- | --- | --- |
| **Mozaiks** | The main application — Studio, AI workflows, and the app runtime | Yes |
| **MongoDB** | A database that stores your apps, build history, and chat sessions | Yes |
| **Keycloak** | A login server that handles user authentication | Optional for local dev, recommended for production |

The simplest setup runs Mozaiks and MongoDB. Keycloak adds proper user login
and is needed when real users will sign into your apps.

---

## Option 1: Single Container (Quickest)

If you already have MongoDB running — or are using
[MongoDB Atlas](https://www.mongodb.com/atlas) (free cloud tier) — you can
start Mozaiks with two commands from the repo root:

```bash
# Build the Mozaiks container image
docker build -t mozaiks -f infra/docker/Dockerfile .

# Run it
docker run -p 8000:8000 \
  -e MONGO_URI="mongodb://your-mongo-host:27017/mozaiks" \
  -e OPENAI_API_KEY="sk-..." \
  mozaiks
```

Open **http://localhost:8000** — Studio is running.

!!! note "Using Anthropic instead of OpenAI?"
    Replace `OPENAI_API_KEY` with `ANTHROPIC_API_KEY="sk-ant-..."`.

**What just happened:** Docker built a container image from the `Dockerfile` in
`infra/docker/`. Think of a container image like a self-contained box that has
Python, Mozaiks, and all its dependencies already installed. The `docker run`
command starts that box and connects it to your MongoDB.

---

## Option 2: Full Local Stack with Docker Compose

Docker Compose starts everything at once — Mozaiks, MongoDB, and Keycloak — with
a single command. This is the recommended way to run the complete stack locally.

**Docker Compose** is a tool that reads a recipe file (`docker-compose.yml`) and
starts all the services your app needs in the right order. You don't have to
manage each one separately.

### What starts

| Service | Port | What it does |
| --- | --- | --- |
| **Mozaiks** (app) | `8000` | Studio and AI runtime |
| **MongoDB** | `27017` | Database |
| **Keycloak** | `8080` | User login and authentication |
| **Postgres** | internal | Keycloak's own database (you don't interact with this) |

### Start the stack

First, copy your environment file:

```bash
cp .env.example .env
# Edit .env and fill in OPENAI_API_KEY (or ANTHROPIC_API_KEY)
```

Then start:

```bash
cd infra/compose
docker compose up
```

The first run takes 30–60 seconds while Keycloak initializes. Once everything is
healthy:

- **Studio:** http://localhost:8000
- **Keycloak admin console:** http://localhost:8080 (default login: `admin` / `admin`)

### Stop the stack

```bash
docker compose down          # stop containers, keep your data
docker compose down -v       # stop containers and delete all saved data
```

### Environment variables

The compose stack reads from `.env` in the repo root automatically. The minimum
you need to add:

| Variable | What it's for |
| --- | --- |
| `OPENAI_API_KEY` | Your OpenAI key, or use `ANTHROPIC_API_KEY` instead |

Everything else (MongoDB connection, Keycloak URL) is pre-wired in the compose
file for local use.

---

## Option 3: Production Server

The production compose file (`infra/compose/docker-compose.prod.yml`) is the same
stack but hardened for a real server:

- No default passwords — all secrets must be set explicitly
- Keycloak runs in production mode (faster, no dev-mode warnings)
- You must set `KC_HOSTNAME` to your actual domain

```bash
cd infra/compose
docker compose -f docker-compose.prod.yml up -d
```

The `-d` flag runs everything in the background.

Required environment variables for production (set these in `.env`):

| Variable | What it's for |
| --- | --- |
| `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` | LLM provider key |
| `MONGO_URI` | MongoDB connection string |
| `KC_ADMIN_USER` | Keycloak admin username |
| `KC_ADMIN_PASSWORD` | Keycloak admin password |
| `KC_DB_PASSWORD` | Password for Keycloak's internal Postgres database |
| `KC_HOSTNAME` | Your public domain (e.g. `mozaiks.yourdomain.com`) |

!!! tip "Put Mozaiks behind a reverse proxy"
    In production, put a reverse proxy (nginx, Caddy, Traefik) in front of port
    `8000` to handle HTTPS, certificates, and domain routing. The container
    exposes HTTP — the proxy handles TLS.

---

## Option 4: Kubernetes with Helm (for scale)

!!! note "You probably don't need this yet"
    If you're running Mozaiks for one team or a small number of users, Docker
    Compose is simpler and sufficient. Come back to this when you need to run
    across multiple servers or handle significant load.

**Kubernetes** is a system for running containers across multiple servers
automatically. It handles restarts when something crashes, scales up when load
increases, and distributes traffic across copies of your app.

**Helm** is the package manager for Kubernetes — similar to how `pip` installs
Python packages. Instead of writing dozens of Kubernetes config files yourself,
Helm lets you install and configure Mozaiks using a pre-built chart and a single
settings file.

The Mozaiks Helm chart lives at `infra/helm/mozaiks/`. It handles:

| What | Plain English |
| --- | --- |
| Deployment | How many copies of Mozaiks to run (default: 2) |
| Auto-scaling | Spin up more copies when CPU or memory is high |
| Ingress | Route your domain name to the app |
| Health checks | Restart a copy automatically if it stops responding |
| Storage | Optional persistent disk for generated artifacts |
| Availability guarantee | Always keep at least 1 copy running during updates |

### Install

```bash
# Install into a Kubernetes cluster
helm install mozaiks ./infra/helm/mozaiks

# Or override settings
helm install mozaiks ./infra/helm/mozaiks \
  --set ingress.enabled=true \
  --set ingress.hosts[0].host=mozaiks.yourdomain.com \
  --set replicaCount=3
```

### Secrets

Helm does not store secrets in its chart. Before deploying, create a Kubernetes
secret with your credentials:

```bash
kubectl create secret generic mozaiks-secrets \
  --from-literal=OPENAI_API_KEY=sk-... \
  --from-literal=MONGO_URI=mongodb+srv://... \
  --from-literal=JWT_SECRET_KEY=your-secret-key
```

The chart picks up that secret automatically via `secretRef.name: mozaiks-secrets`
in `values.yaml`.

### Customize settings

Copy `infra/helm/mozaiks/values.yaml` and edit what you need:

```yaml
# my-values.yaml
replicaCount: 3

ingress:
  enabled: true
  hosts:
    - host: mozaiks.yourdomain.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: mozaiks-tls
      hosts:
        - mozaiks.yourdomain.com

autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 10
```

Then deploy with your overrides:

```bash
helm install mozaiks ./infra/helm/mozaiks -f my-values.yaml
```

---

## Monitoring with Grafana

`infra/grafana/mozaiks-dashboard.json` is a pre-built monitoring dashboard you
can import into [Grafana](https://grafana.com) to see live charts for your running
Mozaiks instance.

**Grafana** is a free tool for visualizing metrics — think of it as a live
dashboard showing request rates, error rates, latency, and token usage.

The Mozaiks container exposes Prometheus metrics at `/metrics` when
`PROMETHEUS_METRICS_ENABLED=true`. Grafana reads those metrics and renders
the charts.

To use it:

1. Install Grafana (or use [Grafana Cloud](https://grafana.com/products/cloud/) — free tier available)
2. Add a Prometheus data source pointing at your metrics endpoint
3. Go to **Dashboards → Import** and upload `infra/grafana/mozaiks-dashboard.json`

---

## About Keycloak

**Keycloak** is an open-source authentication server. When enabled, it manages
user accounts, handles login pages, issues tokens, and controls who can access
what — so Mozaiks doesn't have to build any of that itself.

### Do you need it?

| Situation | Auth setting |
| --- | --- |
| Local development, just you | `AUTH_ENABLED=false` in `.env` — skip Keycloak entirely |
| Team internal use | Keycloak recommended — controls who can log in |
| Public-facing production | Keycloak required |

### What Mozaiks pre-configures

The Docker Compose stack imports the Mozaiks realm into Keycloak automatically.
You don't have to set up realms, clients, or redirect URIs by hand — it's all in
`factory_app/app/brand/realm-export.json`. That file is a repo-local Keycloak
seed for the OSS compose stack. Generated apps should carry provider-neutral
auth behavior in `app/config/auth.yaml`; provider-specific realm export or
social-login setup remains an operator/host concern.

For detailed Keycloak configuration (custom domains, social login, external IdPs)
see [Auth Setup](../architecture/verified/auth-setup.md).
