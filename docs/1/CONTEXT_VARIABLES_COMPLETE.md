# Context Variables System - Complete Documentation

**Status**: Production-Ready (Six-Type Taxonomy)  
**Last Updated**: January 2025  
**Authoritative Source**: This document supersedes all prior context variable documentation

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Six-Type Taxonomy](#six-type-taxonomy)
3. [Configuration Schema](#configuration-schema)
4. [Runtime Loading](#runtime-loading)
5. [Agent Integration](#agent-integration)
6. [Derived Variable Engine](#derived-variable-engine)
7. [Decision Guide](#decision-guide)
8. [Migration from Legacy](#migration-from-legacy)
9. [Extensibility Guidelines](#extensibility-guidelines)
10. [Production Considerations](#production-considerations)

---

## Executive Summary

The MozaiksAI context variables system provides a **declarative, type-safe mechanism** for managing workflow state, configuration, and data access. The system has evolved from a legacy four-type taxonomy (environment, static, database, derived) to a **production-ready six-type taxonomy** that better reflects real-world automation needs.

### Design Principles

1. **Semantic Clarity**: Variable types reflect their actual purpose in automation
2. **Persistence Intent**: Explicitly declare what persists to database vs. stays in-memory
3. **Data Lifecycle**: Clear distinction between existing data, new data, and ephemeral data
4. **Build Integration**: Specs provide everything Build workflow needs to generate code
5. **Determinism**: Every variable's value must be derivable from declarative config + observable runtime state
6. **Production Safety**: Production deployments eliminate environment-based branching variability

### Key Architectural Changes

- **Unified Definitions**: All variable types in one `definitions` object (source.type determines category)
- **AG2-Native Integration**: Per-agent variable exposure via `agents` object for UpdateSystemMessage injection
- **No UI Coupling**: Context variables are backend-only; UI visibility handled in presentation layer
- **Validation**: Pydantic schema validation (`core/workflow/context/schema.py`) with fail-fast on invalid config

---

## Six-Type Taxonomy

### 1. **`config`** - Deployment Configuration

**Purpose**: Settings that vary by environment (dev/staging/prod)

**Schema**:
```json
{
  "max_retry_attempts": {
    "type": "integer",
    "description": "Maximum API retry attempts before failure",
    "source": {
      "type": "config",
      "env_var": "MAX_RETRY_ATTEMPTS",
      "default": 3,
      "required": false
    }
  }
}
```

**Lifecycle**: Load once at workflow start from environment variables  
**Persistence**: Never persists (deployment-specific)  
**Use Cases**: Feature flags, API keys, deployment toggles, resource limits

**Runtime Behavior**:
```python
# Loaded via os.getenv() with type coercion
value = os.getenv("MAX_RETRY_ATTEMPTS", "3")
max_retries = int(value)  # Type-safe conversion
```

---

### 2. **`data_reference`** - Existing Database Data

**Purpose**: Read from collections that ALREADY EXIST in user's database

**Schema**:
```json
{
  "customer_tier": {
    "type": "string",
    "description": "User's subscription tier from existing Users collection",
    "source": {
      "type": "data_reference",
      "database_name": "user_database",
      "collection": "Users",
      "search_by": "user_id",
      "field": "subscription_tier",
      "query_template": {"user_id": "${user_id}"},
      "refresh_strategy": "once"
    }
  }
}
```

**Lifecycle**: 
- Query existing collection at workflow start (or per refresh_strategy)
- Read-only during workflow
- Never writes back

**Persistence**: Data already persisted externally (not our concern)  
**Use Cases**: User profiles, product catalogs, configuration lookups, reference data

**Runtime Behavior**:
```python
async def load_customer_tier(runtime, user_id):
    db = get_mongo_client()["user_database"]
    doc = await db["Users"].find_one({"user_id": user_id})
    return doc.get("subscription_tier") if doc else None
```

**Validation**: Build workflow checks if collection exists in schema. If missing, raises error during generation.

---

### 3. **`data_entity`** - New Database Data (Created by Automation)

**Purpose**: Data this automation CREATES and persists to database

**Schema**:
```json
{
  "workflow_audit": {
    "type": "object",
    "description": "Audit record created by workflow execution",
    "source": {
      "type": "data_entity",
      "collection": "WorkflowAudits",
      "search_by": "workflow_id",
      "schema": {
        "workflow_id": "string",
        "user_id": "string",
        "started_at": "datetime",
        "completed_at": "datetime",
        "status": "string"
      },
      "indexes": [
        {"keys": [["user_id", 1], ["started_at", -1]]}
      ],
      "write_strategy": "on_workflow_end"
    }
  }
}
```

**Lifecycle**:
- Collection created by Build workflow (if doesn't exist)
- Document created during workflow execution
- Writes happen per `write_strategy`

**Persistence**: YES - this is the point  
**Use Cases**: Workflow outputs, generated reports, audit logs, user-submitted data

**Runtime Behavior**:
```python
# DataEntityManager handles deferred writes
manager = DataEntityManager(
    runtime=runtime,
    collection="WorkflowAudits",
    search_by="workflow_id",
    write_strategy="on_workflow_end"
)

# Create or update during workflow
await manager.create({
    "workflow_id": "wf_123",
    "user_id": "user_456",
    "started_at": datetime.now()
})

# Flush pending writes at workflow end
await manager.flush()
```

**Write Strategies**:
- `immediate`: Write to database as soon as variable changes
- `on_phase_transition`: Write when workflow phase changes
- `on_workflow_end`: Buffer changes in memory, write once at completion

**Build Integration**: Build workflow generates:
1. Migration script (if collection doesn't exist)
2. Write functions (create, update)
3. Flush logic for deferred strategies

---

### 4. **`computed`** - Business Logic Outputs

**Purpose**: Values calculated by automation logic (may or may not persist)

**Schema**:
```json
{
  "escalation_score": {
    "type": "number",
    "description": "Calculated priority score for ticket escalation",
    "source": {
      "type": "computed",
      "computation": "escalate_ticket",
      "inputs": ["ticket_priority", "customer_tier", "response_time_hours"],
      "output_type": "float",
      "persist_to": {
        "collection": "Tickets",
        "search_by": "ticket_id",
        "field": "escalation_score"
      }
    }
  }
}
```

**Lifecycle**:
- Computed during workflow (via agent output or explicit calculation)
- Optionally persisted to database if `persist_to` specified

**Persistence**: 
- If `persist_to` is null → Ephemeral (workflow-only)
- If `persist_to` specified → Persists to specified collection/field

**Use Cases**: Calculated metrics, formatted strings, derived values, conditional flags

**Runtime Behavior**:
```python
# Ephemeral computation (no persistence)
def calculate_escalation_score(ticket_priority, customer_tier, response_time_hours):
    score = (ticket_priority * 10) + tier_multiplier[customer_tier] + response_time_hours
    return score

# Persisted computation (writes to database)
async def calculate_and_persist_escalation_score(runtime, ticket_id, inputs):
    score = calculate_escalation_score(**inputs)
    
    db = get_mongo_client()[runtime.database_name]
    await db["Tickets"].update_one(
        {"ticket_id": ticket_id},
        {"$set": {"escalation_score": score}}
    )
    
    return score
```

---

### 5. **`state`** - Workflow Orchestration State

**Purpose**: Internal workflow coordination (phases, approvals, routing decisions)

**Schema**:
```json
{
  "current_phase": {
    "type": "string",
    "description": "Current workflow phase for routing decisions",
    "source": {
      "type": "state",
      "default": "interview",
      "transitions": [
        {
          "from": "interview",
          "to": "planning",
          "trigger": {
            "type": "agent_text",
            "agent": "InterviewAgent",
            "match": {"equals": "NEXT"}
          }
        },
        {
          "from": "planning",
          "to": "execution",
          "trigger": {
            "type": "ui_response",
            "tool": "approval_gate",
            "response_key": "approved"
          }
        }
      ],
      "persist": false
    }
  }
}
```

**Lifecycle**:
- Initialized with `default` value
- Transitions via triggers (agent messages, UI interactions)
- Drives handoff logic and conditional routing

**Persistence**: 
- Usually `persist: false` (ephemeral coordination state)
- Can set `persist: true` if needed for audit/resume (stores in session collection)

**Use Cases**: Phase tracking, approval gates, workflow milestones, routing flags

**Dynamic Extraction (New)**:
- Use regex capture groups (`$1`) to extract dynamic values from agent text.
- Example: `match: {"regex": "ROUTING_DECISION: (.*)"}` + `to: "$1"`
- If agent says "ROUTING_DECISION: billing", variable becomes "billing".
- Eliminates need for hardcoded rules per category.

**Runtime Behavior**:
```python
# DerivedContextManager monitors triggers and updates state
class DerivedContextManager:
    def on_text_event(self, event):
        if event.sender == "InterviewAgent" and event.text.strip() == "NEXT":
            self.context_variables.set("current_phase", "planning")
```

---

### 6. **`external`** - External API Data

**Purpose**: Data fetched from third-party APIs (Stripe, Salesforce, etc.)

**Schema**:
```json
{
  "stripe_customer": {
    "type": "object",
    "description": "Customer data from Stripe API",
    "source": {
      "type": "external",
      "service": "stripe",
      "operation": "customers.retrieve",
      "params": {"customer_id": "${customer_id}"},
      "auth": {
        "type": "api_key",
        "env_var": "STRIPE_API_KEY"
      },
      "cache": {
        "ttl": 300,
        "key_template": "stripe_customer_${customer_id}"
      },
      "retry": {
        "max_attempts": 3,
        "backoff": "exponential"
      }
    }
  }
}
```

**Lifecycle**:
- Fetched on-demand (when agent/tool requests it)
- Cached per TTL
- Retried on failure

**Persistence**: Cached in Redis/memory (not in user's DB)  
**Use Cases**: Payment processing, CRM data, weather data, stock prices

**Runtime Behavior**:
```python
import stripe
from cachetools import TTLCache

stripe_cache = TTLCache(maxsize=100, ttl=300)

async def get_stripe_customer(customer_id, api_key):
    cache_key = f"stripe_customer_{customer_id}"
    
    if cache_key in stripe_cache:
        return stripe_cache[cache_key]
    
    stripe.api_key = api_key
    customer = await stripe.Customer.retrieve_async(customer_id)
    
    stripe_cache[cache_key] = customer
    return customer
```

---

## Configuration Schema

### Complete Example: Order Fulfillment Automation

```json
{
  "context_variables": {
    "definitions": {
      "max_retry_attempts": {
        "type": "integer",
        "description": "Maximum retry attempts for API calls",
        "source": {
          "type": "config",
          "env_var": "MAX_RETRY_ATTEMPTS",
          "default": 3,
          "required": false
        }
      },
      "order_details": {
        "type": "object",
        "description": "Existing order data from Orders collection",
        "source": {
          "type": "data_reference",
          "database_name": "user_database",
          "collection": "Orders",
          "search_by": "order_id",
          "query_template": {"order_id": "${order_id}"},
          "fields": ["items", "shipping_address", "customer_id"],
          "refresh_strategy": "once"
        }
      },
      "customer_profile": {
        "type": "object",
        "description": "Existing customer data from Users collection",
        "source": {
          "type": "data_reference",
          "database_name": "user_database",
          "collection": "Users",
          "search_by": "user_id",
          "query_template": {"user_id": "${user_id}"},
          "fields": ["name", "email", "subscription_tier"],
          "refresh_strategy": "once"
        }
      },
      "fulfillment_record": {
        "type": "object",
        "description": "Fulfillment tracking record (created by workflow)",
        "source": {
          "type": "data_entity",
          "collection": "FulfillmentRecords",
          "search_by": "order_id",
          "schema": {
            "order_id": "string",
            "status": "string",
            "tracking_number": "string",
            "shipped_at": "datetime",
            "delivered_at": "datetime"
          },
          "indexes": [
            {"keys": [["order_id", 1]]},
            {"keys": [["status", 1], ["shipped_at", -1]]}
          ],
          "write_strategy": "immediate"
        }
      },
      "shipping_cost": {
        "type": "number",
        "description": "Calculated shipping cost with tier discount",
        "source": {
          "type": "computed",
          "computation": "calculate_shipping_cost",
          "inputs": ["order_details.items", "customer_profile.subscription_tier", "order_details.shipping_address"],
          "output_type": "float",
          "persist_to": {
            "collection": "Orders",
            "search_by": "order_id",
            "field": "shipping_cost"
          }
        }
      },
      "delivery_date": {
        "type": "string",
        "description": "Estimated delivery date (ephemeral calculation)",
        "source": {
          "type": "computed",
          "computation": "calculate_delivery_date",
          "inputs": ["order_details.shipping_address", "shipping_cost"],
          "output_type": "string",
          "persist_to": null
        }
      },
      "fulfillment_status": {
        "type": "string",
        "description": "Current fulfillment workflow status",
        "source": {
          "type": "state",
          "default": "pending",
          "transitions": [
            {
              "from": "pending",
              "to": "processing",
              "trigger": {
                "type": "agent_text",
                "agent": "FulfillmentAgent",
                "ui_hidden": true,
                "match": {"equals": "START_PROCESSING"}
              }
            },
            {
              "from": "processing",
              "to": "shipped",
              "trigger": {
                "type": "ui_response",
                "tool": "shipping_confirmation",
                "response_key": "tracking_number"
              }
            }
          ],
          "persist": false
        }
      },
      "stripe_payment": {
        "type": "object",
        "description": "Payment intent data from Stripe",
        "source": {
          "type": "external",
          "service": "stripe",
          "operation": "payment_intents.retrieve",
          "params": {"payment_intent_id": "${payment_intent_id}"},
          "auth": {
            "type": "api_key",
            "env_var": "STRIPE_API_KEY"
          },
          "cache": {
            "ttl": 60,
            "key_template": "stripe_payment_${payment_intent_id}"
          },
          "retry": {
            "max_attempts": 3,
            "backoff": "exponential"
          }
        }
      }
    },
    "agents": {
      "FulfillmentAgent": {
        "variables": ["order_details", "customer_profile", "shipping_cost", "fulfillment_status"]
      },
      "ShippingAgent": {
        "variables": ["order_details", "delivery_date", "fulfillment_status", "fulfillment_record"]
      },
      "PaymentAgent": {
        "variables": ["stripe_payment", "order_details", "max_retry_attempts"]
      }
    }
  }
}
```

---

## Runtime Loading

### Loading Sequence (core/workflow/context/variables.py)

1. **Minimal Base Context**
   - Seeds `app_id`, `user_id`, `workflow_name`
   - Loads baked-in flags from environment (unless production mode)

2. **Config Fetch + Validation**
   - Loads `context_variables.json` from workflow directory
   - Validates against Pydantic schema (`core/workflow/context/schema.py`)
   - Fails fast with warning if invalid schema

3. **Production Gating**
   - If `ENVIRONMENT=production`: Suppresses all `config` type variables with env_var
   - If `CONTEXT_INCLUDE_SCHEMA=false`: Suppresses all `data_reference` variables

4. **Variable Resolution by Type**
   ```python
   # config type
   value = os.getenv(source.env_var, source.default)
   
   # data_reference type
   value = await _load_data_reference(runtime, source)
   
   # data_entity type
   value = DataEntityManager(runtime, source)
   
   # computed type
   value = None  # Computed on-demand
   
   # state type
   value = source.default
   
   # external type
   value = None  # Fetched on-demand
   ```

5. **Agent Variable Lists**
   - Reads `agents.<AgentName>.variables` arrays
   - Stores as `_mozaiks_context_agents` metadata
   - Used for AG2 UpdateSystemMessage injection

6. **Logging**
   - Safe summaries (length-limited, no secrets)
   - Verbose diffing with `CONTEXT_VERBOSE_DEBUG=1`

### Type Resolution Methods

```python
# core/workflow/context/variables.py

def _resolve_config(definition):
    """Load from environment variable with type coercion."""
    source = definition["source"]
    value = os.getenv(source["env_var"], source.get("default"))
    
    # Type coercion based on definition["type"]
    if definition["type"] == "boolean":
        return value.lower() in ("true", "1", "yes", "on")
    elif definition["type"] == "integer":
        return int(value)
    return value

async def _resolve_data_reference(runtime, definition):
    """Query MongoDB for existing data.
    
    Important: When multiple documents match the query (e.g., duplicate app_id entries),
    the runtime automatically returns the MOST RECENT document by sorting on _id descending.
    MongoDB ObjectIds contain timestamps, so this ensures chronological ordering.
    """
    source = definition["source"]
    db = get_mongo_client()[source["database_name"]]
    
    # Interpolate query template with runtime values
    query = _interpolate_template(source["query_template"], runtime)
    
    # Sort by _id descending to get most recent document (handles duplicates gracefully)
    cursor = db[source["collection"]].find(query).sort("_id", -1).limit(1)
    docs = await cursor.to_list(length=1)
    doc = docs[0] if docs else None
    
    if not doc:
        return None
    
    if source.get("fields"):
        # Extract specific fields only
        return {field: doc.get(field) for field in source["fields"]}
    elif source.get("field"):
        # Extract single field
        return doc.get(source["field"])
    
    return doc

def _resolve_data_entity(runtime, definition):
    """Create DataEntityManager for workflow-owned writes."""
    source = definition["source"]
    return DataEntityManager(
        runtime=runtime,
        collection=source["collection"],
        search_by=source["search_by"],
        schema=source.get("schema"),
        indexes=source.get("indexes"),
        write_strategy=source["write_strategy"]
    )

def _resolve_state_default(definition):
    """Return default value for state variable."""
    return definition["source"]["default"]
```

---

## Agent Integration

### UpdateSystemMessage Pattern (AG2-Native)

Context variables are injected into agent prompts via AG2's `UpdateSystemMessage` mechanism:

```json
{
  "agents": {
    "InterviewAgent": {
      "variables": ["context_aware", "concept_overview", "monetization_enabled"]
    },
    "WorkflowArchitectAgent": {
      "variables": ["concept_overview", "monetization_enabled", "schema_overview"]
    }
  }
}
```

**Runtime Behavior**:
```python
# Runtime builds UpdateSystemMessage template
update_template = """
Current Context:
- App: {app_id}
- Concept: {concept_overview}
- Context Aware: {context_aware}
- Monetization: {monetization_enabled}
"""

# AG2 injects values before each agent reply
agent.register_update_system_message(
    update_system_message=update_template
)
```

### Tool Access Pattern

Tools can access context variables via dependency injection:

```python
from autogen import ContextVariables

async def my_tool(
    param1: str,
    context_variables: ContextVariables  # AG2 auto-injects
) -> str:
    """Tool with context access."""
    customer_tier = context_variables.get("customer_tier")
    monetization = context_variables.get("monetization_enabled")
    
    if monetization and customer_tier == "premium":
        return "Premium feature enabled"
    return "Standard feature"
```

### Handoff Routing with Context

Only `config` and `state` variables can be used in handoff conditions:

```python
from autogen.agentchat.conditions import OnContextCondition

# Valid: state variable
OnContextCondition(
    target=planning_agent,
    condition="${interview_complete}"
)

# Valid: config variable (dev only)
OnContextCondition(
    target=monetization_agent,
    condition="${monetization_enabled} AND ${interview_complete}"
)

# Invalid: data_reference variables cannot be used in routing
# (keep routing deterministic and config-driven)
```

---

## Derived Variable Engine

### DerivedContextManager (core/workflow/context/derived.py)

Orchestrates `state` variables with `agent_text` triggers:

**Initialization**:
```python
class DerivedContextManager:
    def __init__(self, definitions, context_providers):
        self.definitions = {
            name: defn for name, defn in definitions.items()
            if defn["source"]["type"] == "state"
        }
        self.triggers = self._build_triggers()
        self.context_providers = context_providers
    
    def _build_triggers(self):
        """Extract agent_text triggers from definitions."""
        triggers = []
        for name, defn in self.definitions.items():
            for transition in defn["source"].get("transitions", []):
                if transition["trigger"]["type"] == "agent_text":
                    triggers.append({
                        "variable": name,
                        "agent": transition["trigger"]["agent"],
                        "match": transition["trigger"]["match"],
                        "new_value": transition["to"]
                    })
        return triggers
```

**Event Monitoring**:
```python
def on_text_event(self, event):
    """Monitor TextEvent stream for trigger matches."""
    sender = event.sender if isinstance(event.sender, str) else event.sender.name
    text = event.text.strip()
    
    for trigger in self.triggers:
        if sender != trigger["agent"]:
            continue
        
        # Match logic (equals, contains, regex)
        if self._matches(text, trigger["match"]):
            # Update all context providers
            for provider in self.context_providers:
                provider.set(trigger["variable"], trigger["new_value"])
            
            logger.info(f"Derived variable '{trigger['variable']}' set to '{trigger['new_value']}'")
```

### Trigger Types

#### 1. Agent Text Trigger (Passive)

**Use Case**: Agent outputs text signaling state change

**Example**:
```json
{
  "interview_complete": {
    "type": "boolean",
    "description": "True when InterviewAgent signals readiness",
    "source": {
      "type": "state",
      "default": false,
      "transitions": [
        {
          "from": false,
          "to": true,
          "trigger": {
            "type": "agent_text",
            "agent": "InterviewAgent",
            "match": {"equals": "NEXT"}
          }
        }
      ]
    }
  }
}
```

**Matching Options**:
- `{"equals": "NEXT"}` - Exact match (case-insensitive)
- `{"contains": "approved"}` - Substring match
- `{"regex": "^READY.*"}` - Regex pattern

#### 2. UI Response Trigger (Active)

**Use Case**: User clicks button, submits form, makes UI selection

**Example**:
```json
{
  "action_plan_acceptance": {
    "type": "string",
    "description": "User's response: accepted/rejected/adjustments",
    "source": {
      "type": "state",
      "default": "pending",
      "transitions": [
        {
          "from": "pending",
          "to": "accepted",
          "trigger": {
            "type": "ui_response",
            "tool": "action_plan",
            "response_key": "plan_acceptance"
          }
        }
      ]
    }
  }
}
```

**Tool Implementation**:
```python
async def action_plan_tool(context_variables: ContextVariables):
    """Tool that awaits UI response and updates context."""
    response = await use_ui_tool(
        tool_id="action_plan",
        payload=plan_data,
        wait_for_response=True
    )
    
    # Extract value using response_key from config
    plan_acceptance = response.get("plan_acceptance")
    
    # Update context variable
    if plan_acceptance:
        context_variables.set("action_plan_acceptance", plan_acceptance)
    
    return {"status": "complete", "decision": plan_acceptance}
```

---

## Decision Guide

### Quick Matrix: Choosing the Right Type

| I need… | Characteristics | Pick |
|---------|-----------------|------|
| A tenant-specific field (plan, quota, industry) | Comes from persistent data; may change outside a run | `data_reference` |
| A deployment toggle (enable X in dev, off in prod) | Controlled by ops; should vanish in production context | `config` |
| Data this workflow creates (audit log, report) | Workflow owns the write; creates new records | `data_entity` |
| A calculated value (discount, score, formatted string) | Derived from other data; deterministic | `computed` |
| A flag that flips during the conversation | Changes based on agent messages or UI interactions | `state` |
| Third-party API data (Stripe, weather, CRM) | Fetched from external service; cached | `external` |

### Decision Tree

```
Does this data come from outside the automation?
├─ YES → Is it from user's database?
│         ├─ YES → Does the collection already exist?
│         │         ├─ YES → Does workflow WRITE to it?
│         │         │         ├─ YES → Use `data_entity`
│         │         │         └─ NO → Use `data_reference`
│         │         └─ NO → Use `data_entity` (workflow creates it)
│         └─ NO → Is it from external API?
│                   ├─ YES → Use `external`
│                   └─ NO → Use `config`
│
└─ NO → Is it calculated by automation logic?
          ├─ YES → Does it need to persist?
          │         ├─ YES → Use `computed` with persist_to
          │         └─ NO → Use `computed` with persist_to=null
          └─ NO → Does it change during workflow?
                    ├─ YES → Use `state`
                    └─ NO → Use `config` (static value)
```

### Anti-Patterns to Avoid

❌ **Don't** store secrets in context variables (use secure secret manager)  
❌ **Don't** use `config` for static brand names (use computed or hardcode)  
❌ **Don't** branch on high-cardinality `data_reference` values (convert to boolean/enum via `computed`)  
❌ **Don't** add verbose documents (summarize to smaller form first)  
❌ **Don't** use `data_reference` for workflow-owned data (use `data_entity`)  
❌ **Don't** use `data_entity` for existing collections (use `data_reference`)

---

## Migration from Legacy

### Legacy Four-Type Taxonomy

The original system used four types:

| Legacy Type | Maps To | Migration Rule |
|-------------|---------|----------------|
| `static` | `config` | Literal values → config with env_var=null |
| `environment` | `config` | Environment variables → config with env_var |
| `database` | `data_reference` OR `data_entity` | Reads → data_reference; Writes → data_entity |
| `derived` | `computed` OR `state` | Deterministic → computed; Mutable → state |

### Migration Steps

1. **Identify Variable Purpose**
   - Read-only external data? → `data_reference`
   - Workflow-created data? → `data_entity`
   - Calculated value? → `computed`
   - State machine? → `state`

2. **Update Source Type**
   ```json
   // Before (legacy)
   {
     "customer_tier": {
       "type": "string",
       "source": {
         "type": "database",
         "collection": "Users",
         "field": "subscription_tier"
       }
     }
   }
   
   // After (new taxonomy)
   {
     "customer_tier": {
       "type": "string",
       "source": {
         "type": "data_reference",
         "database_name": "user_database",
         "collection": "Users",
         "search_by": "user_id",
         "query_template": {"user_id": "${user_id}"},
         "field": "subscription_tier",
         "refresh_strategy": "once"
       }
     }
   }
   ```

3. **Update Agent Guidance**
   - WorkflowArchitectAgent now emits six types
   - ContextVariablesAgent validates six-type taxonomy
   - Update any hardcoded type checks in tools

4. **Test Runtime Loading**
   - Verify ContextVariableLoader handles all six types
   - Test DataEntityManager for workflow-owned writes
   - Validate UpdateSystemMessage injection

---

## Extensibility Guidelines

### Adding a New Variable

#### Example: Add New Config Variable

```json
{
  "definitions": {
    "feature_x_enabled": {
      "type": "boolean",
      "description": "Flag to enable experimental feature X",
      "source": {
        "type": "config",
        "env_var": "FEATURE_X_ENABLED",
        "default": false,
        "required": false
      }
    }
  }
}
```

#### Example: Add New Data Reference

```json
{
  "definitions": {
    "user_preferences": {
      "type": "object",
      "description": "User's saved preferences from Settings collection",
      "source": {
        "type": "data_reference",
        "database_name": "user_database",
        "collection": "Settings",
        "search_by": "user_id",
        "query_template": {"user_id": "${user_id}"},
        "fields": ["theme", "language", "notifications"],
        "refresh_strategy": "once"
      }
    }
  }
}
```

#### Example: Add New Data Entity

```json
{
  "definitions": {
    "support_ticket": {
      "type": "object",
      "description": "Support ticket created by workflow",
      "source": {
        "type": "data_entity",
        "collection": "SupportTickets",
        "search_by": "ticket_id",
        "schema": {
          "ticket_id": "string",
          "user_id": "string",
          "subject": "string",
          "description": "string",
          "status": "string",
          "created_at": "datetime"
        },
        "indexes": [
          {"keys": [["user_id", 1], ["created_at", -1]]},
          {"keys": [["status", 1]]}
        ],
        "write_strategy": "immediate"
      }
    }
  }
}
```

#### Example: Add New Computed Variable

```json
{
  "definitions": {
    "priority_score": {
      "type": "number",
      "description": "Calculated priority score for task routing",
      "source": {
        "type": "computed",
        "computation": "calculate_priority",
        "inputs": ["urgency", "impact", "customer_tier"],
        "output_type": "float",
        "persist_to": {
          "collection": "Tasks",
          "search_by": "task_id",
          "field": "priority_score"
        }
      }
    }
  }
}
```

#### Example: Add New State Variable

```json
{
  "definitions": {
    "approval_status": {
      "type": "string",
      "description": "Current approval workflow status",
      "source": {
        "type": "state",
        "default": "pending",
        "transitions": [
          {
            "from": "pending",
            "to": "approved",
            "trigger": {
              "type": "ui_response",
              "tool": "approval_gate",
              "response_key": "decision"
            }
          },
          {
            "from": "pending",
            "to": "rejected",
            "trigger": {
              "type": "agent_text",
              "agent": "ReviewAgent",
              "match": {"equals": "REJECT"}
            }
          }
        ],
        "persist": true
      }
    }
  }
}
```

#### Example: Add New External Variable

```json
{
  "definitions": {
    "weather_data": {
      "type": "object",
      "description": "Current weather from OpenWeather API",
      "source": {
        "type": "external",
        "service": "openweather",
        "operation": "weather.current",
        "params": {"city": "${city}", "units": "metric"},
        "auth": {
          "type": "api_key",
          "env_var": "OPENWEATHER_API_KEY"
        },
        "cache": {
          "ttl": 1800,
          "key_template": "weather_${city}"
        },
        "retry": {
          "max_attempts": 3,
          "backoff": "exponential"
        }
      }
    }
  }
}
```

### Exposing Variables to Agents

Add variable names to agent's variables list:

```json
{
  "agents": {
    "MyAgent": {
      "variables": ["app_id", "feature_x_enabled", "user_preferences"]
    }
  }
}
```

Runtime automatically:
1. Builds UpdateSystemMessage template
2. Injects current values before each agent reply
3. Provides AG2-native context access

---

## Production Considerations

### Production Mode (ENVIRONMENT=production)

**Effects**:
1. **Config Variables**: Suppresses all config variables with `env_var` set (only defaults remain)
2. **ContextVariablesAgent**: Emits empty environment list per system prompt
3. **Database Variables**: Fully accessible (descriptive use, tool arguments)
4. **Routing**: Only `state` variables valid in conditions (no environment-based branching)

**Rationale**: Eliminates configuration drift and accidental variability across deployments

### Security Best Practices

1. **Never store secrets**: Use secure secret manager (Azure Key Vault, AWS Secrets Manager)
2. **Limit exposure**: Only expose variables to agents that need them
3. **Safe logging**: Runtime uses length-limited previews, never logs full secret values
4. **Validate inputs**: Pydantic schema validation prevents malformed configs

### Observability

**Logging Levels**:
- `INFO`: Variable load summary, trigger matches, state transitions
- `DEBUG`: Detailed resolution steps, query templates
- `VERBOSE`: Full context diffs (enable with `CONTEXT_VERBOSE_DEBUG=1`)

**Safe Logging Pattern**:
```python
def _safe_preview(value, max_length=100):
    """Truncate long values for safe logging."""
    s = str(value)
    if len(s) > max_length:
        return s[:max_length] + "... (truncated)"
    return s

logger.info(f"Loaded {name}: {_safe_preview(value)}")
```

### Performance Considerations

1. **Data References**: Cache frequently accessed data (`refresh_strategy: "once"`)
2. **External Variables**: Use TTL caching to avoid repeated API calls
3. **Data Entities**: Use deferred writes (`on_workflow_end`) to batch database operations
4. **Computed Variables**: Cache expensive calculations when possible

### Duplicate Data Handling

**Problem**: Multiple documents with the same query criteria (e.g., same `app_id`) can exist in the database.

**Solution**: The runtime automatically handles duplicates by:
1. Sorting query results by `_id` descending (`sort("_id", -1)`)
2. Returning the **most recent** document (MongoDB ObjectIds contain timestamps)
3. Limiting to 1 result (`limit(1)`)

**Example**:
```python
# MongoDB query execution in db_adapters.py
cursor = client[db_name][collection].find(query, projection).sort("_id", -1).limit(1)
docs = await cursor.to_list(length=1)
doc = docs[0] if docs else None  # Returns most recent or None
```

**Benefits**:
- **Graceful degradation**: No errors if duplicates exist
- **Chronological ordering**: Always uses latest data
- **Resume safety**: Most recent chat/workflow state takes precedence
- **Zero config**: Works automatically, no special handling needed

**When duplicates occur**:
- Multiple workflow runs creating concepts for same app_id
- Chat session resume logic finding multiple "in progress" chats
- Data migrations or imports creating temporary duplicates

**Production recommendation**: While the runtime handles duplicates gracefully, implement unique indexes on critical query fields to prevent duplicates at the database level:
```json
{
  "indexes": [
    {"keys": [["app_id", 1]], "unique": true}
  ]
}
```

---

## Appendix: Schema Reference

### ContextVariableSource (Pydantic Models)

```python
# core/workflow/context/schema.py

from pydantic import BaseModel, Field
from typing import Literal, Optional, Dict, List, Any

class ConfigSource(BaseModel):
    type: Literal["config"]
    env_var: Optional[str] = None
    default: Optional[Any] = None
    required: bool = False

class DataReferenceSource(BaseModel):
    type: Literal["data_reference"]
    database_name: str
    collection: str
    search_by: str
    query_template: Dict[str, Any]
    field: Optional[str] = None
    fields: Optional[List[str]] = None
    refresh_strategy: Literal["once", "per_phase", "per_turn"] = "once"

class DataEntitySource(BaseModel):
    type: Literal["data_entity"]
    collection: str
    search_by: str
    schema: Dict[str, str]
    indexes: Optional[List[Dict[str, Any]]] = None
    write_strategy: Literal["immediate", "on_phase_transition", "on_workflow_end"]

class ComputedSource(BaseModel):
    type: Literal["computed"]
    computation: str  # Function name
    inputs: List[str]  # Input variable names
    output_type: str  # Return type
    persist_to: Optional[Dict[str, str]] = None  # {collection, search_by, field}

class StateSource(BaseModel):
    type: Literal["state"]
    default: Any
    transitions: List["StateTransition"]
    persist: bool = False

class StateTransition(BaseModel):
    from_value: Any = Field(alias="from")
    to: Any
    trigger: "StateTrigger"

class StateTrigger(BaseModel):
    type: Literal["agent_text", "ui_response"]
    agent: Optional[str] = None  # For agent_text
    match: Optional[Dict[str, str]] = None  # For agent_text
    tool: Optional[str] = None  # For ui_response
    response_key: Optional[str] = None  # For ui_response

class ExternalSource(BaseModel):
    type: Literal["external"]
    service: str
    operation: str
    params: Dict[str, Any]
    auth: Dict[str, str]
    cache: Optional[Dict[str, Any]] = None
    retry: Optional[Dict[str, Any]] = None

class ContextVariableDefinition(BaseModel):
    type: str  # JSON type: string, number, boolean, object, array
    description: str
    source: (
        ConfigSource | 
        DataReferenceSource | 
        DataEntitySource | 
        ComputedSource | 
        StateSource | 
        ExternalSource
    )

class ContextVariablesConfig(BaseModel):
    definitions: Dict[str, ContextVariableDefinition]
    agents: Dict[str, Dict[str, List[str]]]  # {AgentName: {variables: [...]}}
```

---

## Change Log

**January 2025 - Six-Type Taxonomy Production Release**
- Migrated from four-type (environment, static, database, derived) to six-type taxonomy
- Added `config`, `data_reference`, `data_entity`, `computed`, `state`, `external` types
- Implemented DataEntityManager for workflow-owned writes
- Updated all agent prompts (agents.json) with new taxonomy guidance
- Aligned backend tools (action_plan.py, update_agent_state_pattern.py)
- Updated frontend UI (ActionPlan.js) with six-type display logic
- Removed all legacy compatibility code

**Previous Updates**
- Initial four-type system implementation
- AG2-native UpdateSystemMessage integration
- DerivedContextManager for passive agent_text triggers
- UI response trigger support via use_ui_tool()
- Production mode suppression of environment variables

---

**End of Document**

This is the authoritative source of truth for the MozaiksAI context variables system. All other context variable documentation is superseded by this document.
