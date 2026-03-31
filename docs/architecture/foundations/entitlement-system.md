# Entitlement System

**Status:** Implemented
**Owner:** Platform Team
**Last Updated:** 2026-03-19

## Overview

The **entitlement system** is an event-driven, declarative approach to subscription-based access control. It separates pricing/billing configuration from runtime access gating, allowing modules and workflows to declare their requirements independently.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                       Request Flow                               │
└─────────────────────────────────────────────────────────────────┘

User Request
     │
     ├─> Route Handler (e.g., /modules/lineup_board/data)
     │
     ├─> check_entitlement(resource_type="module",
     │                      resource_name="lineup_board",
     │                      user_id=user["user_id"])
     │
     ├─> EntitlementChecker.check()
     │       │
     │       ├─> Emit: entitlement.check (DomainEvent)
     │       │
     │       ├─> Load: subscription.yaml (declarative requirements)
     │       │       ├─> platform/modules/lineup_board/subscription.yaml
     │       │       └─> requires: basic
     │       │
     │       ├─> Get: User subscription status (MongoDB)
     │       │       └─> SubscriptionManager.get_user_subscription()
     │       │
     │       ├─> Evaluate: Tier hierarchy + trial status
     │       │
     │       └─> Emit: entitlement.granted | entitlement.denied
     │               └─> Logged to admin dashboard
     │
     └─> Return: True (granted) | False (denied)
             │
             └─> If denied: raise HTTPException(403, "Upgrade required")
```

## Components

### 1. Domain Events

**`mozaikscore/core/entitlements/events.py`**

Three core events:

- **`EntitlementCheckEvent`**: Emitted when access evaluation is needed
- **`EntitlementGrantedEvent`**: Emitted when access is allowed
- **`EntitlementDeniedEvent`**: Emitted when access is blocked (includes upgrade path)

All events inherit from `DomainEvent` and are routed through the unified event bus.

### 2. Declarative Requirements

**`platform/{modules|workflows}/{name}/subscription.yaml`**

Each module or workflow can declare its entitlement requirements:

```yaml
# Simple case: just require a minimum tier
requires: premium

# Advanced case: per-action gating + usage limits
requires: basic
allow_trial: true

actions:
  start: basic
  resume: premium

limits:
  free:
    max_sessions_per_day: 0
  basic:
    max_sessions_per_day: 10
  premium:
    max_sessions_per_day: 100
```

**Missing `subscription.yaml`**: Defaults to open access (no gating).

### 3. Entitlement Checker

**`mozaikscore/core/entitlements/checker.py`**

Core evaluation logic:

1. Load declarative requirements from `subscription.yaml`
2. Get user's subscription status from `SubscriptionManager`
3. Evaluate tier hierarchy:
   ```python
   TIER_LEVELS = {
       "free": 0,
       "basic": 1,
       "premium": 2,
       "unlimited": 99,
   }
   ```
4. Check trial status (if applicable)
5. Emit granted/denied events
6. Return `EntitlementResult`

### 4. Subscription Config

**`platform/config/subscription_config.json`**

Defines pricing, billing cycles, and tier metadata:

```json
{
  "settings": {
    "trial_period_days": 14,
    "trial_plan": "premium"
  },
  "subscription_plans": [
    {
      "name": "free",
      "display_name": "Free",
      "price": 0,
      "billing_cycle": "monthly",
      "tier_level": 0
    },
    {
      "name": "premium",
      "price": 19.99,
      "tier_level": 2
    }
  ]
}
```

**No more `modules_unlocked`**: Access control is now declarative via `subscription.yaml`.

## Usage

### In Routes

Replace direct `subscription_manager.is_module_accessible()` calls:

```python
from mozaikscore.core.entitlements import check_entitlement
from fastapi import HTTPException

@router.get("/modules/{module_name}/data")
async def get_module_data(
    module_name: str,
    user: dict = Depends(get_current_user)
):
    # Event-driven entitlement check
    if not await check_entitlement(
        resource_type="module",
        resource_name=module_name,
        user_id=user["user_id"]
    ):
        raise HTTPException(
            status_code=403,
            detail="Upgrade to access this module"
        )

    # ... rest of logic
```

### In Workflows

Check entitlement before starting a workflow:

```python
from mozaikscore.core.entitlements import check_entitlement

async def start_workflow(workflow_name: str, user_id: str):
    # Check if user can start this workflow
    if not await check_entitlement(
        resource_type="workflow",
        resource_name=workflow_name,
        user_id=user_id,
        action="start"
    ):
        return {
            "error": "subscription_required",
            "message": "Upgrade to premium to start this workflow"
        }

    # ... start workflow
```

## Tier Hierarchy

The system uses numeric tier levels for comparison:

| Plan Name  | Tier Level | Description                    |
|------------|------------|--------------------------------|
| free       | 0          | Base tier, limited access      |
| basic      | 1          | Mid tier, most modules         |
| premium    | 2          | Full access, all workflows     |
| unlimited  | 99         | Admin/special tier             |

**Evaluation Rule**: `user_tier_level >= required_tier_level`

Example: A user on `basic` (level 1) can access resources requiring `free` (level 0) or `basic` (level 1), but not `premium` (level 2).

## Trial Handling

Trial users are granted elevated access for a limited period:

1. User starts trial: `status = "trialing"`, `plan = "premium"`
2. Entitlement checks pass if `requirements.allow_trial = true`
3. Trial expires: `status = "inactive"`, `plan = "free"`
4. Future checks are evaluated against `free` tier

**Override**: Resources can disable trial access by setting `allow_trial: false`.

## Observability

All entitlement decisions are logged as domain events:

### Admin Dashboard

**`/__mozaiks/admin/events/history`**

Shows:
- `entitlement.check` events (who requested what)
- `entitlement.granted` events (successful access)
- `entitlement.denied` events (blocked access with reason)

### Audit Trail

Every denied access includes:
- `user_id`: Who was denied
- `resource_type` + `resource_name`: What they tried to access
- `reason`: Why (e.g., `subscription:insufficient`)
- `required_plan`: Minimum plan needed
- `user_plan`: Current user plan

## Migration from `modules_unlocked`

### Old Model (subscription_config.json)

```json
{
  "subscription_plans": [
    {
      "name": "basic",
      "modules_unlocked": ["lineup_board", "show_archive"]
    }
  ]
}
```

**Problems:**
- Modules can't declare their own requirements
- Centralized config becomes bottleneck
- Hard to add per-action or usage-based gating

### New Model (declarative)

**`platform/config/subscription_config.json`** (pricing only):
```json
{
  "subscription_plans": [
    {
      "name": "basic",
      "price": 9.99,
      "tier_level": 1
    }
  ]
}
```

**`platform/modules/lineup_board/subscription.yaml`** (access control):
```yaml
requires: basic
```

**Benefits:**
- Modules self-declare requirements
- Decoupled from pricing config
- Supports complex rules (actions, limits)
- Event-driven (observable, auditable)

## Build-Time Validation

Optional validation during CI/CD:

```python
from mozaikscore.core.entitlements.loader import validate_all_subscription_yamls

result = validate_all_subscription_yamls()
if result["errors"]:
    print("Subscription YAML validation failed:")
    for error in result["errors"]:
        print(f"  - {error}")
    sys.exit(1)
```

Checks:
- YAML syntax errors
- Referenced plans exist in `subscription_config.json`
- Tier hierarchy is consistent

## Extension Points

### Custom Entitlement Logic

Subscribe to `entitlement.check` events for custom rules:

```python
from mozaikscore.core.event_bus import subscribe
from mozaikscore.core.entitlements import EntitlementCheckEvent

@subscribe("entitlement.check")
async def custom_entitlement_rule(event: EntitlementCheckEvent):
    # Example: Block access during maintenance windows
    if is_maintenance_window():
        raise HTTPException(503, "Maintenance in progress")

    # Example: Grant access if user completed onboarding
    if await user_completed_onboarding(event.user_id):
        # Override standard tier check
        await emit_event(EntitlementGrantedEvent(
            resource_type=event.resource_type,
            resource_name=event.resource_name,
            user_id=event.user_id,
            granted_by="onboarding:complete",
            user_plan="custom"
        ))
        return True
```

### Usage-Based Limits

Check limits in `subscription.yaml`:

```yaml
limits:
  basic:
    max_sessions_per_day: 10
```

Track usage in entitlement checker:

```python
# In EntitlementChecker.check()
if requirements.limits:
    user_usage = await get_user_usage(user_id, resource_name)
    limit = requirements.limits.get(user_plan)
    if limit and user_usage >= limit.max_sessions_per_day:
        return EntitlementResult(
            granted=False,
            reason="limit:exceeded",
            metadata={"usage": user_usage, "limit": limit}
        )
```

## Testing

### Unit Tests

```python
from mozaikscore.core.entitlements import check_entitlement

async def test_basic_user_can_access_basic_module():
    # Mock subscription manager
    with mock_subscription(user_id="u1", plan="basic"):
        result = await check_entitlement(
            resource_type="module",
            resource_name="lineup_board",  # requires: basic
            user_id="u1"
        )
        assert result is True

async def test_free_user_cannot_access_premium_workflow():
    with mock_subscription(user_id="u2", plan="free"):
        result = await check_entitlement(
            resource_type="workflow",
            resource_name="GreenRoom",  # requires: premium
            user_id="u2"
        )
        assert result is False
```

### Integration Tests

Test full flow with event emissions:

```python
async def test_denied_event_emitted():
    events_captured = []

    @subscribe("entitlement.denied")
    async def capture_denied(event):
        events_captured.append(event)

    with mock_subscription(user_id="u3", plan="free"):
        await check_entitlement("module", "lineup_board", "u3")

    assert len(events_captured) == 1
    assert events_captured[0].required_plan == "basic"
```

## Related Systems

- **Event Bus** ([event-system-architecture.md](event-system-architecture.md)): Routes entitlement events
- **Subscription Manager** (`mozaikscore/core/subscription_manager.py`): Provides user subscription data
- **Admin Dashboard** (`platform/pages/admin/`): Displays entitlement events
- **Domain Events** ([runtime-state-and-control-events.md](runtime-state-and-control-events.md)): Event taxonomy

## Future Enhancements

1. **Metered Billing**: Emit `usage.metered` events for token-based pricing
2. **Feature Flags**: Integrate with feature flag system for A/B testing entitlements
3. **Time-Based Access**: Allow temporary grants (e.g., "access until 2026-04-01")
4. **Team Plans**: Support org-level subscriptions with role-based access
5. **Entitlement Caching**: Cache evaluation results per-user for performance

## References

- AG2 Custom Events: https://docs.ag2.ai/latest/docs/use-cases/notebooks/notebooks/agentchat_custom_events/
- Event-Driven Architecture: [event-system-architecture.md](event-system-architecture.md)
- Subscription Config: `platform/config/subscription_config.json`
- Code: `mozaikscore/core/entitlements/`
