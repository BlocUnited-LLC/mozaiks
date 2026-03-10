# Token Management & Billing Architecture

**Overview:** MozaiksAI implements a real-time token tracking and billing system integrated with AG2's native usage summary lifecycle, supporting both free trial mode and production billing.

---

## Architecture Components

### Core Systems

**1. Token Tracking Pipeline:**
```
AG2 Agent LLM Call
    ↓ [tokens consumed]
AG2 Usage Summary (print_usage_summary/gather_usage_summary)
    ↓ [incremental/final totals]
RealtimeTokenLogger (observability/realtime_token_logger.py)
    ↓ [parse and extract usage]
PersistenceManager.update_session_metrics()
    ↓ [update WorkflowStats + ChatSessions]
PersistenceManager.debit_tokens()
    ↓ [atomic wallet deduction if not free trial]
Wallets Collection (Balance updated)
```

**2. Data Storage:**

| **Collection** | **Purpose** | **Key Fields** |
|----------------|-------------|----------------|
| **Wallets** | User token balances | `AppId`, `UserId`, `Balance`, `Transactions` |
| **ChatSessions** | Conversation transcripts + cumulative usage | `messages[]`, `usage_*_final` fields, `status` |
| **WorkflowStats** | Real-time metrics + rollup aggregates | Per-chat metrics, per-agent breakdown, rollup docs |

**3. Free Trial vs. Production:**

```python
# core/core_config.py
FREE_TRIAL_ENABLED = os.getenv("FREE_TRIAL_ENABLED", "true").lower() == "true"

# When FREE_TRIAL_ENABLED = true:
# - Tokens tracked and displayed
# - NO wallet debits
# - Usage metrics still recorded
# - Analytics/reporting fully functional

# When FREE_TRIAL_ENABLED = false:
# - Tokens tracked and displayed
# - Wallet debited immediately on usage
# - Insufficient balance raises "INSUFFICIENT_TOKENS" error
# - Real-time billing enforced
```

---

## Real-time Billing Flow

### Per-Agent Turn Billing

**When an agent responds with token usage:**

```python
# core/observability/performance_manager.py
await performance_manager.record_agent_turn(
    chat_id="chat_123",
    agent_name="planner",
    duration_sec=2.5,
    model="gpt-4",
    prompt_tokens=1000,
    completion_tokens=500,
    cost=0.05  # USD
)
```

**What happens internally:**

1. **Metrics Update** (`update_session_metrics`):
   ```python
   # Increment per-chat metrics in WorkflowStats rollup doc
   await stats_coll.update_one(
       {"_id": "mon_app456_support_triad"},
       {
           "$inc": {
               "chat_sessions.chat_123.prompt_tokens": 1000,
               "chat_sessions.chat_123.completion_tokens": 500,
               "chat_sessions.chat_123.total_tokens": 1500,
               "chat_sessions.chat_123.cost_total_usd": 0.05
           }
       }
   )
   
   # Also increment per-agent metrics dynamically
   await stats_coll.update_one(
       {"_id": "mon_app456_support_triad"},
       {
           "$inc": {
               "agents.planner.sessions.chat_123.prompt_tokens": 1000,
               "agents.planner.sessions.chat_123.completion_tokens": 500,
               # ... etc
           }
       }
   )
   
   # Mirror cumulative usage in ChatSessions doc
   await chat_coll.update_one(
       {"_id": "chat_123"},
       {
           "$inc": {
               "usage_prompt_tokens_final": 1000,
               "usage_completion_tokens_final": 500,
               "usage_total_tokens_final": 1500,
               "usage_total_cost_final": 0.05
           }
       }
   )
   ```

2. **Wallet Debit** (if `FREE_TRIAL_ENABLED = false`):
   ```python
   # core/data/persistence_manager.py
   async def debit_tokens(
       user_id: str,
       app_id: str,
       amount: int,  # total_tokens (1500)
       reason: str,
       strict: bool = True
   ) -> Optional[int]:
       # Atomic debit with balance check
       res = await wallets_collection.find_one_and_update(
           {
               "AppId": app_id,
               "UserId": user_id,
               "Balance": {"$gte": amount}  # ⭐ Ensure sufficient balance
           },
           {
               "$inc": {"Balance": -amount},  # ⭐ Deduct tokens
               "$set": {"UpdatedAt": datetime.now(UTC)}
           },
           return_document=ReturnDocument.AFTER
       )
       
       if res is None:
           if strict:
               raise ValueError("INSUFFICIENT_TOKENS")
           return None
       
       return res["Balance"]  # New balance after debit
   ```

3. **Result:**
   - ✅ Metrics updated in real-time (WorkflowStats + ChatSessions)
   - ✅ Wallet debited immediately (production mode)
   - ✅ Per-agent breakdown tracked dynamically
   - ✅ User sees updated balance on next API call

---

## AG2 Usage Summary Integration

MozaiksAI integrates with AG2's native usage tracking lifecycle for authoritative token accounting.

### AG2 Lifecycle Methods

**1. `print_usage_summary()` - Incremental Visibility:**

```python
# During workflow execution (per-turn or periodic)
if hasattr(agent, 'print_usage_summary'):
    agent.print_usage_summary()
    # Outputs to stdout (captured by AG2 runtime logging):
    # Agent 'planner':
    # - Total tokens: 1500 (prompt: 1000, completion: 500)
    # - Cost: $0.05
```

**Use case:** Real-time visibility into cumulative usage during long-running workflows.

**2. `gather_usage_summary()` - Final Reconciliation:**

```python
from autogen import gather_usage_summary

# At workflow completion
agent_list = [planner, executor, reviewer]
final_summary = gather_usage_summary(agent_list)

# Returns comprehensive usage across all agents:
# {
#   'total_cost': 0.15,
#   'usage_including_cached_inference': {
#     'total_cost': 0.15,
#     'gpt-4': {
#       'cost': 0.15,
#       'prompt_tokens': 3000,
#       'completion_tokens': 1500,
#       'total_tokens': 4500
#     }
#   }
# }
```

**Use case:** Authoritative final totals for billing reconciliation and audit.

### MozaiksAI Integration Pattern

```python
# core/workflow/orchestration_patterns.py

# Per-turn billing (incremental)
for speaker in chat_result.chat_history:
    if speaker == agent:
        # Extract usage from agent's internal tracking
        await perf_mgr.record_agent_turn(
            chat_id=chat_id,
            agent_name=agent.name,
            prompt_tokens=extracted_prompt,
            completion_tokens=extracted_completion,
            cost=calculated_cost,
            duration_sec=turn_duration
        )
        # ⭐ Immediate wallet debit happens here

# Final reconciliation (workflow completion)
try:
    final_summary = gather_usage_summary([planner, executor, reviewer])
    
    # Record any residual usage not captured in per-turn tracking
    await persistence_manager.update_session_metrics(
        chat_id=chat_id,
        # ... final totals from gather_usage_summary
    )
except Exception:
    logger.warning("gather_usage_summary not available (AG2 version compatibility)")
```

**Key Points:**
- ✅ **Incremental billing** prevents users from exceeding budget mid-workflow
- ✅ **Final reconciliation** ensures 100% accurate totals
- ✅ **AG2 native methods** provide authoritative usage data
- ✅ **Fallback handling** for AG2 version compatibility

---

## Wallet Management

### Wallet Document Structure

**Collection:** `MozaiksDB.Wallets` (VE uppercase schema)

```json
{
  "_id": ObjectId("..."),
  "AppId": "app_456",
  "UserId": "user_123",
  "Balance": 50000,  // Current token balance (integer)
  "Transactions": [
    {
      "amount": -1500,  // Negative = debit, Positive = credit
      "timestamp": "2025-10-02T14:30:00Z",
      "reason": "chat_session_usage",
      "meta": {
        "chat_id": "chat_123",
        "workflow": "support_triad"
      }
    }
  ],
  "CreatedAt": "2025-09-01T00:00:00Z",
  "UpdatedAt": "2025-10-02T14:30:00Z"
}
```

### Wallet Operations

**1. Get Balance:**

```python
balance = await persistence_manager.get_wallet_balance(
    user_id="user_123",
    app_id="app_456"
)
# Returns: 50000 (integer token count)
```

**2. Debit Tokens (Strict Mode - Default):**

```python
try:
    new_balance = await persistence_manager.debit_tokens(
        user_id="user_123",
        app_id="app_456",
        amount=1500,
        reason="agent_turn_usage",
        strict=True,  # Raise error if insufficient
        meta={"chat_id": "chat_123", "agent": "planner"}
    )
    print(f"New balance: {new_balance}")  # 48500
except ValueError as e:
    if str(e) == "INSUFFICIENT_TOKENS":
        # Handle insufficient balance
        await prompt_low_balance(chat_id, workflow_name, needed=1500, current=200)
```

**3. Debit Tokens (Lenient Mode):**

```python
new_balance = await persistence_manager.debit_tokens(
    user_id="user_123",
    app_id="app_456",
    amount=1500,
    reason="optional_feature",
    strict=False  # Return None if insufficient (no error)
)

if new_balance is None:
    print("Insufficient balance, skipping optional feature")
else:
    print(f"Feature activated, new balance: {new_balance}")
```

**4. Top-up Balance:**

```python
result = await persistence_manager.topup_tokens(
    user_id="user_123",
    app_id="app_456",
    amount=10000,
    reason="subscription_renewal"
)
# Returns: {"app_id": "app_456", "user_id": "user_123", "balance": 60000}
```

### Atomic Transaction Guarantees

**MongoDB Atomic Update:**

The `debit_tokens` operation uses `find_one_and_update` with a **conditional balance check** to ensure atomicity:

```python
# This operation is atomic - no race conditions
res = await wallets_collection.find_one_and_update(
    {
        "AppId": eid,
        "UserId": user_id,
        "Balance": {"$gte": amount}  # ⭐ Filter ensures sufficient balance
    },
    {
        "$inc": {"Balance": -amount},  # ⭐ Deduct in single operation
        "$set": {"UpdatedAt": datetime.now(UTC)}
    },
    return_document=ReturnDocument.AFTER
)

# If res is None, either:
# - Wallet doesn't exist, OR
# - Balance < amount (insufficient funds)
```

**Race Condition Protection:**

Even with concurrent requests, MongoDB's atomic update ensures:
- ✅ Balance never goes negative
- ✅ No double-spending
- ✅ Transactions are serialized at database level

---

## Metrics & Rollup Architecture

### WorkflowStats Collection Structure

**Three Logical Document Types:**

1. **Real-time Rollup Docs** (`mon_{app}_{workflow}`):
   - Single aggregated document per workflow
   - Updated live as agents respond
   - Used for dashboards and analytics

2. **Per-Session Metrics** (DEPRECATED - now embedded in rollup):
   - Previously: `metrics_{chat_id}` documents
   - Now: Embedded in rollup under `chat_sessions.{chat_id}`

3. **Normalized Event Rows** (DISABLED):
   - Per-event audit trail (intentionally disabled to reduce collection noise)
   - Replay/resume relies on `ChatSessions.messages[]`

### Rollup Document Structure

**Document ID:** `mon_{app_id}_{workflow_name}`

**Example:** `mon_app456_support_triad`

```json
{
  "_id": "mon_app456_support_triad",
  "app_id": "app_456",
  "workflow_name": "support_triad",
  "last_updated_at": "2025-10-02T14:30:00Z",
  
  "overall_avg": {
    "avg_duration_sec": 45.2,
    "avg_prompt_tokens": 2800,
    "avg_completion_tokens": 1200,
    "avg_total_tokens": 4000,
    "avg_cost_total_usd": 0.12
  },
  
  "chat_sessions": {
    "chat_123": {
      "duration_sec": 52.0,
      "prompt_tokens": 3000,
      "completion_tokens": 1500,
      "total_tokens": 4500,
      "cost_total_usd": 0.15,
      "last_event_ts": "2025-10-02T14:30:00Z"
    },
    "chat_124": {
      "duration_sec": 38.4,
      "prompt_tokens": 2600,
      "completion_tokens": 900,
      "total_tokens": 3500,
      "cost_total_usd": 0.09,
      "last_event_ts": "2025-10-02T15:00:00Z"
    }
  },
  
  "agents": {
    "planner": {
      "avg": {
        "avg_duration_sec": 15.0,
        "avg_prompt_tokens": 1000,
        "avg_completion_tokens": 400,
        "avg_total_tokens": 1400,
        "avg_cost_total_usd": 0.04
      },
      "sessions": {
        "chat_123": {
          "duration_sec": 16.5,
          "prompt_tokens": 1100,
          "completion_tokens": 450,
          "total_tokens": 1550,
          "cost_total_usd": 0.045
        }
      }
    },
    "executor": {
      "avg": { /* ... */ },
      "sessions": { /* ... */ }
    }
  }
}
```

### Dynamic Agent Discovery

**Agents are tracked automatically** as they respond - no pre-configuration needed:

```python
# First time "planner" agent responds:
await stats_coll.update_one(
    {"_id": summary_id, "agents.planner": {"$exists": False}},
    {
        "$set": {
            "agents.planner": {
                "avg": {
                    "avg_duration_sec": 0.0,
                    "avg_prompt_tokens": 0,
                    # ... all zeros
                },
                "sessions": {}
            }
        }
    }
)

# Then increment metrics:
await stats_coll.update_one(
    {"_id": summary_id},
    {
        "$inc": {
            "agents.planner.sessions.chat_123.prompt_tokens": 1000,
            # ... etc
        }
    }
)
```

**Benefits:**
- ✅ No agent registry needed
- ✅ Supports dynamic multi-agent workflows
- ✅ Works with auto-generated agents
- ✅ Scales to hundreds of agents per workflow

---

## Low Balance Handling

### Detection & User Notification

**1. Free Trial Mode (`FREE_TRIAL_ENABLED = true`):**

```python
# No balance checks or debits
# Optional: Display usage warnings at thresholds
warning_threshold = 10000  # tokens

if current_usage > warning_threshold:
    logger.warning(
        "free_trial_threshold",
        chat_id=chat_id,
        usage=current_usage,
        threshold=warning_threshold
    )
```

**2. Production Mode (`FREE_TRIAL_ENABLED = false`):**

```python
# Before expensive operation, check balance
balance = await persistence_manager.get_wallet_balance(user_id, app_id)
estimated_cost_tokens = 5000  # Estimate based on workflow

if balance < estimated_cost_tokens:
    # Prompt user via UI tool
    response = await prompt_low_balance(
        chat_id=chat_id,
        workflow_name=workflow_name,
        needed_tokens=estimated_cost_tokens,
        current_balance=balance
    )
    
    if response.get("action") == "top_up":
        # Redirect to billing/top-up flow
        return {"redirect": "/billing/top-up"}
    else:
        # Pause workflow
        await persistence_manager.pause_chat_session(
            chat_id=chat_id,
            reason="insufficient_balance"
        )
```

### Low Balance UI Prompt

**Helper Function:** `core/core_config.py::prompt_low_balance()`

```python
async def prompt_low_balance(
    chat_id: str,
    workflow_name: str,
    needed_tokens: int,
    current_balance: int
) -> dict:
    """Display inline UI prompt for insufficient balance."""
    return await use_ui_tool(
        tool_id="token_top_up_prompt",
        payload={
            "needed_tokens": needed_tokens,
            "current_balance": current_balance,
            "message": "Insufficient balance. Please add funds to continue.",
            "interaction_type": "top_up"
        },
        chat_id=chat_id,
        workflow_name=workflow_name,
        display="inline"
    )
```

**Frontend Response:**

```json
{
  "type": "chat.ui_tool",
  "data": {
    "tool_id": "token_top_up_prompt",
    "payload": {
      "needed_tokens": 5000,
      "current_balance": 1200,
      "message": "Insufficient balance. Please add funds to continue."
    },
    "component_type": "TokenTopUpPrompt"
  }
}
```

**User Actions:**
1. **Top-up** → Redirect to billing page
2. **Cancel** → Pause chat session with `pause_reason: "insufficient_balance"`
3. **Resume Later** → Save chat state, allow resume when balance restored

---

## Cost Calculation

### Token-to-Cost Conversion

**Model Pricing (Example - Update based on actual LLM pricing):**

```python
# core/workflow/llm_config.py or pricing module
MODEL_PRICING = {
    "gpt-4": {
        "prompt": 0.03 / 1000,      # $0.03 per 1K prompt tokens
        "completion": 0.06 / 1000   # $0.06 per 1K completion tokens
    },
    "gpt-4-turbo": {
        "prompt": 0.01 / 1000,
        "completion": 0.03 / 1000
    },
    "gpt-3.5-turbo": {
        "prompt": 0.0005 / 1000,
        "completion": 0.0015 / 1000
    },
    "claude-3-opus": {
        "prompt": 0.015 / 1000,
        "completion": 0.075 / 1000
    }
}

def calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate USD cost for token usage."""
    pricing = MODEL_PRICING.get(model, MODEL_PRICING["gpt-4"])  # Fallback to gpt-4
    
    prompt_cost = prompt_tokens * pricing["prompt"]
    completion_cost = completion_tokens * pricing["completion"]
    
    return round(prompt_cost + completion_cost, 6)  # Round to 6 decimals

# Example:
cost = calculate_cost("gpt-4", prompt_tokens=1000, completion_tokens=500)
# Returns: 0.06 (1000 * 0.00003 + 500 * 0.00006)
```

### AG2 Native Cost Extraction

AG2 provides cost information in usage summaries:

```python
from autogen import gather_usage_summary

final_summary = gather_usage_summary([agent1, agent2])

# Structure:
# {
#   'total_cost': 0.15,  # ⭐ Direct USD cost
#   'usage_including_cached_inference': {
#     'total_cost': 0.15,
#     'gpt-4': {
#       'cost': 0.15,
#       'prompt_tokens': 3000,
#       'completion_tokens': 1500
#     }
#   }
# }

# Extract cost directly:
total_cost_usd = final_summary.get("total_cost", 0.0)
```

---

## API Endpoints

### Token Balance Endpoints

**1. Get Current Balance:**

```http
GET /api/v1/tokens/balance

Headers:
  app_id: app_456
  user_id: user_123

Response:
{
  "balance": 50000,
  "app_id": "app_456",
  "user_id": "user_123"
}
```

**Implementation:** `shared_app.py::get_token_balance()`

---

**2. Consume Tokens (Manual Debit):**

```http
POST /api/v1/tokens/consume

Headers:
  app_id: app_456
  user_id: user_123

Body:
{
  "amount": 1500,
  "reason": "custom_operation",
  "meta": {
    "operation": "batch_processing",
    "items": 10
  }
}

Response:
{
  "success": true,
  "new_balance": 48500,
  "debited": 1500
}

Error (Insufficient Balance):
{
  "error": "INSUFFICIENT_TOKENS",
  "required": 1500,
  "available": 200
}
```

**Implementation:** `shared_app.py::consume_tokens()`

---

**3. Get Usage Analytics:**

```http
GET /api/v1/workflows/{workflow_name}/analytics

Headers:
  app_id: app_456

Response:
{
  "workflow_name": "support_triad",
  "app_id": "app_456",
  "overall_avg": {
    "avg_duration_sec": 45.2,
    "avg_prompt_tokens": 2800,
    "avg_completion_tokens": 1200,
    "avg_total_tokens": 4000,
    "avg_cost_total_usd": 0.12
  },
  "total_sessions": 25,
  "agents": {
    "planner": {
      "avg_prompt_tokens": 1000,
      "avg_completion_tokens": 400,
      "avg_cost_usd": 0.04
    }
  }
}
```

**Implementation:** Query `WorkflowStats` collection for rollup document.

---

## Testing & Validation

### Unit Test Examples

**1. Test Wallet Debit (Sufficient Balance):**

```python
async def test_debit_tokens_success():
    persistence = PersistenceManager()
    
    # Setup: User has 10000 tokens
    await persistence.topup_tokens("user_1", "app_1", 10000, "test_setup")
    
    # Debit 1500 tokens
    new_balance = await persistence.debit_tokens(
        "user_1", "app_1", 1500,
        reason="test_usage",
        strict=True
    )
    
    assert new_balance == 8500
```

**2. Test Wallet Debit (Insufficient Balance):**

```python
async def test_debit_tokens_insufficient():
    persistence = PersistenceManager()
    
    # Setup: User has 500 tokens
    await persistence.topup_tokens("user_1", "app_1", 500, "test_setup")
    
    # Try to debit 1500 tokens (insufficient)
    with pytest.raises(ValueError, match="INSUFFICIENT_TOKENS"):
        await persistence.debit_tokens(
            "user_1", "app_1", 1500,
            reason="test_usage",
            strict=True
        )
```

**3. Test Free Trial Mode:**

```python
async def test_free_trial_no_debit(monkeypatch):
    # Enable free trial
    monkeypatch.setenv("FREE_TRIAL_ENABLED", "true")
    
    persistence = PersistenceManager()
    
    # Record usage (should NOT debit wallet)
    await persistence.update_session_metrics(
        chat_id="chat_1",
        app_id="app_1",
        user_id="user_1",
        workflow_name="test_workflow",
        prompt_tokens=1000,
        completion_tokens=500,
        cost_usd=0.05
    )
    
    # Wallet balance should be unchanged (0 since no top-up)
    balance = await persistence.get_wallet_balance("user_1", "app_1")
    assert balance == 0
```

**4. Test Dynamic Agent Tracking:**

```python
async def test_dynamic_agent_discovery():
    persistence = AG2PersistenceManager()
    
    # Create chat session
    await persistence.create_chat_session(
        chat_id="chat_1",
        app_id="app_1",
        workflow_name="test_workflow",
        user_id="user_1"
    )
    
    # Record usage for "agent_A" (first time - should auto-create)
    await persistence.update_session_metrics(
        chat_id="chat_1",
        app_id="app_1",
        user_id="user_1",
        workflow_name="test_workflow",
        prompt_tokens=1000,
        completion_tokens=500,
        cost_usd=0.05,
        agent_name="agent_A"
    )
    
    # Verify rollup has agent_A
    stats_coll = await persistence._workflow_stats_coll()
    rollup = await stats_coll.find_one({"_id": "mon_app_1_test_workflow"})
    
    assert "agent_A" in rollup["agents"]
    assert rollup["agents"]["agent_A"]["sessions"]["chat_1"]["prompt_tokens"] == 1000
```

### Integration Test Checklist

✅ **Wallet Operations:**
- [ ] Get balance returns correct value
- [ ] Top-up increments balance correctly
- [ ] Debit decrements balance atomically
- [ ] Insufficient balance raises error (strict mode)
- [ ] Insufficient balance returns None (lenient mode)

✅ **Free Trial Mode:**
- [ ] Token usage tracked when FREE_TRIAL_ENABLED = true
- [ ] NO wallet debits when FREE_TRIAL_ENABLED = true
- [ ] Metrics recorded correctly in both modes

✅ **Real-time Metrics:**
- [ ] Per-turn usage updates WorkflowStats rollup
- [ ] Per-agent metrics tracked dynamically
- [ ] ChatSessions usage_*_final fields incremented
- [ ] Rollup document overall_avg computed correctly

✅ **AG2 Integration:**
- [ ] print_usage_summary outputs captured in logs
- [ ] gather_usage_summary provides final totals
- [ ] Final reconciliation updates metrics

✅ **Low Balance Handling:**
- [ ] Low balance UI prompt displayed
- [ ] Chat session paused on insufficient balance
- [ ] Resume works after top-up

---

## Performance Considerations

### Optimization Strategies

**1. Batch Metrics Updates:**

Instead of updating rollup on every single token:

```python
# Accumulate deltas in memory during workflow
usage_buffer = []

for turn in workflow:
    usage_buffer.append({
        "prompt_tokens": turn.prompt_tokens,
        "completion_tokens": turn.completion_tokens,
        "cost": turn.cost
    })

# Flush to database periodically (every N turns or time interval)
if len(usage_buffer) >= 10 or time_since_last_flush > 30:
    total_prompt = sum(u["prompt_tokens"] for u in usage_buffer)
    total_completion = sum(u["completion_tokens"] for u in usage_buffer)
    total_cost = sum(u["cost"] for u in usage_buffer)
    
    await persistence.update_session_metrics(
        # ... batch update with totals
    )
    usage_buffer.clear()
```

**2. Index Optimization:**

```javascript
// MongoDB indexes for fast wallet operations
db.Wallets.createIndex({ "AppId": 1, "UserId": 1 }, { unique: true })

// MongoDB indexes for fast rollup queries
db.WorkflowStats.createIndex({ "_id": 1 })  // Rollup doc retrieval
db.ChatSessions.createIndex({ "app_id": 1, "workflow_name": 1 })
```

**3. Rollup Computation:**

Rollup documents updated incrementally (not recomputed from scratch):

```python
# ✅ Efficient (incremental update)
await stats_coll.update_one(
    {"_id": rollup_id},
    {"$inc": {"chat_sessions.chat_123.prompt_tokens": 1000}}
)

# ❌ Inefficient (full recompute)
all_sessions = await chat_coll.find({"workflow_name": wf_name}).to_list(None)
total_tokens = sum(s["usage_total_tokens_final"] for s in all_sessions)
await stats_coll.update_one({"_id": rollup_id}, {"$set": {"total_tokens": total_tokens}})
```

### Scalability Metrics

**Expected Performance:**

| **Operation** | **Latency** | **Throughput** |
|---------------|-------------|----------------|
| Get wallet balance | <10ms | 10,000 req/sec |
| Debit tokens (atomic) | <20ms | 5,000 req/sec |
| Update session metrics | <30ms | 3,000 req/sec |
| Get rollup document | <10ms | 10,000 req/sec |

**Scaling Considerations:**

- **Sharding:** Shard `Wallets` by `AppId` for multi-tenant scaling
- **Caching:** Cache wallet balances (invalidate on debit/top-up)
- **Read Replicas:** Route analytics queries to read replicas
- **Archival:** Move completed ChatSessions to cold storage after 90 days

---

## Related Documentation

- [**Observability & Tokens**](../OBSERVABILITY_AND_TOKENS.md) - AG2 lifecycle usage summary integration
- [**Real-time Billing Guide**](../../REALTIME_BILLING_GUIDE.md) - Rollup architecture and metrics
- [**Clean Billing Architecture**](../../CLEAN_BILLING_ARCHITECTURE.md) - Two-level billing system
- [**Persistence & Resume**](./persistence_and_resume.md) - ChatSessions and workflow state
- [**Transport & Streaming**](./transport_and_streaming.md) - WebSocket event flow
- [**Event Reference**](../reference/event_reference.md) - Token-related events

---

## Troubleshooting

### Common Issues

**Issue 1: "INSUFFICIENT_TOKENS" error during workflow**

**Cause:** Wallet balance fell below required tokens mid-workflow.

**Fix:**
```python
# Add balance check before starting expensive workflows
balance = await persistence_manager.get_wallet_balance(user_id, app_id)
estimated_cost = 5000  # tokens

if balance < estimated_cost:
    await prompt_low_balance(chat_id, workflow_name, estimated_cost, balance)
    raise ValueError("INSUFFICIENT_BALANCE_PRECHECK")
```

---

**Issue 2: Tokens tracked but wallet not debited**

**Cause:** `FREE_TRIAL_ENABLED = true` in environment.

**Fix:**
```bash
# Set environment variable for production
FREE_TRIAL_ENABLED=false
```

---

**Issue 3: Rollup document shows zero metrics despite completed chats**

**Cause:** Metrics update failed silently or rollup document not created.

**Debug:**
```python
# Check if rollup document exists
stats_coll = await persistence._workflow_stats_coll()
rollup = await stats_coll.find_one({"_id": f"mon_{app_id}_{workflow_name}"})

if rollup is None:
    print("Rollup document missing - run create_chat_session to initialize")

# Check ChatSessions for usage data
chat_coll = await persistence._coll()
session = await chat_coll.find_one({"_id": chat_id})
print(f"Session usage: {session.get('usage_total_tokens_final', 0)} tokens")
```

---

**Issue 4: Race condition - double debit**

**Cause:** Concurrent requests with same chat_id.

**Fix:** MongoDB's atomic update prevents this, but ensure you're using `find_one_and_update` (not separate read + update):

```python
# ✅ Atomic (safe)
res = await wallets_collection.find_one_and_update(
    {"AppId": eid, "UserId": uid, "Balance": {"$gte": amount}},
    {"$inc": {"Balance": -amount}}
)

# ❌ Non-atomic (race condition possible)
doc = await wallets_collection.find_one({"AppId": eid, "UserId": uid})
if doc["Balance"] >= amount:
    await wallets_collection.update_one({"_id": doc["_id"]}, {"$inc": {"Balance": -amount}})
```

---

**Issue 5: AG2 gather_usage_summary returns zero**

**Cause:** Agents not properly configured with `llm_config` containing API key.

**Debug:**
```python
# Verify agent has llm_config
print(agent.llm_config)  # Should show OpenAI config with api_key

# Test direct AG2 usage tracking
if hasattr(agent, 'print_usage_summary'):
    agent.print_usage_summary()  # Check stdout for usage info
else:
    print("Agent doesn't support usage tracking")
```

---

## Summary

MozaiksAI's token management system provides:

✅ **Real-time Billing:** Wallet debited immediately on token usage (production mode)  
✅ **AG2 Integration:** Native usage summary lifecycle for authoritative accounting  
✅ **Free Trial Support:** Track usage without debiting (development/demo mode)  
✅ **Dynamic Agent Tracking:** Automatic per-agent metrics without pre-configuration  
✅ **Atomic Transactions:** Race-condition-free wallet operations  
✅ **Rollup Analytics:** Single aggregated document per workflow for fast queries  
✅ **Low Balance Handling:** UI prompts and chat pause/resume on insufficient funds  

**Key Design Principles:**
- **Accuracy First:** AG2 native methods provide authoritative token counts
- **Real-time Transparency:** Users see balance updates immediately
- **Atomicity:** MongoDB atomic updates prevent double-spending
- **Scalability:** Incremental rollup updates, not full recomputes
- **Flexibility:** Free trial vs. production mode via single env variable
