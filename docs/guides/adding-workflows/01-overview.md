# Add Workflows

Most users should start in Studio with `Create App`. Add a workflow
directly only when you are extending Mozaiks or adding an app-owned workflow.

## Before You Start

A Mozaiks workflow is the AI-driven flow behind a task, assistant, or build step.
It usually coordinates AG2 agents, data passed between them, and optional UI
artifacts shown in chat.

Before writing files, define:

- the outcome the workflow must produce
- the agents needed to interview, reason, and generate structured output
- the data contracts that must be persisted or shown to the user
- the UI artifacts, if any, that should appear in the chat stream
- whether the workflow runs on its own or is started by another workflow

## Where It Lives

Use `factory_app/workflows/{WorkflowName}/` for shared builder workflows such as
app generation, workflow generation, and refinement journeys.

Use `workflows/{WorkflowName}/` for workflows that belong to one generated
app workspace.

## File Set

```text
workflows/{WorkflowName}/
├── orchestrator.yaml
├── context_variables.yaml
├── agents.yaml
├── structured_outputs.yaml
├── tools.yaml
├── transition_graph.yaml
├── ui_config.yaml
├── middleware.yaml
├── extended_orchestration/
│   └── task_batches.yaml
├── tools/
│   ├── __init__.py
│   └── artifact_tools.py
└── ui/{WorkflowName}/
    └── components/
```

`middleware.yaml`, `extended_orchestration/`, workflow tools, and workflow UI are
included only when the workflow needs them.

## Recommended Order

1. Define `orchestrator.yaml`: entry agent, startup mode, turn limits, and human review.
2. Define `context_variables.yaml`: shared state and parent-injected values.
3. Define `structured_outputs.yaml`: strict output models for generator agents.
4. Define `agents.yaml`: conversational agents gather context; generator agents emit typed output.
5. Define `tools.yaml`: bind dumb tools and optional UI emission.
6. Define `transition_graph.yaml`: deterministic routing between agents and the user through AG2 1.0 WorkflowAdapter.
7. Define `ui_config.yaml`: list visual agents that should stream to the UI.

## Contract Snippets

These snippets show the shape of each file. They are intentionally small — use
them as contract references, not as full workflow examples.

=== "orchestrator.yaml"

    ```yaml
    workflow_name: IntakeWorkflow
    max_turns: 20
    human_in_the_loop: true
    workflow_startup_mode: AgentDriven
    orchestration_pattern: ag2_network
    initial_agent: IntakeAgent
    initial_message: "Start with IntakeAgent."
    triggers:
      - type: chat
        description: Start from the chat UI.
    ```

=== "context_variables.yaml"

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

=== "structured_outputs.yaml"

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

=== "agents.yaml"

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

=== "transition_graph.yaml"

    ```yaml
    transition_rules:
      - source_agent: user
        target_agent: IntakeAgent
        transition_type: condition
        condition_type: context_equals
        condition_key: intake_complete
        condition_value: false
        transition_target: AgentTarget

      - source_agent: IntakeAgent
        target_agent: GeneratorAgent
        transition_type: condition
        condition_type: context_equals
        condition_key: intake_complete
        condition_value: true
        transition_target: AgentTarget

      - source_agent: GeneratorAgent
        target_agent: user
        transition_type: after_turn
        transition_target: RevertToUserTarget
    ```

    Handoffs should describe deterministic routing. The runtime compiles these
    rules to a `TransitionGraph` and resolves each turn through AG2 1.0
    `WorkflowAdapter`. Use `context_equals`, `context_expression`, or
    `tool_called` conditions and put same-source condition routes before
    fallback `after_turn` routes.

=== "tools.yaml"

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

    Choose the smallest tool type that matches the job:

    | Tool type | Use when | UI contract |
    |-----------|----------|-------------|
    | `Agent_Tool` | Backend-only work: saving files, calling app APIs, transforming structured output. | No `ui` block. |
    | `UI_Surface` | Show a one-way artifact in the chat stream. | Requires `ui.component` and `ui.mode`. |
    | `UI_Tool` | User decision or structured input from a React component. | Requires `ui.component`, `ui.mode`, and `ui_contract`. |

    Use `UI_Tool` sparingly. Most generated artifacts should be `UI_Surface`; most
    backend actions should be `Agent_Tool`.

=== "ui_config.yaml"

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

Workflow UI registration stays workflow-local. If a first-party factory
workflow component is reused by multiple factory workflows, put the reusable
implementation under `factory_app/workflows/_shared/ui/` and import it from
each consuming workflow's `ui/index.js`. Do not import UI from a sibling
workflow folder.

Generated workflow bundles should not depend on `workflows/_shared` or
`factory_app/workflows/_shared/ui/`; generated workflow UI stays inside that
workflow's own `ui/` folder unless the capability is promoted into a framework
primitive.

## Read Next

- [Workflow Architecture](../../architecture/workflows/workflow-architecture.md)
- [Workflow Authoring Contracts](../../architecture/workflows/workflow-authoring-contracts.md)
- [Shared Workflow Infrastructure](../../architecture/builder/shared-workflow-infrastructure.md)
- [Workflow Task Batches](../../architecture/mozaiksai/task-batches.md)



