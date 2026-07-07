# Backup and Recovery

Procedures for backing up Mozaiks data and recovering from failures.

---

## What to Back Up

| Data | Location | Criticality | Backup Frequency |
|------|----------|-------------|-----------------|
| Chat sessions + history | MongoDB `mozaiks_apps` | Critical | Daily + continuous oplog |
| Audit log | MongoDB `mozaiks_audit` | Critical | Daily |
| Generated artifacts | MongoDB or object store | High | Daily |
| Feature flags | MongoDB `mozaiks_apps.feature_flags` | Medium | Weekly |
| Keycloak realm | Keycloak + Postgres | High | Daily |
| Application config | `app/` bundle files | Medium | Git-based (committed) |

---

## MongoDB Backup

### Using mongodump (point-in-time)

```bash
#!/bin/bash
# infra/scripts/backup-mongo.sh
set -euo pipefail

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/backups/mongo/${TIMESTAMP}"
S3_BUCKET="${BACKUP_S3_BUCKET:-mozaiks-backups}"
S3_PREFIX="mongodb"
MONGO_URI="${MONGO_URI:?MONGO_URI must be set}"

echo "Starting MongoDB backup at ${TIMESTAMP}"
mkdir -p "${BACKUP_DIR}"

# Dump all databases
mongodump --uri="${MONGO_URI}" --out="${BACKUP_DIR}" --gzip

# Upload to S3
aws s3 sync "${BACKUP_DIR}" "s3://${S3_BUCKET}/${S3_PREFIX}/${TIMESTAMP}/" \
    --storage-class STANDARD_IA

# Clean up local files older than 7 days
find /backups/mongo -maxdepth 1 -type d -mtime +7 -exec rm -rf {} +

echo "Backup complete: s3://${S3_BUCKET}/${S3_PREFIX}/${TIMESTAMP}/"
```

Schedule with cron (daily at 3 AM):
```bash
0 3 * * * /infra/scripts/backup-mongo.sh >> /var/log/mozaiks-backup.log 2>&1
```

### Using MongoDB Atlas (managed)

If using MongoDB Atlas, enable:
- **Continuous Cloud Backup** (point-in-time recovery, 1-hour resolution)
- **Scheduled Snapshots** (daily retention: 7 days, weekly: 4 weeks)

### Restore Procedure

```bash
# Restore from mongodump backup
mongorestore --uri="${MONGO_URI}" --gzip --drop /backups/mongo/<TIMESTAMP>/

# Verify restore
mongo "${MONGO_URI}" --eval 'db.adminCommand({listDatabases: 1})'
```

---

## Keycloak Backup

```bash
#!/bin/bash
# Export realm configuration
REALM_NAME="${KEYCLOAK_REALM:-mozaiks}"
KC_URL="${KEYCLOAK_URL:-http://localhost:8080}"
KC_TOKEN=$(curl -s -X POST "${KC_URL}/realms/master/protocol/openid-connect/token" \
    -d "grant_type=password&client_id=admin-cli&username=${KC_ADMIN}&password=${KC_ADMIN_PASSWORD}" \
    | jq -r '.access_token')

curl -s -H "Authorization: Bearer ${KC_TOKEN}" \
    "${KC_URL}/admin/realms/${REALM_NAME}/partial-export?exportClients=true&exportGroupsAndRoles=true" \
    > "infra/keycloak/realm-export-$(date +%Y%m%d).json"
```

Commit realm exports to git after each significant change:
```bash
git add infra/keycloak/realm-export-*.json
git commit -m "chore: update Keycloak realm export $(date +%Y-%m-%d)"
```

---

## Object Store Backup (if using S3ArtifactStore)

S3 artifact backups via cross-region replication:
```bash
# Enable versioning on the bucket
aws s3api put-bucket-versioning \
    --bucket mozaiks-artifacts \
    --versioning-configuration Status=Enabled

# Enable cross-region replication
aws s3api put-bucket-replication \
    --bucket mozaiks-artifacts \
    --replication-configuration file://infra/s3-replication.json
```

---

## Recovery Testing

Run monthly recovery tests:

```bash
#!/bin/bash
# Test restore from most recent backup
LATEST_BACKUP=$(aws s3 ls s3://${BACKUP_S3_BUCKET}/mongodb/ | sort | tail -1 | awk '{print $2}')
aws s3 sync "s3://${BACKUP_S3_BUCKET}/mongodb/${LATEST_BACKUP}" /tmp/restore-test/

mongorestore --uri="${TEST_MONGO_URI}" --gzip --drop /tmp/restore-test/

# Verify critical collections
mongo "${TEST_MONGO_URI}" --eval '
  print("Sessions:", db.getSiblingDB("mozaiks_apps").chat_sessions.countDocuments());
  print("Audit logs:", db.getSiblingDB("mozaiks_audit").audit_log.countDocuments());
'

echo "Recovery test completed"
```

Log recovery test results and store with the backup manifest.

---

## RTO / RPO Targets

| Scenario | Target RTO | Target RPO |
|----------|-----------|-----------|
| Single instance failure | < 2 min (auto-restart) | 0 (stateless) |
| MongoDB primary failure | < 5 min (replica set failover) | < 1s (oplog) |
| Full MongoDB cluster failure | < 30 min | < 24h (daily snapshot) |
| Complete data center failure | < 2 hours | < 1h (continuous backup) |
