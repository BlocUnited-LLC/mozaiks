# Add Workflows

Most users should start in the Console with `Create App`. Add a workflow
directly only when you are extending Mozaiks or adding an app-owned workflow.

## Workflow Authoring Model

A Mozaiks workflow is a deterministic state machine around AG2 agents.

Before writing files, define:

- the outcome the workflow must produce
- the agents needed to interview, reason, and generate structured output
- the data contracts that must be persisted or shown to the user
- the UI artifacts, if any, that should appear in the chat stream
- whether the workflow is standalone or triggered mid-flight by a parent workflow

## Choose The Owner

Use `factory_app/workflows/{WorkflowName}/` for shared builder workflows such as
app generation, workflow generation, and refinement journeys.

Use `app/workflows/{WorkflowName}/` for workflows that belong to one generated
app workspace.

## Canonical File Set

```text
workflows/{WorkflowName}/
├── orchestrator.yaml
├── context_variables.yaml
├── agents.yaml
├── structured_outputs.yaml
├── tools.yaml
├── handoffs.yaml
├── ui_config.yaml
├── hooks.yaml
├── extended_orchestration/
│   └── mfj_extension.json
├── tools/
│   ├── __init__.py
│   └── artifact_tools.py
└── ui/{WorkflowName}/
    └── components/
```

`hooks.yaml`, `extended_orchestration/`, workflow tools, and workflow UI are
included only when the workflow needs them.

## Authoring Order

1. Define `orchestrator.yaml`: entry agent, startup mode, turn limits, and human review.
2. Define `context_variables.yaml`: shared state and parent-injected values.
3. Define `structured_outputs.yaml`: strict output models for generator agents.
4. Define `agents.yaml`: conversational agents gather context; generator agents emit typed output.
5. Define `tools.yaml`: bind dumb tools and optional UI emission.
6. Define `handoffs.yaml`: deterministic routing between agents and the user.
7. Define `ui_config.yaml`: list visual agents that should stream to the UI.

## Contract Snippets

These snippets show the shape of each file. They are intentionally small - use
them as contract references, not as full workflow examples.

### `orchestrator.yaml`

```yaml
workflow_name: IntakeWorkflow
max_turns: 20
human_in_the_loop: true
workflow_startup_mode: AgentDriven
orchestration_pattern: Pipeline
initial_agent: IntakeAgent
initial_message: "Start with IntakeAgent."
initial_message_to_user: null
triggers:
  - type: chat
    description: Start from the chat UI.
```

`initial_message` is a hidden runtime seed. Use `initial_message_to_user` only
when the workflow needs a visible startup message.

### `context_variables.yaml`

```yaml
definitions:
  intake_complete:
    type: boolean
    description: True when the intake agent has enough information.
    source:
      type: state
      default: false
  user_goal:
    type: string
    description: The user's requested outcome.
    source:
      type: state
      default: null

agents:
  IntakeAgent:
    variables: [user_goal, intake_complete]
  GeneratorAgent:
    variables: [user_goal]
```

Keep shared state explicit. Do not rely on prompt-only memory for values that a
later agent, tool, or UI surface must consume.

### `structured_outputs.yaml`

```yaml
registry:
  IntakeAgent: null
  GeneratorAgent: BuildPlan

models:
  BuildPlan:
    type: model
    description: The deterministic plan emitted by the generator agent.
    fields:
      title:
        type: str
        description: Short name for the generated plan.
      tasks:
        type: list
        items: str
        description: Ordered implementation tasks.
```

Conversational agents usually map to `null`. Generator agents map to a strict
model and should set `structured_outputs_required: true` in `agents.yaml`.

### `agents.yaml`

```yaml
agents:
  - name: IntakeAgent
    structured_outputs_required: false
    max_consecutive_auto_reply: 5
    prompt_sections:
      - id: role
        heading: "[ROLE]"
        content: Ask focused questions until the user's goal is clear.

  - name: GeneratorAgent
    structured_outputs_required: true
    max_consecutive_auto_reply: 3
    prompt_sections:
      - id: role
        heading: "[ROLE]"
        content: Produce only the BuildPlan structured output.
```

Put reasoning instructions in prompts. Put auto-tool behavior in `tools.yaml`,
not in `agents.yaml`.

### `handoffs.yaml`

```yaml
handoff_rules:
  - source_agent: user
    target_agent: IntakeAgent
    handoff_type: condition
    condition_type: string_llm
    condition: When the user starts or answers an intake question.
    transition_target: AgentTarget

  - source_agent: IntakeAgent
    target_agent: GeneratorAgent
    handoff_type: condition
    condition_type: string_llm
    condition: When intake_complete is true.
    transition_target: AgentTarget

  - source_agent: GeneratorAgent
    target_agent: user
    handoff_type: after_work
    transition_target: RevertToUserTarget
```

Handoffs should describe deterministic routing. Avoid broad "do whatever is
best next" conditions.

### `tools.yaml`

```yaml
tools:
  - agent: GeneratorAgent
    file: save_build_plan.py
    function: save_build_plan
    description: Persist the generated plan.
    tool_type: Agent_Tool
    auto_tool_call: true

  - agent: GeneratorAgent
    file: show_build_plan.py
    function: show_build_plan
    description: Render the generated plan in the chat stream.
    tool_type: UI_Surface
    auto_tool_call: true
    ui:
      component: BuildPlanCard
      mode: artifact

lifecycle_tools: []
```

Use `Agent_Tool` for backend-only work. Use `UI_Surface` for one-way rendered
artifacts. Use `UI_Tool` only when the user must interact with the component.

### `ui_config.yaml`

```yaml
visual_agents:
  - IntakeAgent
  - GeneratorAgent
  - user
```

Only visual agents stream user-visible messages and UI-bearing outputs through
the websocket.

## Implementation Snippets

### Tool Python

```python
# tools/show_build_plan.py
from __future__ import annotations

from typing import Any, Dict

from mozaiksai.core.workflow.ui_tools import emit_ui_surface


async def show_build_plan(context_variables: Any = None) -> Dict[str, Any]:
    data = context_variables.get("structured_output") if context_variables else {}
    chat_id = context_variables.get("chat_id") if context_variables else None
    workflow_name = context_variables.get("workflow_name") if context_variables else None

    event_id = await emit_ui_surface(
        "BuildPlanCard",
        {"title": data.get("title"), "tasks": data.get("tasks", [])},
        chat_id=chat_id,
        workflow_name=workflow_name,
    )

    return {"success": True, "surface_event_id": event_id}
```

Tools should read already-reasoned structured output, persist or emit it, and
return a small result. Do not put LLM-style inference in Python tools.

### Workflow UI JavaScript

```jsx
// ui/IntakeWorkflow/components/BuildPlanCard.js
const BuildPlanCard = ({ payload = {} }) => {
  const tasks = Array.isArray(payload.tasks) ? payload.tasks : [];

  return (
    <section className="rounded-xl border border-border bg-card p-4 text-card-foreground">
      <h3 className="text-base font-semibold text-foreground">
        {payload.title || 'Build plan'}
      </h3>
      <ol className="mt-3 space-y-2 text-sm text-muted-foreground">
        {tasks.map((task, index) => (
          <li key={`${task}-${index}`}>{index + 1}. {task}</li>
        ))}
      </ol>
    </section>
  );
};

export default BuildPlanCard;
```

Workflow UI components should render the exact payload declared by the tool.
Use theme variables and shipped UI primitives where possible. Avoid fake charts,
placeholder panels, and decorative dashboard sections.

## Tool Rule

Tools stay dumb. Agents reason through prompts and structured outputs. Tool code
should read structured output, persist or transform it deterministically, and
emit UI events when needed.

## UI Rule

Use workflow UI components only for artifacts that belong in the chat stream.
Persistent app pages belong under the generated app workspace, not inside a
workflow UI folder.

## Read Next

- [Workflow Architecture](../../architecture/workflows/workflow-architecture.md)
- [Workflow Authoring Contracts](../../architecture/workflows/workflow-authoring-contracts.md)
- [Mid-Flight Journeys](../../architecture/mozaiksai/mid-flight-journeys.md)
