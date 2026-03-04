# Instruction Prompt: View Cost Data

**Task:** Show user how to view and query cost data

**Complexity:** Low (cost tracking is already enabled)

---

## Context for AI Agent

Cost tracking is **enabled by default** in Mozaiks. The user doesn't need to enable anything — they just need to know how to VIEW the data.

Show them:
1. How to calculate costs programmatically
2. How to query cost data in MongoDB
3. How to add custom pricing (only if using non-standard models)

---

## Step 1: Verify Cost Tracking Works

```python
from mozaiksai.core.observability import calculate_cost

result = calculate_cost(
    prompt_tokens=1000,
    completion_tokens=500,
    model_name="gpt-4o"
)

print(f"Input: ${result.input_cost_usd:.4f}")   # $0.0050
print(f"Output: ${result.output_cost_usd:.4f}") # $0.0075
print(f"Total: ${result.total_cost_usd:.4f}")   # $0.0125
```

---

## Step 2: Query Costs in MongoDB

### Total Costs by User

```javascript
db.ChatSessions.aggregate([
  { $match: { app_id: "your_app_id" } },
  { $group: {
      _id: "$user_id",
      total_cost: { $sum: "$metrics.total_cost_usd" },
      total_tokens: { $sum: "$metrics.total_tokens" },
      conversations: { $sum: 1 }
  }},
  { $sort: { total_cost: -1 } }
])
```

### Daily Costs

```javascript
db.ChatSessions.aggregate([
  { $match: { app_id: "your_app_id" } },
  { $group: {
      _id: { $dateToString: { format: "%Y-%m-%d", date: "$created_at" } },
      total_cost: { $sum: "$metrics.total_cost_usd" },
      conversations: { $sum: 1 }
  }},
  { $sort: { _id: -1 } }
])
```

### Costs by Workflow

```javascript
db.ChatSessions.aggregate([
  { $match: { app_id: "your_app_id" } },
  { $group: {
      _id: "$workflow_name",
      avg_cost: { $avg: "$metrics.total_cost_usd" },
      total_cost: { $sum: "$metrics.total_cost_usd" },
      conversations: { $sum: 1 }
  }}
])
```

### Most Expensive Conversations

```javascript
db.ChatSessions.find(
  { app_id: "your_app_id" },
  { chat_id: 1, user_id: 1, "metrics.total_cost_usd": 1 }
).sort({ "metrics.total_cost_usd": -1 }).limit(10)
```

---

## Step 3: Custom Pricing (Only If Needed)

Default pricing covers: gpt-4o, gpt-4o-mini, gpt-4-turbo, claude-3-opus, claude-3.5-sonnet, claude-3-haiku

If using other models, create `model_pricing.json`:

```json
{
  "models": {
    "my-custom-model": {
      "input_cost_per_1k": 0.001,
      "output_cost_per_1k": 0.002,
      "context_window": 32000,
      "provider": "custom"
    }
  }
}
```

Add to `.env`:
```bash
MODEL_PRICING_JSON=/path/to/model_pricing.json
```

---

## Summary

```markdown
## Cost Data Access

### Verified
- [ ] calculate_cost() returns expected values
- [ ] MongoDB queries return data

### Custom Pricing
- Needed: [yes/no]
- File: [path if yes]
```
