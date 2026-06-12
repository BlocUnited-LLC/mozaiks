# Structured Output Extraction Contract

This document defines the canonical contract for how agent structured outputs are extracted, validated, and consumed in the mozaiks runtime.

## Overview

The structured output extraction pattern enables:
1. Agents to produce typed JSON outputs
2. Runtime to validate outputs against Pydantic models
3. Auto-invoked tools to consume validated outputs
4. UI artifacts to be generated from agent outputs

## Architecture

```text
Agent (structured_outputs_required: true)
  │
  ▼ emits JSON matching registered model
  │
structured_outputs.yaml (registry: Agent → Model)
  │
  ▼ runtime validates against Pydantic model
  │
AutoToolEventHandler
  │
  ▼ finds tool with auto_tool_call: true for this agent
  │
tools.yaml (agent + auto_tool_call: true)
  │
  ▼ invokes tool function with validated payload
  │
tools/{function}.py
  │
  ▼ persists, emits UI events, updates context
  │
UI (receives tool_call/tool_response events)
```

## Key Files

| File | Purpose |
|------|---------|
| `structured_outputs.yaml` | Model definitions + agent→model registry |
| `agents.yaml` | Agent config with `structured_outputs_required: true` |
| `tools.yaml` | Tool bindings with `auto_tool_call: true` |
| `tools/*.py` | Python tool implementations |
| `extended_orchestration/task_batches.yaml` | Optional workflow-local task batch config |

## Contract Details

### 1. structured_outputs.yaml

Defines Pydantic models and maps agents to their output models.

```yaml
# Registry: agent name → model name
registry:
  ExampleDecomposerAgent: ExampleDecomposition
  ExamplePresenterAgent: ExamplePresentation

# Model definitions
models:
  ExampleTask:
    type: model
    fields:
      task_id:
        type: str
        description: Stable task id
      initial_agent:
        type: str
        description: Worker agent to execute the task
      initial_message:
        type: str
        description: Prompt for the worker agent

  ExampleDecomposition:
    type: model
    fields:
      agent_message:
        type: str
        description: Status message shown to user
      workflows:
        type: list
        items: ExampleChildSpec
        description: Child workflows to spawn
```

**Supported field types:**
- `str`, `int`, `float`, `bool`
- `optional_str` (Optional[str])
- `list` (requires `items`)
- `optional_list` (Optional[List])
- `dict` (Dict[str, Any])
- `literal` (requires `values` list → becomes Enum)
- `union` (requires `variants` list)
- Model references (use model name as type)

### 2. agents.yaml

Agents that produce structured outputs must set `structured_outputs_required: true`.

```yaml
agents:
  - name: ExampleDecomposerAgent
    prompt_sections:
      - id: role
        heading: '[ROLE]'
        content: You are JokeDecomposer...
      - id: output_format
        heading: '[OUTPUT FORMAT]'
        content: |
          Respond with ONLY valid JSON matching ExampleDecomposition.
          No extra text. No markdown fences.
    structured_outputs_required: true  # REQUIRED
    max_consecutive_auto_reply: 3
```

**Agent prompt contract:**
- Include `[OUTPUT FORMAT]` section specifying exact JSON shape
- Instruct agent to output ONLY JSON, no extra text
- Reference the model name from structured_outputs.yaml

### 3. tools.yaml

Tools with `auto_tool_call: true` are called automatically when their agent produces output.

```yaml
tools:
  # Auto-invoked tool for structured output consumption
  - agent: ExampleDecomposerAgent
    file: spawn_example_workers.py
    function: spawn_example_workers
    tool_type: Agent_Tool
    auto_tool_call: true  # Called automatically after agent output

  # One-way UI surface for artifact display
  - agent: ExamplePresenterAgent
    file: display_presentation.py
    function: display_presentation
    tool_type: UI_Surface
    auto_tool_call: true
    ui:
      component: ExampleGallery
      mode: artifact
```

**auto_tool_call behavior:**
- When agent emits output → runtime validates against model
- If valid → finds tool with `auto_tool_call: true` for this agent
- Invokes tool function with validated payload as kwargs
- Tool receives structured output fields directly as parameters

### 4. Tool Implementation

Tools receive the validated structured output fields as keyword arguments.

```python
# tools/spawn_example_workers.py

async def spawn_example_workers(
    agent_message: str,
    workflows: list,
    context_variables=None,  # Optional: runtime context
    **kwargs
) -> dict:
    """
    Receives validated ExampleDecomposition fields directly.

    Args:
        agent_message: From ExampleDecomposition.agent_message
        workflows: From ExampleDecomposition.workflows (list of ExampleChildSpec)
        context_variables: Runtime context container
    """
    # Tool logic - persist, spawn children, emit events
    for workflow_spec in workflows:
        # workflow_spec is already validated against JokeAngleSpec
        child_name = workflow_spec["name"]
        child_message = workflow_spec["initial_message"]
        # ... spawn logic

    return {
        "success": True,
        "spawned": len(workflows),
        "context_updates": {
            "decomposition_complete": True,
        }
    }
```

**Tool parameter binding:**
- Model field names are matched to function parameters
- Case-insensitive matching (`agent_message` matches `agentMessage`)
- `context_variables` is injected if function accepts it
- Metadata (`chat_id`, `app_id`, `workflow_name`, `turn_idempotency_key`) injected if accepted

## Runtime Flow

### AutoToolEventHandler

Located at: `mozaiksai/core/events/auto_tool_handler.py`

The handler:
1. Listens for `chat.agent_output_validated` events
2. Checks if `auto_tool_call: true`
3. Resolves tool binding from workflow's tools.yaml
4. Validates structured_data against the model
5. Builds kwargs from validated payload + context
6. Invokes the tool function
7. Persists context variable updates
8. Emits `tool_call` and `tool_response` events to UI

### Event Flow

```text
Agent output → TextEvent
    │
    ▼
Stream processor detects structured output
    │
    ▼
Emits chat.agent_output_validated event:
{
  "kind": "runtime.agent_output_validated",
  "agent_name": "ExampleDecomposerAgent",
  "model_name": "ExampleDecomposition",
  "structured_data": { ... validated JSON ... },
  "auto_tool_call": true,
  "context": { "chat_id", "app_id", "workflow_name" }
}
    │
    ▼
AutoToolEventHandler.handle_tool_dispatch()
    │
    ▼
Tool function invoked with validated kwargs
    │
    ▼
Emits chat.tool_call + chat.tool_response to UI
```

## Task Batches

For workflows that execute a typed list of work items in parallel, use
`extended_orchestration/task_batches.yaml`.

```yaml
version: 1
batches:
  - id: example_generation
    trigger_agent: ExampleDecomposerAgent
    source:
      kind: context_variable
      path: example_plan.tasks
      task_model: ExampleTask
    worker:
      mode: ag2_agent
      agent_field: initial_agent
      prompt_field: initial_message
    result:
      context_key: example_task_results
      status_key: example_task_status
```

**Task batch structured output contract:**
- `trigger_agent` must have `structured_outputs_required: true` when the source
  is structured output.
- The task model must include the worker fields referenced by
  `worker.agent_field` and `worker.prompt_field`.
- Results are injected into context as `result.context_key`.

## What Does NOT Exist

The following do NOT exist in the mozaiks runtime:

| Aspirational Concept | Reality |
|---------------------|---------|
| Standalone extractor classes | Does not exist. Tools consume structured outputs directly |
| Separate "extraction" phase | Does not exist. Auto-invoke tools handle consumption inline |

## Complete Example

Below is a schematic workflow bundle showing the complete contract shape.
Use live production workflows under `factory_app/workflows/*` or an active
app root's `workflows/*` for implementation references:

```
ExampleWorkflow/
├── orchestrator.yaml           # workflow_startup_mode, initial_agent
├── agents.yaml                 # Agents with structured_outputs_required
├── structured_outputs.yaml     # ExampleDecomposition, ExamplePresentation models
├── transition_graph.yaml              # Agent-to-agent routing
├── tools.yaml                 # auto_tool_call tools
├── context_variables.yaml     # State definitions
├── middleware.yaml                 # Optional hooks
├── ui_config.yaml             # visual_agents
├── extended_orchestration/
│   └── task_batches.yaml      # task batch config (optional)
└── tools/
    ├── save_jokes.py
    ├── rate_joke.py
    └── display_ratings.py
```

## Validation

The runtime validates all YAML files using Pydantic models with `extra="forbid"`.

Contract enforcement is in: `mozaiksai/core/workflow/declarative/contracts.py`

Key validation models:
- `StructuredOutputsConfig` - validates structured_outputs.yaml
- `ToolsConfig` - validates tools.yaml
- `AgentsConfig` - validates agents.yaml

## Cross References

- [workflow-authoring-contracts.md](workflow-authoring-contracts.md)
- `mozaiksai/core/events/auto_tool_handler.py`
- `mozaiksai/core/workflow/outputs/structured.py`



