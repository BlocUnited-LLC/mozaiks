# MozaiksAI Runtime - Deployment Guide

## Quick Start (Local Development)

```bash
# From repo root
cd infra/compose
docker compose up -d
```

This starts:
- **MongoDB** on `localhost:27017`
- **MozaiksAI Runtime** on `localhost:8000`

## Quick Start (Production)

```bash
cd infra/compose
docker compose -f docker-compose.prod.yml up -d
```

## Building the Image

```bash
# From repo root
docker build -t mozaiks/runtime:latest -f infra/docker/Dockerfile .

# Or with version tag
docker build -t mozaiks/runtime:1.0.0 -f infra/docker/Dockerfile .
```

## Environment Variables

Create a `.env` file in the repo root (or pass via `-e` flags):

```env
# Required
OPENAI_API_KEY=sk-...

# MongoDB (default works for compose)
MONGO_URI=mongodb://mongo:27017/MozaiksAI

# Optional - Azure Key Vault (if using Azure for secrets)
# AZURE_KEYVAULT_NAME=your-keyvault
# AZURE_CLIENT_ID=...
# AZURE_CLIENT_SECRET=...
# AZURE_TENANT_ID=...

# Multi-tenant settings
DEFAULT_APP_ID=default
```

## Runtime API Endpoints

Once running, the runtime exposes:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/health` | GET | Health check |
| `/api/sessions` | POST | Create new chat session |
| `/api/sessions/{chat_id}` | GET | Get session state |
| `/api/sessions?app_id=X&user_id=Y` | GET | List user sessions |
| `/ws/{workflow}/{app_id}/{chat_id}/{user_id}` | WS | WebSocket connection |

## Deployment Options

### Option 1: Docker Compose (Simple)

Best for: single-server deployments, small scale

```bash
docker compose -f docker-compose.prod.yml up -d
```

### Option 2: Kubernetes (Scalable)

Best for: production, auto-scaling, high availability

```yaml
# Example Kubernetes deployment (create your own based on this)
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mozaiksai-runtime
spec:
  replicas: 3
  selector:
    matchLabels:
      app: mozaiksai-runtime
  template:
    metadata:
      labels:
        app: mozaiksai-runtime
    spec:
      containers:
      - name: runtime
        image: mozaiks/runtime:latest
        ports:
        - containerPort: 8000
        env:
        - name: MONGO_URI
          valueFrom:
            secretKeyRef:
              name: mozaiksai-secrets
              key: mongo-uri
        - name: OPENAI_API_KEY
          valueFrom:
            secretKeyRef:
              name: mozaiksai-secrets
              key: openai-api-key
        livenessProbe:
          httpGet:
            path: /api/health
            port: 8000
          initialDelaySeconds: 15
          periodSeconds: 30
        resources:
          requests:
            memory: "512Mi"
            cpu: "250m"
          limits:
            memory: "2Gi"
            cpu: "1000m"
```

### Option 3: Cloud Run / App Service (Managed)

Best for: serverless, pay-per-use

**Google Cloud Run:**
```bash
gcloud run deploy mozaiksai-runtime \
  --image gcr.io/your-project/mozaiks-runtime:latest \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars "MONGO_URI=mongodb+srv://..." \
  --set-secrets "OPENAI_API_KEY=openai-key:latest"
```

**Azure Container Apps:**
```bash
az containerapp create \
  --name mozaiksai-runtime \
  --resource-group your-rg \
  --image mozaiks/runtime:latest \
  --target-port 8000 \
  --ingress external \
  --env-vars "MONGO_URI=mongodb+srv://..."
```

## Self-Host Checklist

For customers who want to run their own runtime:

- [ ] MongoDB instance (Atlas, self-hosted, or managed)
- [ ] OpenAI API key (or compatible LLM endpoint)
- [ ] Docker runtime or Kubernetes cluster
- [ ] Network access from their frontend to runtime (CORS configured)
- [ ] (Optional) Reverse proxy with TLS (nginx, Traefik, etc.)

## Connecting ChatUI to the Runtime

In your React app:

```jsx
<ChatUIProvider
  config={{
    api: { baseUrl: 'https://your-runtime-url.com' },
    ws: { baseUrl: 'wss://your-runtime-url.com' },
    chat: { defaultAppId: 'your-app-id' }
  }}
>
  {/* Your app */}
</ChatUIProvider>
```

## Monitoring & Observability

The runtime logs to stdout (Docker captures these). For production:

1. **Logs**: Use Docker logging drivers or ship to your log aggregator
2. **Metrics**: Runtime exposes `/api/health` for uptime monitoring
3. **Traces**: Token usage is logged per `(app_id, user_id, chat_id)`

## Troubleshooting

**Container won't start:**
```bash
docker logs mozaiksai-runtime
```

**MongoDB connection issues:**
```bash
# Check if mongo is healthy
docker compose ps
# Test connection from runtime container
docker exec mozaiksai-runtime python -c "from pymongo import MongoClient; print(MongoClient('mongodb://mongo:27017').admin.command('ping'))"
```

**WebSocket connection fails:**
- Check CORS settings in your reverse proxy
- Ensure WebSocket upgrade is allowed through load balancer
- Verify `ws://` vs `wss://` matches your TLS setup
