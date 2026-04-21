# AGENTS.md v2 - Modular Architecture

**For:** `.claude/AGENTS.md` in the mozaiks repository
**Architecture:** Modular composition (not merged runtimes)

---

## Repository Structure

```
mozaiks/
├── packages/
│   ├── core/           # Shared primitives (interfaces, types)
│   ├── ai/             # AI workflow execution (AG2)
│   ├── modules/        # Module execution (CRUD)
│   ├── runtime/        # App composition layer
│   ├── ui/             # UI rendering
│   └── cli/            # CLI tool
├── templates/          # App templates
└── examples/           # Example apps
```

---

## CRITICAL: Dependency Rules

```
                          ┌─────────┐
                          │   cli   │
                          └────┬────┘
                               │
                               ▼
                          ┌─────────┐
                          │ runtime │
                          └────┬────┘
                               │
                ┌──────────────┼──────────────┐
                │              │              │
                ▼              ▼              ▼
          ┌─────────┐    ┌─────────┐    ┌─────────┐
          │   ai    │    │   ui    │    │ modules │
          └────┬────┘    └────┬────┘    └────┬────┘
               │              │              │
               └──────────────┼──────────────┘
                              │
                              ▼
                         ┌─────────┐
                         │  core   │
                         └─────────┘

ALLOWED:
✅ cli → runtime
✅ runtime → ai, modules, ui, core
✅ ai → core
✅ modules → core
✅ ui → core

FORBIDDEN:
❌ ai → modules
❌ modules → ai
❌ ai → runtime
❌ modules → runtime
❌ core → anything
```

### Import Rules by Package

#### packages/core/

**CAN import:** Standard library, pydantic, pyyaml, motor

**CANNOT import:** mozaiks_ai, mozaiks_modules, mozaiks_runtime, mozaiks_ui

#### packages/ai/

**CAN import:** mozaiks_core, ag2, openai, anthropic

**CANNOT import:** mozaiks_modules, mozaiks_runtime, mozaiks_ui

#### packages/modules/

**CAN import:** mozaiks_core

**CANNOT import:** mozaiks_ai, mozaiks_runtime, mozaiks_ui

#### packages/ui/

**CAN import:** mozaiks_core, jinja2

**CANNOT import:** mozaiks_ai, mozaiks_modules, mozaiks_runtime

#### packages/runtime/

**CAN import:** mozaiks_core, mozaiks_ai (optional), mozaiks_modules (optional), mozaiks_ui (optional)

This is the ONLY package that can import from ai and modules.

#### packages/cli/

**CAN import:** mozaiks_runtime (which transitively includes others)

---

## How AI and Modules Communicate

**They do NOT import each other.** Communication happens through:

### 1. Runtime Composition (Recommended)

When running in "full" mode, the runtime provides executors to tools:

```python
# In an AI tool (packages/ai)
async def get_contacts(context, limit: int = 10):
    """Tool that needs module data."""

    # Runtime injects this into context
    module_executor = context.executors.get("modules")

    if module_executor:
        # Direct call (same process)
        from mozaiks_core.interfaces import ExecutionRequest, ExecutorType

        result = await module_executor.execute(ExecutionRequest(
            executor_type=ExecutorType.MODULE,
            target="contacts",
            action="list",
            params={"limit": limit},
            app_id=context.app_id,
            user_id=context.user_id,
        ))
        return result.data
    else:
        # Fallback: AI-only mode, no modules available
        return {"error": "Modules not available"}
```

### 2. HTTP Fallback (Separate Processes)

If modules run in a separate process:

```python
# In an AI tool
async def get_contacts(context, limit: int = 10):
    import httpx

    modules_url = context.config.get("modules_url", "http://localhost:8002")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{modules_url}/api/execute/contacts",
            json={"action": "list", "limit": limit},
            headers={"Authorization": f"Bearer {context.token}"},
        )
        return response.json()
```

### 3. Events (Async, Fire-and-Forget)

For notifications or async workflows:

```python
# In a module (packages/modules)
from mozaiks_core.interfaces import Event

async def create_contact(data: dict, event_bus) -> dict:
    # ... create contact ...

    # Publish event - AI might react to this
    await event_bus.publish(Event.create(
        event_type="module.contacts.created",
        source="modules",
        app_id=data["app_id"],
        payload={"contact_id": contact_id},
    ))

    return {"id": contact_id}
```

```yaml
# In a workflow (packages/ai)
# orchestrator.yaml
triggers:
  - event: module.contacts.created
    condition: "payload.source == 'import'"
```

---

## Package Responsibilities

### core - Shared Foundation

**Put here:**
- Interface definitions (Protocol classes)
- Event envelope schema
- JWT validation logic
- MongoDB connection utilities
- Config loading (YAML, env vars)
- Request context definition
- Storage abstractions

**Don't put here:**
- Business logic
- HTTP servers
- UI components
- Workflow execution

### ai - AI Workflow Execution

**Put here:**
- Workflow YAML loading
- AG2 agent creation
- Multi-agent execution
- Tool loading and execution
- WebSocket streaming
- Chat persistence
- Token tracking

**Don't put here:**
- Module logic
- CRUD operations
- Page rendering
- Request routing

### modules - Module Execution

**Put here:**
- Module loading
- Module execution
- Context injection
- Module events

**Don't put here:**
- AI/workflow logic
- Agent definitions
- Chat handling
- WebSocket transport

### runtime - App Composition

**Put here:**
- App definition loading
- Request routing
- Middleware chain
- Executor registry
- Mode detection
- Unified server

**Don't put here:**
- Workflow implementation
- Module implementation
- Direct business logic

### ui - UI Rendering

**Put here:**
- Page schema
- Navigation building
- Component registry
- Server-side rendering
- React app (frontend/)

**Don't put here:**
- Business logic
- API endpoints
- Data storage

### cli - CLI Tool

**Put here:**
- Project scaffolding
- Dev server commands
- Template management

**Don't put here:**
- Runtime logic
- Business logic

---

## Decision Tree: Where Does This Code Go?

```
Is this a shared interface, type, or utility?
├── YES → packages/core/
└── NO ↓

Is this about AI agents, workflows, or chat?
├── YES → packages/ai/
└── NO ↓

Is this about CRUD modules or plugin logic?
├── YES → packages/modules/
└── NO ↓

Is this about composing AI + modules into an app?
├── YES → packages/runtime/
└── NO ↓

Is this about rendering UI or pages?
├── YES → packages/ui/
└── NO ↓

Is this about CLI commands or project scaffolding?
├── YES → packages/cli/
└── NO → Ask for clarification
```

---

## RED FLAGS - Stop and Reconsider

### Import Violations

❌ **Importing mozaiks_modules from mozaiks_ai**
```python
# IN packages/ai/... - FORBIDDEN
from mozaiks_modules import ModuleExecutor
```
✅ **Use runtime-injected executor**
```python
# IN packages/ai/...
module_executor = context.executors.get("modules")
```

❌ **Importing mozaiks_ai from mozaiks_modules**
```python
# IN packages/modules/... - FORBIDDEN
from mozaiks_ai import WorkflowExecutor
```
✅ **Use events instead**
```python
# IN packages/modules/...
await event_bus.publish(Event.create(...))
```

### Architecture Violations

❌ **Adding HTTP routes in packages/ai/**
- Routes belong in packages/runtime/

❌ **Adding module logic in packages/ai/tools/**
- Tools should call modules via context, not implement CRUD

❌ **Adding workflow logic in packages/modules/**
- Modules handle data, workflows handle orchestration

---

## Testing Boundaries

Before every commit:

```bash
# Check for boundary violations
python scripts/check_boundaries.py

# Run package tests
pytest packages/core/tests
pytest packages/ai/tests
pytest packages/modules/tests
pytest packages/runtime/tests
```

### Boundary Check Script

```python
# scripts/check_boundaries.py

RULES = {
    "packages/core": {
        "forbidden": ["mozaiks_ai", "mozaiks_modules", "mozaiks_runtime", "mozaiks_ui"],
    },
    "packages/ai": {
        "forbidden": ["mozaiks_modules", "mozaiks_runtime", "mozaiks_ui"],
        "allowed": ["mozaiks_core"],
    },
    "packages/modules": {
        "forbidden": ["mozaiks_ai", "mozaiks_runtime", "mozaiks_ui"],
        "allowed": ["mozaiks_core"],
    },
    "packages/ui": {
        "forbidden": ["mozaiks_ai", "mozaiks_modules", "mozaiks_runtime"],
        "allowed": ["mozaiks_core"],
    },
    "packages/runtime": {
        "forbidden": [],
        "allowed": ["mozaiks_core", "mozaiks_ai", "mozaiks_modules", "mozaiks_ui"],
    },
}
```

---

## Execution Modes

### AI-Only Mode

```yaml
# app.yaml
capabilities:
  ai: true
  modules: false
```

- Only packages/ai is loaded
- Module routes return 404
- Tools cannot call modules

### Modules-Only Mode

```yaml
# app.yaml
capabilities:
  ai: false
  modules: true
```

- Only packages/modules is loaded
- No WebSocket chat
- No workflow routes

### Full Mode

```yaml
# app.yaml
capabilities:
  ai: true
  modules: true
```

- Both loaded
- Tools can call modules via context
- Events flow between systems

---

## Event Naming Convention

```
{source}.{domain}.{action}

Examples:
- module.contacts.created
- module.contacts.updated
- module.notes.deleted
- ai.workflow.started
- ai.workflow.completed
- ai.tool.executed
- runtime.request.received
- runtime.error.occurred
```

All events MUST use the Event class from core:

```python
from mozaiks_core.interfaces import Event

event = Event.create(
    event_type="module.contacts.created",
    source="modules",
    app_id=context.app_id,
    payload={"contact_id": "123"},
)
```

---

## Summary

1. **Core is the foundation** - Interfaces only, no implementation
2. **AI and modules are independent** - They never import each other
3. **Runtime composes them** - It's the only place they meet
4. **Tools use context** - Runtime injects executors
5. **Events for async** - Fire-and-forget communication
6. **Modes are explicit** - app.yaml declares what's enabled

When in doubt, ask: "Can this package run standalone?" If yes, you're on the right track.
