# Workflow Authoring Contracts

This document defines the canonical, strict YAML contracts for workflow bundles.

The runtime validates these files with Pydantic (`extra="forbid"`). Workflow
bundles use the canonical YAML shapes documented here.

## Required Files

At minimum, a workflow should include:

- `orchestrator.yaml`
- `agents.yaml`
- `handoffs.yaml`
- `context_variables.yaml`
- `structured_outputs.yaml`
- `tools.yaml`
- `ui_config.yaml`
- `hooks.yaml`

`a2a.yaml` is optional.

## Canonical Directory

```text
platform/workflows/{workflow_name}/
  orchestrator.yaml
  agents.yaml
  handoffs.yaml
  context_variables.yaml
  structured_outputs.yaml
  tools.yaml
  ui_config.yaml
  hooks.yaml
  tools/
    *.py
  ui/
    *.js
```

## Canonical File Shapes

### `orchestrator.yaml`

```yaml
workflow_name: JokeFactory
max_turns: 20
human_in_the_loop: true
workflow_startup_mode: AgentDriven
orchestration_pattern: Pipeline
initial_message_to_user: null
initial_message: "Start with JokeHostAgent."
initial_agent: JokeHostAgent
triggers:
  - type: chat
    description: Start from chat transport
```

Rules:
- `workflow_name` must match directory name.
- `workflow_startup_mode` must be one of:
  - `AgentDriven`
  - `UserDriven`
  - `BackendOnly`

### `agents.yaml`

```yaml
agents:
  - name: JokeHostAgent
    prompt_sections:
      - id: role
        heading: "[ROLE]"
        content: "You are a host."
    max_consecutive_auto_reply: 5
    auto_tool_mode: false
    structured_outputs_required: false
```

Rules:
- Each agent needs `name`.
- Each agent must provide either:
  - `prompt_sections` or `prompt_sections_custom`, or
  - `system_message`.

### `handoffs.yaml`

```yaml
handoff_rules:
  - source_agent: user
    target_agent: JokeHostAgent
    handoff_type: condition
    condition_type: string_llm
    condition: "When user starts the conversation."
    transition_target: AgentTarget
  - source_agent: JokeHostAgent
    target_agent: user
    handoff_type: after_work
    transition_target: RevertToUserTarget
```

Rules:
- `handoff_type` is `after_work` or `condition`.
- `condition` is required when `handoff_type: condition`.

### `context_variables.yaml`

```yaml
definitions:
  host_complete:
    type: boolean
    description: True when host has enough input
    source:
      type: state
      default: false
      triggers:
        - type: agent_text
          agent: JokeHostAgent
          ui_hidden: true
          match:
            equals: NEXT
  joke_topic:
    type: string
    source:
      type: state
      default: null

agents:
  JokeHostAgent:
    variables:
      - host_complete
      - joke_topic
```

Rules:
- `definitions` must be a mapping (`name -> definition`), not a list.
- `agents` must be a mapping (`agent_name -> {variables: [...]}`), not a list.
- Valid source types:
  - `config`
  - `data_reference`
  - `data_entity`
  - `computed`
  - `state`
  - `external`
  - `file`

### `structured_outputs.yaml`

```yaml
registry:
  JokeWriterAgent: JokeCollection

models:
  JokeCollection:
    type: model
    fields:
      jokes:
        type: list
        items: str
```

Rules:
- `models.<Name>.type` must be `model`.
- `registry` values must reference existing `models` keys.

### `tools.yaml`

```yaml
tools:
  - agent: JokeWriterAgent
    file: save_jokes.py
    function: save_jokes
    tool_type: Agent_Tool
    auto_invoke: true

  - agent: JokeCriticAgent
    file: display_ratings.py
    function: display_ratings
    tool_type: UI_Tool
    auto_invoke: true
    ui:
      component: JokeRatingsCard
      mode: inline

lifecycle_tools:
  - trigger: after_chat
    file: cleanup.py
    function: finalize
```

Rules:
- `tools[].tool_type` must be `Agent_Tool` or `UI_Tool`.
- `UI_Tool` requires `ui.component` and `ui.mode`.
- Tool references use `file` and `function`.

### `ui_config.yaml`

```yaml
visual_agents:
  - JokeHostAgent
chat_pane_agents:
  - JokeHostAgent
artifact_agents:
  - JokeCriticAgent
```

### `hooks.yaml`

```yaml
hooks:
  - hook_type: update_agent_state
    hook_agent: JokeWriterAgent
    filename: hook_inject_preferences.py
    function: inject_preferences
```

Rules:
- Valid `hook_type` values:
  - `process_message_before_send`
  - `update_agent_state`
  - `process_last_received_message`
  - `process_all_messages_before_reply`

## Guardrails

- Author YAML files directly; do not use `.json` declarative files for workflows.
- Keep tool implementations in `tools/*.py`; declaratives only reference them.
- Keep app-backend CRUD policy outside workflow declaratives.
