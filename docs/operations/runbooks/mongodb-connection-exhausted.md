# Runbook: MongoDB Connection Pool Exhausted

**Symptom:** `ServerSelectionTimeoutError`, `ConnectionPoolFull`, requests hanging.

---

## 1. Identify

```bash
# Check active connections on MongoDB
mongo "${MONGO_URI}" --eval '
  db.adminCommand({serverStatus: 1}).connections
'
# Look for: current, available, totalCreated

# Check runtime readiness
curl http://<instance>/api/health/readiness
```

## 2. Immediate Mitigation

1. **Scale down active workflows** — reduce `MOZAIKS_MAX_PARALLEL_WORKFLOWS` temporarily:
   ```bash
   # Restart with reduced concurrency
   docker stop mozaiks_instance_X
   MOZAIKS_MAX_PARALLEL_WORKFLOWS=2 docker start mozaiks_instance_X
   ```

2. **Identify long-running queries** on MongoDB:
   ```js
   db.adminCommand({currentOp: 1, active: true, secs_running: {$gte: 5}})
   ```

3. **Kill runaway operations** if found:
   ```js
   db.adminCommand({killOp: 1, op: <opid>})
   ```

## 3. Root Cause Analysis

- Is `MOZAIKS_MAX_PARALLEL_WORKFLOWS` too high for the connection pool?
- Are there slow queries (missing indexes)?
- Did a migration create a lock on a collection?

Check slow query log:
```bash
mongo "${MONGO_URI}" --eval '
  db.setProfilingLevel(1, {slowms: 100})
'
# Wait 5 minutes, then check
mongo "${MONGO_URI}" --eval '
  db.system.profile.find().sort({ts: -1}).limit(20).pretty()
'
```

## 4. Long-term Fix

- Tune Motor pool size: set `MONGO_MAX_POOL_SIZE` env var.
- Add missing indexes for slow query patterns.
- Reduce `MOZAIKS_MAX_PARALLEL_WORKFLOWS` if connection pressure is systematic.
