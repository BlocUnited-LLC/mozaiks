# Instruction Prompt: Configure Trace Export

**Task:** Configure where OpenTelemetry traces are exported

**Complexity:** Low (environment variables + optional Docker)

---

## Context for AI Agent

The user wants to configure WHERE their traces go. Options:
- **Console** — Print to stdout (development)
- **Jaeger** — Local visualization UI
- **Grafana/Tempo** — Cloud observability
- **Datadog** — Cloud APM

Ask which one they want, then configure it.

---

## Step 1: Ask User

**"Where do you want to export traces?"**
- Console (just print them, good for development)
- Jaeger (local web UI at localhost:16686)
- Grafana Cloud
- Datadog

---

## Step 2: Configure Based on Choice

### Console (Development)

```bash
# .env
AG2_OTEL_ENABLED=true
AG2_OTEL_EXPORTER=console
AG2_OTEL_SERVICE_NAME=my-app
```

Done. Traces print to stdout.

### Jaeger (Local Visualization)

```bash
# Start Jaeger
docker run -d --name jaeger \
  -p 16686:16686 \
  -p 4317:4317 \
  jaegertracing/all-in-one:latest
```

```bash
# .env
AG2_OTEL_ENABLED=true
AG2_OTEL_EXPORTER=otlp
AG2_OTEL_SERVICE_NAME=my-app
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
```

View at: http://localhost:16686

### Grafana Cloud

```bash
# .env
AG2_OTEL_ENABLED=true
AG2_OTEL_EXPORTER=otlp
AG2_OTEL_SERVICE_NAME=my-app
OTEL_EXPORTER_OTLP_ENDPOINT=https://tempo-us-central1.grafana.net:443
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic [base64_instance_id:api_key]
```

Get credentials from Grafana Cloud dashboard → Tempo → Configure.

### Datadog

```bash
# .env (with Datadog Agent running locally)
AG2_OTEL_ENABLED=true
AG2_OTEL_EXPORTER=otlp
AG2_OTEL_SERVICE_NAME=my-app
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
```

---

## Step 3: Add Initialization

Ensure startup code has:

```python
from mozaiksai.core.observability import initialize_otel_tracing
initialize_otel_tracing()
```

---

## Step 4: Verify

- **Console:** Look for "Span:" output when running workflows
- **Jaeger:** Open http://localhost:16686, search by service name
- **Grafana/Datadog:** Check their respective dashboards

---

## Summary

```markdown
## Trace Export Configured

### Settings
- Exporter: [console/Jaeger/Grafana/Datadog]
- Endpoint: [URL if OTLP]
- Service name: [name]

### Verified
- [ ] Traces appear in [destination]
```
