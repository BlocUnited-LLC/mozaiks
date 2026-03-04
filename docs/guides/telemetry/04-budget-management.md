# Budget Management & Subscriptions

Build a subscription system with spending limits and tier-based enforcement.

---

!!! tip "New to Development?"

    **Let AI build your subscription system!** Copy this prompt into Claude Code:

    ```
    Build a subscription system for my Mozaiks app.

    Please read the instruction prompt at:
    docs/instruction-prompts/telemetry/04-budget-management.md

    My subscription tiers:
    - Free: $[X]/month, [X] tokens
    - Pro: $[X]/month, [X] tokens
    - Enterprise: unlimited
    ```

---

## Quick Start

```python
from mozaiksai.core.observability import get_budget_tracker, calculate_cost

tracker = get_budget_tracker()

# Set a $10/month budget
tracker.configure_budget(
    app_id="my_app",
    user_id="user_123",
    budget_type="monthly",
    limit_usd=10.00,
    warning_threshold_pct=0.8,  # Warn at 80%
)

# After each LLM call, record the spend
cost = calculate_cost(prompt_tokens=1000, completion_tokens=200, model_name="gpt-4o")
status = await tracker.record_spend(
    app_id="my_app",
    user_id="user_123",
    cost_usd=cost.total_cost_usd,
    budget_type="monthly",
)

if status.is_warning:
    print(f"Warning: User at {status.usage_percent:.1f}% of budget")
if status.is_exceeded:
    print(f"Budget exceeded! ${status.spent_usd:.2f} of ${status.limit_usd:.2f}")
```

---

## Budget Events

The tracker emits events for platform consumption:

| Event Type | When Emitted |
|------------|--------------|
| `budget.warning` | Usage exceeds warning threshold (default 80%) |
| `budget.exceeded` | Usage exceeds 100% of budget |

### Event Payload

```json
{
  "event_type": "budget.exceeded",
  "payload": {
    "app_id": "my_app",
    "user_id": "user_123",
    "budget_type": "monthly",
    "limit_usd": 10.0,
    "spent_usd": 10.52,
    "remaining_usd": 0,
    "usage_percent": 105.2,
    "is_exceeded": true,
    "timestamp": "2026-03-01T15:45:00Z"
  }
}
```

### Listen for Budget Events

```python
from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher

async def on_budget_warning(event_type: str, payload: dict):
    user_id = payload["user_id"]
    percent = payload["usage_percent"]
    await send_notification(user_id, f"You've used {percent:.0f}% of your budget")

async def on_budget_exceeded(event_type: str, payload: dict):
    user_id = payload["user_id"]
    app_id = payload["app_id"]
    await pause_user_workflows(app_id, user_id)
    await send_upgrade_prompt(user_id)

dispatcher = get_event_dispatcher()
dispatcher.subscribe("budget.warning", on_budget_warning)
dispatcher.subscribe("budget.exceeded", on_budget_exceeded)
```

---

## Subscription Tier Patterns

### Define Tiers

```python
SUBSCRIPTION_TIERS = {
    "free": {
        "monthly_token_limit": 100_000,
        "monthly_cost_limit_usd": 1.00,
        "rate_limit_per_min": 10,
    },
    "pro": {
        "monthly_token_limit": 1_000_000,
        "monthly_cost_limit_usd": 20.00,
        "rate_limit_per_min": 60,
    },
    "enterprise": {
        "monthly_token_limit": float("inf"),
        "monthly_cost_limit_usd": float("inf"),
        "rate_limit_per_min": 300,
    },
}
```

### Configure Budget Based on Tier

```python
async def setup_user_budget(user_id: str, app_id: str, tier: str):
    limits = SUBSCRIPTION_TIERS[tier]
    tracker = get_budget_tracker()

    tracker.configure_budget(
        app_id=app_id,
        user_id=user_id,
        budget_type="monthly",
        limit_usd=limits["monthly_cost_limit_usd"],
        warning_threshold_pct=0.8,
        soft_limit=(tier == "enterprise"),  # Don't block enterprise
    )
```

### Check Before Running Workflow

```python
async def check_can_run(user_id: str, app_id: str, tier: str) -> bool:
    """Check if user can run a workflow based on their tier."""
    if tier == "enterprise":
        return True  # No limits for enterprise

    tracker = get_budget_tracker()
    status = await tracker.get_status(app_id, user_id, "monthly")

    limits = SUBSCRIPTION_TIERS[tier]
    if status.spent_usd >= limits["monthly_cost_limit_usd"]:
        return False

    return True
```

---

## Production Budget Implementation

The built-in `BudgetTracker` is in-memory (demo). For production, implement persistent storage:

### MongoDB-Backed Budget Tracker

```python
from datetime import datetime, UTC
from pymongo import ReturnDocument

class MongoDBBudgetTracker:
    def __init__(self, db):
        self.budgets = db["Budgets"]
        self.usage = db["UsageRecords"]

    def _current_period(self) -> str:
        """Get current monthly period key."""
        return datetime.now(UTC).strftime("%Y-%m")

    async def configure_budget(
        self,
        app_id: str,
        user_id: str,
        limit_usd: float,
        warning_threshold_pct: float = 0.8,
    ):
        """Set or update a user's budget."""
        await self.budgets.update_one(
            {"app_id": app_id, "user_id": user_id},
            {
                "$set": {
                    "limit_usd": limit_usd,
                    "warning_threshold_pct": warning_threshold_pct,
                    "updated_at": datetime.now(UTC),
                }
            },
            upsert=True,
        )

    async def record_spend(
        self,
        app_id: str,
        user_id: str,
        cost_usd: float,
    ):
        """Record spending and check against budget."""
        period = self._current_period()

        # Atomically increment usage
        result = await self.usage.find_one_and_update(
            {
                "app_id": app_id,
                "user_id": user_id,
                "period": period,
            },
            {
                "$inc": {"spent_usd": cost_usd},
                "$setOnInsert": {"period_start": datetime.now(UTC)},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )

        # Check against budget
        budget = await self.budgets.find_one({
            "app_id": app_id,
            "user_id": user_id,
        })

        if budget:
            spent = result["spent_usd"]
            limit = budget["limit_usd"]
            threshold = budget["warning_threshold_pct"]

            if spent >= limit:
                await self._emit_exceeded(app_id, user_id, spent, limit)
            elif spent >= limit * threshold:
                await self._emit_warning(app_id, user_id, spent, limit)

        return result

    async def get_status(self, app_id: str, user_id: str):
        """Get current budget status."""
        period = self._current_period()

        budget = await self.budgets.find_one({
            "app_id": app_id,
            "user_id": user_id,
        })

        usage = await self.usage.find_one({
            "app_id": app_id,
            "user_id": user_id,
            "period": period,
        })

        spent = usage["spent_usd"] if usage else 0.0
        limit = budget["limit_usd"] if budget else float("inf")

        return {
            "spent_usd": spent,
            "limit_usd": limit,
            "remaining_usd": max(0, limit - spent),
            "usage_percent": (spent / limit * 100) if limit > 0 else 0,
            "is_exceeded": spent >= limit,
        }
```

---

## Event-Driven Enforcement

### Pause Workflows on Budget Exceeded

```python
# In your event handler
async def on_budget_exceeded(event_type: str, payload: dict):
    app_id = payload["app_id"]
    user_id = payload["user_id"]

    # Mark user as paused in database
    await db.Users.update_one(
        {"app_id": app_id, "user_id": user_id},
        {"$set": {"workflows_paused": True, "paused_reason": "budget_exceeded"}}
    )

    # Send notification
    await notifications.send(
        user_id=user_id,
        title="Budget Exceeded",
        message="You've reached your monthly limit. Upgrade to continue.",
        action_url="/upgrade"
    )

# In your workflow runner
async def run_workflow(app_id: str, user_id: str, workflow_name: str):
    user = await db.Users.find_one({"app_id": app_id, "user_id": user_id})

    if user.get("workflows_paused"):
        raise BudgetExceededError("Workflows paused due to budget limit")

    # Continue with workflow...
```

### Reset Budgets Monthly

```python
# Scheduled job (e.g., first of each month)
async def reset_monthly_budgets():
    # Usage records are keyed by period, so old periods just stop being used
    # Optionally archive old records
    await db.UsageRecords.update_many(
        {"period": {"$lt": current_period()}},
        {"$set": {"archived": True}}
    )

    # Unpause users
    await db.Users.update_many(
        {"workflows_paused": True, "paused_reason": "budget_exceeded"},
        {"$set": {"workflows_paused": False, "paused_reason": None}}
    )
```

---

## Troubleshooting

### Budget Events Not Firing

1. Ensure budget is configured:
   ```python
   tracker = get_budget_tracker()
   tracker.configure_budget(app_id, user_id, ...)
   ```

2. Verify event dispatcher is initialized

3. Check subscriber is registered before events fire

### Budget Not Resetting

The built-in tracker is in-memory. For persistence:
1. Use `budget_type="monthly"` to key by period
2. Implement MongoDB-backed tracker (see above)
3. Schedule monthly reset job

### Users Not Being Paused

1. Check event listener is registered
2. Verify database update is working
3. Check workflow runner checks pause status

---

## Custom Model Pricing

For self-hosted LLMs, fine-tuned models, or Azure deployments, configure custom pricing.

### Create Pricing Config

```bash
cp config/model_pricing.example.json config/model_pricing.json
```

### Example Config

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

### Enable It

```bash
# .env
MODEL_PRICING_JSON=./config/model_pricing.json
```

---

## Personal Budget Monitoring

For personal use (avoiding API cost overruns without a full subscription system):

```python
from mozaiksai.core.observability import get_budget_tracker

# Set a $10/month personal budget
tracker = get_budget_tracker()
tracker.configure_budget(
    app_id="personal",
    user_id="me",
    budget_type="monthly",
    limit_usd=10.00,
    warning_threshold_pct=0.5,  # Warn at 50%
    soft_limit=True,  # Just warn, don't block
)

# Get notified when approaching limit
async def my_alert(event_type, status):
    if status.is_warning:
        print(f"Heads up: ${status.spent_usd:.2f} / ${status.limit_usd:.2f} used")

tracker.add_listener(my_alert)
```

---

## Platform Integration

MozaiksAI is designed for modular integration with billing platforms.

### Architecture

```
┌─────────────────────┐    events     ┌────────────────────┐
│  MozaiksAI Runtime  │ ───────────►  │  mozaiks-platform  │
│  (this repo)        │               │  (billing layer)   │
│                     │               │                    │
│  • calculate_cost() │               │  • enforce limits  │
│  • emit events      │               │  • process payment │
│  • budget tracking  │               │  • usage reports   │
└─────────────────────┘               └────────────────────┘
```

### Design Principles

1. **Measurement only** — Runtime calculates and emits, never enforces
2. **Event-driven** — Platform subscribes to `chat.cost_calculated`, `budget.warning`, `budget.exceeded`
3. **Platform hooks** — Enforcement via `platform_hooks.py` (optional)

### Event Subscription Pattern

```python
# In platform code (not in runtime)
from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher

async def platform_record_usage(event_type, payload):
    """Record usage in platform billing database."""
    await platform_db.record_usage(
        user_id=payload["user_id"],
        tokens=payload["total_tokens"],
        cost_usd=payload["total_cost_usd"],
    )

async def platform_enforce_limit(event_type, payload):
    """Enforce spending limits at platform level."""
    user = await platform_db.get_user(payload["user_id"])
    if not user.has_payment_method:
        await platform_api.pause_user(payload["user_id"])

dispatcher = get_event_dispatcher()
dispatcher.subscribe("chat.cost_calculated", platform_record_usage)
dispatcher.subscribe("budget.exceeded", platform_enforce_limit)
```

### Migration Path

| Stage | Budget Storage | Enforcement | Use Case |
|-------|---------------|-------------|----------|
| Development | In-memory | Manual checks | Testing |
| Self-hosted | MongoDB | Event handlers | Personal/small team |
| Platform | Platform DB | Platform hooks | SaaS product |

The **runtime code stays the same** — only the event listeners and storage change.

---

## Next Steps

- Add [custom model pricing](#custom-model-pricing) for your LLMs
- Set up [personal monitoring](#personal-budget-monitoring) for cost awareness
- [Integrate with a billing platform](#platform-integration) for monetization
