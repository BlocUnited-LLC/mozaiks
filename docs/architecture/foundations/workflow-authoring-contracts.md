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

`extended_orchestration/mfj_extension.json` is required when the workflow uses mid-flight journeys (MFJ).

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
  extended_orchestration/
    mfj_extension.json    # MFJ config (only when needed)
  tools/
    *.py
  ui/
    *.js
```

There is no canonical `workflows/_shared` folder. Generated tools are owned by
one workflow and live under that workflow's `tools/` directory. If multiple
workflows need the same capability, either generate explicit workflow-local
tools for each workflow or promote the reusable behavior into a framework-owned
`mozaiksai.core.*` API with a documented contract.

## Generation vs Refinement

Workflow authors must treat initial generation and post-generation refinement as
separate authoring modes.

Initial generation workflows:

- create canonical state or the first canonical artifact set
- define ownership boundaries that later refinement can reuse
- stay focused on first-pass compilation, not universal revision handling

Refinement workflows:

- start from a persisted artifact version
- consume a classified change request
- operate on explicit scoped units
- widen scope only when the router decides the current unit boundary is insufficient

For builder workflows such as `ValueEngine`, `DesignDocs`, `AgentGenerator`, and
`AppGenerator`, prompt design should stay clean:

- do not route delivered-bundle adjustments back through intake by default
- do not assume every change means "start over"
- do emit structured ownership metadata that later refinement can consume

`AppGenerator`'s `build_tasks` with `owned_paths`, `depends_on`, and
`acceptance_criteria` are the current canonical example of refinement-ready
metadata.

Detailed refinement-routing plans are internal. The public authoring contract is
that workflows should emit scoped ownership metadata so later refinement can
choose the smallest valid re-entry point.

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
    structured_outputs_required: false
```

Rules:
- Each agent needs `name`.
- Each agent must provide either:
  - `prompt_sections` or `prompt_sections_custom`, or
  - `system_message`.
- Auto-tool execution is derived from tools.yaml (agents with `auto_tool_call: true` tools); agents.yaml does not define a matching field.

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
    auto_tool_call: true

  - agent: JokeCriticAgent
    file: display_ratings.py
    function: display_ratings
    tool_type: UI_Surface
    auto_tool_call: true
    ui:
      component: JokeRatingsCard
      mode: inline

  - agent: ReviewerAgent
    file: request_revision.py
    function: request_revision
    tool_type: UI_Tool
    auto_tool_call: false
    ui:
      component: RevisionRequestCard
      mode: artifact

lifecycle_tools:
  - trigger: after_chat
    file: cleanup.py
    function: finalize
```

Rules:
- `tools[].tool_type` must be one of:
  - `Agent_Tool`
  - `UI_Tool`
  - `UI_Surface`
- `Agent_Tool` is backend-only and must not declare `ui`.
- `UI_Tool` is interactive and requires `ui.component` and `ui.mode`.
- `UI_Surface` is one-way and requires `ui.component` and `ui.mode`.
- `ui_contract` belongs only on `UI_Tool`.
- Tool references use `file` and `function`.

### `extended_orchestration/mfj_extension.json`

Only add this file if the workflow uses mid-flight journeys.

**Minimal single-phase form:**

```json
{
  "version": 3,
  "mid_flight_journeys": [
    {
      "id": "my_journey",
      "description": "Brief human-readable summary of the fan-out purpose.",
      "decomposition_agent": "DecompositionAgent",
      "fan_out": {
        "spawn_mode": "workflow",
        "max_children": 5
      },
      "fan_in": {
        "resume_agent": "SummaryAgent",
        "inject_as": "mfj_my_results"
      }
    }
  ]
}
```

**Multi-phase form (stages):**

Use `stages` when one trigger agent powers multiple sequential fan-out → fan-in
phases with a mid-flight user gate between them:

```json
{
  "version": 3,
  "mid_flight_journeys": [
    {
      "id": "my_journey",
      "description": "Stage 1 plans, user approves, stage 2 implements.",
      "decomposition_agent": "DecompositionAgent",
      "fan_out": { "spawn_mode": "workflow", "max_children": 10 },
      "stages": [
        {
          "id": "plan",
          "child_initial_agent": "PlanningAgent",
          "resume_agent": "ReviewAgent",
          "inject_as": "mfj_plan_results"
        },
        {
          "id": "implement",
          "gate_agent": "ApprovalAgent",
          "child_initial_agent": "ImplementationAgent",
          "resume_agent": "PackagingAgent",
          "inject_as": "mfj_impl_results"
        }
      ]
    }
  ]
}
```

Rules:
- `inject_as` values must start with `mfj_`.
- `gate_agent` is required for every stage after the first.
- `stages` and `fan_in` are mutually exclusive on the same journey.
- The runtime auto-synthesizes context variable declarations for every `inject_as`
  key and the five `_mfj_resume_*` handshake fields. Do not manually declare
  these in `context_variables.yaml`.
- The `resume_agent` for each stage must reference the `inject_as` key by name
  in its `[CONTEXT]` prompt section — the runtime injects the value but the agent
  must be authored to know what to do with it.

### `ui_config.yaml`

```yaml
visual_agents:
  - JokeHostAgent
  - user
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
- Do not create or reference global shared workflow tool folders such as
  `workflows/_shared` or `app.workflows._shared`.
- Keep app-backend CRUD policy outside workflow declaratives.
