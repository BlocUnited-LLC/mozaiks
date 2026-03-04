# Production & Backup

Checklist and procedures for production deployments.

---

## Production Checklist

### Environment Variables

| Setting | Dev Default | Production |
|---------|-------------|------------|
| `MONGO_URI` | `localhost:27017` | MongoDB Atlas or managed |
| `KC_DB_PASSWORD` | `keycloak` | Strong random password |
| `KC_ADMIN_PASSWORD` | `admin` | Strong random password |
| `KC_HOSTNAME` | `localhost` | Your auth domain |
| `AUTH_ENABLED` | `true` | `true` (never disable) |
| `MOZAIKS_OIDC_AUTHORITY` | `http://localhost:8080/...` | `https://auth.yourapp.com/...` |

---

## MongoDB Production

- [ ] Use MongoDB Atlas or managed service
- [ ] Enable authentication (username/password in URI)
- [ ] Enable TLS/SSL connections
- [ ] Set up automated backups
- [ ] Configure replica set for high availability
- [ ] Create appropriate indexes (runtime creates basics automatically)

---

## PostgreSQL Production

- [ ] Use strong `KC_DB_PASSWORD`
- [ ] Consider managed PostgreSQL (AWS RDS, Azure, etc.)
- [ ] Set up automated backups
- [ ] Monitor disk usage (event log grows)

---

## Backup & Restore

### MongoDB

```bash
# Backup
mongodump --uri="mongodb://localhost:27017" --db=MozaiksAI --out=./backup

# Restore
mongorestore --uri="mongodb://localhost:27017" --db=MozaiksAI ./backup/MozaiksAI
```

Docker volume backup:
```bash
docker run --rm -v mozaiksai_mongo_data:/data -v $(pwd):/backup alpine \
  tar czf /backup/mongo-backup.tar.gz /data
```

### PostgreSQL (Keycloak)

```bash
# Backup
docker exec mozaiksai-keycloak-db \
  pg_dump -U keycloak keycloak > keycloak-backup.sql

# Restore
cat keycloak-backup.sql | docker exec -i mozaiksai-keycloak-db \
  psql -U keycloak keycloak
```

---

## Data Lifecycle

### `docker compose down`

```bash
# Stops containers, PRESERVES data
docker compose -f infra/compose/docker-compose.yml down

# Stops containers AND DELETES all data
docker compose -f infra/compose/docker-compose.yml down -v
```

| Command | MongoDB | Keycloak Users | Keycloak Realm |
|---------|---------|----------------|----------------|
| `down` | Preserved | Preserved | Preserved |
| `down -v` | **Deleted** | **Deleted** | Re-imported |

### Factory Reset

```bash
docker compose -f infra/compose/docker-compose.yml down -v
docker compose -f infra/compose/docker-compose.yml up -d
```

Wipes all chat history, users (except `dev` test user), and workflow state.

!!! warning "Always backup before `down -v`"

---

## Troubleshooting

??? question "Deleted Docker volumes — is my data gone?"
    MongoDB data and Keycloak users are gone. Realm config re-imports from `realm-export.json` on next start.

??? question "Can I use SQLite instead of Postgres?"
    For development only. Keycloak's `start-dev` mode can use embedded H2 (remove `KC_DB*` vars). **Not for production** — H2 doesn't support clustering.
