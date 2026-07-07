# Zero-Downtime Deployment Runbook

Procedure for deploying a new Mozaiks version without dropping active chat sessions.

---

## Prerequisites

- Load balancer with health check on `/api/health` (liveness) and `/api/health/readiness` (readiness)
- MongoDB accessible from all instances
- At least 2 runtime instances running (N+1 during deploy)

---

## Deploy Procedure

### Step 1: Drain one instance

Pick one instance to update first.

1. Remove the instance from the load balancer pool:
   ```bash
   # nginx upstream — comment out the instance
   # Traefik — update the service weight to 0
   # AWS ALB — deregister the instance from the target group
   ```

2. Wait for in-flight requests to complete. Check active workflows:
   ```bash
   curl http://<instance>/api/health/active-runs
   ```
   Wait until `active_runs` reaches 0 (or apply your drain timeout — recommended: 60s).

3. Confirm no WebSocket connections remain (optional — check instance logs).

### Step 2: Update and restart the instance

```bash
# Pull new image / code
docker pull mozaiks:<new-version>

# Stop the instance gracefully (SIGTERM → drain → SIGKILL after 30s)
docker stop --time 30 mozaiks_instance_1

# Start with new version
docker run -d --name mozaiks_instance_1 mozaiks:<new-version>
```

### Step 3: Health check the new instance

```bash
# Wait for the instance to be ready
until curl -sf http://<instance>/api/health/readiness; do
  echo "Waiting for readiness..."
  sleep 2
done
echo "Instance ready"
```

### Step 4: Return to load balancer pool

Re-add the updated instance to the load balancer.

Monitor logs for errors:
```bash
docker logs -f mozaiks_instance_1 | grep -E 'ERROR|CIRCUIT_OPEN|AUTH_FAIL'
```

### Step 5: Repeat for remaining instances

Repeat steps 1–4 for each remaining instance.

---

## Database Migrations During Deploy

Mozaiks uses additive-only JSON migrations (no destructive operations).

1. Migrations run automatically at startup (`apply_data_migrations()` in the lifespan).
2. They are tracked by hash in `AppDataMigrations` collection — idempotent.
3. New indexes are created in the background (`background=True`) — no locking.

**No manual migration step is required unless a new migration file was added.**

If a migration fails:
1. Check logs: `docker logs mozaiks_instance_1 | grep MIGRATION`
2. Inspect the `AppDataMigrations` collection for the failed record.
3. Fix the migration JSON and restart the instance.

---

## Session Continuity

Active chat sessions are stored in MongoDB. When a client's WebSocket drops:
- The chat-ui automatically reconnects.
- The new instance resumes the session from MongoDB state.
- The distributed lock (`core/runtime/persistence/distributed_lock.py`) prevents two instances from resuming the same chat simultaneously.

**There is no in-memory session state.** Restarts do not lose sessions.

---

## Rollback

If the new version is bad:

1. Drain and remove the updated instance from the load balancer.
2. Start the old image:
   ```bash
   docker stop mozaiks_instance_1
   docker run -d --name mozaiks_instance_1 mozaiks:<old-version>
   ```
3. Health check and return to pool.
4. Repeat for any other instances already updated.

Migrations run by the new version are additive and read-compatible — the old version can read the same collections.

---

## Kubernetes Rolling Update

If running on Kubernetes, the standard `kubectl rollout` handles this automatically:

```bash
kubectl set image deployment/mozaiks mozaiks=mozaiks:<new-version>
kubectl rollout status deployment/mozaiks --timeout=5m
```

Ensure your Deployment spec includes:
```yaml
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxUnavailable: 0
    maxSurge: 1
readinessProbe:
  httpGet:
    path: /api/health/readiness
    port: 8000
  initialDelaySeconds: 10
  periodSeconds: 5
livenessProbe:
  httpGet:
    path: /api/health
    port: 8000
  initialDelaySeconds: 30
  periodSeconds: 10
terminationGracePeriodSeconds: 60
```
