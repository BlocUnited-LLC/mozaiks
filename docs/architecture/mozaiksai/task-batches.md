# Workflow Task Batches

Task batches are the canonical Mozaiks contract for bounded deterministic task
DAGs inside one workflow. They replace persisted session-based decomposition
for normal builder work such as generating modules, pages, services,
contracts, and review items.

They are intentionally different from AG2 1.0 beta `Task` and sub-agent delegation:

- AG2 `Task` is a lifecycle and observability wrapper around a unit of work.
  It does not assign, schedule, dependency-sort, or merge artifact work.
- AG2 sub-agent delegation lets an agent call another agent as a tool. The
  calling LLM decides when and what to delegate.
- Mozaiks task batches execute a planner-emitted, typed task list
  deterministically. Mozaiks owns dependency order, concurrency, output
  ownership, failure policy, and the result merge surface. AG2 should own the
  worker execution and lifecycle observation wherever its Network/Task APIs
  support that shape.

The long-term execution alignment is tracked in
[AG2 Execution Alignment Plan](../workflows/ag2-execution-alignment-plan.md).

## Contract

Workflow-local task batches live beside the workflow bundle:

```text
workflows/{WorkflowName}/extended_orchestration/task_batches.yaml
```

The file is optional. Use it only when an agent emits a typed list of similar
work items that can be processed independently by AG2 agents.

```yaml
version: 1
batches:
  - id: app_build_tasks
    trigger_agent: AppPlanAgent
    source:
      kind: context_variable
      path: app_task_batch_items
      task_model: AppBuildTask
    worker:
      mode: ag2_agent
      agent_field: initial_agent
      prompt_field: initial_message
      context_fields:
        - task_id
        - task_type
        - owned_paths
        - acceptance_criteria
    execution:
      concurrency: 8
      dependency_field: depends_on
      failure_policy: fail_batch
      retry_limit: 2
      timeout_seconds: 300
    result:
      context_key: app_task_batch_results
      status_key: app_task_batch_status
      merge_strategy: collect_task_outputs
      require_owned_paths: true
```

`source.path` points at a deterministic task list. `worker.agent_field` and
`worker.prompt_field` tell the runtime which AG2 agent should execute each item
through an AG2 Network task channel.
`result.context_key` is the single merge location consumed by later agents.
`trigger_agent` names the agent turn after which the source path is expected to
exist; it is not a planner by itself.

### Decomposition Conveyors

For the common case where YAML should declare only the static conveyor contract,
use `conveyors[]`. The runtime compiles each conveyor into a normal batch with
canonical defaults:

```yaml
version: 1
conveyors:
  - id: workflow_generation
    decomposition_agent: DecompositionAgent
    execution_agents:
      - AgentRosterAgent
      - ContextVariablesAgent
      - StructuredModelsAgent
      - ToolsAgent
      - TransitionGraphAgent
    concurrency: 6
```

The decomposition agent must emit a structured output with
`DecompositionPlan.tasks[]`. Each task uses the canonical `DecomposedTask`
shape:

```yaml
task_id: content_creation_transitions
execution_agent: TransitionGraphAgent
task_prompt: Create transition rules for ContentCreationWorkflow.
depends_on:
  - content_creation_agents
```

The runtime derives the rest:

- task source: `DecompositionPlan.tasks`
- task model: `DecomposedTask`
- agent field: `execution_agent`
- prompt field: `task_prompt`
- dependency field: `depends_on`
- result context: `{conveyor_id}_results`
- status context: `{conveyor_id}_status`

`execution_agents[]` is an allow-list. A decomposed task that names any other
agent is rejected before it can execute.

## Runtime Execution Semantics

Task batches execute inside the current workflow run, but worker tasks do not
join the parent workflow `transition_graph.yaml`.

For workflows without task batches, Mozaiks opens one AG2 workflow channel for
the full `transition_graph.yaml`. For workflows with task batches, Mozaiks uses
phased AG2 Network execution:

```text
parent AG2 workflow phase
  -> decomposition/trigger agent emits structured task list
  -> Mozaiks extracts tasks and dependency graph
  -> each ready task runs in its own AG2 workflow channel
  -> Mozaiks validates and merges task outputs
  -> downstream AG2 workflow phase starts with result context keys populated
```

For a matching batch, each task item is executed through AG2 Network:

```text
task.initial_agent
  -> create/select that AG2 agent
  -> open one-task AG2 workflow channel
  -> pass task_context through workflow channel context_vars
  -> AG2 default handler invokes the worker
  -> read worker output from AG2 WAL / structured output result
  -> normalize returned structured/code output
  -> validate output stays within task-owned paths
```

`initial_agent` is therefore a worker selector, not a request to jump into the
parent transition graph at that agent. Worker results do not trigger
parent `transition_graph.yaml` handoffs. Ordering inside the batch is
controlled only by `depends_on`, `concurrency`, and the batch failure policy.
Once all ready tasks complete, the downstream parent workflow phase starts with
the updated `result.context_key` and `status_key` context values.

For both explicit batches and decomposition conveyors, `depends_on` controls
both scheduling and context routing. A downstream task receives:

- `decomposition_plan`: the full task graph for the current batch;
- `current_task`: the task currently being executed;
- `completed_task_outputs`: all task outputs completed so far;
- `dependency_task_outputs`: only outputs from tasks listed in `depends_on`.

The decomposition agent owns the dependency graph. The runtime owns injection
of actual upstream outputs because those outputs do not exist when
decomposition runs.

## Harness Fields

Task batches should keep the execution harness narrow. The runtime needs only
stable fields for identity, routing, ownership, ordering, and result handling:

- `task_id`
- `initial_agent`
- `initial_message`
- `owned_paths`
- `depends_on`
- `acceptance_criteria`
- `context_variables`
- `integration_needs`

Domain-specific metadata should travel as pass-through context for the selected
worker agent. For domain-agnostic build workflows, prefer a typed base task with
a `domain_context` object instead of adding every domain's planning fields to
the harness-visible model.

## Decomposition Taxonomy

Use task batches when a workflow has a repeatable artifact-production fanout:

| Work shape | Typical planner output | Worker output |
|---|---|---|
| App module generation | `build_tasks[]` | `modules/{module_id}/...` files |
| Page/schema generation | `build_tasks[]` | `ui/pages/*.yaml` or bounded custom route files |
| Workflow bundle generation | `workflow_bundle_tasks[]` | complete `workflows/{name}/...` bundle |
| Extract/transform review | `review_items[]` or `records[]` | structured findings or transformed records |
| Multi-section document generation | `section_tasks[]` | named document sections |

A generated workflow that needs task batching must contain all required pieces:

1. A planner/coordinator agent that emits a typed task list.
2. A structured output model for each task item.
3. A save/materialization tool that writes the concrete context variable named
   by `source.path`.
4. `extended_orchestration/task_batches.yaml` with `trigger_agent`, `source`,
   worker mapping, execution policy, and result keys.
5. A downstream synthesis or assembly step that reads only `result.context_key`.

If any of those pieces is missing, the workflow has a prompt-level idea of
decomposition but not a runnable Mozaiks task batch.

## Builder Usage

AppGenerator uses this pattern directly:

- `AppPlanAgent` emits `AppBuildPlan.build_tasks[]`.
- `app_build_plan.py` stores normalized `app_task_batch_items`.
- `extended_orchestration/task_batches.yaml` declares how those tasks run.
- `assemble_app_tasks.py` merges `app_task_batch_results` into generated files.
- `IntegrationReadinessAgent` reads `app_task_batch_results` before validation.

AgentGenerator uses the same abstraction for generated workflows:

- `WorkflowBundleBuilderAgent` is the task batch worker that generates each
  full workflow bundle in parallel.
- The agent writes `extended_orchestration/task_batches.yaml` directly into its
  `WorkflowBundleBuilderOutput.files` when the generated workflow requires it.
- Generated workflows can declare task batches through the builder's structured
  output without custom runtime code.

## Boundaries

Task batches are not global workflow sequencing. Cross-workflow build order
belongs in `extension_registry.json` and `workflow_sequences[]`.

Task batches are not semantic routing. Intent classification and re-entry belong
to the control-plane harness.

Task batches are not a persistence authority. They operate over workflow context
and artifact outputs; persistence remains owned by the runtime and artifact
stores.

## Failure Model

`task_batches.yaml` declares failure behavior as data:

- `fail_batch` stops the batch when required work fails.
- `continue_with_available` lets synthesis proceed with successful items.
- `collect_errors` records failures for explicit downstream handling.

Retry limits and timeouts are contract fields, not prompt prose. Agents reason
about the typed task list and worker outputs; runtime code enforces the
execution policy.

## When To Use

Use task batches when work is:

- a typed list of similar items;
- bounded to one workflow's context;
- safe to execute as bounded AG2 task channels;
- mergeable through one declared result key;
- short enough that independent child sessions are unnecessary.

Keep work as normal handoffs when the workflow is a small linear conversation.
Use cross-workflow sequence steps when the work has independent runtime,
artifact-family, or user-review ownership.

Use AG2 sub-agent delegation instead when the parent agent should make dynamic,
same-turn tool-call decisions and no Mozaiks artifact ownership or deterministic
merge contract is required. Use AG2 `Task` lifecycle events as an observability
enhancement around long work, not as a replacement for `task_batches.yaml`.
