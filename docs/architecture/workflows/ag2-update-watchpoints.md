# AG2 Update Watchpoints

This is the living update log for Mozaiks' AG2 beta integration. Use it when
AG2 changes, when Mozaiks adds workflow runtime behavior, or when a control-plane
change depends on agentic execution mechanics.

Keep this file focused on intentional divergences from AG2. Broad architecture
rules belong in [AG2 Ownership Boundary](ag2-ownership-boundary.md). The
current replacement plan lives in
[AG2 Execution Alignment Plan](ag2-execution-alignment-plan.md).

## Current Baseline

Reviewed on June 11, 2026 against:

- installed package: `autogen==0.13.2` from `.venv/Lib/site-packages/autogen`
- declared dependency floor: `ag2[a2a,openai,lmm]>=0.13.2`
- AG2 docs:
  - <https://docs.ag2.ai/latest/docs/beta/network/overview/>
  - <https://docs.ag2.ai/latest/docs/beta/network/hub_and_identity/>
  - <https://docs.ag2.ai/latest/docs/beta/network/agent_clients/>
  - <https://docs.ag2.ai/latest/docs/beta/network/adapters_overview/>
  - <https://docs.ag2.ai/latest/docs/beta/network/migration_from_group_chat/>
  - <https://docs.ag2.ai/latest/docs/beta/network/task_observation/>
  - <https://docs.ag2.ai/latest/docs/beta/tasks/>
  - <https://docs.ag2.ai/latest/docs/beta/ag2_compatibility/>

AG2 currently owns the primitives Mozaiks should not recreate:

- `Hub`, `HubClient`, `AgentClient`, channel lifecycle, WAL, and audit state
- `WorkflowAdapter`, `TransitionGraph`, `AgentTarget`, `TerminateTarget`,
  `FromSpeaker`, `ToolCalled`, and `ContextEquals`
- default agent notify handling, including WAL projection into agent context,
  `Agent.ask(...)`, and round-end packet emission
- `Task`, task lifecycle events, `TaskMirror`, and capability observation

Mozaiks still owns deterministic product contracts around those primitives:

- workflow YAML validation and compilation
- tenant/session/app context boundaries
- structured-output validation for generated artifact contracts
- Studio/platform transport projection and persistence
- artifact lineage, validation, promotion, and scoped refinement policy
- task-batch DAG policy and result ownership checks

## Intentional Divergences

| Divergence | Current files | Why Mozaiks owns it now | AG2 watchpoint |
| --- | --- | --- | --- |
| AG2 workflow runner boundary | `mozaiksai/core/adapters/ag2_network_runner.py`, `mozaiksai/core/adapters/ag2_orchestration.py` | Mozaiks must adapt workflow YAML, app/session IDs, structured-output registry, and Mozaiks `RunResult` semantics to AG2 Hub channels. | If AG2 adds a stable high-level workflow runner over `Hub`/`AgentClient`, shrink `AG2NetworkRunner` to request/result conversion only. |
| Turn failure result mapping | `mozaiksai/core/adapters/ag2_network_runner.py` | AG2 reports agent turn crashes through `HubListener.on_turn_failed` while leaving the channel alive. Mozaiks maps that listener event to a failed `RunResult` so runtime callers do not wait for channel timeout. | If AG2 Workflow channels gain first-class failure policy or auto-close behavior for turn crashes, replace the local listener with the native channel result. |
| Round-end context mutation bridge | `_install_context_update_handler` in `mozaiksai/core/adapters/ag2_network_runner.py` | Mozaiks tools mutate `ContextVariablesBridge`, while AG2 workflow routing reads packet `context_updates` before `WorkflowAdapter.fold(...)` selects the next speaker. The current bridge wraps AG2's default handler to merge those updates into `EV_PACKET`. | Replace with an AG2-supported round-end packet transform hook, default-handler middleware, or native context update helper when available. This is the most fragile divergence. |
| Source-scoped deterministic transition conditions | `mozaiksai/core/adapters/ag2_transition_conditions.py`, `mozaiksai/core/workflow/execution/network_graph.py` | Mozaiks workflow YAML declares `source_agent` per rule, while AG2 `ContextEquals`, `ToolCalled`, and classic `ContextExpression` do not include source scope by themselves. Mozaiks registers tiny source-scoped beta `TransitionCondition` adapters over those AG2-owned evaluators. | If AG2 adds native condition composition such as `FromSpeaker AND ContextEquals`, `FromSpeaker AND ToolCalled`, or beta-native `ContextExpression`, replace the local adapters with native composition. |
| Structured-output validation after AG2 packets | `mozaiksai/core/workflow/outputs/runtime_validation.py`, `mozaiksai/core/workflow/outputs/runtime_events.py`, `AG2NetworkRunner._validate_wal_structured_outputs(...)` | AG2 owns model execution; Mozaiks owns canonical app/workflow/module artifact schemas and hard validation. | If AG2 Network supports per-agent `response_schema` on workflow channels, use it for model pressure, but keep Mozaiks validation as the artifact contract authority. |
| Task-batch scheduling and result merge | `mozaiksai/core/workflow/task_batches.py`, `mozaiksai/core/adapters/ag2_task_batch_runner.py` | AG2 `Task` is lifecycle/observation; it does not assign, dependency-sort, enforce owned paths, or merge generated artifact outputs. | If AG2 adds a deterministic task graph/scheduler with dependency and observation semantics, move worker execution and lifecycle there while keeping Mozaiks artifact ownership validation. |
| Phased task-batch workflow execution | `mozaiksai/core/workflow/orchestration_patterns.py` | A planning phase runs through AG2, Mozaiks executes deterministic task channels, then a continuation phase resumes with merged context. This keeps the DAG deterministic but splits one logical workflow across channels. | Replace with AG2-native parent/child workflow channels or task lineage when AG2 can preserve parent workflow context, WAL lineage, cancellation, and observation in one execution surface. |
| Control-plane LLM checkpoints | `mozaiksai/control_plane/implementations/*`, `mozaiksai/core/adapters/ag2_agent_runner.py` | The control plane is deterministic artifact-aware policy; AG2 should only own the LLM call used for classifier/proposer/coding-plan structured output. | If AG2 Harness gains a typed one-shot agent primitive that better fits this use, adapt `AG2StructuredAgentRunner`. Do not move artifact routing, promotion, invalidation, or scoped patch policy into AG2. |
| Studio/platform event projection | `_project_ag2_wal_to_mozaiks_transport(...)` in `mozaiksai/core/adapters/ag2_network_runner.py` | The frontend consumes Mozaiks websocket events and app-scoped chat persistence, not raw AG2 envelopes. | Prefer AG2 Hub listeners or channel event subscriptions for live projection when they support app-scoped transport and chat persistence boundaries. |
| Cancel/resume boundary | `AG2OrchestrationAdapter.cancel(...)`, `AG2OrchestrationAdapter.resume(...)` | Current cancel/resume is transport/session managed. Resume re-enters Mozaiks orchestration with persisted context rather than hydrating and continuing an AG2 channel. | Move toward AG2 channel cancellation/resume if AG2 exposes durable channel continuation with tenant-safe store boundaries. |

## Too-Much-Ownership Flags

Treat these as cleanup or upstream-collaboration triggers:

- Any new Mozaiks loop that decides the next agent turn without AG2
  `WorkflowAdapter`/`TransitionGraph`.
- Any new Mozaiks wrapper around `Agent.ask(...)` for multi-agent workflow
  execution outside `mozaiksai.core.adapters`.
- Any new custom task observation stream when `TaskMirror` can provide the
  lifecycle signal.
- Any new workflow runtime state store that duplicates AG2 channel WAL or Hub
  audit without a tenant/session persistence reason.
- Any control-plane feature that lets an AG2 agent directly promote artifacts,
  mutate routing policy, or choose workspace state without deterministic
  Mozaiks validation.

## Update Procedure

Run this procedure whenever AG2 is upgraded or when workflow runtime code
changes under `mozaiksai/core/workflow`, `mozaiksai/core/adapters`, or
`mozaiksai/control_plane`.

1. Confirm the installed AG2 version:

   ```powershell
   @'
   import autogen
   print(getattr(autogen, "__version__", "unknown"))
   print(autogen.__file__)
   '@ | .\.venv\Scripts\python.exe -
   ```

2. Inspect AG2 beta surfaces in the installed package:

   ```powershell
   rg --hidden --no-ignore "class (Hub|HubClient|AgentClient|WorkflowAdapter|TransitionGraph|TaskMirror|Task)|context_vars|EV_PACKET|EV_CHANNEL_CLOSED" .\.venv\Lib\site-packages\autogen\beta\network -n
   ```

3. Re-check official AG2 docs for Network, Tasks, Task Observation, and
   Adapter behavior.

4. For each divergence above, decide one of:
   - `retire`: AG2 now owns the capability directly.
   - `shrink`: Mozaiks still needs an adapter, but the adapter can get thinner.
   - `keep`: the behavior is a Mozaiks product/runtime contract.
   - `upstream`: the behavior is generic agent orchestration and should be
     raised with AG2.

5. Run targeted tests:

   ```powershell
   pytest tests/test_ag2_network_execution_alignment.py tests/test_workflow_network_graph.py tests/test_ag2_agent_runner.py
   ```

6. Update this file and any affected architecture docs in the same change.

## Current Decision Log

### June 11, 2026 (update 2)

- **Context expression routing uses AG2 evaluators**:
  `network_graph.py` compiles deterministic routing into AG2 beta
  `TransitionCondition` objects registered from `mozaiksai.core.adapters`.
  Simple equality uses AG2 `ContextEquals`; tool routes use AG2 `ToolCalled`;
  composite context routes use the canonical `condition_type: context_expression`
  field and AG2's `ContextExpression` parser/evaluator. The remaining
  divergence is source-scoping those AG2 conditions to Mozaiks' declarative
  `source_agent` field and bridging beta `WorkflowState.context_vars` into the
  AG2 expression evaluator.
- **Stream projection boundary**: The active Studio/platform event projection
  surface is `_project_ag2_wal_to_mozaiks_transport(...)` in
  `ag2_network_runner.py`.

### June 11, 2026

- Keep `AG2NetworkRunner`, `AG2TaskBatchRunner`, and
  `AG2StructuredAgentRunner` as narrow adapters under `mozaiksai.core.adapters`.
- `AG2NetworkRunner` now listens for AG2 `on_turn_failed` events and returns a
  failed Mozaiks runner result promptly, while keeping the failure mapping
  inside the adapter boundary.
- Keep task-batch DAG scheduling in Mozaiks because AG2 `Task` is lifecycle and
  observation, not deterministic assignment or dependency scheduling.
- Keep the control plane as deterministic Mozaiks policy. Use AG2 only for
  typed LLM checkpoints behind `AG2StructuredAgentRunner`.
- Treat `_install_context_update_handler` as the highest-priority AG2
  compatibility watchpoint. It is acceptable only because it is isolated to the
  AG2 adapter boundary.
- Do not create a separate root `AGENT_UPDATE.md`; this document is the
  canonical update tracker and is included in the architecture docs navigation.

## Upstream Questions

Use these with the AG2 team when planning the next alignment pass:

1. Is a public default-handler hook planned for inspecting or transforming the
   round-end `EV_PACKET` before `WorkflowAdapter.fold(...)`?
2. What is the intended durable production `KnowledgeStore` path for tenant
   scoped Hub WAL and audit data?
3. Should deterministic external task DAGs be represented as workflow channels,
   consulting channels, task envelopes, or a future AG2 task graph primitive?
4. Will AG2 expose native condition composition for deterministic transitions,
   especially `FromSpeaker AND ContextEquals` and `FromSpeaker AND ToolCalled`,
   or should applications keep registering source-scoped custom conditions?
5. What is the recommended durable cancellation/resume model for long-running
   workflow channels with HITL pauses?
