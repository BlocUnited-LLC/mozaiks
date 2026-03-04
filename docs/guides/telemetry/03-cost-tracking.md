# Cost Tracking

Track how much your AI workflows cost with automatic token-to-cost calculation.

---

!!! tip "New to Development?"

    **Let AI show you cost data!** Copy this prompt into Claude Code:

    ```
    Show me how to view and query cost data in my Mozaiks app.

    Please read: docs/instruction-prompts/telemetry/03-cost-tracking.md
    ```

---

## Quick Start

```python
from mozaiksai.core.observability import calculate_cost

# After an LLM call
result = calculate_cost(
    prompt_tokens=1500,
    completion_tokens=500,
    model_name="gpt-4o"
)

print(f"Input cost: ${result.input_cost_usd:.4f}")   # $0.0075
print(f"Output cost: ${result.output_cost_usd:.4f}") # $0.0075
print(f"Total cost: ${result.total_cost_usd:.4f}")   # $0.0150
```

---

## How Cost Calculation Works

1. **Token Capture** — AG2's `RealtimeTokenLogger` captures token counts from every LLM call
2. **Pricing Lookup** — Model name is matched against pricing data
3. **Cost Calculation** — Input and output costs computed separately
4. **Event Emission** — `chat.cost_calculated` event emitted for platform consumption
5. **Persistence** — Costs stored in MongoDB `ChatSessions` collection

---

## Built-in Model Pricing

Default pricing (as of early 2026):

| Model | Input (per 1K tokens) | Output (per 1K tokens) |
|-------|----------------------|------------------------|
| gpt-4o | $0.005 | $0.015 |
| gpt-4o-mini | $0.00015 | $0.0006 |
| gpt-4-turbo | $0.01 | $0.03 |
| claude-3-opus | $0.015 | $0.075 |
| claude-3.5-sonnet | $0.003 | $0.015 |
| claude-3-haiku | $0.00025 | $0.00125 |

---

## Custom Pricing Configuration

For self-hosted LLMs, fine-tuned models, or Azure deployments, configure custom pricing.

### 1. Copy the Template

```bash
cp config/model_pricing.example.json config/model_pricing.json
```

### 2. Add Your Models

```json
{
  "models": {
    "my-local-llama": {
      "input_cost_per_1k": 0.0,
      "output_cost_per_1k": 0.0,
      "context_window": 8192,
      "provider": "local"
    },
    "my-finetuned-gpt4": {
      "input_cost_per_1k": 0.012,
      "output_cost_per_1k": 0.036,
      "context_window": 8192,
      "provider": "openai-finetuned"
    },
    "azure-gpt4-deployment": {
      "input_cost_per_1k": 0.005,
      "output_cost_per_1k": 0.015,
      "context_window": 128000,
      "provider": "azure"
    }
  }
}
```

### 3. Enable It

```bash
# .env
MODEL_PRICING_JSON=./config/model_pricing.json
```

**Notes:**

- Self-hosted models can use `0.0` costs
- Fine-tuned OpenAI models typically cost 3x the base model
- Model names must match what AG2 sees (check `RealtimeTokenLogger` output)

---

## Cost Events

Every LLM call emits a `chat.cost_calculated` event:

```json
{
  "event_type": "chat.cost_calculated",
  "payload": {
    "chat_id": "chat_abc123",
    "app_id": "app_xyz",
    "user_id": "user_456",
    "workflow_name": "customer_support",
    "agent_name": "support_agent",
    "model_name": "gpt-4o",
    "prompt_tokens": 1500,
    "completion_tokens": 500,
    "total_tokens": 2000,
    "input_cost_usd": 0.0075,
    "output_cost_usd": 0.0075,
    "total_cost_usd": 0.015,
    "timestamp": "2026-03-01T10:30:00Z"
  }
}
```

### Listen for Cost Events

```python
from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher

async def on_cost_calculated(event_type: str, payload: dict):
    print(f"Chat {payload['chat_id']} spent ${payload['total_cost_usd']:.4f}")

dispatcher = get_event_dispatcher()
dispatcher.subscribe("chat.cost_calculated", on_cost_calculated)
```

---

## MongoDB Cost Reports

### Total Costs by User

```javascript
db.ChatSessions.aggregate([
  { $match: { app_id: "your_app_id" } },
  { $group: {
      _id: "$user_id",
      total_prompt_tokens: { $sum: "$metrics.total_prompt_tokens" },
      total_completion_tokens: { $sum: "$metrics.total_completion_tokens" },
      total_cost_usd: { $sum: "$metrics.total_cost_usd" },
      chat_count: { $sum: 1 }
  }},
  { $sort: { total_cost_usd: -1 } }
])
```

### Daily Costs by Workflow

```javascript
db.ChatSessions.aggregate([
  { $match: {
      app_id: "your_app_id",
      created_at: { $gte: ISODate("2026-03-01") }
  }},
  { $group: {
      _id: {
        day: { $dateToString: { format: "%Y-%m-%d", date: "$created_at" } },
        workflow: "$workflow_name"
      },
      total_cost: { $sum: "$metrics.total_cost_usd" },
      sessions: { $sum: 1 }
  }},
  { $sort: { "_id.day": -1 } }
])
```

### Average Cost per Conversation

```javascript
db.ChatSessions.aggregate([
  { $match: { app_id: "your_app_id" } },
  { $group: {
      _id: "$workflow_name",
      avg_cost: { $avg: "$metrics.total_cost_usd" },
      avg_tokens: { $avg: "$metrics.total_tokens" },
      sessions: { $sum: 1 }
  }}
])
```

### Most Expensive Conversations

```javascript
db.ChatSessions.find(
  { app_id: "your_app_id" },
  {
    chat_id: 1,
    user_id: 1,
    workflow_name: 1,
    "metrics.total_cost_usd": 1,
    "metrics.total_tokens": 1
  }
).sort({ "metrics.total_cost_usd": -1 }).limit(10)
```

---

## Troubleshooting

### Costs Showing Zero

1. **Check model name** — Must match pricing data exactly
   ```python
   from mozaiksai.core.observability.cost_tracker import DEFAULT_MODEL_PRICING
   print(DEFAULT_MODEL_PRICING.keys())  # Available models
   ```

2. **Check tokens are captured** — Look at `RealtimeTokenLogger` output

3. **Check cost tracking enabled**
   ```bash
   echo $COST_TRACKING_ENABLED  # Should not be "false"
   ```

### Unknown Model

If your model isn't in default pricing:
1. Create `model_pricing.json` with your model
2. Set `MODEL_PRICING_JSON` environment variable
3. Restart the app

### Costs Not Persisted

1. Check MongoDB connection
2. Verify `ChatSessions` collection exists
3. Check logs for persistence errors

---

## Next Steps

- [Budget Management](04-budget-management.md) — Set spending limits based on costs
