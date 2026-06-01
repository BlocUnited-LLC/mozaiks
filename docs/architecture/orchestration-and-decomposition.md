# Orchestration and Decomposition

**Status**: Active  
**Date**: May 31, 2026  
**Purpose**: Define how Mozaiks decomposes large app/workflow builds while keeping execution deterministic and AG2-aligned.

## Non-Negotiable Rules

- Workflow sequencing, workflow-local agent routing, workflow-local task batching,
  and control-plane routing are separate contracts.
- Natural-language reasoning does not belong in runtime graphs.
- LLMs may produce plans, task lists, classifications, and structured outputs.
  Runtime code validates and executes those contracts deterministically.
- AG2 owns agent execution mechanics: `agent.ask`, tools, handoffs, streams, and
  workflow-local Network routing.
- Mozaiks owns continuity: artifact state, app/build scope, workflow sequence
  selection, persistence, validation, and user-facing lifecycle events.
- There is no single global orchestration prompt over every user request.
  Builder-context free-text analysis belongs in the control-plane harness.

## Execution Scopes

### 1. Global Workflow Sequence

The global pack graph in
`factory_app/workflows/extended_orchestration/extension_registry.json` sequences
whole workflows through `workflow_sequences[]`.

Use it for coarse build phases:

- `ValueEngine -> ThemeCapture -> DesignDocs -> AgentGenerator -> AppGenerator -> AppReview`
- `ExistingAppDiscovery -> brownfield path selection`
- downstream design/revision paths that must restart at a workflow boundary

It does not decide how a workflow decomposes its internal task work.

### 2. Workflow-Local AG2 Routing

Workflow-local agent movement belongs in `handoffs.yaml`, compiled to an AG2
Network `TransitionGraph`.

It answers:

- which agent can speak next
- which deterministic context expression controls a route
- when a workflow terminates
- when an agent should ask for human input

It does not batch-generate modules, pages, services, workflow files, or app
bundles.

### 3. Workflow-Local Task Batches

Short parallel agent work belongs in `extended_orchestration/task_batches.yaml`.
This is the default decomposition primitive for factory artifact generation.

Use this for:

- module generation
- page schema generation
- service/repo/policy file generation
- workflow artifact generation
- review lanes where each lane is a bounded LLM task

The production shape is:

```text
planner agent emits typed task specs
  -> task_batches.yaml selects source, worker mapping, limits, and result key
  -> runtime validates dependencies, ownership, and concurrency bounds
  -> AG2 worker calls run with bounded parallelism
  -> runtime writes one merged payload to workflow context
  -> normal AG2 handoff routing continues
```

The LLM may plan the tasks, but Python owns validation, concurrency bounds, and
merge shape. Shared artifact state still belongs to Mozaiks.

### 4. Control-Plane Routing

The builder-session harness chooses the route before any workflow or coding
worker starts.

Use this taxonomy:

| Decision | Use when |
| --- | --- |
| `build_sequence` | User is creating a new app or broad product build. |
| `discovery_sequence` | User points Mozaiks at an existing app/repo. |
| `refinement_sequence` | Request changes generated artifacts or invalidates upstream specs. |
| `scoped_coding_task` | Existing files have a bounded patch scope and validation can prove the edit. |
| `workflow_local_batch` | A selected workflow needs short bounded parallel agent work. |
| `ask_for_scope` | Scope is ambiguous or risk is too high to choose safely. |

## Canonical Task Batch Contract

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

Meaning:

- `source.path` points to a deterministic list of typed task specs.
- `worker.agent_field` and `worker.prompt_field` map each task to an AG2 worker
  call.
- `execution` is enforced by runtime code, not prompt prose.
- `result.context_key` is the only merge surface downstream agents read.

## Factory Workflow Alignment

AppGenerator:

- `AppPlanAgent` emits `AppBuildPlan.build_tasks[]`.
- `app_build_plan.py` normalizes `app_task_batch_items`.
- `task_batches.yaml` declares the `app_build_tasks` batch.
- `assemble_app_tasks.py` consumes `app_task_batch_results`.
- `IntegrationReadinessAgent` aggregates connector needs from planning,
  task outputs, and recorded runtime needs.

AgentGenerator:

- `PackBuildCoordinator` collects the pack spec; the `workflow_bundle_tasks`
  task batch fires a `WorkflowBundleBuilderAgent` instance per workflow.
- Each worker writes a complete workflow bundle — including
  `extended_orchestration/task_batches.yaml` when the generated workflow
  requires it — as `WorkflowBundleBuilderOutput.files`.
- Generated workflows author task batches through the builder's structured
  output, not by writing custom orchestration Python.

## Cross-Workflow Data Transfer

Global workflows do not magically share workflow-local context.

Cross-workflow carry must be explicit:

1. workflow A persists canonical fields to artifacts or the chat session
2. workflow B loads them in a lifecycle tool or startup context preload
3. workflow B seeds its own declared context variables

Use it for app specs, brand tokens, design docs, app context graphs, and build
plans.

## BuildApp Guidance

Decompose into product artifacts first, not workflows first.

- modules and persistent pages come from deterministic product planning
- workflows are attached only when a capability requires agentic behavior
- refinements route by artifact boundary and workflow sequence impact
- third-party credentials are requested agentically at the point of need
- task agents may declare or record `integration_needs`; the parent readiness
  checkpoint aggregates them before validation/download

Typed builder contracts anchor the artifact pipeline:

- `ProductSpec` captures the approved product capability and UX intent
- `ExperienceSpec` owns persistent app UI definitions — pages, shell,
  navigation, and schema-first surface contracts
- `AgentAugmentationPlan` declares which capabilities require AI workflows
  and which workflow bundles need to be generated by AgentGenerator

## Summary

- Global pack graphs sequence workflows.
- `handoffs.yaml` controls workflow-local AG2 routing.
- `task_batches.yaml` handles short parallel LLM work inside a workflow.
- The control-plane harness chooses build/refinement/coding routes.
- Decomposition belongs to typed agent outputs, not runtime graph prose.
- Cross-workflow carry is explicit persistence plus lifecycle loading.
- The runtime executes compiled contracts, not natural-language logic.
