# Instruction Prompt: Configure Telemetry

**Task:** Configure telemetry settings for a Mozaiks app

**Complexity:** Low (environment variables)

---

## Context for AI Agent

Mozaiks has telemetry built-in:
- **Cost tracking** — Enabled by default, tracks token costs automatically
- **Agent tracing** — Requires `AG2_OTEL_ENABLED=true` to activate

Your job is to help the user configure WHERE traces go and verify everything is working.

---

## Step 1: Check Current Configuration

Check their `.env` file for these variables:

```bash
# Tracing (optional - disabled by default)
AG2_OTEL_ENABLED=true/false
AG2_OTEL_EXPORTER=console/otlp
AG2_OTEL_SERVICE_NAME=app-name

# Cost tracking (enabled by default, no config needed)
# COST_TRACKING_ENABLED=true  # Only set if they want to disable
```

---

## Step 2: Configure Based on Needs

### If user wants to see traces:

Add to `.env`:
```bash
AG2_OTEL_ENABLED=true
AG2_OTEL_EXPORTER=console  # or otlp for Jaeger/Grafana
AG2_OTEL_SERVICE_NAME=my-app
```

Add to startup code:
```python
from mozaiksai.core.observability import initialize_otel_tracing
initialize_otel_tracing()
```

### If user wants to export to Jaeger/Grafana:

See [02-agent-tracing.md](02-agent-tracing.md) for detailed backend setup.

### If user wants to query cost data:

Cost tracking is already enabled. Show them the MongoDB queries in [03-cost-tracking.md](03-cost-tracking.md).

---

## Step 3: Verify

```python
# Test tracing (if enabled)
# Should see "Span:" output in console

# Test cost tracking
from mozaiksai.core.observability import calculate_cost
result = calculate_cost(prompt_tokens=1000, completion_tokens=500, model_name="gpt-4o")
print(f"Cost: ${result.total_cost_usd:.4f}")  # Should print ~$0.0125
```

---

## Summary

```markdown
## Telemetry Configuration

### Tracing
- Enabled: [yes/no]
- Exporter: [console/otlp/disabled]
- Service name: [name]

### Cost Tracking
- Enabled: yes (default)
- Custom pricing: [yes/no]

### Verified
- [ ] Traces appear (if enabled)
- [ ] calculate_cost() returns expected values
```
