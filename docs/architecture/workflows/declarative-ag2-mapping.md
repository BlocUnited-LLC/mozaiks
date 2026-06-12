# Declarative Config to AG2 Mapping

This document maps canonical workflow YAML declaratives to AG2-native execution.

Runtime loading is strict. Files are validated by typed contracts (Pydantic with
`extra="forbid"`), so examples here use only canonical shapes.

## Core Point

Only workflow declaratives map to AG2.

These do not map to AG2:

- app backend declaratives
- shell declaratives
- domain event contracts
- workflow triggers

Those are Mozaiks layers that exist before a workflow starts.

## Workflow File Mapping

| Workflow file | Role | AG2 relationship |
| --- | --- | --- |
| `orchestrator.yaml` | pattern, turns, startup behavior | mostly AG2-native |
| `agents.yaml` | agent roster and prompts | AG2-native with Mozaiks composition helpers |
| `transition_graph.yaml` | routing rules inside the workflow | AG2 beta WorkflowAdapter / TransitionGraph |
| `context_variables.yaml` | workflow state bindings | AG2-native container plus Mozaiks adapters |
| `tools.yaml` | tool declarations | AG2 tool calling plus Mozaiks wrappers |
| `middleware.yaml` | prompt injection declarations | Mozaiks declarations compiled to AG2 beta middleware |
| `structured_outputs.yaml` | typed runtime validation | Mozaiks layer |
| `ui_config.yaml` | frontend exposure metadata | frontend-only |
| `extended_orchestration/task_batches.yaml` | workflow-local deterministic task DAG contract | Mozaiks contract layer executed through AG2 where possible |

## Native or Near-Native Mappings

### `orchestrator.yaml`

Maps to workflow-local execution concerns such as:

- pattern selection
- startup mode
- initial agent
- initial message
- turn budget

Canonical startup key is `workflow_startup_mode` (`AgentDriven`, `UserDriven`,
`BackendOnly`).

`orchestration_pattern` is metadata describing the selected AG2 Network
patternbook label. The runtime does not route from this string; it routes from
the compiled `transition_graph.yaml` transition graph.

### `agents.yaml`

Each agent entry becomes an AG2 agent definition after prompt composition and
tool binding.

### `transition_graph.yaml`

Maps to AG2 beta Network transition conditions and targets. Mozaiks compiles
the rules into a `TransitionGraph`; turn-to-turn routing is resolved through
AG2 beta `WorkflowAdapter`.

Runtime compilation rules:

- unconditional `after_turn` rules compile to `FromSpeaker`
- `condition_type: context_equals` rules compile to a source-scoped adapter over
  AG2 `ContextEquals`
- `condition_type: context_expression` rules compile to a source-scoped custom
  AG2 beta `TransitionCondition` that evaluates AG2's `ContextExpression`
  syntax against workflow context state
- `condition_type: tool_called` rules compile to a source-scoped adapter over
  AG2 `ToolCalled`
- `target_agent: user` pauses the run for user input
- `target_agent: terminate` compiles to `TerminateTarget`

LLM classification belongs before routing: a control-plane route, agent tool, or
structured output sets context state; the graph then routes deterministically.

Termination is declarative. A workflow bundle ends through
`transition_graph.yaml`, usually by routing the final agent to:

```yaml
- source_agent: FinalAgent
  target_agent: terminate
  transition_type: after_turn
  transition_target: TerminateTarget
```

The runtime does not inspect message text for completion and does not use a
separate Python termination handler. During execution, Mozaiks resolves the next
speaker through AG2 beta `WorkflowAdapter`; when that resolution returns AG2
`TerminateTarget`, the turn loop reports `run_completed=true` and the runtime
marks the app-scoped `ChatSessions` run as completed. `max_turns` remains a
safety cap, not the primary happy-path completion mechanism.

### `context_variables.yaml`

Maps to the shared workflow state container used during execution.

Canonical shape:

```yaml
definitions:
  var_name:
    type: string
    source:
      type: state
      default: null
agents:
  AgentName:
    variables:
      - var_name
```

### `tools.yaml`

Maps to callable tool registration, with optional Mozaiks validation or wrapper
behavior.

Canonical shape:

```yaml
tools:
  - agent: AgentName
    file: tool_file.py
    function: run_tool
    tool_type: Agent_Tool
  - agent: AgentName
    file: render_status.py
    function: render_status
    tool_type: UI_Surface
    ui:
      component: StatusPanel
      mode: artifact
lifecycle_tools: []
```

### `middleware.yaml`

Maps to Mozaiks prompt-injection declarations. Runtime registration is AG2 beta
agent middleware, not the old AG2 hook registration style. Lifecycle behavior belongs
in `tools.yaml` `lifecycle_tools`.

Canonical shape:

```yaml
prompt_middleware:
  - agent: AgentName
    filename: hook_file.py
    function: update_state
```

## Mozaiks-Only Workflow Layers

### `structured_outputs.yaml`

Used for:

- typed validation
- deterministic auto-tool flows
- stronger execution guarantees than prompt text alone

Canonical shape:

```yaml
registry:
  AgentName: ModelName
models:
  ModelName:
    type: model
    fields:
      field_name:
        type: str
```

### `ui_config.yaml`

Used for:

- frontend rendering metadata
- agent visibility rules

### `extended_orchestration/task_batches.yaml`

Used for:

- declaring which agent produces or exposes a typed task list
- mapping each task item to an AG2 worker agent and prompt
- bounding concurrency, retries, timeouts, dependencies, and failure policy
- declaring the result context key consumed by later workflow agents

The typed DAG contract is Mozaiks-owned because AG2 `Task` does not assign,
dependency-sort, or merge artifact work. Worker execution, task lifecycle
observation, and network turn handling should use AG2 Network/Task primitives
where practical. See [AG2 Execution Alignment Plan](ag2-execution-alignment-plan.md).

**Minimal task batch authored form:**

```yaml
version: 1
batches:
  - id: my_tasks
    trigger_agent: PlanningAgent
    source:
      kind: context_variable
      path: plan.tasks
      task_model: MyTask
    worker:
      mode: ag2_agent
      agent_field: initial_agent
      prompt_field: initial_message
    result:
      context_key: my_task_results
      status_key: my_task_status
```

Schema defaults that do not need to be authored:
- `worker.mode` defaults to `ag2_agent`
- `execution.concurrency` defaults to `4`
- `execution.failure_policy` defaults to `fail_batch`
- `result.merge_strategy` defaults to `collect_task_outputs`

Declare result keys in `context_variables.yaml` when agents need to read them.
`task_batches.yaml` is execution config; it is not a substitute for typed shared
workflow state.

## What Sits Before AG2

The following app-bundle families are consumed before AG2 is involved:

- `app/data/*`
- `app/modules/*`
- `app/config/*`
- workflow `triggers:` declared in `workflows/*/orchestrator.yaml` for app-owned workflows, or `factory_app/workflows/*/orchestrator.yaml` for builder/system workflows

Most importantly:

- a domain event becomes a route decision before AG2 sees a workflow input

That route decision belongs to Mozaiks.

## Design Guardrails

Do not use AG2 as the conceptual home for:

- CRUD state
- navigation
- settings and subscription policy
- domain event naming
- event-to-workflow policy

Use AG2 for what it is good at:

- conversations
- handoffs
- tool use
- agent coordination inside a workflow

AgentGenerator selects workflow shapes from the shared
[AG2 Network Patternbook](ag2-network-patternbook.md). The patternbook guides
generation; the generated YAML remains the runtime contract.

## Authoring Note

Use `*.yaml` declaratives in workflow bundles.

## Cross References

- [workflow-architecture.md](workflow-architecture.md)
- [../foundations/events-and-data/event-system.md](../foundations/events-and-data/event-system.md)
- [../foundations/core-product-app-bundle-boundary.md](../foundations/core-product-app-bundle-boundary.md)

