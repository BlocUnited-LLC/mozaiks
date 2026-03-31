# mozaiksai — AI Workflow Runtime

This is the AI orchestration runtime. It executes multi-agent workflows using AG2 (AutoGen).

## What This Service Does

- Executes AI agent conversations (AG2 GroupChat)
- Streams events to frontend via WebSocket
- Persists chat sessions to MongoDB
- Handles tool calls from agents
- Manages workflow state (in-progress, completed)
- Token accounting and observability

## Directory Structure

```
mozaiksai/
├── core/
│   ├── adapters/         # AG2-specific orchestration (implements OrchestrationPort)
│   ├── auth/             # JWT validation, WebSocket auth
│   ├── automation/       # Event-driven workflow routing
│   ├── events/           # 3-layer event system (business, UI tools, AG2)
│   ├── multitenant/      # app_id/user_id/chat_id scoping
│   ├── observability/    # Performance tracking, token logging
│   ├── orchestration/    # Universal router, change classification
│   ├── ports/            # Engine-agnostic contracts (OrchestrationPort)
│   ├── transport/        # WebSocket transport, session registry
│   └── workflow/         # Execution patterns, context management
```

## Key Entry Points

| File | Purpose |
|------|---------|
| `core/workflow/orchestration_patterns.py` | Main execution: `run_workflow_orchestration()` |
| `core/ports/orchestration.py` | Engine-agnostic contract: `OrchestrationPort` |
| `core/transport/simple_transport.py` | WebSocket manager: `SimpleTransport` |
| `core/events/unified_event_dispatcher.py` | Event routing: `UnifiedEventDispatcher` |

## Event System (3 Layers)

This runtime has THREE distinct event systems. Don't confuse them.

### 1. Business Events (Observability)
```python
emit_business_event("WORKFLOW_STARTED", "description", context={...})
```
Used for logging, metrics, system lifecycle.

### 2. UI Tool Events (Agent → UI)
```python
emit_ui_tool_event("api_key_input", payload={...}, workflow_name="...")
```
Used for interactive UI components triggered by agents.

### 3. AG2 Runtime Events (Workflow Execution)
```
chat.text, chat.input_request, chat.tool_call, chat.run_complete
```
Streamed via WebSocket during workflow execution.

## How Execution Works

```
WebSocket message received
    ↓
SimpleTransport.receive() parses message
    ↓
run_workflow_orchestration(workflow_name, chat_id, ...)
    ↓
Load workflow config from platform/workflows/{name}/
    ↓
Create AG2 pattern (agents, handoffs, tools)
    ↓
AG2 GroupChat.a_run_group_chat()
    ↓
Stream events via SimpleTransport.broadcast_event()
    ↓
Persist session to MongoDB
```

## Multi-Tenant Isolation

Every operation is scoped by:
- `app_id` — Tenant identifier (required)
- `user_id` — User within tenant (required)
- `chat_id` — Conversation/run identifier (required)

## Engine-Agnostic Boundary

The `OrchestrationPort` protocol in `core/ports/orchestration.py` is the boundary between the runtime and AG2. All AG2-specific code lives in `core/adapters/`. Everything above the port is engine-agnostic.

```python
class OrchestrationPort(Protocol):
    async def run(request: RunRequest) -> RunResult
    async def resume(request: ResumeRequest) -> RunResult
    async def cancel(run_id: str) -> None
```

## Workflows Are Declarative

Workflows are defined in `platform/workflows/{name}/`:
- `workflow.yaml` — Workflow metadata
- `agents.yaml` — Agent definitions
- `tools.yaml` — Tool bindings
- `tools/*.py` — Tool implementations

Don't hardcode workflow behavior in this runtime.
