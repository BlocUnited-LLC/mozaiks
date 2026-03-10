# Lifecycle Tools - Workflow Orchestration Hooks

## Overview

Lifecycle tools provide a declarative way to execute custom logic at specific points in the workflow orchestration lifecycle. This feature enables workflows to run initialization, cleanup, monitoring, or state management code without modifying the core runtime.

**Platform Defaults**: The runtime automatically loads workflow start/end logging from `logs/workflow_lifecycle.py` for ALL workflows (always-on observability). Workflows can define additional lifecycle tools via `tools.json`.

## Architecture

- **Platform Defaults**: Auto-loaded workflow observability tools (start/end logging) from `logs/workflow_lifecycle.py`
- **Declarative Configuration**: Workflow-specific tools defined in `workflows/<workflow>/tools.json`
- **Workflow-Agnostic**: Each workflow can declare custom lifecycle tools
- **AG2-Native Context**: Tools receive runtime context via AG2's `ContextVariables` dependency injection
- **Event Integration**: Lifecycle tool execution emits events to the unified event system for observability
- **Non-Blocking**: Failures log but don't halt workflow execution (error-tolerant design)

## Trigger Points

### 1. `before_chat`
- **When**: Executed once after AG2 pattern initialization, before the event stream loop begins
- **Use Cases**: 
  - Workflow initialization logging
  - Loading external resources
  - Setting up monitoring/tracing context
  - Validating prerequisites
- **Agent Scope**: Chat-level (agent field must be `null`)

### 2. `after_chat`
- **When**: Executed once after the workflow completes successfully, before final cleanup
- **Use Cases**:
  - Cleanup temporary resources
  - Final metrics/analytics submission
  - Workflow completion notifications
  - State persistence or archival
- **Agent Scope**: Chat-level (agent field must be `null`)

### 3. `before_agent`
- **When**: Executed immediately before an agent's turn begins (after `SelectSpeakerEvent`)
- **Use Cases**:
  - Agent-specific context setup
  - Per-agent metrics initialization
  - Loading agent-specific configuration
  - Pre-turn validation
- **Agent Scope**: Agent-level (set `agent` field to specific agent name) or all agents (agent field `null`)

### 4. `after_agent`
- **When**: Executed immediately after an agent's turn completes (before next `SelectSpeakerEvent`)
- **Use Cases**:
  - Agent-specific cleanup
  - Per-agent metrics finalization
  - State synchronization
  - Post-turn validation
- **Agent Scope**: Agent-level (set `agent` field to specific agent name) or all agents (agent field `null`)

## Schema

Add a `lifecycle_tools` array to your workflow's `tools.json`:

```json
{
  "tools": [ /* standard tools */ ],
  "lifecycle_tools": [
    {
      "trigger": "before_chat",        // Required: before_chat | after_chat | before_agent | after_agent
      "agent": null,                   // Required: null for chat-level, agent_name for agent-specific
      "file": "workflow_init.py",      // Required: Python file (root or tools/ subdir)
      "function": "initialize",        // Required: Function name
      "description": "..."             // Optional: For logging/observability
    }
  ]
}
```

### Field Definitions

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `trigger` | string | Yes | One of: `before_chat`, `after_chat`, `before_agent`, `after_agent` |
| `agent` | string\|null | Yes | `null` for chat-level hooks, agent name for agent-specific hooks |
| `file` | string | Yes | Python file path (supports root or `tools/` subdirectory) |
| `function` | string | Yes | Function name to invoke |
| `description` | string | No | Purpose description for logging/observability |

## Tool Function Contract

### Signature

Lifecycle tools can accept an optional `context_variables` parameter for AG2's native dependency injection:

```python
# Basic lifecycle tool (no context)
def my_lifecycle_hook():
    """Execute lifecycle logic without context."""
    print("Lifecycle hook executed")

# Context-aware lifecycle tool (recommended)
def my_lifecycle_hook_with_context(context_variables):
    """Execute lifecycle logic with runtime context.
    
    Args:
        context_variables: AG2 ContextVariables instance containing:
            - workflow_name: Current workflow name
            - app_id: App/tenant ID
            - chat_id: Chat session ID
            - user_id: User ID
            - concept_overview: Platform concept data (if CONTEXT_AWARE=true)
            - schema_overview: Platform schema data (if CONTEXT_AWARE=true)
            - <workflow-specific keys>: From context_variables.json
    """
    # Access context data
    workflow = context_variables.data.get('workflow_name')
    chat_id = context_variables.data.get('chat_id')
    
    print(f"Lifecycle hook for workflow={workflow}, chat={chat_id}")
    
    # Optionally update context (mutations are visible to subsequent tools/agents)
    context_variables.data['lifecycle_timestamp'] = datetime.now().isoformat()
```

### Async Support

Both sync and async functions are supported:

```python
async def async_lifecycle_hook(context_variables):
    """Async lifecycle tool with I/O operations."""
    await some_async_operation()
    
    # Update context
    context_variables.data['resource_id'] = await allocate_resource()
```

### Return Values

Return values are logged but not consumed by the runtime. Use them for observability:

```python
def my_hook(context_variables):
    """Return status for observability."""
    result = perform_operation()
    return {"status": "success", "items_processed": result.count}
```

## Examples

### Platform-Default Tools (Always-On)

The runtime automatically loads these for ALL workflows:

**`logs/workflow_lifecycle.py`:**
- `log_workflow_start` (before_chat) - Logs workflow initialization with chat_id, user_id, app_id, timestamp
- `log_workflow_completion` (after_chat) - Logs workflow completion with duration metrics

**Example Log Output:**
```
🚀 Workflow Initialized | workflow=Generator | chat=chat_abc123 | user=user_xyz | app=ent_001 | timestamp=2025-10-10T14:32:15.123Z
✅ Workflow Completed | workflow=Generator | chat=chat_abc123 | duration=42.37s | timestamp=2025-10-10T14:32:57.456Z
```

**Note**: Platform defaults do NOT need to be declared in `tools.json` - they load automatically.

### Example 1: Workflow Initialization Logger

```json
{
  "lifecycle_tools": [
    {
      "trigger": "before_chat",
      "agent": null,
      "file": "workflow_logger.py",
      "function": "log_workflow_start",
      "description": "Log workflow initialization for observability"
    }
  ]
}
```

**`tools/workflow_logger.py`:**
```python
import logging
from datetime import datetime, UTC

logger = logging.getLogger(__name__)

def log_workflow_start(context_variables):
    """Log workflow start with context details."""
    workflow = context_variables.data.get('workflow_name', 'unknown')
    chat_id = context_variables.data.get('chat_id', 'unknown')
    user_id = context_variables.data.get('user_id', 'unknown')
    
    logger.info(
        f"🚀 Workflow started: {workflow} | chat={chat_id} | user={user_id} | "
        f"timestamp={datetime.now(UTC).isoformat()}"
    )
    
    # Set initialization timestamp in context
    context_variables.data['workflow_start_time'] = datetime.now(UTC).isoformat()
```

### Example 2: Agent-Specific Resource Allocation

```json
{
  "lifecycle_tools": [
    {
      "trigger": "before_agent",
      "agent": "DataProcessorAgent",
      "file": "setup_data_context.py",
      "function": "allocate_data_resources",
      "description": "Allocate database connections before DataProcessorAgent"
    },
    {
      "trigger": "after_agent",
      "agent": "DataProcessorAgent",
      "file": "cleanup_data_context.py",
      "function": "release_data_resources",
      "description": "Release database connections after DataProcessorAgent"
    }
  ]
}
```

**`tools/setup_data_context.py`:**
```python
async def allocate_data_resources(context_variables):
    """Allocate database connection pool for DataProcessorAgent."""
    db_pool = await create_db_pool()
    
    # Store in context for agent access
    context_variables.data['db_connection'] = db_pool
    
    return {"status": "allocated", "pool_size": db_pool.size}
```

**`tools/cleanup_data_context.py`:**
```python
async def release_data_resources(context_variables):
    """Release database connection pool."""
    db_pool = context_variables.data.get('db_connection')
    if db_pool:
        await db_pool.close()
        del context_variables.data['db_connection']
    
    return {"status": "released"}
```

### Example 3: Metrics & Cleanup

```json
{
  "lifecycle_tools": [
    {
      "trigger": "after_chat",
      "agent": null,
      "file": "finalize_metrics.py",
      "function": "submit_workflow_metrics",
      "description": "Submit final workflow metrics to analytics"
    },
    {
      "trigger": "after_chat",
      "agent": null,
      "file": "cleanup_temp_files.py",
      "function": "cleanup_temporary_files",
      "description": "Remove temporary files created during workflow"
    }
  ]
}
```

### Example 4: All-Agent Hook (Logging)

```json
{
  "lifecycle_tools": [
    {
      "trigger": "before_agent",
      "agent": null,
      "file": "agent_logger.py",
      "function": "log_agent_start",
      "description": "Log every agent turn start"
    }
  ]
}
```

This hook runs before EVERY agent's turn (agent=null means "all agents").

## Observability

Lifecycle tool execution emits business events through the unified event dispatcher:

- **Event Type**: `lifecycle.tool_call` (on invocation)
- **Event Type**: `lifecycle.tool_result` (on completion)
- **Event Type**: `lifecycle.tool_error` (on failure)

**Event Payload:**
```json
{
  "trigger": "before_chat",
  "agent": null,
  "function": "log_workflow_start",
  "file": "workflow_logger.py",
  "status": "success",
  "elapsed_ms": 12.5,
  "result": "..."
}
```

## Error Handling

- Lifecycle tools are **non-blocking**: failures log errors but don't halt workflow execution
- Exceptions are caught, logged with full stack traces, and emitted as `lifecycle.tool_error` events
- The workflow continues even if lifecycle tools fail (resilient design)

## Best Practices

1. **Keep Tools Lightweight**: Avoid heavy computation in lifecycle hooks; they run in the main event loop
2. **Use Async for I/O**: Prefer async functions for network/disk operations
3. **Minimal Side Effects**: Lifecycle tools should be idempotent where possible
4. **Context Awareness**: Leverage `context_variables` for runtime data instead of globals
5. **Observability First**: Return status objects for metrics; use structured logging
6. **Error Recovery**: Handle exceptions gracefully; don't assume resources exist
7. **Agent-Specific vs. Global**: Use `agent: null` for global hooks, specific names for targeted setup/cleanup
8. **Sequential Ordering**: Tools in the array execute in order; order matters for dependencies

## Integration Points

### Runtime Loading
- Lifecycle tools are loaded by `LifecycleToolManager` during workflow discovery
- Tool functions are dynamically imported from workflow directories (no sys.modules caching)

### Orchestration Hooks
- `before_chat`: Triggered in `run_workflow_orchestration` before pattern execution
- `after_chat`: Triggered in `run_workflow_orchestration` after completion (before final cleanup)
- `before_agent`: Triggered in `_stream_events` when `SelectSpeakerEvent` identifies next agent
- `after_agent`: Triggered in `_stream_events` when previous agent's turn completes

### Context Bridge
- AG2's `ContextVariables` instance is passed directly (same instance used by agents)
- Mutations to `context_variables.data` are visible to downstream tools and agents
- No manual parameter injection needed (AG2 dependency injection handles it)

### Event Stream Compensation (Synthetic Events)

**Important**: Lifecycle tools that pause workflow execution (e.g., waiting for user input via `InputRequestEvent`) trigger the runtime's **synthetic event system** to maintain frontend visibility.

**Why?**: When AG2 resumes after a pause (e.g., after `collect_api_keys_from_action_plan` completes), it does NOT re-emit `SelectSpeakerEvent` for the next agent. This would cause thinking bubbles to disappear in the frontend, breaking the user's sense of workflow progression.

**Runtime Compensation**: The orchestration layer detects speaker changes after resume and automatically emits synthetic `select_speaker` events to fill the gap. This ensures:
- Thinking bubbles appear for every agent turn (even after pauses)
- Frontend receives unbroken event stream
- User experience remains continuous across lifecycle tool execution

**See**: [Synthetic Events Documentation](synthetic_events.md) for complete technical details on how the runtime compensates for AG2's resume behavior.

## Limitations

1. **Sync Runtime**: Tools run in the main async event loop; blocking operations should use async
2. **No Return Value Consumption**: Return values are logged but not used by the runtime
3. **No Inter-Tool Communication**: Lifecycle tools don't directly communicate (use context_variables instead)
4. **Execution Order**: Tools execute sequentially, not in parallel (by design for predictability)

## Future Enhancements (Roadmap)

- Conditional execution (skip tools based on context flags)
- Tool dependencies (explicit ordering constraints)
- Timeout configuration per tool
- Retry logic for transient failures
- Lifecycle tool telemetry dashboard

---

**Status**: Production-ready (v1.0)  
**Owner**: MozaiksAI Runtime Team  
**Last Updated**: 2025-01-09
