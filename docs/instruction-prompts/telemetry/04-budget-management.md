# Instruction Prompt: Build Subscription System

**Task:** Set up budget management with subscription tiers

**Complexity:** Medium (code implementation + event handling)

---

## Context for AI Agent

The user wants to build a subscription system. This is an **intent-based** request — they have a specific goal (monetization, user limits, etc.). Help them:

1. Define subscription tiers (Free, Pro, Enterprise)
2. Set up budget limits and enforcement
3. Handle events (warnings, exceeded limits)
4. **Keep it modular** — if they later use mozaiks-platform, the system should integrate seamlessly

**Key Principle:** The runtime provides **measurement and events**, the platform provides **enforcement and billing**. Design for this separation.

---

## Step 1: Understand Requirements

Ask the user:

1. **"What subscription tiers do you have?"**
   - Free (with limits)
   - Paid tiers (Pro, Enterprise, etc.)
   - Custom tiers

2. **"What are the limits for each tier?"**
   - Monthly cost limit (USD)
   - Monthly token limit
   - Rate limit (requests per minute)

3. **"What should happen when limits are reached?"**
   - Warn user at 80%
   - Block workflows at 100%
   - Send upgrade prompts
   - Notify admins

---

## Step 2: Define Subscription Tiers

Create a configuration:

```python
# config/subscription_tiers.py
SUBSCRIPTION_TIERS = {
    "free": {
        "monthly_cost_limit_usd": [free_cost_limit],
        "monthly_token_limit": [free_token_limit],
        "rate_limit_per_min": [free_rate_limit],
        "soft_limit": False,  # Block at limit
    },
    "pro": {
        "monthly_cost_limit_usd": [pro_cost_limit],
        "monthly_token_limit": [pro_token_limit],
        "rate_limit_per_min": [pro_rate_limit],
        "soft_limit": False,
    },
    "enterprise": {
        "monthly_cost_limit_usd": float("inf"),  # Unlimited
        "monthly_token_limit": float("inf"),
        "rate_limit_per_min": [enterprise_rate_limit],
        "soft_limit": True,  # Warn but don't block
    },
}
```

Example values:
```python
SUBSCRIPTION_TIERS = {
    "free": {
        "monthly_cost_limit_usd": 1.00,
        "monthly_token_limit": 100_000,
        "rate_limit_per_min": 10,
        "soft_limit": False,
    },
    "pro": {
        "monthly_cost_limit_usd": 20.00,
        "monthly_token_limit": 1_000_000,
        "rate_limit_per_min": 60,
        "soft_limit": False,
    },
    "enterprise": {
        "monthly_cost_limit_usd": float("inf"),
        "monthly_token_limit": float("inf"),
        "rate_limit_per_min": 300,
        "soft_limit": True,
    },
}
```

---

## Step 3: Set Up Budget Tracking

### Configure User Budgets

```python
from mozaiksai.core.observability import get_budget_tracker
from config.subscription_tiers import SUBSCRIPTION_TIERS

async def setup_user_budget(user_id: str, app_id: str, tier: str):
    """Configure budget for a user based on their tier."""
    if tier not in SUBSCRIPTION_TIERS:
        raise ValueError(f"Unknown tier: {tier}")

    limits = SUBSCRIPTION_TIERS[tier]
    tracker = get_budget_tracker()

    tracker.configure_budget(
        app_id=app_id,
        user_id=user_id,
        budget_type="monthly",
        limit_usd=limits["monthly_cost_limit_usd"],
        warning_threshold_pct=0.8,
        soft_limit=limits.get("soft_limit", False),
    )
```

### Record Spending

```python
from mozaiksai.core.observability import calculate_cost, get_budget_tracker

async def record_llm_spend(
    app_id: str,
    user_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    model_name: str,
):
    """Record spending after an LLM call."""
    # Calculate cost
    cost = calculate_cost(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        model_name=model_name,
    )

    # Record to budget tracker
    tracker = get_budget_tracker()
    status = await tracker.record_spend(
        app_id=app_id,
        user_id=user_id,
        cost_usd=cost.total_cost_usd,
        budget_type="monthly",
    )

    return status
```

---

## Step 4: Set Up Event Handlers

### Register Event Listeners

```python
# In shared_app.py or startup code
from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher

async def on_budget_warning(event_type: str, payload: dict):
    """Handle 80% budget warning."""
    user_id = payload["user_id"]
    app_id = payload["app_id"]
    percent = payload["usage_percent"]
    remaining = payload["remaining_usd"]

    # Send warning notification
    await send_notification(
        user_id=user_id,
        title="Budget Warning",
        message=f"You've used {percent:.0f}% of your monthly budget. ${remaining:.2f} remaining.",
        type="warning"
    )

async def on_budget_exceeded(event_type: str, payload: dict):
    """Handle budget exceeded."""
    user_id = payload["user_id"]
    app_id = payload["app_id"]
    tier = await get_user_tier(user_id)

    if SUBSCRIPTION_TIERS[tier].get("soft_limit"):
        # Enterprise - just notify, don't block
        await notify_admin(f"Enterprise user {user_id} exceeded budget")
    else:
        # Free/Pro - pause workflows
        await pause_user_workflows(app_id, user_id)
        await send_upgrade_prompt(user_id, tier)

# Register at startup
def setup_budget_events():
    dispatcher = get_event_dispatcher()
    dispatcher.subscribe("budget.warning", on_budget_warning)
    dispatcher.subscribe("budget.exceeded", on_budget_exceeded)
```

### Helper Functions

```python
async def pause_user_workflows(app_id: str, user_id: str):
    """Mark user as paused in database."""
    await db.Users.update_one(
        {"app_id": app_id, "user_id": user_id},
        {
            "$set": {
                "workflows_paused": True,
                "paused_reason": "budget_exceeded",
                "paused_at": datetime.now(UTC),
            }
        }
    )

async def send_upgrade_prompt(user_id: str, current_tier: str):
    """Send upgrade suggestion to user."""
    next_tier = {
        "free": "pro",
        "pro": "enterprise",
    }.get(current_tier)

    if next_tier:
        await send_notification(
            user_id=user_id,
            title="Upgrade Your Plan",
            message=f"You've reached your {current_tier} limit. Upgrade to {next_tier} for more!",
            action_url="/upgrade",
            action_text="View Plans"
        )
```

---

## Step 5: Enforce Limits in Workflow Runner

```python
async def run_workflow(
    app_id: str,
    user_id: str,
    workflow_name: str,
    **kwargs
):
    """Run a workflow with budget enforcement."""
    # Check if user is paused
    user = await db.Users.find_one({"app_id": app_id, "user_id": user_id})

    if user and user.get("workflows_paused"):
        reason = user.get("paused_reason", "unknown")
        raise WorkflowPausedError(
            f"Workflows paused: {reason}. Please upgrade your plan."
        )

    # Check remaining budget
    tracker = get_budget_tracker()
    status = await tracker.get_status(app_id, user_id, "monthly")

    tier = user.get("tier", "free")
    if not SUBSCRIPTION_TIERS[tier].get("soft_limit") and status.is_exceeded:
        raise BudgetExceededError(
            f"Monthly budget exceeded. Spent ${status.spent_usd:.2f} of ${status.limit_usd:.2f}"
        )

    # Run the workflow
    result = await execute_workflow(workflow_name, **kwargs)
    return result
```

---

## Step 6: Monthly Reset (For Production)

```python
# Scheduled job - run on 1st of each month
async def reset_monthly_budgets():
    """Reset budgets for new month."""
    # The built-in tracker resets automatically with budget_type="monthly"
    # For MongoDB-backed tracker, archive old records:

    current_period = datetime.now(UTC).strftime("%Y-%m")

    # Archive old usage records
    await db.UsageRecords.update_many(
        {"period": {"$ne": current_period}, "archived": {"$ne": True}},
        {"$set": {"archived": True, "archived_at": datetime.now(UTC)}}
    )

    # Unpause users who were paused due to budget
    await db.Users.update_many(
        {"workflows_paused": True, "paused_reason": "budget_exceeded"},
        {
            "$set": {
                "workflows_paused": False,
                "paused_reason": None,
                "unpaused_at": datetime.now(UTC),
            }
        }
    )

    print(f"Monthly budget reset complete for period {current_period}")
```

---

## Step 7: Summary Template

```markdown
## Budget Management Configured

### Subscription Tiers
| Tier | Cost Limit | Token Limit | Enforcement |
|------|-----------|-------------|-------------|
| Free | $[X]/month | [X] tokens | Hard block |
| Pro | $[X]/month | [X] tokens | Hard block |
| Enterprise | Unlimited | Unlimited | Soft (notify only) |

### Event Handlers
- ✅ budget.warning → Send notification at 80%
- ✅ budget.exceeded → Pause workflows, send upgrade prompt

### Files Created/Modified
- ✅ `config/subscription_tiers.py` — Tier definitions
- ✅ `shared_app.py` — Event listeners registered
- ✅ `workflow_runner.py` — Budget enforcement added

### Verification
- [ ] Configure test user with $1 budget
- [ ] Trigger warning at 80%
- [ ] Trigger exceeded at 100%
- [ ] Verify workflow blocks after exceeded
- [ ] Verify monthly reset unpauses users
```

---

## Troubleshooting

### "Events not firing"

1. Check event listener registered at startup
2. Verify budget is configured for user
3. Check dispatcher is initialized

### "User not being paused"

1. Check on_budget_exceeded handler is async
2. Verify database update succeeds
3. Check tier has soft_limit=False

### "Budget not resetting"

1. Use budget_type="monthly" (not "total")
2. Schedule monthly reset job
3. Check database update in reset job

### "Wrong tier limits applied"

1. Verify user's tier is stored correctly
2. Check SUBSCRIPTION_TIERS has correct values
3. Call setup_user_budget when tier changes

---

## For Production

Replace the in-memory BudgetTracker with MongoDB-backed implementation:

```python
# See: docs/guides/telemetry/04-budget-management.md
# Section: "Production Budget Implementation"
```

Key differences:
- Persists across restarts
- Handles multi-instance deployments
- Atomic increment operations
- Historical data for reporting

---

## Custom Model Pricing

For self-hosted LLMs, fine-tuned models, or Azure deployments:

### 1. Create pricing config

```bash
cp config/model_pricing.example.json config/model_pricing.json
```

### 2. Edit for your models

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
    }
  }
}
```

### 3. Set environment variable

```bash
# .env
MODEL_PRICING_JSON=./config/model_pricing.json
```

---

## Personal Budget Monitoring

For **personal use** (avoiding API cost overruns):

```python
from mozaiksai.core.observability import get_budget_tracker

# Set a personal monthly budget ($10)
tracker = get_budget_tracker()
tracker.configure_budget(
    app_id="personal",
    user_id="me",
    budget_type="monthly",
    limit_usd=10.00,
    warning_threshold_pct=0.5,  # Warn at 50%
    soft_limit=True,  # Just warn, don't block
)

# Listen for warnings
async def my_budget_alert(event_type, status):
    if status.is_warning:
        print(f"Budget warning: ${status.spent_usd:.2f} / ${status.limit_usd:.2f}")
    if status.is_exceeded:
        print("BUDGET EXCEEDED - consider pausing!")

tracker.add_listener(my_budget_alert)
```

---

## Platform Integration Pattern

When users graduate to mozaiks-platform for hosted billing:

### Architecture

```
┌──────────────────┐    events     ┌───────────────────┐
│  MozaiksAI       │ ──────────►   │  mozaiks-platform │
│  Runtime         │               │  (billing layer)  │
│                  │               │                   │
│  • calculate_cost│               │  • enforce limits │
│  • emit events   │               │  • process payment│
│  • budget tracker│               │  • usage reports  │
└──────────────────┘               └───────────────────┘
```

### Event-Based Integration

```python
# Platform subscribes to runtime events
dispatcher = get_event_dispatcher()

# Cost events → Platform billing
dispatcher.subscribe("chat.cost_calculated", platform_record_usage)

# Budget events → Platform enforcement
dispatcher.subscribe("budget.warning", platform_send_warning)
dispatcher.subscribe("budget.exceeded", platform_enforce_limit)
```

### Platform Hooks (mozaiks-platform only)

```python
# In mozaiks-platform (not in this repo)
# platform_hooks.py

async def chat_prereqs(app_id: str, user_id: str) -> dict:
    """Called before each chat starts."""
    balance = await platform_db.get_balance(user_id)
    if balance <= 0:
        raise PaymentRequired("Please add credits to continue")
    return {"balance": balance}
```

### Migration Path

1. **Start with:** In-memory BudgetTracker (development)
2. **Self-host:** MongoDB-backed tracker (see Production section)
3. **Platform:** Subscribe mozaiks-platform to events (no core changes)

The runtime remains identical — only the **listeners** change.
