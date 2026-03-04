# Telemetry Overview

MozaiksCore includes built-in telemetry for tracking, costing, and managing your AI workflows.

---

## What's Included

| Feature | Purpose |
|---------|---------|
| **Agent Tracing** | OpenTelemetry spans for every conversation, agent turn, and LLM call |
| **Cost Tracking** | Automatic token-to-cost calculation with configurable pricing |
| **Budget Management** | Patterns for implementing spending limits and subscription tiers |

---

!!! tip "New to Development?"

    **Let AI configure telemetry!** Copy this prompt into Claude Code:

    ```
    Configure telemetry for my Mozaiks app.

    Please read: docs/instruction-prompts/telemetry/01-overview.md
    ```

---

## Quick Start

### Enable OpenTelemetry Tracing

```bash
# Environment variables
export AG2_OTEL_ENABLED=true
export AG2_OTEL_EXPORTER=console  # or "otlp" for production
export AG2_OTEL_SERVICE_NAME=my-app
```

```python
from mozaiksai.core.observability import initialize_otel_tracing

# Initialize once at startup
initialize_otel_tracing()
```

### Calculate Costs

```python
from mozaiksai.core.observability import calculate_cost

result = calculate_cost(
    prompt_tokens=1500,
    completion_tokens=500,
    model_name="gpt-4o"
)

print(f"Total cost: ${result.total_cost_usd:.4f}")
# Output: Total cost: $0.0150
```

### Track Budgets

```python
from mozaiksai.core.observability import get_budget_tracker

tracker = get_budget_tracker()

# Set a $10/month budget
tracker.configure_budget(
    app_id="my_app",
    user_id="user_123",
    budget_type="monthly",
    limit_usd=10.00,
    warning_threshold_pct=0.8,
)
```

---

## Guide Sections

| Section | What You'll Learn |
|---------|-------------------|
| [Agent Tracing](02-agent-tracing.md) | OpenTelemetry setup, span types, exporting to Jaeger/Grafana |
| [Cost Tracking](03-cost-tracking.md) | Token-to-cost calculation, pricing configuration, MongoDB queries |
| [Budget Management](04-budget-management.md) | Spending limits, subscription tiers, event-driven enforcement |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Your Workflow                           │
│   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐   │
│   │   Agent 1   │───▶│   Agent 2   │───▶│   Agent 3   │   │
│   └─────────────┘    └─────────────┘    └─────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    MozaiksCore Runtime                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ OTEL Tracing │  │ Cost Tracker │  │ Token Logger │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
          │                  │                  │
          ▼                  ▼                  ▼
    ┌──────────┐       ┌──────────┐       ┌──────────┐
    │  Jaeger  │       │  Events  │       │ MongoDB  │
    │ Grafana  │       │  System  │       │          │
    └──────────┘       └──────────┘       └──────────┘
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AG2_OTEL_ENABLED` | `false` | Enable OpenTelemetry tracing |
| `AG2_OTEL_EXPORTER` | `console` | `console`, `otlp`, or `both` |
| `AG2_OTEL_SERVICE_NAME` | `mozaiks-runtime` | Service name in traces |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTLP collector endpoint |
| `COST_TRACKING_ENABLED` | `true` | Enable cost calculation |
| `MODEL_PRICING_JSON` | (built-in) | Path to custom pricing file |

---

## Event Types

The telemetry system emits these events:

| Event | When |
|-------|------|
| `chat.usage_delta` | After each LLM call with token counts |
| `chat.cost_calculated` | After cost is computed for an LLM call |
| `budget.warning` | When usage exceeds warning threshold (default 80%) |
| `budget.exceeded` | When usage exceeds 100% of budget |

---

## Next Steps

Start with [Agent Tracing](02-agent-tracing.md) to see what your AI is doing.
