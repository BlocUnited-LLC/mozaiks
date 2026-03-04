# Keycloak & PostgreSQL

Keycloak handles authentication. PostgreSQL is its internal database — you never touch it directly.

---

## What PostgreSQL Stores

Keycloak's internal schema (~100 tables):

| Table Group | Contents |
|-------------|----------|
| `user_entity`, `credential` | User accounts, passwords, MFA |
| `realm`, `client` | Realm config, OIDC clients |
| `user_session` | Active login sessions |
| `user_role_mapping` | Role assignments |
| `event_entity` | Audit log |

**You don't manage this.** Keycloak handles it automatically.

---

## Configuration

PostgreSQL runs inside Docker as `keycloak-db`. Not exposed on host — only Keycloak can reach it.

In `.env`:

```dotenv
KC_DB_PASSWORD=keycloak
KC_ADMIN_PASSWORD=admin
```

!!! warning "Production"
    Change default passwords:
    ```dotenv
    KC_DB_PASSWORD=<strong-random-password>
    KC_ADMIN_PASSWORD=<strong-random-password>
    ```

---

## Keycloak Admin Console

After container is healthy, open [http://localhost:8080/admin](http://localhost:8080/admin):

- **Username:** `admin`
- **Password:** `admin` (or `KC_ADMIN_PASSWORD`)

From here you can:
- Create/edit users
- Assign roles
- Configure social login
- Enable MFA
- View audit logs

---

## Pre-Configured Realm

On first boot, Keycloak imports `infra/keycloak/realm-export.json`:

| Item | Value |
|------|-------|
| Realm | `mozaiks` |
| Client | `mozaiks-app` (public, PKCE) |
| Default roles | `user`, `admin` |
| Test user | `dev` / `dev` |
| Self-registration | Enabled |

---

## Customizing the Realm

**Option 1:** Edit in Admin Console
- Changes persist in PostgreSQL volume

**Option 2:** Edit realm-export.json and reset
```bash
docker compose -f infra/compose/docker-compose.yml down -v
docker compose -f infra/compose/docker-compose.yml up -d
```

---

## Troubleshooting

??? question "Keycloak shows 'unhealthy' for a long time"
    Normal on first boot (30-60 seconds). Check logs:
    ```bash
    docker compose -f infra/compose/docker-compose.yml logs keycloak -f
    ```
    Wait for "Running the server in development mode".

??? question "How do I connect to Postgres directly?"
    For debugging only:
    ```bash
    docker exec -it mozaiksai-keycloak-db psql -U keycloak keycloak
    ```

??? question "Can I use MySQL/MariaDB instead?"
    Yes. Change `KC_DB`, `KC_DB_URL`, `KC_DB_USERNAME`, `KC_DB_PASSWORD` in docker-compose.yml. See [Keycloak docs](https://www.keycloak.org/server/db).
