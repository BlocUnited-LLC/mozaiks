# Runbook: LLM API Down

**Symptom:** Workflows fail immediately; logs show `CIRCUIT_OPEN name=app_backend` or OpenAI API errors.

---

## 1. Identify the Scope

```bash
# Check circuit breaker status
curl http://<instance>/api/health | jq '.circuit_breakers'

# Check recent workflow failures
grep "CIRCUIT_OPEN\|openai\|LLM" logs/mozaiks.log | tail -50

# Check OpenAI status
curl https://status.openai.com/api/v2/status.json | jq '.status.description'
```

## 2. Immediate Actions

- **If OpenAI is down globally:** No action required in Mozaiks — the circuit breaker is protecting resources. Notify users via status page. Wait for OpenAI recovery.
- **If OpenAI is partially degraded (rate limits):** Reduce `MOZAIKS_MAX_PARALLEL_WORKFLOWS` to lower request rate.
- **If the API key is invalid:** Rotate the key per `secrets-rotation.md` → Section 1.

## 3. Circuit Breaker Recovery

The circuit breaker auto-recovers after `CIRCUIT_BREAKER_RECOVERY_TIMEOUT` (default: 30s).

When OpenAI is back, the circuit transitions OPEN → HALF_OPEN → CLOSED automatically. No manual intervention needed.

To force-reset (if needed):
```bash
# Restart the affected instance — circuit state is in-memory only
docker restart mozaiks_instance_1
```

## 4. Communicate

Post to status page: "AI workflow processing is paused due to LLM API unavailability. No data loss. Workflows will resume automatically."

## 5. Post-Incident

- Check if `OPENAI_MODEL_FALLBACK` is configured as a backup model.
- Consider adding a second LLM provider for failover.
