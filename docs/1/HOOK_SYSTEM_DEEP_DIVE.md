# Hook System Deep Dive - MozaiksAI Generator Workflow

## Overview

MozaiksAI uses **two parallel hook mechanisms** to modify agent behavior at runtime:

1. **AG2's Native Hook System** (`register_hook()`) - For message processing and capabilities
2. **AG2's Update Agent State** (`update_agent_state_before_reply` constructor parameter) - For system message injection

---

## AG2 Hook System Architecture

### Supported Hook Types

AG2's `ConversableAgent` supports these hookable methods (via `agent.register_hook()`):

| Hook Type | Signature | When It Fires | Use Case |
|-----------|-----------|---------------|----------|
| `process_message_before_send` | `(sender, message, recipient, silent) -> message` | Before agent sends ANY message | Message transformation, auto-responses |
| `process_last_received_message` | `(messages: list) -> list` | After receiving message, before reply | Last message modification |
| `process_all_messages_before_reply` | `(messages: list) -> list` | Before generating reply | Full conversation history modification |
| `update_agent_state` | `(agent, messages: list) -> None` | Before generating reply | System message updates, context injection |

### Hook Registration Flow

```python
# During agent construction
agent = ConversableAgent(
    name="MyAgent",
    # ... other params
)

# After agent construction (AG2's register_hook method)
agent.register_hook("process_message_before_send", my_hook_function)
```

---

## MozaiksAI's Two Hook Mechanisms

### 1. InterviewAgent Auto-NEXT Hook (Testing Mode)

**Location**: `core/workflow/agents/factory.py` - `_build_interview_message_hook()`

**Type**: `process_message_before_send` (AG2 native, registered via `register_hook()`)

**Purpose**: 
- **First Reply**: Show context variables and ask "What would you like to automate?"
- **Second+ Replies**: Auto-respond with "NEXT" (testing mode to skip manual input)

**Registration**:
```python
# Built during agent creation
interview_message_hook = _build_interview_message_hook(agent_exposures, agent_variables)

# Registered AFTER agent construction
agent.register_hook("process_message_before_send", interview_message_hook)
```

**Behavior**:
```python
def _hook(sender=None, message=None, recipient=None, silent=False):
    reply_count["count"] += 1
    
    # AUTO-NEXT for testing (comment out for production)
    if reply_count["count"] > 1:
        return "NEXT"  # Skips manual user input
    
    # First message: Show context + question
    return f"What would you like to automate?\n\n{context_block}"
```

**To Disable Auto-NEXT for Production**:
```python
# In _build_interview_message_hook(), comment out this block:
# if current_count > 1:
#     logger.info(f"[InterviewAgent][HOOK] Auto-responding with NEXT (reply #{current_count})")
#     if isinstance(message, dict):
#         updated = dict(message)
#         updated["content"] = "NEXT"
#         return updated
#     return "NEXT"
```

---

### 2. Pattern Guidance Injection Hooks (Production)

**Location**: 
- Hook definitions: `workflows/Generator/tools/update_agent_state_pattern.py`
- Hook loading: `core/workflow/agents/factory.py` (lines 415-441)
- Hook configuration: `workflows/Generator/hooks.json`

**Type**: `update_agent_state` (AG2 constructor parameter `update_agent_state_before_reply`)

**Purpose**: Inject pattern-specific guidance into agent system messages dynamically

**Hooks Defined**:
1. `inject_workflow_strategy_guidance` → WorkflowStrategyAgent
2. `inject_workflow_architect_guidance` → WorkflowArchitectAgent
3. `inject_workflow_implementation_guidance` → WorkflowImplementationAgent
4. `inject_project_overview_guidance` → ProjectOverviewAgent
5. `inject_handoffs_guidance` → HandoffsAgent

**Registration Flow**:

```python
# STEP 1: Load hooks from hooks.json during agent creation
from ..execution.hooks import _resolve_import
from pathlib import Path

hooks_json_path = Path("workflows") / workflow_name / "hooks.json"
with open(hooks_json_path, 'r') as f:
    hooks_data = json.load(f)

# STEP 2: Resolve hook functions for this specific agent
for entry in hooks_data["hooks"]:
    if entry["hook_type"] == "update_agent_state" and entry["hook_agent"] == agent_name:
        fn, qual = _resolve_import(workflow_name, entry["filename"], entry["function"], workflow_path)
        if fn:
            update_hooks.append(fn)  # Add to list

# STEP 3: Pass hooks during agent construction (CRITICAL!)
agent = ConversableAgent(
    name=agent_name,
    # ... other params
    update_agent_state_before_reply=update_hooks or None,  # ← Must be here!
)
```

**Why Constructor Parameter?**

AG2's `update_agent_state_before_reply` parameter is processed during `__init__()`:

```python
# From autogen/agentchat/conversable_agent.py (lines 475-486)
if update_agent_state_before_reply:
    if isinstance(update_agent_state_before_reply, list):
        for func in update_agent_state_before_reply:
            if isinstance(func, UpdateSystemMessage):
                # Wrap and register
                self.register_hook(hookable_method="update_agent_state", hook=create_wrapper(func))
            else:
                self.register_hook(hookable_method="update_agent_state", hook=func)
```

**Key Insight**: You **CANNOT** use `register_hook()` for `update_agent_state` hooks after construction if they need to fire during the first reply. They must be passed during construction.

---

## Hook Execution Timeline

### Timeline: InterviewAgent First Interaction

```
1. User connects WebSocket
2. Runtime calls: run_workflow_orchestration()
3. Agents created: create_agents()
   │
   ├─► InterviewAgent created
   │   └─► update_hooks = []  (no update_agent_state hooks for InterviewAgent)
   │   └─► interview_message_hook built (_build_interview_message_hook)
   │   └─► agent.register_hook("process_message_before_send", interview_message_hook)
   │
4. GroupChat starts
5. InterviewAgent selected to speak
6. InterviewAgent.generate_reply() called
   │
   ├─► update_agent_state_before_reply() fires
   │   └─► (no hooks registered for InterviewAgent)
   │
   ├─► LLM generates response
   │
   ├─► send() called to send message
   │   └─► _process_message_before_send() fires
   │       └─► interview_message_hook executes ← AUTO-NEXT HAPPENS HERE
   │           └─► reply_count["count"] = 1
   │           └─► Returns: "What would you like to automate?\n\n{context}"
   │
7. Message sent to user via WebSocket
8. User sends response: "automate marketing"
9. InterviewAgent.generate_reply() called AGAIN
   │
   ├─► LLM generates response (e.g., "NEXT")
   │
   ├─► send() called
   │   └─► _process_message_before_send() fires
   │       └─► interview_message_hook executes ← AUTO-NEXT HAPPENS HERE
   │           └─► reply_count["count"] = 2
   │           └─► Returns: "NEXT"  (overrides LLM response!)
   │
10. "NEXT" sent to user automatically
```

---

### Timeline: WorkflowStrategyAgent Pattern Injection

```
1. PatternAgent calls pattern_selection tool
   └─► Stores in context_variables.data["PatternSelection"] = {"is_multi_workflow": false, "pack_name": "...", "workflows": [{"pattern_id": 8, ...}]}

2. GroupChat transitions to WorkflowStrategyAgent

3. WorkflowStrategyAgent.generate_reply() called
   │
   ├─► update_agent_state_before_reply() fires
   │   └─► inject_workflow_strategy_guidance(agent, messages) executes ← PATTERN INJECTION
   │       │
   │       ├─► _get_pattern_from_context(agent)
   │       │   └─► Resolves pattern ID 8 from PatternSelection.workflows[current_workflow_index]
   │       │
   │       ├─► _load_pattern_guidance_text()
   │       │   └─► Loads docs/pattern_guidance.md
   │       │
   │       ├─► _load_pattern_example_str()
   │       │   └─► Loads docs/pattern_examples/pattern_8_star.yaml (WorkflowStrategy section)
   │       │
   │       ├─► Maps pattern 8 → Star (Hub-and-Spoke)
   │       │
   │       ├─► Builds guidance string with module topology + JSON example
   │       │
   │       └─► agent.system_message += guidance  ← SYSTEM MESSAGE UPDATED
   │
   ├─► LLM generates response with pattern-aware system message
   │
   └─► Returns WorkflowStrategy JSON with Star pattern structure
```

---

## The Critical Bug (Fixed)

### The Problem

**Before Fix**: Pattern injection hooks were loaded and registered AFTER agent construction:

```python
# WRONG (before fix)
agent = ConversableAgent(
    name=agent_name,
    update_agent_state_before_reply=update_hooks or None,  # ← Empty list!
)

# Hooks loaded AFTER construction (too late!)
wm.register_hooks(workflow_name, agents, force=False)
```

**Result**: `update_hooks` was always empty during construction, so hooks never fired.

---

### The Fix

**After Fix**: Pattern injection hooks are loaded and added to `update_hooks` BEFORE agent construction:

```python
# CORRECT (after fix)
update_hooks: List[Callable | UpdateSystemMessage] = []

# Load update_agent_state hooks from hooks.json BEFORE construction
hooks_json_path = Path("workflows") / workflow_name / "hooks.json"
if hooks_json_path.exists():
    with open(hooks_json_path, 'r') as f:
        hooks_data = json.load(f)
    
    for entry in hooks_data["hooks"]:
        if entry["hook_type"] == "update_agent_state" and entry["hook_agent"] == agent_name:
            fn, qual = _resolve_import(workflow_name, entry["filename"], entry["function"], workflow_path)
            if fn:
                update_hooks.append(fn)  # ← Hooks added BEFORE construction

agent = ConversableAgent(
    name=agent_name,
    update_agent_state_before_reply=update_hooks or None,  # ← Now contains hooks!
)
```

---

## Hook System Best Practices

### 1. Hook Signature Requirements

**`process_message_before_send`**:
```python
def my_hook(sender=None, message=None, recipient=None, silent=False):
    # Must return modified message or original
    return message
```

**`update_agent_state`**:
```python
def my_hook(agent: ConversableAgent, messages: List[Dict[str, Any]]) -> None:
    # Modify agent state directly (no return value)
    agent.system_message += "\n\n[INJECTED GUIDANCE]"
```

---

### 2. When to Use Each Hook Type

| Use Case | Hook Type | Registration Method |
|----------|-----------|---------------------|
| Auto-respond with specific text | `process_message_before_send` | `register_hook()` after construction |
| Transform message content | `process_message_before_send` | `register_hook()` after construction |
| Inject pattern guidance into system message | `update_agent_state` | Constructor parameter `update_agent_state_before_reply` |
| Modify conversation history | `process_all_messages_before_reply` | `register_hook()` after construction |

---

### 3. Testing vs Production

**Testing Mode** (current):
- InterviewAgent auto-responds with "NEXT" on second+ replies
- Skips manual user input for faster iteration

**Production Mode**:
- Comment out auto-NEXT block in `_build_interview_message_hook()`
- InterviewAgent will wait for real user input

---

## Troubleshooting

### Hook Not Firing?

**Check 1: Is the hook registered?**
```python
# Add logging in factory.py
logger.info(f"[AGENTS][HOOK] Registered {hook_type} hook for {agent_name}")
```

**Check 2: Is the hook function valid?**
```python
# Verify hook callable
if not callable(fn):
    logger.error(f"Hook {qual} is not callable!")
```

**Check 3: For `update_agent_state` hooks, are they in constructor parameter?**
```python
# MUST be passed during construction, not via register_hook() later
agent = ConversableAgent(
    update_agent_state_before_reply=update_hooks,  # ← Check this!
)
```

**Check 4: Verify hook execution**
```python
# Add logging inside hook function
def my_hook(agent, messages):
    logger.info(f"[HOOK_EXEC] Hook firing for {agent.name}")
    # ... rest of hook
```

---

### Auto-NEXT Not Working?

**Check 1: Is hook registered for InterviewAgent?**
```bash
# Look for this in logs:
[AGENTS][HOOK] ✓ Registered process_message_before_send hook for InterviewAgent
```

**Check 2: Is reply_count incrementing?**
```python
# Check logs for:
[InterviewAgent][HOOK] Processing message #1 before send
[InterviewAgent][HOOK] Processing message #2 before send
[InterviewAgent][HOOK] Auto-responding with NEXT (reply #2)
```

**Check 3: Is the hook function being built?**
```bash
# Look for:
[AGENTS] Built interview message hook for InterviewAgent (auto-NEXT enabled for testing)
```

---

## Summary

| Hook Mechanism | Purpose | Registration | Timing |
|----------------|---------|--------------|--------|
| **InterviewAgent Auto-NEXT** | Testing: Skip manual input | `register_hook()` after construction | After agent creation |
| **Pattern Injection Hooks** | Production: Inject pattern guidance | Constructor parameter `update_agent_state_before_reply` | **BEFORE** agent creation |

**Key Takeaway**: `update_agent_state` hooks MUST be loaded and passed during agent construction. `process_message_before_send` hooks CAN be registered after construction via `register_hook()`.
