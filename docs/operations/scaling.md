# Scaling Guide

How to scale Mozaiks horizontally to handle higher workflow concurrency and user load.

---

## Architecture: What Is Stateless

All runtime instances share:
- **MongoDB** — all session state, chat history, artifacts, audit logs
- **Redis** (optional) — JWKS cache, app context cache, workflow queue
- **Object store** (optional) — large artifact blobs (S3/MinIO)

Each instance is stateless — any instance can serve any request.

**Exception:** WebSocket connections are instance-sticky. A client connected to instance A
must reconnect if instance A restarts. This is handled automatically by the chat-ui.

---

## Workflow Concurrency

### Per-instance (default)

```bash
MOZAIKS_MAX_PARALLEL_WORKFLOWS=4  # per-instance concurrency limit
```

With 4 instances: up to 16 concurrent workflows globally (no coordination).

### Global queue (recommended for production)

Enable MongoDB-backed global queue:

```bash
WORKFLOW_QUEUE_BACKEND=mongo
WORKFLOW_QUEUE_MAX_CONCURRENCY=20  # global limit across all instances
```

This ensures fair scheduling regardless of how many instances are running.

---

## Session Affinity

WebSocket clients should reconnect to the same instance for session continuity.

### nginx

```nginx
upstream mozaiks {
    # Sticky sessions by IP (development)
    ip_hash;
    server mozaiks_1:8000;
    server mozaiks_2:8000;
    server mozaiks_3:8000;
}
```

For production, use cookie-based affinity:

```nginx
upstream mozaiks {
    server mozaiks_1:8000;
    server mozaiks_2:8000;
    server mozaiks_3:8000;
    sticky cookie mozaiks_session expires=1h;
}
```

### AWS Application Load Balancer

Enable "Stickiness" on the target group:
- Type: Load balancer generated cookie
- Duration: 1 hour

### Traefik

```yaml
services:
  mozaiks:
    loadBalancer:
      sticky:
        cookie:
          name: mozaiks_session
          httpOnly: true
          secure: true
```

---

## Distributed Cache (Redis)

Enable Redis to share JWKS and app context across instances:

```bash
# Install redis extra
pip install "mozaiks[monitoring]"

# Configure
REDIS_URL=redis://redis-host:6379
REDIS_CACHE_ENABLED=true
REDIS_CACHE_TTL=300       # 5 minutes default
REDIS_CACHE_PREFIX=mozaiks:
```

Without Redis, each instance maintains its own in-memory cache (JWKS TTL: 1h).
Redis reduces upstream JWKS fetches significantly at scale.

---

## MongoDB Connection Pool

```bash
# Default Motor pool size is 100 connections
# For high-concurrency deployments, ensure the MongoDB cluster allows enough connections
# Rule of thumb: max_pool_size = (MAX_PARALLEL_WORKFLOWS * instances) * 3
```

Monitor pool exhaustion:
```
grep "connection pool" logs/mozaiks.log | grep -i "wait\|timeout\|exhausted"
```

---

## Kubernetes HPA (Horizontal Pod Autoscaler)

Scale based on active workflow count (custom metric via Prometheus adapter):

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: mozaiks
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: mozaiks
  minReplicas: 2
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
    - type: Pods
      pods:
        metric:
          name: mozaiks_active_workflows
        target:
          type: AverageValue
          averageValue: "3"   # Scale when average active workflows per pod > 3
```

---

## Recommended Instance Sizing

| Load | Instances | vCPU/instance | RAM/instance | MAX_PARALLEL_WORKFLOWS |
|------|-----------|---------------|--------------|------------------------|
| Dev/staging | 1 | 2 | 2GB | 4 |
| Light prod | 2 | 2 | 4GB | 8 |
| Medium prod | 3–4 | 4 | 8GB | 12 |
| Heavy prod | 6–10 | 8 | 16GB | 16 |

MongoDB and Redis should each have dedicated instances sized separately.
