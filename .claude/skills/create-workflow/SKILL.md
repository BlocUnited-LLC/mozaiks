---
name: create-workflow
description: Create a new AI workflow from scratch. Generates workflow YAML files, tools, and optional UI components.
argument-hint: "[WorkflowName] [description of what it should do]"
---

Help the user create a new workflow named $ARGUMENTS.

When acting on this skill, your job is to transform human intent into a deterministic, state-driven workflow executed by Mozaiks.

## Phase 1: Architectural Discovery & Planning

Before writing any code, interview the user or analyze their prompt to define the underlying state machine. Answer the following internally or with the user:
1. **The Objective:** What is the fundamental outcome this workflow must achieve?
2. **The Agents:** What are the distinct "personas" required? Remember: split conversational reasoning (free-form) from structured generation (JSON).
3. **The Data Contracts:** What is the exact shape of the data that needs to be generated, persisted, and shown to the user?
4. **The UI Artifacts:** Which agent outputs require a custom UI Component injected into the chat stream?
5. **The Lifecycle:** Is this a standalone workflow, or is it triggered mid-flight by a parent workflow (`is_child_workflow`)? What context variables flow into it?

## Phase 2: Scaffold the Workflow Directory

A Mozaiks workflow is an independent module. Scaffold the folder structure first:

```
app/workflows/[WorkflowName]/
├── orchestrator.yaml       # Defines entry point, initial agent, and constraints
├── context_variables.yaml  # Default/schema for workflow shared state
├── agents.yaml             # Agent definitions, system prompts, structure rules
├── structured_outputs.yaml # Pydantic-like models the AI must strictly output
├── tools.yaml              # Tool bindings, auto_tool triggers, UI rendering metadata
├── handoffs.yaml           # Deterministic routing logic between agents
├── ui_config.yaml          # Frontend exposure metadata (visual_agents)
├── hooks.yaml              # Lifecycle hooks (optional)
├── extended_orchestration/ # Mid-Flight Journey extensions & triggers (Optional)
│   └── mfj_extension.json
├── tools/                  # Python implementations for the tools (DUMB tools)
│   ├── __init__.py
│   └── artifact_tools.py
└── ui/[WorkflowName]/      # React UI components tied to tools (Optional)
    └── components/
```

## Phase 3: Define the Contracts (YAML)

Always define the declarative contracts BEFORE the implementation.

### 1. `orchestrator.yaml`
```yaml
workflow_name: [WorkflowName]
max_turns: 20
human_in_the_loop: true
startup_mode: AgentDriven
orchestration_pattern: DefaultPattern
initial_agent: [FirstAgentName]
initial_message: "[FirstAgentName]: [Initial greeting or prompt]"
```

### 2. `context_variables.yaml`
Define the initial state schema. If variables are injected by a parent, document them here with their default (or empty) state.

### 3. `structured_outputs.yaml`
Define the shape of any data the workflow produces that needs to be parsed by tools or UI.
```yaml
structured_outputs:
  registry:
    # Conversational agent: null
    InterviewAgent: null
    # Generator agent: Maps strictly to a model
    GeneratorAgent: MyOutputModel

  models:
    MyOutputModel:
      type: model
      description: What this model represents
      fields:
        title: { type: str, description: "Title of the artifact" }
        payload: { type: dict, description: "Data to render" }
```

### 4. `agents.yaml`
Separate agents by capability.
- **Conversational Agents:** `structured_outputs_required: false` (Gathers requirements, asks questions).
- **Generator Agents:** `structured_outputs_required: true` (Takes requirements from `context_variables` and emits rigid JSON matching the model).

*Note: Never define tool auto-invocation inside agents.yaml. That belongs in tools.yaml.*

```yaml
agents:
- name: GeneratorAgent
  structured_outputs_required: true
  prompt_sections:
    - id: instructions
      content: You take the user's intent and generate a canonical MyOutputModel.
```

### 5. `tools.yaml`
Bind Python functions to agents and declare how they map to UI components.

- **Auto-invoke (The preferred Mozaiks pattern for UI artifacts):** When `structured_outputs_required: true`, set `auto_tool_call: true`. The runtime will force the agent to emit JSON, then instantly pass that JSON to the tool.

```yaml
tools:
- agent: GeneratorAgent
  file: artifact_tools.py
  function: save_and_render_artifact
  description: Persists output and emits the artifact to the UI.
  tool_type: Agent_Tool
  auto_tool_call: true
  ui:
    component: MyComponent  # Name of your React component
    mode: artifact          # Renders as a full artifact card
```

### 6. `handoffs.yaml`
Define deterministic transitions between agents, or from agent back to `user`, based on state.

### 7. `ui_config.yaml`
Define which agents are `visual_agents`.
**CRITICAL:** Only agents listed under `visual_agents` will have their messages/outputs piped through the websocket to the UI. If an agent is not listed here, it functions as a "background" or "silent" agent.

```yaml
visual_agents:
- InterviewAgent
- GeneratorAgent
- user
```

### 8. `extended_orchestration/mfj_extension.json` (Optional)
If your workflow is triggered as a Mid-Flight Journey (a child workflow automatically invoked by the system based on a trigger event), you define those triggers here.

## Phase 4: Implementation (Dumb Tools, Smart System)

**CRITICAL RULE:** Tools are dumb. LLMs reason. Never write heuristics, if-statements, or inference logic inside a tool. The tool's only job is to read from `context_variables.get("structured_output")`, persist it, and emit UI events.

**tools/artifact_tools.py:**
```python
from typing import Annotated, Any, Dict, Optional
import logging

try:
    from mozaiksai.core.transport.simple_transport import SimpleTransport
    _HAS_TRANSPORT = True
except ImportError:
    _HAS_TRANSPORT = False

async def save_and_render_artifact(context_variables: Annotated[Optional[Any], "Runtime context"] = None) -> Dict[str, Any]:
    if not context_variables: return {"success": False}

    # 1. Grab the reasoned output the LLM already generated
    data = context_variables.get("structured_output")
    chat_id = context_variables.get("chat_id")

    # 2. Persist it downstream to logic/db
    context_variables["my_domain_data"] = data

    # 3. Emit the UI Action
    if _HAS_TRANSPORT:
        transport = await SimpleTransport.get_instance()
        await transport.send_ui_tool_event(
            event_id=f"artifact_{chat_id}",
            chat_id=str(chat_id) if chat_id else None,
            tool_name="save_and_render_artifact",
            component_name="MyComponent",     # Must match tools.yaml
            display_type="artifact",          # Must match tools.yaml
            payload=data,                     # Pass the exact JSON payload to React
            agent_name="GeneratorAgent",
        )
    return {"success": True, "message": "Artifact rendered"}
```

## Phase 5: UI Implementation

If your workflow generated a UI artifact component, implement the React file.
It receives the JSON payload emitted by the tool.

**ui/[WorkflowName]/components/MyComponent.js:**
```jsx
// Use standard React structure. No default React imports required.
// Do not hardcode colors; rely on tailwind classes and --mz- variables.

const MyComponent = ({ payload = {} }) => {
  const { title, payload: data } = payload;
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <h3 className="text-lg font-semibold text-foreground">{title}</h3>
      {/* Render your data here */}
    </div>
  );
};
export default MyComponent;
```

## Review & Deliver
1. **Did you separate reasoning?** Verify that logic lives in prompts/outputs, not Python tools.
2. **Did you bridge the gap?** Check that `structured_outputs.yaml` registry -> `agents.yaml` requirement -> `tools.yaml` binding -> python function -> React component all use the exact same variable names, payload shapes, and component names.
3. Inform the user they can test it via the Studio at `/api/workflows` or by running a Mozaiks session.
- **Structured output not captured**: Ensure `structured_outputs_required: true` in agents.yaml
- **UI not rendering**: Check component export and `tools.yaml` component name match
