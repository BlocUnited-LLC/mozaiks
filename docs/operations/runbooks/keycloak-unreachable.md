# Runbook: Keycloak Unreachable

**Symptom:** All authenticated requests return 401/503; logs show `AUTH_FAILED` or JWKS fetch errors.

---

## 1. Identify

```bash
# Check auth failures in logs
grep "AUTH_FAILED\|JWKS\|keycloak" logs/mozaiks.log | tail -30

# Test Keycloak directly
curl -sf ${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}/.well-known/openid-configuration | jq '.issuer'

# Check Keycloak container
docker ps | grep keycloak
docker logs keycloak --tail 50
```

## 2. Immediate: Enable Auth Bypass (Emergency Only)

> **WARNING**: Only use in emergencies. Disables all auth enforcement.

```bash
# Restart instance with auth disabled temporarily
AUTH_ENABLED=false docker restart mozaiks_instance_1
```

Inform users that the instance is running without auth. Roll back as soon as Keycloak recovers.

## 3. Restart Keycloak

```bash
# Docker Compose
docker compose -f infra/compose/docker-compose.yml restart keycloak

# Wait for readiness
until curl -sf ${KEYCLOAK_URL}/health/ready; do
  echo "Waiting for Keycloak..."
  sleep 5
done

# Restart Mozaiks to re-enable auth
AUTH_ENABLED=true docker restart mozaiks_instance_1
```

## 4. JWKS Cache

Mozaiks caches JWKS for 1 hour (in-memory) or `REDIS_CACHE_TTL` seconds (Redis).
If Keycloak rotated keys during the outage, clear the cache by restarting the instance.

## 5. Realm Recovery

If Keycloak's database was lost, restore from backup (see `backup.md`):
```bash
# Restore realm export
KC_TOKEN=$(curl -s -X POST "${KC_URL}/realms/master/protocol/openid-connect/token" \
    -d "grant_type=password&client_id=admin-cli&username=${KC_ADMIN}&password=${KC_ADMIN_PASSWORD}" \
    | jq -r '.access_token')

curl -s -X POST -H "Authorization: Bearer ${KC_TOKEN}" \
    -H "Content-Type: application/json" \
    "${KC_URL}/admin/realms" \
    -d @infra/keycloak/realm-export-latest.json
```

## 6. Long-term

- Run Keycloak in HA mode (clustered with shared Postgres).
- Configure Keycloak health checks in compose and Kubernetes.
- Consider falling back to JWT-only auth (no Keycloak dependency) for critical paths.
