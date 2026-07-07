# Runbook: Disk Full

**Symptom:** `OSError: [Errno 28] No space left on device` in logs; instance may crash.

---

## 1. Immediate: Free Space

```bash
# Check disk usage
df -h /

# Find large files
du -sh /logs/* 2>/dev/null | sort -rh | head -20
du -sh /var/log/* 2>/dev/null | sort -rh | head -10

# Compress old logs immediately
find /logs -name "mozaiks.log.*" -mtime +1 -exec gzip {} \;

# Remove logs older than 30 days
find /logs -name "*.log.gz" -mtime +30 -delete

# If AG2 runtime logs are large
find /logs -name "ag2_runtime.log*" -mtime +7 -delete
```

## 2. Verify Instance Recovery

```bash
# Restart instance if it crashed
docker restart mozaiks_instance_1

# Verify health
curl http://<instance>/api/health/readiness
```

## 3. Prevent Recurrence

Configure log rotation in environment (already set in production logging):
```bash
# logs/logging_config.py: setup_production_logging()
# maxBytes=50*1024*1024 (50MB), backupCount=10
# This is already configured — check if LOG_LEVEL is set too verbose
```

Reduce log verbosity:
```bash
LOG_LEVEL=WARNING  # Reduce from INFO
LOGS_AS_JSON=false # JSON logs are larger
```

## 4. Add Disk Alert

Add to alerting config:
```yaml
- alert: MozaiksDiskSpaceLow
  expr: node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"} < 0.15
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Disk space < 15% on {{ $labels.instance }}"
```
