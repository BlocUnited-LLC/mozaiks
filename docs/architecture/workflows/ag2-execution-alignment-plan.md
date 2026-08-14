# AG2 Execution Alignment Plan

This document audits Mozaiks workflow execution against AG2 1.0 Network and
Task APIs. The goal is to shrink Mozaiks-owned agentic runtime code and keep
Mozaiks focused on deterministic contracts around AG2.

Related boundary: [AG2 Ownership Boundary](ag2-ownership-boundary.md).

## Source Baseline

Reviewed against:

- AG2 docs, 2026-06-08 pages:
  - <https://docs.ag2.ai/latest/docs/beta/network/quick_start/>
  - <https://docs.ag2.ai/latest/docs/beta/network/hub_and_identity/>
  - <https://docs.ag2.ai/latest/docs/beta/network/agent_clients/>
  - <https://docs.ag2.ai/latest/docs/beta/network/adapters_overview/>
  - <https://docs.ag2.ai/latest/docs/beta/network/task_observation/>
  - <https://docs.ag2.ai/latest/docs/beta/tasks/>
  - <https://docs.ag2.ai/latest/docs/beta/task_delegation/>
- Installed AG2 package inspected locally: `ag2==1.0.0`.

Important AG2 facts:

- `Hub` is the network's authoritative state surface: registry, channel table,
  write-ahead logs, audit log, rule checks, and expectation evaluation.
- `HubClient.register(...)` returns an `AgentClient`; `AgentClient` owns
  channel opening, sending, waiting, and inbound envelope handlers.
- The default handler reads the channel WAL, projects view history, attaches a
  `TaskMirror`, calls `Agent.ask(...)`, and posts the adapter-shaped response
  envelope.
- `WorkflowAdapter` owns workflow-channel turn progression through
  `TransitionGraph`; termination happens when the graph emits
  `TerminateTarget` or the turn cap is reached.
- AG2 `Task` is lifecycle/observability only. AG2 explicitly does not assign,
  schedule, dependency-sort, or merge tasks.
- AG2 sub-task delegation is LLM-driven delegation through tools; the calling
  LLM decides when and what to delegate.

## Current Mozaiks Ownership

| Current file | Current responsibility | Boundary decision |
| --- | --- | --- |
| `mozaiksai/core/adapters/ag2_orchestration.py` | Runtime port adapter into AG2-flavored workflow execution | Keep as the boundary, but make it own the AG2 Hub runner instead of delegating to a custom loop. |
| `mozaiksai/core/workflow/orchestration_patterns.py` | Loads workflow config, persistence, context, agents, lifecycle, transport, then invokes `AG2NetworkRunner` | Keep as Mozaiks run setup/teardown. Generic turn execution belongs to AG2 Hub/AgentClient. |
| `mozaiksai/core/workflow/outputs/runtime_validation.py` and `runtime_events.py` | Validate AG2 reply bodies against Mozaiks structured-output contracts and emit runtime validation events | Keep. These are Mozaiks contract observers around AG2 envelopes, not an execution loop. |
| `mozaiksai/core/workflow/execution/network_graph.py` | Compiles `transition_graph.yaml` to AG2 `TransitionGraph`; resolves next speaker through `WorkflowAdapter.fold` manually | Keep compile logic. Remove manual fold as primary runtime routing once Hub workflow channels drive progression. |
| `mozaiksai/core/workflow/task_batches.py` | Validates batch/conveyor config, extracts typed task lists, dependency-sorts tasks, runs workers through AG2 Task lifecycle-wrapped turns, merges outputs into context | Keep typed DAG contract, dependency scheduler, ownership validation, and result merge. Current worker invocation uses standalone AG2 Task stream evidence; future Hub-backed worker channels should use real AG2 channel ids and TaskMirror. |
| `mozaiksai/core/workflow/execution/stream_bridge.py` | Bridges AG2 stream events to Mozaiks transport | Keep, but attach to AG2 channel/task streams rather than only a local per-turn stream. |

## Target Execution Shape

```text
Mozaiks workflow YAML
  -> validate declarative contracts
  -> create AG2 Agents
  -> compile transition_graph.yaml to AG2 TransitionGraph
  -> open AG2 Hub workflow channel
  -> AG2 AgentClients/default handlers execute turns
  -> Mozaiks observers validate structured outputs and run deterministic hooks
  -> Mozaiks persists context, run status, and canonical artifacts
```

Mozaiks remains the owner of:

- workflow file validation
- prompt and tool declaration loading
- context-variable policy and persistence
- structured-output validation
- task DAG contract validation
- artifact ownership and materialization
- Studio/platform transport events and build lifecycle

AG2 becomes the owner of:

- channel lifecycle
- agent registration identity
- turn-taking enforcement
- view/WAL projection
- default `Agent.ask(...)` invocation
- task observation mirroring
- turn-failure reporting
- network/channel audit history

## Replacement Decisions

### 1. Workflow Runner

The custom local turn loop has been replaced by an AG2 Hub workflow runner
behind `AG2OrchestrationAdapter`.

Required behavior:

- open `Hub` with a durable or session-scoped `KnowledgeStore`;
- register every Mozaiks workflow agent as an AG2 `AgentClient`;
- use stable hub-assigned `agent_id` values for routing;
- compile `transition_graph.yaml` into `TransitionGraph.to_dict()`;
- open a `workflow` channel with `knobs.graph`;
- seed the channel with the startup message;
- keep process-live paused channels open for the next user reply when the
  backend process still owns the AG2 Hub;
- route the next user reply through the same AG2 workflow channel before
  falling back to persisted event bootstrap;
- wait for `EV_CHANNEL_CLOSED`, HITL pause, failure, or cancellation;
- replay/read channel WAL for persistence and UI summary.

Mozaiks-specific work remains around the runner:

- Studio websocket events;
- app/chat persistence;
- lifecycle hooks that are contract-specific;
- structured-output validation after agent replies;
- context persistence on completion or pause.

### 2. Transition Graph

Keep the YAML-to-AG2 compiler, but reduce the custom condition surface.

Preferred mapping:

| Mozaiks transition need | AG2 target |
| --- | --- |
| after one named speaker | `FromSpeaker` |
| route to named agent | `AgentTarget` |
| terminate | `TerminateTarget` |
| simple context equality | AG2 `ContextEquals` |
| route after tool call | AG2 `ToolCalled` |
| source-scoped context equality | Mozaiks adapter over AG2 `FromSpeaker` + `ContextEquals` until AG2 has native condition composition |
| source-scoped composite context expression | Mozaiks adapter over AG2 `FromSpeaker` plus a Mozaiks expression evaluator until AG2 has native expression conditions or composition |
| source-scoped tool route | Mozaiks adapter over AG2 `FromSpeaker` + `ToolCalled` until AG2 has native condition composition |
| one-shot bootstrap dispatch | `BootstrapInitialDispatch`, a Mozaiks adapter scoped to the injected human-initiator first turn so that bootstrap routing cannot steal later user replies |

Workflow generation emits only the canonical deterministic condition types:
`condition_type: context_equals`, `condition_type: context_expression`, or
`condition_type: tool_called`.

### 3. Context Variables

AG2 workflow channels already carry `context_vars` and support context update
packets. Mozaiks should use that surface instead of treating context only as a
local Python dict passed into `agent.ask`.

Target:

- initial `context_variables.yaml` state is passed through workflow channel
  `knobs.context_vars`;
- tools or Mozaiks observers emit AG2 context update envelopes for state
  changes that affect routing;
- Mozaiks persists selected context snapshots to app/chat storage;
- generated artifact data remains in Mozaiks artifact stores, not in AG2
  channel context.

### 4. Structured Outputs

AG2 owns model execution, but Mozaiks owns canonical output contracts.

Target integration:

- observe AG2 round-end envelopes or the per-turn stream;
- extract agent reply bodies;
- validate against `structured_outputs.yaml`;
- emit `runtime.agent_output_validated`;
- write validated data into Mozaiks context/artifact stores.

Do not make AG2 responsible for Mozaiks artifact schemas.

### 5. Task Batches And Conveyors

Keep `task_batches.yaml` as a Mozaiks deterministic DAG contract, but align
execution with AG2 primitives.

AG2 does not currently assign or dependency-sort tasks, so Mozaiks may own:

- reading a typed task list from structured output or context;
- validating allowed execution agents;
- dependency graph scheduling;
- output ownership checks;
- result merge shape and result context keys.

AG2 should own:

- worker agent invocation;
- task lifecycle events through `Agent.task(...)`;
- task observation through `TaskMirror`;
- per-worker channel or subtask streams where practical.

Target worker execution options, in preference order:

1. Use AG2 workflow/consulting channels per worker task when channel lifecycle,
   WAL replay, and task observation are needed.
2. Use `Agent.as_tool()` or AG2 sub-task delegation only when the parent LLM
   should decide delegation dynamically.
3. Direct `Agent.ask(...)` is not a task-batch execution path.

### 6. Lifecycle Hooks

Mozaiks lifecycle hooks should remain Mozaiks-owned only when they enforce
Mozaiks contracts, such as prompt/context injection, artifact persistence,
preload, validation, or Studio events.

Generic turn lifecycle should be expressed through AG2 handler hooks,
middleware, stream observers, task events, or hub listeners.

## Implementation Sequence

### Step 1: Live AG2 Network Smoke

Create a runtime smoke test that uses real AG2 Network objects with fake or
non-network LLM-safe agents:

- `Hub.open(MemoryKnowledgeStore())`
- `LocalLink`
- one `HubClient` per agent or process boundary
- `HubClient.register(..., attach_plugin=True)`
- `workflow` channel with `TransitionGraph.to_dict()`
- a seeded user/workflow message
- `wait_for_channel_event(... EV_CHANNEL_CLOSED ...)`
- WAL replay assertions

Assertions:

- AG2, not Mozaiks, enforces expected speaker order.
- channel state closes from `TerminateTarget`.
- WAL contains each agent turn.
- `WorkflowAdapter` receives context vars.
- a task lifecycle event can be mirrored to the hub when an agent uses
  `agent.task(...)`.

Current checkpoint:

- `tests/test_ag2_network_execution_alignment.py` verifies real AG2
  `Hub`/`HubClient`/`AgentClient`/`WorkflowAdapter` channel execution without
  live LLM calls.
- The smoke registers a human initiator plus two deterministic AG2 agents,
  routes `human -> PlannerAgent -> WorkerAgent -> terminate`, asserts AG2 WAL
  packets, verifies channel context vars, and confirms termination comes from
  `TerminateTarget`.

### Step 2: Introduce `AG2NetworkRunner`

Add a narrow runner behind the existing `OrchestrationPort` boundary.

The runner should accept already-loaded Mozaiks workflow config and agents, not
read workflow files directly. It should return a Mozaiks `RunResult`-compatible
summary plus the AG2 channel id.

Current checkpoint:

- `mozaiksai/core/adapters/ag2_network_runner.py` defines
  `AG2NetworkRunner`, `AG2NetworkRunnerRequest`, and
  `AG2NetworkRunnerResult`.
- The runner accepts already-created AG2 agents plus validated transition
  rules, wraps the workflow graph with a synthetic Mozaiks initiator, opens a
  real AG2 `workflow` channel, waits for `EV_CHANNEL_CLOSED`, and returns
  serializable WAL/context/close metadata.
- `tests/test_ag2_network_execution_alignment.py` covers successful runner
  execution and validation failure for an invalid initial agent.

### Step 3: Move Structured-Output Validation To Observer Path

Create an envelope/stream observer that maps AG2 replies to the existing
structured output validation function.

Do not call this from a custom turn loop. It should subscribe to AG2 execution
events or process WAL entries after each accepted round.

Current checkpoint:

- `mozaiksai/core/workflow/outputs/runtime_validation.py` owns shared reply-body
  extraction and structured-output validation.
- `AG2NetworkRunner` validates AG2 workflow WAL `EV_PACKET` reply bodies against
  the workflow structured-output registry after AG2 channel execution.
- Structured-output contract failures return a failed Mozaiks runner result even
  when AG2 correctly closed the workflow channel; this keeps model execution
  owned by AG2 and canonical artifact contracts owned by Mozaiks.
- `mozaiksai/core/workflow/outputs/runtime_events.py` owns the Mozaiks
  `runtime.agent_output_validated` event emission for validated replies.
- `tests/test_ag2_network_execution_alignment.py` covers successful structured
  output extraction from AG2 WAL and hard failure on invalid agent output.

### Step 4: Align Task Batches With AG2 Network Observation

Wrap each deterministic Mozaiks batch task in an AG2-observable execution
surface.

The Mozaiks scheduler may still decide readiness and dependency order, but
worker execution should produce AG2 channel/WAL history rather than direct
runtime `Agent.ask(...)` calls.

Current checkpoint:

- `mozaiksai/core/adapters/ag2_task_batch_runner.py` executes one deterministic
  task-batch work item as an AG2 Task lifecycle-wrapped worker turn and records
  normalized Task stream evidence.
- `mozaiksai/core/workflow/task_batches.py` still owns task extraction,
  dependency readiness, concurrency, failure policy, output ownership, and
  result merge, but no longer calls worker agents directly.
- Worker task context is passed through the scoped worker turn variables and
  dependencies; this standalone path does not create a per-task AG2 Network
  channel or WAL.
- `run_workflow_orchestration(...)` supports task-batch workflows through
  phased AG2 Network execution: trigger-agent phase, deterministic task
  lifecycle-wrapped worker turns, then downstream continuation phase with batch
  result context.
- `tests/test_ag2_network_execution_alignment.py` verifies planner -> task
  batch -> synthesis execution across AG2 phases.

### Step 5: Use AG2 For Turn Routing

Once the AG2 runner covers workflow execution, remove the local loop and keep
only small helpers that are still Mozaiks-specific:

- reply body extraction;
- structured-output validation;
- context persistence;
- transport projection;
- transition graph compile helpers.

Current checkpoint:

- `run_workflow_orchestration(...)` now calls `AG2NetworkRunner` for workflow
  execution after Mozaiks loads config, context, agents, lifecycle, and
  persistence.
- AG2 WAL packets are projected back into Mozaiks transport and run-message
  persistence after the channel completes.
- The remaining structured-output event helper moved to
  `mozaiksai/core/workflow/outputs/runtime_events.py`.
- Workflows with `task_batches.yaml` execute through phased AG2 Network
  orchestration rather than falling back to a local turn loop.
- Tool mutations to `context_variables` are committed into the outgoing AG2
  workflow `EV_PACKET.context_updates` payload before AG2 folds the transition
  graph. This makes `transition_graph.yaml` expression routes read AG2
  `WorkflowState.context_vars`, not a parallel Mozaiks-only dict.

## AG2 Compatibility Watchpoints

The living upgrade checklist and current divergence log now live in
[AG2 Update Watchpoints](ag2-update-watchpoints.md). Keep this section as the
execution-plan summary; update the watchpoint document whenever AG2 or Mozaiks
runtime behavior changes.

Track these whenever AG2 updates:

| Watchpoint | Why it matters |
| --- | --- |
| Durable `KnowledgeStore` options for Hub WAL | Determines whether Mozaiks can use AG2 WAL as the workflow audit source or must mirror to its own store. |
| Workflow adapter context update API | Determines whether Mozaiks can replace local context dict mutation with AG2 context envelopes. |
| Public default handler hooks | Determines how much custom code is needed for structured-output validation and Studio event projection. |
| Round-end packet transform hook | Mozaiks currently wraps the AG2 default handler narrowly to merge tool context mutations into `EV_PACKET.context_updates`. Replace this wrapper with an upstream hook when AG2 exposes one. |
| TaskMirror lifecycle guarantees | Determines whether Mozaiks task batches can rely on AG2 task audit for worker observability. |
| `TransitionGraph` condition composition | Determines whether `SourceScopedContextEquals` and `SourceScopedToolCalled` can be replaced with native AG2 condition composition. |
| Hub/AgentClient cancellation semantics | Determines whether Mozaiks `cancel()` can stop channels through AG2 instead of cancelling local background tasks. |
| HITL/human client behavior | Determines how user pauses/resumes should map to AG2 human clients instead of Mozaiks-only pause state. |

## Upstream Questions For AG2

Ask or verify upstream before final replacement:

1. What is the intended durable store for Hub WAL in production apps?
2. Is there a supported hook for inspecting or transforming every round-end
   reply before the next transition folds?
3. Should deterministic external schedulers post worker tasks as workflow
   channels, consulting channels, or AG2 `Task` events with custom metadata?
4. Is `ContextEquals` intended to cover only equality, or should richer
   deterministic expressions live in AG2?
5. What is the recommended cancellation/resume model for long-running workflow
   channels?

## Non-Goals

- Do not move Mozaiks artifact schemas into AG2.
- Do not make AG2 responsible for generated app promotion.
- Do not let AG2 task delegation replace deterministic artifact DAG contracts.
- Do not keep two permanent workflow runners.
- Do not add new Mozaiks-owned generic agent scheduling features while the AG2
  Hub/AgentClient path is being adopted.
