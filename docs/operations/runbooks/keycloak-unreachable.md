# Runbook: Keycloak Unreachable

Authenticated requests may fail when the configured issuer or its JWKS endpoint
is unavailable. Keep authentication enabled while diagnosing the outage. Do not
make private app data publicly accessible as a recovery step.

## Identify

From the repository root, inspect the local containers:

```bash
docker compose --env-file .env -f infra/compose/docker-compose.yml ps
docker compose --env-file .env -f infra/compose/docker-compose.yml logs --tail 50 keycloak
docker inspect mozaiksai-keycloak --format '{{.State.Health.Status}}'
```

Test the configured issuer's discovery URL separately, for example
`http://localhost:8080/realms/mozaiks/.well-known/openid-configuration` locally.
A reachable discovery endpoint does not by itself establish container readiness.

## Health Configuration

Compose enables `KC_HEALTH_ENABLED` and checks `/health/ready` on the internal
management port 9000. This port is not published to the host. Checking that path
on the public authentication port 8080 is not the configured readiness probe.

The stock image must apply build-time health settings at startup, so the supplied
production Compose file does not use `--optimized`. An operator-built optimized
image must bake those settings into its image build before using that flag.
See [Keycloak health checks](https://www.keycloak.org/observability/health).

After changing health settings, recreate only Keycloak using the same Compose
project name and environment file used for the running stack:

```bash
docker compose --env-file .env -f infra/compose/docker-compose.yml up -d --no-deps keycloak
docker inspect mozaiksai-keycloak --format '{{.State.Health.Status}}'
```

This preserves the existing Postgres volume. Do not run `down -v` or delete the
identity database as a health-check repair. No cloud service is required.

## Authentication Recovery

Once Keycloak is healthy, verify discovery, the expected issuer and client,
then a real sign-in. Restart the local application only if its configuration
changed or its cached discovery/JWKS state requires it. A shell assignment
followed by `docker restart` does not change an existing container's environment.

If identity data was lost, restore the Postgres backup under the operator's
recovery procedure. The checked-in realm seed is not a backup of users, client
credentials, or live realm settings. Do not replace an existing realm with it.
