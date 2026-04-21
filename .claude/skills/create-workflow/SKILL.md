---
name: create-workflow
description: Create a new AI workflow from scratch. Generates workflow YAML files, tools, and optional UI components.
argument-hint: "[WorkflowName] [description of what it should do]"
---

Help the user create a new workflow named $ARGUMENTS.

## Before Starting

Gather from the user:
1. What should this workflow do?
2. What agents are needed?
3. Should any agent output structured data (for UI artifacts)?
4. Does the workflow need any UI components?
5. Will this workflow be called as a child of another workflow?
6. Workflow name (PascalCase like `CustomerSupport`)

## Workflow Structure

Create under `platform/workflows/[WorkflowName]/`:

```
platform/workflows/[WorkflowName]/
├── orchestrator.yaml       # Workflow execution bootstrap
├── agents.yaml             # Agent roster and prompts
├── handoffs.yaml           # Agent-to-agent routing
├── context_variables.yaml  # Shared workflow state
├── structured_outputs.yaml # Typed outputs + registry
├── tools.yaml              # Tool bindings and UI tool metadata
├── ui_config.yaml          # Frontend exposure metadata
├── hooks.yaml              # Lifecycle hooks (optional)
├── extended_orchestration/mfj_extension.json # MFJ triggers (optional)
├── tools/                  # Python tool implementations
│   ├── __init__.py
│   └── my_tool.py
└── ui/[WorkflowName]/      # Workflow-specific UI (optional)
    ├── components/
    │   └── MyComponent.js
    └── styles/
```

## Steps

### 1. Configure orchestrator.yaml
```yaml
workflow_name: [WorkflowName]  # Must match folder name
max_turns: 20
human_in_the_loop: true
startup_mode: AgentDriven
orchestration_pattern: DefaultPattern
initial_agent: [FirstAgentName]
initial_message: "[FirstAgentName]: [Initial greeting]"
```

### 2. Configure agents.yaml

**Free-form conversational agent:**
```yaml
agents:
- name: InterviewAgent
  prompt_sections:
    - id: role
      heading: '[ROLE]'
      content: You are...
    - id: instructions
      heading: '[INSTRUCTIONS]'
      content: |
        1. Ask questions...
        2. When done, emit: NEXT
  max_consecutive_auto_reply: 10
  structured_outputs_required: false
```

**Structured output agent (for UI artifacts):**
```yaml
- name: OutputAgent
  prompt_sections:
    - id: output_format
      heading: '[OUTPUT FORMAT]'
      content: |
        Output ONLY valid MyModel JSON:
        ```json
        {"field1": "value", "items": [...]}
        ```
  max_consecutive_auto_reply: 10
  structured_outputs_required: true  # Enforces JSON output
  # Note: auto_tool_mode is derived from tools.yaml (agents with auto_tool_call tools)
```

**Child workflow aware agent:**
```yaml
- name: InterviewAgent
  prompt_sections:
    - id: instructions
      content: |
        1. **IF `is_child_workflow` is true**:
           - Do NOT greet user
           - Read context from `concept_overview` or `value_manifest`
           - Emit ONLY: NEXT
        2. Otherwise, ask questions...
```

### 3. Configure structured_outputs.yaml

```yaml
structured_outputs:
  registry:
    # Conversational agents have null (no structured output)
    InterviewAgent: null
    # Output agent maps to a model
    OutputAgent: MyOutputModel

  models:
    MyOutputModel:
      type: model
      description: Structured output for UI artifact
      fields:
        agent_message:
          type: str
          description: User-facing status message
        field1:
          type: str
          description: Main field
        items:
          type: optional_list
          items: str
          description: List of items
```

### 4. Configure tools.yaml

**Auto-invoke tool (triggered after structured output):**
```yaml
tools:
- agent: OutputAgent
  file: my_tool.py
  function: save_output
  description: Auto-invoked after OutputAgent outputs. Persists and emits UI artifact.
  tool_type: Agent_Tool
  auto_tool_call: true  # Called automatically after agent speaks
  ui:
    component: MyComponent  # React component name
    mode: artifact          # Renders as artifact card
```

**Manual tool (agent calls explicitly):**
```yaml
- agent: InterviewAgent
  file: my_tool.py
  function: get_data
  description: Retrieve data from storage.
  tool_type: Agent_Tool
  auto_tool_call: false
  ui:
    component: null
    mode: null
```

### 5. Add tool implementations

**tools/__init__.py:**
```python
from .my_tool import save_output, get_data

__all__ = ["save_output", "get_data"]
```

**tools/my_tool.py:**
```python
"""
Workflow tools - tools are dumb, LLMs reason.
Read from context_variables, persist/emit. No heuristics.
"""

from typing import Annotated, Any, Dict, Optional
import logging

logger = logging.getLogger(__name__)

# Import mozaiks runtime
try:
    from mozaiksai.core.transport.simple_transport import SimpleTransport
    _HAS_TRANSPORT = True
except ImportError:
    _HAS_TRANSPORT = False


async def save_output(
    context_variables: Annotated[Optional[Any], "Runtime context"] = None,
) -> Dict[str, Any]:
    """
    Auto-invoked after OutputAgent outputs structured data.
    Reads structured_output from context and emits UI artifact.
    """
    if not context_variables:
        return {"success": False, "error": "No context"}

    # Read structured output (runtime injects it)
    data = context_variables.get("structured_output")
    if not data:
        return {"success": False, "error": "No structured output"}

    chat_id = context_variables.get("chat_id")

    # Transform to UI payload format
    ui_payload = {
        "title": data.get("field1", "Output"),
        "items": data.get("items", []),
    }

    # Emit UI artifact
    if _HAS_TRANSPORT:
        transport = await SimpleTransport.get_instance()
        await transport.send_ui_tool_event(
            event_id=f"output_{chat_id}",
            chat_id=str(chat_id) if chat_id else None,
            tool_name="save_output",
            component_name="MyComponent",
            display_type="artifact",
            payload=ui_payload,
            agent_name="OutputAgent",
        )

    # Store in context for downstream use
    context_variables["my_output"] = data

    return {"success": True, "message": "Output saved"}
```

### 6. Configure handoffs.yaml

```yaml
handoff_rules:
# InterviewAgent → OutputAgent when interview complete
- source_agent: InterviewAgent
  target_agent: OutputAgent
  handoff_type: condition
  condition_type: expression
  condition: ${interview_complete} == True
  transition_target: AgentTarget

# OutputAgent → user (present result)
- source_agent: OutputAgent
  target_agent: user
  handoff_type: after_work
  transition_target: RevertToUserTarget
```

### 7. Add UI components (if needed)

**ui/[WorkflowName]/components/MyComponent.js:**
```jsx
// No default React import — JSX transform handles it.
// Use named imports only if you need hooks.
// import { useState } from 'react'

// Use --mz-* semantic tokens via Tailwind. No hardcoded color scales,
// no --color-* CSS variables, no artifactDesignSystem imports.

const MyComponent = ({ payload = {} }) => {
  const { title, items = [] } = payload;

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <h3 className="text-lg font-semibold text-foreground">{title}</h3>
      <ul className="mt-2 space-y-1">
        {items.map((item, idx) => (
          <li key={idx} className="text-sm text-muted-foreground">• {item}</li>
        ))}
      </ul>
    </div>
  );
};

export default MyComponent;
```

## Key Principles

1. **Tools are dumb, LLMs reason** - No heuristics in tools. Tools read from `context_variables["structured_output"]` and persist/emit.

2. **Structured outputs drive UI** - Agent outputs JSON → tool auto-invokes → tool emits artifact.

3. **Child workflow awareness** - Check `is_child_workflow` in context to skip interview and use parent context.

4. **Context flows downstream** - Store outputs in `context_variables` for other agents/workflows to read.

## Verification

1. Check `http://localhost:8000/api/workflows` - workflow should appear
2. Test the workflow flow end-to-end
3. Verify UI artifacts render correctly
4. Check MongoDB for persisted data

## Common Issues

- **Workflow not appearing**: Check `workflow_name` matches folder name exactly
- **Agent not responding**: Check `initial_agent` exists in `agents.yaml`
- **Tool not executing**: Check `tools.yaml` function name matches Python function
- **Structured output not captured**: Ensure `structured_outputs_required: true` in agents.yaml
- **UI not rendering**: Check component export and `tools.yaml` component name match
