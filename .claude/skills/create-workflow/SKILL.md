---
name: create-workflow
description: Create a new AI workflow from scratch. Generates workflow YAML files, tools, and optional UI components.
argument-hint: "[WorkflowName] [description of what it should do]"
---

Help the user create a new workflow named $ARGUMENTS.

When acting on this skill, transform human intent into a deterministic workflow
bundle that matches the current Mozaiks workflow authoring contract.

## First Decision: Which Workflow Root Owns It?

Choose the owning workflow root before writing files:

- App-owned workflows belong under `app/workflows/{WorkflowName}/`.
- Shared factory/builder workflows in this repo belong under
  `factory_app/workflows/{WorkflowName}/`.

Do not treat every workflow as an app-owned workflow by default.

## Phase 1: Architectural Discovery

Before writing any files, define the deterministic contract:

1. What outcome must the workflow produce?
2. Which agents are needed, and which ones are conversational vs structured-output agents?
3. What exact typed outputs must the workflow emit?
4. Which tools persist data or emit UI artifacts?
5. Is the workflow standalone, part of a cross-workflow sequence, or using workflow-local task batches?
6. Which context variables are required at startup or on resume?

## Routing Model: Do Not Mix These Up

Mozaiks has multiple routing layers. Keep them separate:

- `transition_graph.yaml` = workflow-local agent routing inside one workflow.
- `workflow_sequences[]` in `extended_orchestration/extension_registry.json` = cross-workflow build/revision sequencing.
- `transitions[]` in `extended_orchestration/extension_registry.json` = user choice and context-seeding routes.
- `entrypoints[]` in `extended_orchestration/extension_registry.json` = external route entry into a sequence or transition.

Important:

- A workflow sequence is not a human-in-the-loop handoff mechanism.
- A transition is not an agent handoff.
- Do not try to encode build-sequence policy inside `transition_graph.yaml`.

## Phase 2: Scaffold the Workflow Directory

Start with the canonical file set for the owning workflow root:

```text
app/workflows/{WorkflowName}/
├── orchestrator.yaml
├── agents.yaml
├── transition_graph.yaml
├── context_variables.yaml
├── structured_outputs.yaml
├── tools.yaml
├── ui_config.yaml            # include when the workflow has websocket-visible agents or UI artifacts
├── middleware.yaml                # include when the workflow needs lifecycle hooks
├── extended_orchestration/
│   └── task_batches.yaml     # only when the workflow uses task batches
├── tools/
│   ├── __init__.py
│   └── *.py
└── ui/{WorkflowName}/
    └── components/
```

For factory-owned builder workflows, use the same file contract under
`factory_app/workflows/{WorkflowName}/`.

## Phase 3: Define the Declarative Contracts First

Always define YAML contracts before implementation.

### 1. `orchestrator.yaml`

Declare the required document version and use the current startup field name:

```yaml
schema_version: mozaiks.orchestrator.v1
workflow_name: ExampleWorkflow
max_turns: 20
human_in_the_loop: true
workflow_startup_mode: AgentDriven
orchestration_pattern: Pipeline
initial_message: "Start with ExampleHostAgent."
initial_agent: ExampleHostAgent
triggers:
  - type: chat
    description: Start from chat transport
```

Rules:

- Use `workflow_startup_mode`, not `startup_mode`.
- `initial_message` is a hidden runtime seed, not visible UI copy.

### 2. `structured_outputs.yaml`

Declare the required document version in the current top-level shape:

```yaml
schema_version: mozaiks.structured_outputs.v1
registry:
  InterviewAgent: null
  GeneratorAgent: MyOutputModel

models:
  MyOutputModel:
    type: model
    fields:
      title:
        type: str
      payload:
        type: dict
```

Do not wrap `registry` and `models` inside an extra `structured_outputs:` object.

### 3. `agents.yaml`

Separate conversational agents from structured-output agents.

```yaml
agents:
  - name: GeneratorAgent
    structured_outputs_required: true
    prompt_sections:
      - id: instructions
        content: Generate valid MyOutputModel JSON only.
```

Rules:

- `structured_outputs_required: true` belongs on agents that must emit the registered model.
- Auto-tool execution is declared in `tools.yaml`, not in `agents.yaml`.

### 4. `context_variables.yaml`

Declare the workflow state and which agents can see which variables.

```yaml
definitions:
  artifact_request:
    type: string
    source:
      type: state
      default: null

agents:
  GeneratorAgent:
    variables:
      - artifact_request
```

### 5. `tools.yaml`

Bind tools to agents and declare UI metadata there.

```yaml
tools:
  - agent: GeneratorAgent
    file: artifact_tools.py
    function: save_and_render_artifact
    description: Persist the structured artifact and emit a UI event.
    tool_type: Agent_Tool
    auto_tool_call: true
    ui:
      component: MyComponent
      mode: artifact
```

### 6. `transition_graph.yaml`

Use this only for workflow-local agent routing.

All conditions must use `condition_type: expression`. LLM classification belongs
before routing — set context variables in agent tools or structured outputs, then
route deterministically.

```yaml
transition_rules:
  - source_agent: user
    target_agent: ExampleHostAgent
    transition_type: condition
    condition_type: expression
    condition: ${intake_complete} == false
    transition_target: AgentTarget

  - source_agent: ExampleHostAgent
    target_agent: user
    transition_type: after_turn
    transition_target: RevertToUserTarget
```

### 7. `ui_config.yaml`

Declare `visual_agents` when the workflow has websocket-visible agent messages
or UI-bearing outputs.

```yaml
visual_agents:
  - InterviewAgent
  - GeneratorAgent
  - user
```

### 8. `middleware.yaml` and `extended_orchestration/task_batches.yaml`

- Use `middleware.yaml` for lifecycle hook declarations when needed.
- Use `extended_orchestration/task_batches.yaml` only when the workflow needs bounded workflow-local parallel task execution.
- Do not put workflow sequence routing into task batch config.

## Phase 4: Implementation Rules

Tools stay dumb. LLMs reason.

- Do not write heuristics or routing policy in Python tools.
- Read `context_variables.get("structured_output")` when the tool is triggered by structured output.
- Persist or emit UI payloads without reinterpreting the agent's reasoning.

Example:

```python
from typing import Any, Dict, Optional


async def save_and_render_artifact(context_variables: Optional[Any] = None) -> Dict[str, Any]:
    if not context_variables:
        return {"success": False}

    data = context_variables.get("structured_output")
    context_variables["my_domain_data"] = data
    return {"success": True, "artifact": data}
```

If the workflow needs a UI artifact, add the matching React component under the
workflow's `ui/{WorkflowName}/components/` directory and keep its props aligned
with the emitted payload shape.

## Review Checklist

Before finishing, verify:

1. `workflow_startup_mode` is used in `orchestrator.yaml`.
2. `structured_outputs.yaml` declares `schema_version: mozaiks.structured_outputs.v1` with top-level `registry` and `models`.
3. `transition_graph.yaml` handles only workflow-local routing.
4. Any cross-workflow sequencing or routed entry behavior is authored in
   `extended_orchestration/extension_registry.json`, not in workflow files.
5. `ui_config.yaml` only exposes agents that should be visible to the UI.
6. Any task batch config is local to the workflow and only added when needed.

When explaining the result to the user, describe whether the workflow is
app-owned or factory-owned and call out any required follow-up in
`extension_registry.json` separately from the workflow bundle itself.


