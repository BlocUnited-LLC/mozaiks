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
| **OIDC provider** | A login server, such as Keycloak, that authenticates users | Required for authenticated deployments; optional for explicit local development |

The simplest local setup runs Mozaiks and MongoDB with authentication explicitly
disabled. Authenticated deployments configure an OIDC provider and a public
browser client. The included Compose stack provides Keycloak as one option.

---

## Option 1: Single Container (Quickest)

If you already have MongoDB running — or are using
[MongoDB Atlas](https://www.mongodb.com/atlas) (free cloud tier) — you can
start Mozaiks with two commands from the repo root:

```bash
# Build the Mozaiks container image
docker build -t mozaiks -f infra/docker/Dockerfile .

# Run it locally with explicit development access
docker run -p 127.0.0.1:8000:8000 \
  -e ENV=development -e ENVIRONMENT=development \
  -e AUTH_ENABLED=false -e AUTH_ANON_ROLES=admin,user \
  -e MONGO_URI="mongodb://your-mongo-host:27017/mozaiks" \
  -e OPENAI_API_KEY="sk-..." \
  mozaiks
```

The backend listens at **http://localhost:8000**. To use Studio in a browser,
start the repository's web shell separately as described in Option 2.

!!! note "Using Anthropic instead of OpenAI?"
    Set `LLM_PRIMARY_API_TYPE=anthropic` and replace `OPENAI_API_KEY` with
    `ANTHROPIC_API_KEY="sk-ant-..."`. Supply the intended model through
    `LLM_PRIMARY_MODEL`.

For authenticated access, configure the issuer, audience, public browser client,
and callback described in [Factory security](factory-security.md). The audience
(`AUTH_AUDIENCE`) is required: with JWT authentication enabled, Mozaiks refuses
to start without it.

**What just happened:** Docker built a container image from the `Dockerfile` in
`infra/docker/`. Think of a container image like a self-contained box that has
Python, Mozaiks, and all its dependencies already installed. The `docker run`
command starts that box and connects it to your MongoDB.

---

## Option 2: Full Local Stack with Docker Compose

Docker Compose starts the Mozaiks backend, MongoDB, and Keycloak with a single
command. Run the repository's web shell separately for browser access.

**Docker Compose** is a tool that reads a recipe file (`docker-compose.yml`) and
starts these backend services in the right order.

### What starts

| Service | Port | What it does |
| --- | --- | --- |
| **Mozaiks** (app) | `8000` | Studio API and AI runtime |
| **MongoDB** | `27017` | Database |
| **Keycloak** | `8080` | User login and authentication |
| **Postgres** | internal | Keycloak's own database (you don't interact with this) |

### Start the stack

First, copy your environment file:

```bash
cp .env.example .env
# Edit .env: fill in OPENAI_API_KEY (or ANTHROPIC_API_KEY), then set
# AUTH_ENABLED=true and AUTH_AUDIENCE=mozaiks-studio.
# Set AUTH_JWKS_URL=http://keycloak:8080/realms/mozaiks/protocol/openid-connect/certs
# For browser sign-in, set VITE_OIDC_CLIENT_ID=mozaiks-studio and
# VITE_OIDC_AUTHORITY=http://localhost:8080/realms/mozaiks.
```

Then start:

```bash
cd infra/compose
docker compose --env-file ../../.env up
```

The first run takes 30–60 seconds while Keycloak initializes. Once everything is
healthy:

- **Mozaiks API:** http://localhost:8000
- **Keycloak admin console:** http://localhost:8080 (default login: `admin` / `admin`)

From the repo root, start the browser shell in another terminal:

```bash
npm --prefix web_shell ci
npm --prefix web_shell run dev -- --host localhost --port 3000 --strictPort
```

Open **Studio** at http://localhost:3000/apps. The shell proxies API requests
to the backend on port `8000`; the included Keycloak client accepts its login
callback at `http://localhost:3000/auth/callback`.

### Stop the stack

```bash
docker compose down          # stop containers, keep your data
docker compose down -v       # stop containers and delete all saved data
```

### Environment variables

The app service reads the repo-root `.env`; `--env-file` also makes its values
available for Compose variable substitution. With the included Keycloak realm,
set these values:

| Variable | What it's for |
| --- | --- |
| `OPENAI_API_KEY` | Your OpenAI key, or use `ANTHROPIC_API_KEY` instead |
| `AUTH_AUDIENCE` | `mozaiks-studio`, the audience emitted by the included Keycloak client. JWT auth refuses to start without an audience. |
| `AUTH_JWKS_URL` | `http://keycloak:8080/realms/mozaiks/protocol/openid-connect/certs` lets the backend container fetch Keycloak signing keys on the Compose network. |
| `VITE_OIDC_CLIENT_ID` | `mozaiks-studio`, the public browser client in the included realm. |
| `VITE_OIDC_AUTHORITY` | `http://localhost:8080/realms/mozaiks` for the local browser. |

Keycloak publishes its browser-facing `localhost` URL in OIDC discovery. The
backend uses `AUTH_JWKS_URL` for signing keys while discovery still provides
the expected token issuer. The MongoDB connection and Keycloak discovery URL
are pre-wired in the Compose file for local use.

---

## Option 3: Production Server

The production compose file (`infra/compose/docker-compose.prod.yml`) is the same
stack but hardened for a real server:

- No default passwords — all secrets must be set explicitly
- Keycloak runs in production mode (faster, no dev-mode warnings)
- You must set `KC_HOSTNAME` to your actual domain

```bash
cd infra/compose
docker compose --env-file ../../.env -f docker-compose.prod.yml up -d
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
| `AUTH_AUDIENCE` | `mozaiks-studio` for the included client, or the audience configured on your own client. Compose requires this value and the runtime verifies it on every token. |

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

The Mozaiks container exposes AG2-level Prometheus metrics at `/metrics/ag2` when
`AG2_METRICS_ENABLED=true` (requires `pip install "ag2[metrics]"`). Grafana reads
those metrics and renders the charts. Set `AG2_METRICS_PATH` to override the path.

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
| Local development, just you | Explicit development environment, `AUTH_ENABLED=false`, and intended local roles |
| Team internal use | Configure an OIDC provider, such as Keycloak, and the browser client |
| Public-facing production | Authentication enabled with a configured provider and backend token validation |

### What Mozaiks pre-configures

The Docker Compose stack imports the Mozaiks realm from
`factory_app/app/brand/realm-export.json`. That file is a repo-local Keycloak
seed for the OSS compose stack. It includes one public `mozaiks-studio` client
with a local callback and an Audience mapper. For a deployed browser, change
the registered callback and web origin to your actual browser URL, and configure
the public `VITE_OIDC_*` settings alongside backend auth settings. Generated apps carry provider-neutral
auth behavior in `app/config/auth.yaml`; provider-specific realm export or
social-login setup remains an operator/host concern.

### Token audience

The imported `mozaiks-studio` client has standard OIDC flow enabled, client
authentication off, and an Audience mapper that adds `mozaiks-studio` to access
tokens' `aud` claim. For the local browser, set both `AUTH_AUDIENCE` and
`VITE_OIDC_CLIENT_ID` to `mozaiks-studio`. Its callback is
`http://localhost:3000/auth/callback`; update the client redirect URI and web
origin before using another browser URL. Keep the browser client public and use
authorization code flow with PKCE.

If you register another client, add an **Audience** mapper to it (**Client
scopes → <client-id>-dedicated → Add mapper → By configuration → Audience**).
Set **Included Client Audience** to the audience your backend expects, turn
**Add to access token** on, and set `AUTH_AUDIENCE` to that same value.

Mozaiks always checks the access token's `aud` claim against `AUTH_AUDIENCE`.
It refuses to start with JWT authentication enabled and no `AUTH_AUDIENCE`,
and it rejects tokens whose `aud` does not include that value, such as tokens
issued to the realm's other clients. With the
`keycloak` provider (`KEYCLOAK_URL` + `KEYCLOAK_REALM`) the same rule applies to
`KEYCLOAK_CLIENT_ID`.

For detailed Keycloak configuration (custom domains, social login, external IdPs)
see [Auth Setup](../architecture/verified/auth-setup.md).
