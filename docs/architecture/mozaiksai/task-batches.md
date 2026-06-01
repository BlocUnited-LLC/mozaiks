# Workflow Task Batches

Task batches are the canonical Mozaiks contract for bounded parallel work inside
one workflow. They replace persisted session-based decomposition for normal
builder work such as generating modules, pages, services, contracts, and review
items.

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
`worker.prompt_field` tell the runtime which AG2 agent to call for each item.
`result.context_key` is the single merge location consumed by later agents.

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
- safe to execute with `asyncio.gather`-style AG2 agent calls;
- mergeable through one declared result key;
- short enough that independent child sessions are unnecessary.

Keep work as normal handoffs when the workflow is a small linear conversation.
Use cross-workflow sequence steps when the work has independent runtime,
artifact-family, or user-review ownership.
