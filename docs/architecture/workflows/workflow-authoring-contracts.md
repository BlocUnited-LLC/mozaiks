# Workflow Authoring Contracts

This document defines the canonical, strict YAML contracts for workflow bundles.

The runtime validates these files with Pydantic (`extra="forbid"`). Workflow
bundles use the canonical YAML shapes documented here.

## Required Files

At minimum, a workflow should include:

- `orchestrator.yaml`
- `agents.yaml`
- `transition_graph.yaml`
- `context_variables.yaml`
- `structured_outputs.yaml`
- `tools.yaml`
- `ui_config.yaml`

`extended_orchestration/task_batches.yaml` is optional and required only when
the workflow uses workflow-local AG2 task batches.

`middleware.yaml` is optional and should be present only when the workflow uses
prompt middleware.

`a2a.yaml` is optional.

## Canonical Directory

```text
workflows/{workflow_name}/
  orchestrator.yaml
  agents.yaml
  transition_graph.yaml
  context_variables.yaml
  structured_outputs.yaml
  tools.yaml
  ui_config.yaml
  middleware.yaml             # prompt middleware config (only when needed)
  extended_orchestration/
    task_batches.yaml     # task batch config (only when needed)
  tools/
    *.py
  ui/
    *.js
```

For builder/system workflows, the same contract applies under the shared
generation-core workflow root. The file shape is canonical; the owning root
depends on whether the workflow is app-owned or generation-core-owned.

There is no canonical generated-workflow `workflows/_shared` folder. Generated
tools are owned by one workflow and live under that workflow's `tools/`
directory. If multiple generated workflows need the same capability, either
generate explicit workflow-local tools for each workflow or promote the
reusable behavior into a framework-owned `mozaiksai.core.*` API with a
documented contract.

Factory-owned builder infrastructure is different: shared builder-only Python
modules may live under `factory_app/workflows/_shared/` when multiple factory
workflows consume them. That path is for the factory repo itself, not for
generated workflow bundle output.

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

### Revision-aware workflow context

Builder workflows should not rely on one boolean like `refinement_mode` as the
long-term revision contract.

The target workflow input contract is:

- `build_mode` — `initial` or `revision`
- `revision_scope` — `patch`, `design`, `feature`, or `core`
- `change_request_id` — stable revision lineage id
- `artifact_kind`
- `artifact_version_id`
- `refinement_request`
- `refinement_request_meta`
- `change_intent`
- `impact_set`
- `sequence_status`
- `revision_origin_workflow`

Rules:

- `build_mode=revision` means the workflow starts from persisted builder state,
  not from a blank-slate assumption
- a `core` reroute into `ValueEngine` is still `build_mode=revision`
- workflows should anchor first on the revision request and persisted upstream
  summaries, then decide what to preserve or regenerate
- workflows should emit updated summaries, ownership metadata, and invalidation
  hints so later revisions stay structured

## Canonical File Shapes

### `orchestrator.yaml`

```yaml
workflow_name: ExampleWorkflow
max_turns: 20
human_in_the_loop: true
workflow_startup_mode: AgentDriven
orchestration_pattern: Pipeline
initial_message_to_user: null
initial_message: "Start with ExampleHostAgent."
initial_agent: ExampleHostAgent
triggers:
  - type: chat
    description: Start from chat transport
```

Rules:
- `workflow_name` must match directory name.
- `orchestration_pattern` should be the selected AG2 Network patternbook label
  when known, or `ag2_network` for generic runtime workflows. It is metadata;
  routing still comes from `transition_graph.yaml`.
- `workflow_startup_mode` must be one of:
  - `AgentDriven`
  - `UserDriven`
  - `BackendOnly`
- `initial_message` is a hidden AG2/runtime seed. It is not user-facing transcript content and should not be used as visible copy.
- `initial_message_to_user` is the optional user-facing startup prompt. When it is null, the first visible chat message should come from the workflow's actual initial agent output.

### `agents.yaml`

```yaml
agents:
  - name: ExampleHostAgent
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

### `transition_graph.yaml`

```yaml
transition_rules:
  - source_agent: user
    target_agent: ExampleHostAgent
    transition_type: condition
    condition_type: context_equals
    condition_key: intake_complete
    condition_value: false
    transition_target: AgentTarget
  - source_agent: ExampleHostAgent
    target_agent: user
    transition_type: after_turn
    transition_target: RevertToUserTarget
```

Rules:
- `transition_type` is `after_turn` or `condition`.
- `condition_type` is `context_equals`, `context_expression`, or `tool_called` when
  `transition_type: condition`.
- `context_equals` routes require `condition_key` and `condition_value`; they
  compile to a source-scoped AG2 `ContextEquals` condition.
- `context_expression` routes require `context_expression` using AG2
  `ContextExpression` syntax over declared `${context_variable}` references;
  they compile to a registered AG2 beta `TransitionCondition`.
- `tool_called` routes require `tool_name`; they compile to a source-scoped AG2
  `ToolCalled` condition.
- Same-source condition rules must appear before fallback `after_turn` rules
  because AG2 evaluates lower priority first.
- LLM intent classification belongs in the control plane before a workflow run
  is started or resumed.
- The runtime compiles these rules into an AG2 beta `TransitionGraph` and
  resolves each turn through `WorkflowAdapter`.
- AgentGenerator derives pattern-specific transition rules from
  `factory_app/build_context/AgentGenerator/ag2_network_patterns.yaml`.

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
        - type: user_text
          match:
            contains: approved
  example_topic:
    type: string
    source:
      type: state
      default: null

agents:
  ExampleHostAgent:
    variables:
      - host_complete
      - example_topic
```

Rules:
- `definitions` must be a mapping (`name -> definition`), not a list.
- `agents` must be a mapping (`agent_name -> {variables: [...]}`), not a list.
- `context_variables.yaml` is the declaration layer for workflow state. At
  runtime, AG2 `WorkflowState.context_vars` is the live state used for routing.
- Agent prompts and structured outputs own semantic reasoning. Context
  variables declare the typed state and artifact values that reasoning produces.
- Every `agents.<Agent>.variables[]` entry must reference a declared
  `definitions` key.
- Every `transition_graph.yaml` `condition_key` and every `${...}` reference in
  `context_expression` must reference a declared `definitions` key.
- Every task-batch `result.context_key` and `result.status_key`, including
  conveyor-derived `${id}_results` and `${id}_status`, must be declared.
- Valid trigger types for `state` variables:
  - `agent_text`
  - `user_text`
  - `ui_response`
- Valid source types:
  - `config`
  - `data_reference`
  - `data_entity`
  - `computed`
  - `state`
  - `external`
  - `file`
  - `build_context`

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

### `extended_orchestration/task_batches.yaml`

Only add this file if the workflow uses workflow-local task batches. Task
batches execute a typed list of work items with AG2 agents and merge results
into one declared context key.

**Minimal form:**

```yaml
version: 1
batches:
  - id: document_reviews
    trigger_agent: TriageAgent
    source:
      kind: context_variable
      path: review_plan.tasks
      task_model: DocumentReviewTask
    worker:
      mode: ag2_agent
      agent_field: initial_agent
      prompt_field: initial_message
      context_fields:
        - task_id
        - owned_paths
        - acceptance_criteria
    execution:
      concurrency: 4
      dependency_field: depends_on
      failure_policy: fail_batch
      retry_limit: 1
    result:
      context_key: document_review_results
      status_key: document_review_status
      merge_strategy: collect_task_outputs
      require_owned_paths: true
```

Rules:
- `version` is `1`.
- `batches[].id` values must be unique within a workflow.
- `source.kind` is `context_variable` or `structured_output`.
- `worker.mode` is `ag2_agent`.
- `result.context_key` and `result.status_key` must be explicit context
  variables that downstream agents understand.
- Cross-workflow sequencing still belongs in `extension_registry.json`, not in
  `task_batches.yaml`.

### `ui_config.yaml`

```yaml
visual_agents:
  - JokeHostAgent
  - user
```

### `middleware.yaml`

```yaml
prompt_middleware:
  - agent: JokeWriterAgent
    filename: hook_inject_preferences.py
    function: inject_preferences
```

Rules:
- Prompt middleware declarations are compiled to AG2 beta middleware.
- Use lifecycle tools for side effects and structured outputs/runtime
  validators for output validation.

## Guardrails

- Author YAML files directly; do not use `.json` declarative files for workflows.
- Keep tool implementations in `tools/*.py`; declaratives only reference them.
- Do not create or reference global shared workflow tool folders such as
  `workflows/_shared` or `app.workflows._shared` in generated workflow bundles.
- Factory-owned shared builder infrastructure may live under
  `factory_app/workflows/_shared/`, but generated workflow packs must not emit
  or depend on that path.
- Keep app-backend CRUD policy outside workflow declaratives.

