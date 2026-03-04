# Agent Tracing

OpenTelemetry tracing lets you see exactly what your agents are doing — every conversation, LLM call, and tool execution.

---

!!! tip "New to Development?"

    **Let AI configure trace export!** Copy this prompt into Claude Code:

    ```
    Configure where my Mozaiks traces are exported.

    Please read: docs/instruction-prompts/telemetry/02-agent-tracing.md

    I want to export to: [console / Jaeger / Grafana / Datadog]
    ```

---

## Quick Start

### Enable Console Tracing (Development)

```bash
export AG2_OTEL_ENABLED=true
export AG2_OTEL_EXPORTER=console
export AG2_OTEL_SERVICE_NAME=my-app
```

```python
from mozaiksai.core.observability import initialize_otel_tracing

# Call once at app startup
initialize_otel_tracing()
```

---

## What Gets Traced

MozaiksCore integrates with AG2's OpenTelemetry module to capture:

| Span Type | Description | Key Attributes |
|-----------|-------------|----------------|
| `conversation` | Full conversation lifecycle | `ag2.agent`, `mozaiks.chat_id` |
| `agent` | Individual agent invocation | `ag2.agent_name`, `duration` |
| `llm` | LLM API call | `model`, `tokens`, `cost` |
| `tool` | Tool/function execution | `tool_name`, `args`, `result` |
| `code_execution` | Code interpreter runs | `agent`, `code_hash` |
| `speaker_selection` | GroupChat speaker selection | `selected_agent` |

---

## Configuration Options

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `AG2_OTEL_ENABLED` | `false` | Enable OTEL tracing |
| `AG2_OTEL_SERVICE_NAME` | `mozaiks-runtime` | Service name in traces |
| `AG2_OTEL_EXPORTER` | `console` | `console`, `otlp`, or `both` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTLP collector endpoint |
| `OTEL_EXPORTER_OTLP_HEADERS` | (none) | Auth headers (e.g., `api-key=xxx`) |
| `AG2_OTEL_CAPTURE_MESSAGES` | `false` | Log full message content |

!!! warning "Security Note"
    `AG2_OTEL_CAPTURE_MESSAGES=true` logs full conversation content. Only enable in development or secure environments.

---

## Instrumenting Workflows

### Instrument AG2 Patterns

For GroupChat workflows, instrument the entire pattern:

```python
from mozaiksai.core.observability import instrument_pattern
from mozaiksai.core.workflow.execution.patterns import create_ag2_pattern

# Create your AG2 pattern
pattern = create_ag2_pattern(
    pattern_name="AutoPattern",
    initial_agent=coordinator,
    agents=[agent1, agent2, agent3],
    context_variables=ctx_vars,
)

# Instrument it (captures all agents + speaker selection)
instrument_pattern(pattern)
```

### Add Workflow Context

Wrap workflow execution with Mozaiks context:

```python
from mozaiksai.core.observability import traced_workflow

async def run_workflow(chat_id: str, app_id: str, user_id: str):
    with traced_workflow(
        workflow_name="customer_support",
        chat_id=chat_id,
        app_id=app_id,
        user_id=user_id,
    ):
        # All traces inside this block include mozaiks.* attributes
        result = await pattern.run(task="Help the customer")
        return result
```

This adds `mozaiks.chat_id`, `mozaiks.app_id`, and `mozaiks.user_id` to all spans within the block.

---

## Exporting to Backends

### Jaeger (Local Development)

```bash
# Run Jaeger with OTLP support
docker run -d --name jaeger \
  -p 16686:16686 \
  -p 4317:4317 \
  jaegertracing/all-in-one:latest

# Configure MozaiksCore
export AG2_OTEL_ENABLED=true
export AG2_OTEL_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
```

View traces at http://localhost:16686

### Grafana Cloud / Tempo

```bash
export AG2_OTEL_ENABLED=true
export AG2_OTEL_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_ENDPOINT=https://tempo-us-central1.grafana.net:443
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic YOUR_ENCODED_CREDENTIALS"
```

### Datadog

```bash
export AG2_OTEL_ENABLED=true
export AG2_OTEL_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317  # Datadog Agent

# Or direct to Datadog:
# export OTEL_EXPORTER_OTLP_ENDPOINT=https://trace.agent.datadoghq.com
# export OTEL_EXPORTER_OTLP_HEADERS="DD-API-KEY=your_api_key"
```

---

## Reading Trace Output

### Console Output Example

```
Span: conversation
  - ag2.agent: "coordinator"
  - mozaiks.chat_id: "chat_abc123"
  - duration: 4.2s

  └── Span: agent (coordinator)
      - ag2.agent_name: "coordinator"
      - duration: 1.1s

      └── Span: llm
          - model: "gpt-4o"
          - prompt_tokens: 1500
          - completion_tokens: 200
          - duration: 0.8s

  └── Span: speaker_selection
      - selected_agent: "order_agent"

  └── Span: agent (order_agent)
      └── Span: tool
          - tool_name: "get_order_status"
          - duration: 0.3s
```

### Jaeger UI

In Jaeger, you can:
- Search by service name
- Filter by `mozaiks.chat_id` to find specific conversations
- See duration breakdown by span
- Identify slow LLM calls or tools

---

## Troubleshooting

### Traces Not Appearing

1. Verify environment variable:
   ```bash
   echo $AG2_OTEL_ENABLED  # Should be "true"
   ```

2. Check initialization in logs:
   ```
   [INFO] OTEL tracing initialized (exporter=console)
   ```

3. Verify OTLP endpoint is reachable:
   ```bash
   curl http://localhost:4317
   ```

4. Check AG2 tracing is installed:
   ```bash
   pip install 'ag2[tracing]'
   ```

### Missing Attributes

If `mozaiks.*` attributes are missing:
- Ensure `traced_workflow()` context manager wraps your code
- Check the workflow code is running inside the context block

### Performance Impact

OTEL tracing adds minimal overhead (~1-5ms per span). For high-throughput scenarios:
- Use batch exporters (default in OTLP)
- Sample traces in production: set `OTEL_TRACES_SAMPLER=parentbased_traceidratio` and `OTEL_TRACES_SAMPLER_ARG=0.1` (10% sampling)

---

## Next Steps

- [Cost Tracking](03-cost-tracking.md) — Track how much workflows cost
- [Budget Management](04-budget-management.md) — Set spending limits
