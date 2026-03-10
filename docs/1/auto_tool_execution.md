# Auto-Tool Execution Guide

## Purpose

This document explains the automatic tool invocation system in MozaiksAI, covering how the runtime automatically detects structured agent outputs, invokes corresponding UI tools, and manages deduplication to ensure tools execute exactly once per agent turn. Understanding auto-tool execution is essential for building predictable, efficient agent-driven workflows.

## Overview

**Prroblem** is that if you have an agent who requires structured outputs to call a tool, this is not possible within the AG2 framework. 

**Auto-tool execution** is the runtime mechanism that **automatically invokes UI tools** when agents with `auto_tool_mode: true` emit structured outputs. This eliminates the need for agents to manually call tools, reducing prompt complexity and ensuring consistent tool invocation patterns.

**Key Benefits:**
- **Simplified Agent Prompts**: Agents emit JSON; runtime handles tool invocation
- **Guaranteed Execution**: Tools always fire when agents produce valid structured outputs
- **Deduplication**: Built-in protection against duplicate tool invocations
- **Event Transparency**: Full observability via `chat.tool_call` and `chat.tool_response` events
- **Error Resilience**: Validation failures don't crash workflows; agents can retry

## Auto-Tool Pattern Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Auto-Tool Execution Flow                          │
└─────────────────────────────────────────────────────────────────────┘

1. Agent Emission
   ┌────────────────┐
   │ ContextAgent   │ auto_tool_mode: true
   │ (AG2 Agent)    │ structured_outputs_required: true
   └───────┬────────┘
           │
           ├─> Emits Structured JSON:
           │   {
           │     "ActionPlan": { "workflow": {...} },
           │     "agent_message": "Review the plan"
           │   }
           │
           v
2. Runtime Validation
   ┌─────────────────────────┐
   │ Structured Output       │
   │ Validation Engine       │
   └───────┬─────────────────┘
           │
           ├─> Validate against Pydantic schema (ActionPlanCall)
           ├─> Schema match? ✅ Continue
           │                 ❌ Retry with error feedback
           v
3. Auto-Tool Detection
   ┌─────────────────────────┐
   │ AutoToolEventHandler    │
   │ (core/events/)          │
   └───────┬─────────────────┘
           │
           ├─> Receives chat.structured_output_ready event
           ├─> Check auto_tool_mode flag
           ├─> Extract model_name, agent_name, structured_data
           │
           v
4. Binding Resolution
   ┌─────────────────────────┐
   │ Workflow Bindings Cache │
   └───────┬─────────────────┘
           │
           ├─> Lookup: workflow → model → AutoToolBinding
           ├─> Binding contains:
           │   - tool_name (e.g., "action_plan")
           │   - function (Python callable)
           │   - param_names (expected parameters)
           │   - ui_config (component, mode)
           │   - model_cls (Pydantic model for validation)
           │
           v
5. Deduplication Check
   ┌─────────────────────────┐
   │ Turn Idempotency Cache  │
   └───────┬─────────────────┘
           │
           ├─> Generate cache_key: "{chat_id}:{turn_idempotency_key}"
           ├─> Check if already processed
           ├─> Duplicate? ⚠️  Skip execution (log warning)
           │             ✅  Continue to invocation
           │
           v
6. Tool Invocation
   ┌─────────────────────────┐
   │ Tool Function Execution │
   │ (workflows/.../tools/)  │
   └───────┬─────────────────┘
           │
           ├─> Build kwargs from structured_data
           ├─> Inject context_variables (chat_id, workflow_name, etc.)
           ├─> Call async tool function
           ├─> Tool emits chat.tool_call event via use_ui_tool(...)
           ├─> Tool waits for user response
           │
           v
7. Event Emission
   ┌─────────────────────────┐
   │ SimpleTransport         │
   │ (WebSocket Layer)       │
   └───────┬─────────────────┘
           │
           ├─> Emit chat.tool_call to frontend
           │   {
           │     "kind": "tool_call",
           │     "tool_name": "action_plan",
           │     "component_type": "ActionPlan",
           │     "payload": {...},
           │     "corr": "turn_abc123",
           │     "awaiting_response": false  // Auto-tool handled internally
           │   }
           │
           └─> Emit chat.tool_response when tool completes
               {
                 "kind": "tool_response",
                 "tool_name": "action_plan",
                 "status": "ok",
                 "success": true,
                 "content": "Tool completed successfully",
                 "corr": "turn_abc123"
               }
```

## Configuration Requirements

### 1. Agent Configuration (agents.json)

```json
{
  "agents": {
    "ContextAgent": {
      "system_message": "[ROLE] You are a workflow architect...",
      "max_consecutive_auto_reply": 5,
      "auto_tool_mode": true,               // ✅ Enable auto-tool
      "structured_outputs_required": true   // ✅ Require schema validation
    }
  }
}
```

**Key Fields:**
- `auto_tool_mode: true`: Enables automatic tool invocation when agent emits structured output
- `structured_outputs_required: true`: Enforces Pydantic schema validation before tool invocation

**System Message Pattern:**

```
[GUIDELINES]
- Do NOT manually call the action_plan tool; runtime invokes UI automatically.
- Provide BOTH: ActionPlan (with nested workflow) AND agent_message.
- agent_message is MANDATORY: one concise (<=140 chars) review invitation.

[OUTPUT FORMAT]
{
  "ActionPlan": { /* nested workflow structure */ },
  "agent_message": "Blueprint ready—review and approve."
}
```

**Critical:** System message should **NOT** include manual tool calling syntax (e.g., `tool_call: action_plan(...)`). Runtime handles invocation automatically.

### 2. Tool Registry (tools.json)

```json
{
  "tools": [
    {
      "agent": "ContextAgent",
      "file": "action_plan.py",
      "function": "action_plan",
      "description": "Render the Action Plan artifact for user review",
      "tool_type": "UI_Tool",               // ✅ Must be UI_Tool
      "ui": {
        "component": "ActionPlan",          // React component name
        "mode": "artifact"                  // Display mode
      }
    }
  ]
}
```

**Matching Rules:**
- `agent` field must match agent name in `agents.json`
- `tool_type` must be `"UI_Tool"` (backend-only Agent_Tools are not auto-invoked)
- `function` must match callable in `workflows/{workflow}/tools/{file}`

### 3. Structured Outputs Registry (structured_outputs.json)

```json
{
  "structured_outputs": {
    "models": {
      "WorkflowStrategyOutput": {
        "type": "model",
        "fields": {
          "WorkflowStrategy": { "type": "WorkflowStrategy", "description": "Workflow structure" }
        }
      }
    },
    "registry": {
      "WorkflowStrategyAgent": "WorkflowStrategyOutput"      // ✅ Map agent to model
    }
  }
}
```

**Binding Logic:**
1. Agent emits JSON → Runtime validates against `WorkflowStrategyOutput` schema
2. Validation succeeds → Runtime looks up `WorkflowStrategyAgent` in registry
3. Registry maps to `WorkflowStrategyOutput` model
4. Runtime finds UI_Tool owned by `WorkflowStrategyAgent`
5. Runtime invokes tool function with validated data

## AutoToolEventHandler Architecture

**Component:** `core/events/auto_tool_handler.py`

### AutoToolBinding Structure

```python
@dataclass(frozen=True)
class AutoToolBinding:
    """Represents the runtime contract for auto-invoked UI tools."""
    
    model_name: str          # Pydantic model name (e.g., "ActionPlanCall")
    agent_name: str          # Owning agent (e.g., "ContextAgent")
    tool_name: str           # Tool identifier (e.g., "action_plan")
    function: Callable       # Python callable from workflows/{workflow}/tools/
    param_names: tuple       # Expected function parameters
    accepts_context: bool    # Whether function accepts context_variables kwarg
    ui_config: Dict          # UI metadata (component, mode)
    model_cls: Any           # Pydantic model class for validation
```

**Creation Process:**

```python
# Simplified binding creation (from _load_bindings_for_workflow)
async def create_binding(workflow_name: str, agent_name: str) -> AutoToolBinding:
    # 1. Load structured outputs registry
    registry = get_structured_outputs_for_workflow(workflow_name)
    model_cls = registry.get(agent_name)  # Get Pydantic model
    
    # 2. Load tool functions
    tool_functions = load_agent_tool_functions(workflow_name)
    agent_funcs = tool_functions.get(agent_name, {})
    
    # 3. Parse tools.json
    tools_data = json.loads(Path(f"workflows/{workflow_name}/tools.json").read_text())
    
    # 4. Find UI_Tool entry for this agent
    for entry in tools_data["tools"]:
        if entry["agent"] == agent_name and entry["tool_type"] == "UI_Tool":
            function_name = entry["function"]
            func = agent_funcs.get(function_name)
            
            # 5. Inspect function signature
            sig = inspect.signature(func)
            param_names = [p for p in sig.parameters if p not in {"self"}]
            accepts_context = "context_variables" in sig.parameters
            
            # 6. Create binding
            return AutoToolBinding(
                model_name=model_cls.__name__,
                agent_name=agent_name,
                tool_name=function_name,
                function=func,
                param_names=tuple(param_names),
                accepts_context=accepts_context,
                ui_config=entry.get("ui", {}),
                model_cls=model_cls,
            )
```

### Event Handling Flow

**Entry Point:** `handle_structured_output_ready(event)`

```python
async def handle_structured_output_ready(self, event: Dict[str, Any]) -> None:
    """Process structured-output-ready event and trigger corresponding tool."""
    
    # Step 1: Extract event data
    auto_mode = bool(event.get("auto_tool_mode"))
    if not auto_mode:
        return  # Not an auto-tool event
    
    agent_name = str(event["agent_name"])
    model_name = str(event["model_name"])
    structured_data = event.get("structured_data")
    context = event.get("context", {})
    workflow_name = str(context.get("workflow_name"))
    chat_id = context.get("chat_id")
    turn_key = str(event.get("turn_idempotency_key", ""))
    
    # Step 2: Deduplication check
    cache_key = f"{chat_id}:{turn_key}"
    if cache_key in self._processed_keys:
        logger.debug("Duplicate turn detected -> skipping (key=%s)", cache_key)
        return
    
    # Step 3: Resolve binding
    binding = await self._resolve_binding(workflow_name, model_name, agent_name)
    if not binding:
        logger.warning("No tool binding for workflow=%s model=%s agent=%s",
                      workflow_name, model_name, agent_name)
        await self._register_turn(cache_key)
        return
    
    # Step 4: Validate structured data
    try:
        validated = binding.model_cls.model_validate(structured_data)
        normalized = validated.model_dump(mode='json')
    except ValidationError as err:
        logger.error("Structured data failed validation: %s", err.errors())
        await self._register_turn(cache_key)
        return
    
    # Step 5: Build tool kwargs
    kwargs = self._build_tool_kwargs(binding, normalized, context)
    
    # Step 6: Emit chat.tool_call event
    await self._emit_tool_call(binding, agent_name, chat_id, kwargs, turn_key)
    
    # Step 7: Invoke tool function
    result_payload, status = await self._invoke_tool(binding, kwargs)
    
    # Step 8: Emit chat.tool_response event
    await self._emit_tool_result(binding, agent_name, chat_id, result_payload, status, turn_key)
    
    # Step 9: Register turn as processed
    await self._register_turn(cache_key)
```

### Parameter Building

**Function:** `_build_tool_kwargs(binding, normalized_payload, context)`

```python
def _build_tool_kwargs(
    self,
    binding: AutoToolBinding,
    normalized_payload: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """Map structured output fields to tool function parameters."""
    
    # Create case-insensitive parameter lookup
    param_lookup = {name.lower(): name for name in binding.param_names}
    
    kwargs = {}
    
    # Map structured output fields to function parameters
    for key, value in normalized_payload.items():
        matched = param_lookup.get(str(key).lower())
        if matched:
            kwargs[matched] = value
    
    # Inject context_variables if function accepts it
    if binding.accepts_context:
        kwargs.setdefault("context_variables", {
            "chat_id": context.get("chat_id"),
            "app_id": context.get("app_id"),
            "workflow_name": context.get("workflow_name"),
            "turn_idempotency_key": context.get("turn_idempotency_key"),
            "agent_name": context.get("agent_name"),
        })
    
    return kwargs
```

**Example Mapping:**

```python
# Structured output (from agent)
{
  "ActionPlan": {"workflow": {"name": "Example", ...}},
  "agent_message": "Review the plan"
}

# Tool function signature
async def action_plan(
    ActionPlan: dict,
    agent_message: str,
    context_variables: Any = None,
) -> dict:
    ...

# Built kwargs
{
  "ActionPlan": {"workflow": {"name": "Example", ...}},
  "agent_message": "Review the plan",
  "context_variables": {
    "chat_id": "chat_abc123",
    "workflow_name": "Generator",
    "turn_idempotency_key": "turn_xyz789",
    ...
  }
}
```

## Deduplication Mechanism

### Problem Statement

Without deduplication, the same tool could execute multiple times for a single agent turn due to:
- **Event replays** during connection interruptions
- **Message retries** from AG2 internal logic
- **Duplicate emissions** from orchestration layer
- **Race conditions** in async event processing

### Solution: Turn Idempotency Cache

**Data Structure:**

```python
class AutoToolEventHandler:
    def __init__(self):
        self._processed_keys: set[str] = set()            # Fast lookup
        self._processed_order: asyncio.Queue[str] = asyncio.Queue()  # FIFO eviction
```

**Cache Key Generation:**

```python
cache_key = f"{chat_id}:{turn_idempotency_key}"

# Example:
# chat_id = "chat_abc123"
# turn_idempotency_key = "turn_xyz789"
# cache_key = "chat_abc123:turn_xyz789"
```

**Deduplication Check:**

```python
if cache_key in self._processed_keys:
    logger.debug("Duplicate turn detected -> skipping (key=%s)", cache_key)
    return  # Exit early; do not invoke tool
```

**Registration:**

```python
async def _register_turn(self, cache_key: str) -> None:
    """Mark turn as processed and enforce cache size limit."""
    
    if cache_key in self._processed_keys:
        return  # Already registered
    
    # Add to set
    self._processed_keys.add(cache_key)
    
    # Add to queue for FIFO eviction
    try:
        self._processed_order.put_nowait(cache_key)
    except asyncio.QueueFull:
        pass
    
    # Enforce cache size limit (512 entries)
    while len(self._processed_keys) > CACHE_LIMIT:
        try:
            oldest = self._processed_order.get_nowait()
            self._processed_keys.discard(oldest)
        except asyncio.QueueEmpty:
            break
```

**Cache Size Limit:**

```python
_CACHE_LIMIT = 512  # Maximum cached turn keys
```

**Why 512?**
- Balances memory usage (~50KB for 512 string keys) with deduplication coverage
- Covers ~512 agent turns across all active chats
- Unlikely to see duplicate events beyond 512 turns in practice

### Turn Idempotency Key

**Generation:** `core/workflow/orchestration_patterns.py`

```python
turn_idempotency_key = f"turn_{chat_id}_{agent_name}_{turn_number}_{timestamp_hash}"

# Example:
# "turn_chat_abc123_ContextAgent_5_e4f2a1b"
```

**Components:**
- `chat_id`: Unique chat session identifier
- `agent_name`: Agent producing the output
- `turn_number`: Monotonically increasing turn counter
- `timestamp_hash`: Short hash of timestamp for additional uniqueness

**Properties:**
- **Deterministic**: Same turn always generates same key
- **Unique**: Different turns generate different keys
- **Collision-resistant**: Timestamp hash prevents same-turn collisions
- **Debugging-friendly**: Human-readable components

## Event Emission

### Tool Call Event

**Emitted By:** `AutoToolEventHandler._emit_tool_call`

**Purpose:** Notify frontend that UI tool is being invoked (for observability)

**Event Structure:**

```json
{
  "kind": "tool_call",
  "agent": "ContextAgent",
  "tool_name": "action_plan",
  "tool_call_id": "turn_chat_abc123_ContextAgent_5_e4f2a1b",
  "corr": "turn_chat_abc123_ContextAgent_5_e4f2a1b",
  "awaiting_response": false,
  "component_type": "ActionPlan",
  "payload": {
    "tool_args": {
      "ActionPlan": { /* workflow structure */ },
      "agent_message": "Review the plan"
    },
    "agent_name": "ContextAgent",
    "interaction_type": "auto_tool",
    "workflow_name": "Generator"
  }
}
```

**Key Fields:**
- `kind`: Always `"tool_call"`
- `tool_name`: Tool identifier (matches `tools.json`)
- `tool_call_id` / `corr`: Turn idempotency key for correlation
- `awaiting_response`: `false` for auto-tools (handled internally by tool function)
- `component_type`: React component to render
- `payload.interaction_type`: `"auto_tool"` indicates automatic invocation

**Frontend Handling:**

Frontend receives this event and performs **two-stage payload processing**:

**Stage 1: Flatten `tool_args`**

The backend sends auto-tool payloads with arguments nested under `tool_args`:

```javascript
// Backend payload structure:
{
  tool_args: {
    ActionPlan: { workflow: {...} },
    agent_message: "Review the plan"
  },
  interaction_type: "auto_tool"
}
```

The frontend first extracts and flattens `tool_args`:

```javascript
if (basePayload.interaction_type === 'auto_tool' && basePayload.tool_args) {
  const { tool_args, ...restPayload } = basePayload;
  basePayload = { ...restPayload, ...tool_args };
}
```

**Stage 2: Extract Nested Pydantic Models**

For Pydantic structured outputs, the model name (e.g., `ActionPlan`) often becomes a nested key. The frontend detects this pattern and promotes the nested content:

```javascript
// After Stage 1 flattening:
{
  ActionPlan: { workflow: {...} },    // ← Still nested
  agent_message: "Review the plan",
  interaction_type: "auto_tool"
}

// Nested extraction (if key matches component_type):
const nestedKey = Object.keys(tool_args).find(key => 
  key === componentType && typeof tool_args[key] === 'object'
);

if (nestedKey) {
  const { [nestedKey]: nestedContent, ...otherToolArgs } = tool_args;
  basePayload = {
    ...restPayload,
    ...nestedContent,     // ← workflow: {...} promoted to top level
    ...otherToolArgs      // ← agent_message preserved
  };
}

// After Stage 2 extraction:
{
  workflow: {...},                    // ← Promoted from ActionPlan.workflow
  agent_message: "Review the plan",
  interaction_type: "auto_tool"
}
```

**Why Two Stages?**

1. **`tool_args` flattening**: Unwraps the generic container that holds all tool parameters
2. **Nested model extraction**: Handles Pydantic structured output pattern where the model class name becomes a wrapper key

This processing ensures UI components receive a **flat payload** with all fields at the top level, regardless of how deeply nested the backend Pydantic model structure is.

**Display & Logging:**

Frontend can also:
- Display loading indicator for tool execution
- Show tool name in conversation timeline
- Log tool invocations for debugging

**Important:** `awaiting_response: false` means frontend should **not** wait for user input. The tool function itself handles any necessary UI interactions via `use_ui_tool(...)`.

### Tool Result Event

**Emitted By:** `AutoToolEventHandler._emit_tool_result`

**Purpose:** Notify frontend that tool execution completed

**Event Structure (Success):**

```json
{
  "kind": "tool_response",
  "agent": "ContextAgent",
  "tool_name": "action_plan",
  "call_id": "turn_chat_abc123_ContextAgent_5_e4f2a1b",
  "corr": "turn_chat_abc123_ContextAgent_5_e4f2a1b",
  "status": "ok",
  "success": true,
  "content": "Tool action_plan completed successfully.",
  "interaction_type": "auto_tool",
  "payload": {
    "status": "success",
    "ui_event_id": "ui_tool_def456",
    "data": { /* user response from UI component */ }
  }
}
```

**Event Structure (Error):**

```json
{
  "kind": "tool_response",
  "agent": "ContextAgent",
  "tool_name": "action_plan",
  "call_id": "turn_chat_abc123_ContextAgent_5_e4f2a1b",
  "corr": "turn_chat_abc123_ContextAgent_5_e4f2a1b",
  "status": "error",
  "success": false,
  "content": "Tool action_plan reported status error.",
  "interaction_type": "auto_tool",
  "payload": {
    "status": "error",
    "message": "User cancelled operation",
    "code": "user_cancelled"
  }
}
```

**Key Fields:**
- `interaction_type`: `"auto_tool"` marks this as an auto-tool response (used by frontend to filter intermediate events)
- `success`: `true` if tool executed without errors, `false` otherwise
- `status`: Handler execution status (`"ok"` or `"error"`)
- `content`: Human-readable message about tool execution

**Frontend Handling:**

Frontend **suppresses successful auto-tool responses** to avoid showing confusing "Tool Response" messages for intermediate events that are already handled by the UI renderer:

```javascript
if (data.interaction_type === 'auto_tool' && data.success) {
  console.log(`⏭️ Skipping auto-tool success response - handled by UI renderer`);
  return; // Don't show message in chat
}
```

**Why Suppress Successes?**
- Auto-tool agents emit **multiple tool_response events** (e.g., `action_plan`, `mermaid_sequence_diagram`, final UI tool)
- Only the final event with `display: "artifact"` is rendered by the UI
- Intermediate successes are expected behavior and clutter the chat
- **Failures are still shown** for debugging purposes

**Success Determination:**

```python
success = (
    status == "ok" 
    and not (isinstance(result, dict) and result.get('status') in {"error", "failed"})
)
```

Tool is considered successful if:
- Handler `status` is `"ok"` (no exceptions during execution)
- Tool return value doesn't indicate error (no `status: "error"` in result dict)

## UI_HIDDEN Trigger Pattern

### Purpose

**UI_HIDDEN** is a coordination mechanism that allows agents to emit internal state transition tokens (like `NEXT`, `PROCEED`, `TERMINATE`) that:
1. **Trigger derived variables** to flip state (e.g., `interview_complete = true`)
2. **Enable conditional handoffs** based on state
3. **Hide from frontend** to avoid confusing users with internal coordination tokens

### Configuration

**Context Variables (context_variables.json):**

```json
{
  "derived_variables": [
    {
      "name": "interview_complete",
      "trigger_type": "agent_text_equals",
      "source_agent": "InterviewAgent",
      "trigger_value": "NEXT",
      "description": "True once InterviewAgent has enough context to proceed",
      "ui_hidden": true    // ✅ Hide "NEXT" message from frontend
    }
  ]
}
```

**Agent System Message:**

```
[INSTRUCTIONS]
...
Step 2 - After the user's reply:
- Emit only NEXT on its own line to signal the downstream handoff.

[OUTPUT FORMAT]
Turn 1:
What would you like to automate?

Context Variables:
CONCEPT_OVERVIEW: ...
CONTEXT_AWARE: true

Turn 2:
NEXT
```

**Critical Constraint:** Agent must emit **exact** trigger value (`NEXT`) with no extra text. Runtime performs **exact string match**:

```python
if message_content.strip() == trigger_value:
    derived_variable["value"] = True
    
    if ui_hidden:
        # Suppress rendering this message in frontend
        message["ui_hidden"] = True
```

### Execution Flow

```
1. InterviewAgent emits: "NEXT"
   ↓
2. Runtime detects agent_text_equals trigger
   ↓
3. Checks: message_content.strip() == "NEXT"
   ✅ Match → Set interview_complete = True
   ↓
4. Checks: ui_hidden flag in derived variable definition
   ✅ True → Mark message with ui_hidden: true
   ↓
5. Frontend receives message with ui_hidden: true
   → Message not rendered in chat UI
   → State variable still accessible for handoff conditions
   ↓
6. Handoff evaluation:
   condition: "${interview_complete} == True"
   ✅ True → Transition to next agent
```

### Frontend Message Filtering

**Message Renderer:**

```javascript
const MessageRenderer = ({ message }) => {
  // Check ui_hidden flag
  if (message.ui_hidden === true) {
    return null;  // Don't render this message
  }
  
  return (
    <div className="message">
      {message.content}
    </div>
  );
};
```

**Conversation State:**

```javascript
// Backend maintains full message history (including hidden)
backend_messages = [
  {"role": "assistant", "content": "What would you like to automate?"},
  {"role": "user", "content": "Automate my social media"},
  {"role": "assistant", "content": "NEXT", "ui_hidden": true},  // Hidden
  {"role": "assistant", "content": "Planning your workflow..."}
]

// Frontend filters for display
visible_messages = backend_messages.filter(m => !m.ui_hidden)
// Result: First, second, and fourth messages only
```

### Best Practices

**1. Use Exact Tokens:**

```
✅ GOOD:
Turn 2:
NEXT

❌ BAD:
Turn 2:
Proceeding to next phase: NEXT

❌ BAD:
Turn 2:
NEXT.

❌ BAD:
Turn 2:
next  (case-sensitive)
```

**2. Document in System Message:**

```
[INSTRUCTIONS]
...
Step 2 - After the user's reply:
- Emit only NEXT on its own line and nothing else.
- Never emit NEXT before the user responds, and never append punctuation or additional words to it.
```

**3. Use for Internal Coordination Only:**

```
✅ GOOD use cases:
- Phase transition tokens (NEXT, PROCEED, TERMINATE)
- Internal routing signals
- Workflow stage markers

❌ BAD use cases:
- User-visible approvals (use "Approved", "Rejected" with ui_hidden: false)
- Status updates (show these to users)
- Error messages (users need to see these)
```

**4. Coordinate with Handoff Rules:**

```json
// context_variables.json
{
  "derived_variables": [
    {
      "name": "interview_complete",
      "trigger_value": "NEXT",
      "ui_hidden": true
    }
  ]
}

// handoffs.json
{
  "handoff_rules": [
    {
      "source_agent": "InterviewAgent",
      "target_agent": "ContextAgent",
      "handoff_type": "condition",
      "condition": "${interview_complete} == True",
      "transition_target": "AgentTarget"
    }
  ]
}
```

## Validation and Error Handling

### Schema Validation Failures

**Scenario:** Agent emits JSON that doesn't match registered model

```python
# Agent emits (INVALID - missing required field)
{
  "ActionPlan": { /* workflow */ }
  # Missing "agent_message"
}

# AutoToolEventHandler catches ValidationError
except ValidationError as err:
    logger.error("Structured data failed validation: %s", err.errors())
    await self._register_turn(cache_key)  # Prevent retry loops
    return  # Skip tool invocation
```

**Agent Retry Pattern:**

```python
# Orchestration layer injects error feedback
error_message = {
    "role": "user",
    "content": f"Your output did not match the required schema. Error: {err}"
}

# Agent sees error in next turn and can retry
Agent: "I apologize. Here's the corrected output with agent_message included..."
```

**Best Practice:** System messages should document exact JSON structure to minimize validation failures.

### Tool Execution Failures

**Scenario:** Tool function raises exception

```python
async def _invoke_tool(
    self, binding: AutoToolBinding, kwargs: Dict[str, Any]
) -> tuple[Any, str]:
    """Execute tool function and capture result."""
    try:
        result = binding.function(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result, "ok"
    except Exception as exc:
        logger.exception("Tool execution failed for agent=%s tool=%s",
                        binding.agent_name, binding.tool_name)
        return {"status": "error", "message": str(exc)}, "error"
```

**Error Propagation:**

```
Tool raises exception
  ↓
AutoToolEventHandler catches
  ↓
Emits chat.tool_response with status="error"
  ↓
Frontend displays error (if UI component subscribes)
  ↓
Agent receives error in next turn context
  ↓
Agent can retry, skip, or terminate workflow
```

### Binding Resolution Failures

**Scenario:** No tool found for agent/model combination

```python
binding = await self._resolve_binding(workflow_name, model_name, agent_name)
if not binding:
    logger.warning("No tool binding for workflow=%s model=%s agent=%s",
                  workflow_name, model_name, agent_name)
    await self._register_turn(cache_key)  # Prevent retry
    return
```

**Common Causes:**
1. Agent has `auto_tool_mode: true` but no UI_Tool in `tools.json`
2. Agent not registered in `structured_outputs.json` registry
3. Model name mismatch between registry and schema definition
4. Tool function not loaded (file missing or syntax error)

**Fix:** Ensure complete auto-tool configuration (see Configuration Requirements above).

## Performance Considerations

### Binding Cache

**Problem:** Loading bindings from disk on every event is expensive

**Solution:** In-memory workflow bindings cache

```python
class AutoToolEventHandler:
    def __init__(self):
        self._workflow_bindings: Dict[str, Dict[str, AutoToolBinding]] = {}
        # Structure: {workflow_name: {model_name: AutoToolBinding}}
```

**Cache Population:**

```python
async def _load_bindings_for_workflow(self, workflow_name: str):
    # Check cache first
    cached = self._workflow_bindings.get(workflow_name)
    if cached is not None:
        return cached  # ⚡ Fast path
    
    # Cache miss: Load from disk
    mapping = {}
    # ... load structured outputs, tools.json, function modules ...
    
    # Store in cache
    self._workflow_bindings[workflow_name] = mapping
    return mapping
```

**Cache Lifetime:** Lives for entire runtime process; never invalidated (assumes static workflow configurations)

**Memory Usage:** ~1-5 KB per binding × ~10 bindings per workflow × ~5 workflows = ~50-250 KB total (negligible)

### Deduplication Cache Eviction

**LRU-Like Strategy:**

```python
_CACHE_LIMIT = 512  # Bounded cache size

# FIFO eviction when limit reached
while len(self._processed_keys) > CACHE_LIMIT:
    oldest = self._processed_order.get_nowait()
    self._processed_keys.discard(oldest)
```

**Why FIFO (not LRU)?**
- Simpler implementation (no access tracking)
- Turn keys are naturally chronological
- Oldest turns least likely to be replayed
- Eviction is rare in practice (512-turn window)

**Performance:** O(1) lookup, O(1) insertion, O(k) eviction where k = entries beyond limit (typically 1)

### Async Tool Invocation

**Non-Blocking Execution:**

```python
result = binding.function(**kwargs)
if inspect.isawaitable(result):
    result = await result  # Await only if async
```

**Benefit:** Supports both sync and async tool functions without performance penalty

**Pattern:**

```python
# Async tool (most UI tools)
async def action_plan(...) -> dict:
    response = await use_ui_tool(...)  # Awaits user interaction
    return response

# Sync tool (rare, for simple operations)
def echo(...) -> dict:
    return {"echoed": message}
```

## Debugging

### Enable Debug Logging

```bash
# Set environment variables
export LOG_LEVEL=DEBUG
export LOGS_AS_JSON=false

# Restart server
python run_server.py
```

**Log Output:**

```
[AUTO_TOOL] Received structured_output_ready event: agent=ContextAgent turn=turn_chat_abc123_ContextAgent_5_e4f2a1b
[AUTO_TOOL] Processing auto tool turn=turn_chat_abc123_ContextAgent_5_e4f2a1b for agent=ContextAgent workflow=Generator
[AUTO_TOOL] Binding resolved for agent=ContextAgent tool=action_plan model=ActionPlanCall
[AUTO_TOOL] Prepared kwargs for action_plan: {'ActionPlan': {...}, 'agent_message': '...'}
[AUTO_TOOL] Emitting chat.tool_call for agent=ContextAgent tool=action_plan turn=turn_chat_abc123_ContextAgent_5_e4f2a1b
[AUTO_TOOL] Emitting chat.tool_response for agent=ContextAgent tool=action_plan status=ok turn=turn_chat_abc123_ContextAgent_5_e4f2a1b
```

### Common Issues

**Auto-Tool Not Invoking:**

**Symptom:** Agent emits structured output but tool never executes

**Checks:**
1. Agent has `auto_tool_mode: true` and `structured_outputs_required: true`
2. Agent registered in `structured_outputs.json` registry
3. Tool exists with `tool_type: "UI_Tool"` owned by agent
4. Structured output matches registered model schema
5. Check logs for validation errors

**Debug Commands:**

```bash
# Check agent configuration
grep -A 5 '"ContextAgent"' workflows/Generator/agents.json

# Check registry mapping
grep -A 2 '"registry"' workflows/Generator/structured_outputs.json

# Check tool registration
grep -A 10 '"agent": "ContextAgent"' workflows/Generator/tools.json

# Tail logs for auto-tool events
tail -f logs/runtime_*.log | grep "AUTO_TOOL"
```

**Duplicate Tool Invocations:**

**Symptom:** Same tool executes multiple times for single agent turn

**Checks:**
1. Verify turn_idempotency_key is unique per turn
2. Check deduplication cache is functioning (look for "Duplicate turn detected" logs)
3. Ensure tool function doesn't call itself recursively
4. Verify frontend doesn't emit duplicate `chat.tool_response` events

**Fix:**

```python
# Check cache key generation
logger.debug("Cache key: %s", cache_key)

# Verify cache hit
if cache_key in self._processed_keys:
    logger.warning("DUPLICATE DETECTED: %s", cache_key)
```

**Validation Failures:**

**Symptom:** Agent output rejected with schema validation error

**Debug:**

```python
# Log exact validation errors
except ValidationError as err:
    logger.error("Validation errors: %s", err.errors())
    # Example output:
    # [{'loc': ('agent_message',), 'msg': 'field required', 'type': 'value_error.missing'}]
```

**Fix:** Update agent system message to include missing fields in output format.

## Best Practices

### Agent System Messages

1. **Document Exact Schema:** Include JSON structure in `[OUTPUT FORMAT]` section
2. **No Manual Tool Calls:** Don't include tool calling syntax for auto-tools
3. **Required Fields:** Explicitly mark which fields are mandatory
4. **Type Examples:** Show example values for each field type

### Tool Implementation

1. **Accept context_variables:** Always include `context_variables` parameter
2. **Validate Inputs:** Check required fields before processing
3. **Return Structured Responses:** Always return dict with `status` key
4. **Handle Errors Gracefully:** Catch exceptions and return error dicts
5. **Log Appropriately:** Use workflow logger for debugging

### Workflow Configuration

1. **Complete Auto-Tool Setup:** Ensure all three manifests properly configured
2. **Test Validation:** Verify schema matches agent outputs before deployment
3. **Monitor Logs:** Watch for validation failures and binding errors
4. **Deduplication Awareness:** Understand turn keys prevent duplicate execution

### UI_HIDDEN Usage

1. **Internal Tokens Only:** Use for coordination tokens, not user-facing messages
2. **Exact Matching:** Document exact trigger values in system messages
3. **Coordinate with Handoffs:** Ensure derived variables used in handoff conditions
4. **Test Visibility:** Verify hidden messages don't appear in frontend

## Next Steps

- **[UI Tool Pipeline](./ui_tool_pipeline.md)**: Complete UI tool execution lifecycle
- **[Structured Outputs Guide](./structured_outputs.md)**: Schema design for auto-tool agents
- **[Tool Manifest Reference](./tool_manifest.md)**: Tool registration patterns
- **[Workflow Authoring Guide](./workflow_authoring.md)**: Complete workflow creation guide
- **[Event Pipeline](../runtime/event_pipeline.md)**: Event routing and correlation
- **[Observability](../runtime/observability.md)**: Monitoring auto-tool execution
