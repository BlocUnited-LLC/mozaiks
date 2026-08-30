# AG2 Update Watchpoints

This is the living update log for Mozaiks' AG2 1.0 integration. Use it when
AG2 changes, when Mozaiks adds workflow runtime behavior, or when a Refinement Engine
change depends on agentic execution mechanics.

Keep this file focused on intentional divergences from AG2. Broad architecture
rules belong in [AG2 Ownership Boundary](ag2-ownership-boundary.md). The
current replacement plan lives in
[AG2 Execution Alignment Plan](ag2-execution-alignment-plan.md).

## Current Baseline

Reviewed on August 25, 2026 against:

- installed package: `ag2==1.0.2` from the `ag2` import package
- declared dependencies: `ag2[a2a,openai,tracing]==1.0.2`,
  `ag2[gemini]==1.0.2`, and `ag2[anthropic]==1.0.2`
- AG2 docs:
  - <https://docs.ag2.ai/latest/docs/beta/network/overview/>
  - <https://docs.ag2.ai/latest/docs/beta/network/hub_and_identity/>
  - <https://docs.ag2.ai/latest/docs/beta/network/agent_clients/>
  - <https://docs.ag2.ai/latest/docs/beta/network/adapters_overview/>
  - <https://docs.ag2.ai/latest/docs/beta/network/migration_from_group_chat/>
  - <https://docs.ag2.ai/latest/docs/beta/network/task_observation/>
  - <https://docs.ag2.ai/latest/docs/beta/tasks/>
  - <https://docs.ag2.ai/latest/docs/beta/ag2_compatibility/>
  - <https://docs.ag2.ai/docs/blog/2026/05/14/AG2-Action-Driven-Network/>
  - <https://docs.ag2.ai/docs/blog/2026/05/16/AG2-Network-What-Survives/>
  - <https://docs.ag2.ai/docs/blog/2026/06/16/AG2-Network-Networks-You-Can-Deploy/>
  - <https://docs.ag2.ai/docs/blog/2026/06/17/AG2-Agent-Harness/>

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
| Source-scoped deterministic transition conditions | `mozaiksai/core/adapters/ag2_transition_conditions.py`, `mozaiksai/core/workflow/execution/network_graph.py` | Mozaiks workflow YAML declares `source_agent` per rule, while AG2 `ContextEquals` and `ToolCalled` do not include source scope by themselves. AG2 1.0.2 does not ship an upstream expression evaluator for Mozaiks `${var}` workflow contracts, so Mozaiks keeps a small deterministic evaluator at the adapter boundary. | If AG2 adds native condition composition such as `FromSpeaker AND ContextEquals`, `FromSpeaker AND ToolCalled`, or a native expression evaluator, replace the local adapters with native composition. |
| App-preview/validation sandboxes vs AG2 code execution | `mozaiksai/core/ports/sandbox.py`, `mozaiksai/core/adapters/e2b_sandbox.py`, `mozaiksai/core/adapters/docker_sandbox.py`, `factory_app/workflows/AppGenerator/tools/app_validation.py`, `mozaiksai/core/sandbox/preview_sessions.py` | AG2's `SandboxCodeTool`/`SandboxShellTool` execute agent-written snippets and shell commands (fresh process per call, stdout/stderr, docker/daytona/tenki backends) and expose no long-lived app servers or preview URLs. Mozaiks' `SandboxPort` boots a full generated application (write bundle, install, dev server, preview URL, session lifecycle) — application-runtime behavior Mozaiks owns. Agent-level execution already uses AG2 (`sandbox_shell: true` agents via `ag2.tools.shell.SandboxShellTool`); the control-plane coding worker validates via local subprocess and deliberately excludes an `e2b` strategy label until a sandbox execution path exists. | If AG2 `CodeEnvironment` backends gain long-lived sessions with exposed service ports, preview URLs, and per-session budgets, collapse `SandboxPort` adapters into request/result conversion over `CodeEnvironment` and adopt AG2 backends (daytona/tenki) alongside or instead of e2b. If AG2 ships a sandboxed code-execution tool suitable for the coding worker, wire `validation_strategy: e2b`-class execution through it rather than a Mozaiks-owned runner. |
| One-shot workflow bootstrap transition | `BootstrapInitialDispatch` in `mozaiksai/core/adapters/ag2_transition_conditions.py`, injected by `AG2NetworkRunner._compile_graph_with_initiator(...)` | Mozaiks opens workflow channels from a human initiator and injects a first-turn dispatch to the declared initial agent. This condition is intentionally bootstrap-only and cannot be configured as a general workflow-author transition. | Remove this adapter if AG2 exposes a native workflow-channel startup target that dispatches the initial message without adding a reusable human-speaker transition. |
| Structured-output validation after AG2 packets | `mozaiksai/core/workflow/outputs/runtime_validation.py`, `mozaiksai/core/workflow/outputs/runtime_events.py`, `AG2NetworkRunner._validate_wal_structured_outputs(...)` | AG2 owns model execution; Mozaiks owns canonical app/workflow/module artifact schemas and hard validation. | If AG2 Network supports per-agent `response_schema` on workflow channels, use it for model pressure, but keep Mozaiks validation as the artifact contract authority. |
| Task-batch scheduling and result merge | `mozaiksai/core/workflow/task_batches.py`, `mozaiksai/core/adapters/ag2_task_batch_runner.py` | AG2 `Task` is lifecycle/observation; it does not assign, dependency-sort, enforce owned paths, or merge generated artifact outputs. Mozaiks now wraps each already-authorized worker turn in an AG2 1.0.2 `Task`, subscribes to that task's standalone `MemoryStream`, and records normalized `TaskStarted`, `TaskCompleted`, `TaskFailed`, or `TaskExpired` evidence. `TaskMirror` is not active here because AG2 requires a `HubClient` or `Hub`; no AG2 channel id or durable channel resume is claimed for this standalone path. | If AG2 adds a deterministic task graph/scheduler with dependency and observation semantics, move worker execution and lifecycle there while keeping Mozaiks artifact ownership validation. If task batches move into real Hub/AgentClient worker channels, attach `TaskMirror` and use real channel ids from AG2. |
| Phased task-batch workflow execution | `mozaiksai/core/workflow/orchestration_patterns.py` | A planning phase runs through AG2, Mozaiks executes deterministic task lifecycle-wrapped worker turns, then a continuation phase resumes with merged context. This keeps the DAG deterministic but splits one logical workflow across channels/task streams. | Replace with AG2-native parent/child workflow channels or task lineage when AG2 can preserve parent workflow context, WAL lineage, cancellation, and observation in one execution surface. |
| Approved-generation smoke coordinator uses AG2 task primitive | `scripts/smoke_agentgenerator_live_pack.py` | Live AgentGenerator pack smoke found the single-agent AG2 Network coordinator/metadata path timing out before packet emission, while direct AG2 task calls completed reliably. The smoke keeps the production-critical parallel workflow generation path on the real AG2 task batch runner and keeps this one-shot approved-boundary coordinator outside Mozaiks runtime code. | The AG2 Consulting channel shape (one question, one reply, auto-close) is the likely native primitive for these one-shot coordinator/metadata calls. If Consulting channels gain deterministic packet emission/close behavior, move the smoke calls back through `AG2NetworkRunner` or a Consulting-channel path. |
| Refinement Engine LLM checkpoints | `mozaiksai/control_plane/implementations/*`, `mozaiksai/core/adapters/ag2_agent_runner.py` | The Refinement Engine is deterministic artifact-aware policy; AG2 should only own the LLM call used for classifier/proposer/coding-plan structured output. | Each LLM checkpoint is semantically an AG2 Consulting interaction: one question, one structured reply, hard close. If AG2's Consulting channel shape hardens into a typed one-shot primitive with `response_schema` support, adapt `AG2StructuredAgentRunner` to use it. Do not move artifact routing, promotion, invalidation, or scoped patch policy into AG2. |
| Studio/platform event projection | `_project_ag2_wal_to_mozaiks_transport(...)` in `mozaiksai/core/adapters/ag2_network_runner.py` | The frontend consumes Mozaiks websocket events and app-scoped chat persistence, not raw AG2 envelopes. | Prefer AG2 Hub listeners or channel event subscriptions for live projection when they support app-scoped transport and chat persistence boundaries. |
| Durable human identity reattachment | `_attach_human_client(...)` in `mozaiksai/core/adapters/ag2_network_runner.py` | Chat-scoped `MongoAG2KnowledgeStore` hydration, Agent `HubClient.attach(...)`, and `resume_pending_turns()` now own restart recovery. AG2 1.0.2 does not expose a public human equivalent of `HubClient.attach(...)`, so Mozaiks locally reconnects the hydrated `HumanClient` identity through the same Hub records. UI transcript events are projection input only and never reconstruct Network execution state. | Replace the localized private-API seam as soon as AG2 exposes `HubClient.attach_human(...)` or an equivalent public identity-reconnection API. |

## Too-Much-Ownership Flags

Treat these as cleanup or upstream-collaboration triggers:

- Any new Mozaiks loop that decides the next agent turn without AG2
  `WorkflowAdapter`/`TransitionGraph`.
- Any new Mozaiks wrapper around `Agent.ask(...)` for multi-agent workflow
  execution outside `mozaiksai.core.adapters`.
- Any new custom task observation stream when a real `HubClient` or `Hub` is
  present and `TaskMirror` can provide the lifecycle signal.
- Any new workflow runtime state store that duplicates AG2 channel WAL or Hub
  audit without a tenant/session persistence reason.
- Any Refinement Engine feature that lets an AG2 agent directly promote artifacts,
  mutate routing policy, or choose workspace state without deterministic
  Mozaiks validation.

## Update Procedure

Run this procedure whenever AG2 is upgraded or when workflow runtime code
changes under `mozaiksai/core/workflow`, `mozaiksai/core/adapters`, or
`mozaiksai/control_plane`.

1. Confirm the installed AG2 version:

   ```powershell
   @'
   import ag2
   print(getattr(ag2, "__version__", "unknown"))
   print(ag2.__file__)
   '@ | .\.venv\Scripts\python.exe -
   ```

2. Inspect AG2 1.0 surfaces in the installed package:

   ```powershell
   rg --hidden --no-ignore "class (Hub|HubClient|AgentClient|WorkflowAdapter|TransitionGraph|TaskMirror|Task)|context_vars|EV_PACKET|EV_CHANNEL_CLOSED" .\.venv\Lib\site-packages\ag2\network -n
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

### August 25, 2026

- **AG2 1.0.2 compatibility audit completed**: an isolated prospect
  installation passed dependency checks, imports, signature comparisons, and
  non-network probes for the AG2 surfaces Mozaiks uses. No production or test
  compatibility changes were required.
- **Modernization remains separate**: this upgrade does not adopt AG2 assembly
  policies, evaluation, or additional dependency-injection primitives.
- **Existing fragile seams remain explicit watchpoints**: the wrapped Network
  default handler, channel-state dependency key, event-class serialization,
  event representation parsing, and detailed task lifecycle assumptions remain
  covered by focused tests rather than changed in this upgrade.

### August 23, 2026

- **Sandbox ownership boundary codified**: app-preview/validation sandboxes
  (`SandboxPort` + e2b/docker adapters, preview URLs, app boot) are
  Mozaiks-owned application-runtime behavior outside AG2's snippet-execution
  scope; agent-level execution stays on AG2 `SandboxShellTool` (already used
  by six AppGenerator agents). The coding worker's unimplemented `e2b`
  validation label was removed so build records only claim strategies that
  actually ran. Session identity/metadata now persists (validation results
  carry `sandbox_session_id`/`sandbox_provider`; preview sandboxes are
  created with identity metadata and provider-side kill deadlines), closing
  the orphaned-sandbox billing vector ahead of hosted e2b activation.
- **Upstream questions filed as AG2 discussion #3201**: the declarative-network
  asks (serialized TransitionGraph schema, per-agent `response_schema` on
  workflow channels, composable/serializable transition conditions, a public
  round-end packet hook, a typed one-shot Consulting primitive) are now posted
  at <https://github.com/ag2ai/ag2/discussions/3201>. Check that thread before
  the next alignment pass; fold maintainer answers into the divergence rows
  above.
- **AG2 skills user guide reviewed** (<https://docs.ag2.ai/docs/user-guide/skills/>):
  no impact on current Mozaiks surfaces. Two standing rules from the review:
  - If factory prompt catalogs (capability routing, module archetypes) ever
    move to on-demand loading for token efficiency, AG2 `SkillPlugin`
    progressive disclosure is the sanctioned mechanism — do not build a
    Mozaiks-owned lazy prompt loader.
  - Runtime skill installation (`SkillSearchToolkit.install_skill` against the
    skills registry) must never be wired into factory or generated-app agents:
    it is nondeterministic and a supply-chain vector into the build pipeline.

### August 22, 2026

- **AG2 Harness and deployable-network series reviewed; build repair remains
  Mozaiks-owned deterministic policy**: reviewed the Agent Harness, Networks You
  Can Deploy, and What Survives posts alongside the Action-Driven Network review.
  Conclusions:
  - `MemoryStream`, retry/telemetry middleware, token observers, tools, and
    human-input hooks are useful execution and observability primitives. Mozaiks
    already uses the applicable primitives behind adapter boundaries.
  - AG2 `LoopDetector` observes repeated tool calls; it does not replace the
    AppGenerator acceptance gate. AppGenerator therefore fingerprints normalized
    validation failures and deterministically blocks an unchanged repair result.
  - AG2 compaction, aggregation, and `KnowledgeStore` are conversation-memory
    concerns. App context, validation evidence, artifact lineage, staleness,
    acceptance, and promotion remain typed Mozaiks records rather than agent
    memory.
  - Hub WAL, checkpoint storage, at-least-once delivery, authentication, dynamic
    membership, and federation matter when workflow workers cross process or
    trust boundaries. They are not prerequisites for the local build-repair
    loop. Mozaiks should adopt the native durable channel path when it can
    preserve tenant scope and exact resumability, rather than creating a second
    agent network.
  - None of the four posts changes the ownership boundary: AG2 may execute agent
    turns and preserve channel events; Mozaiks selects the artifact version,
    classifies impact, validates generated files, bounds repair, requires review,
    and authorizes promotion.

- **Action-Driven Network blog post reviewed, layering confirmed**: reviewed
  AG2's Action-Driven Network post
  (<https://docs.ag2.ai/docs/blog/2026/05/14/AG2-Action-Driven-Network/>)
  against the Mozaiks routing layers. Conclusions:
  - `transition_graph.yaml` compiled through `AG2NetworkRunner` is the layer
    aligned with the ADN Workflow shape (`WorkflowAdapter` + `TransitionGraph`).
    Keep it thin and ADN-shaped so it continues to delegate routing to AG2.
  - `factory_app/workflows/extended_orchestration/extension_registry.json`
    (cross-workflow build journeys, human `user_choice_context` screen
    transitions, artifact dependency graph, `affected_declarative_families`)
    sits above what ADN models. ADN channels have no concept of human screen
    steps, artifact lineage, or staleness-driven re-entry. This stays
    Mozaiks-owned; do not model a build journey as one ADN Workflow channel.
  - The Refinement Engine (`mozaiksai/control_plane/`) is deterministic
    artifact policy whose LLM checkpoints map to the ADN Consulting shape
    (1Q1R, auto-close) via `AG2StructuredAgentRunner`. It stays Mozaiks-owned
    per the existing ownership boundary; only the one-shot LLM call belongs to
    AG2.
  - No new divergence introduced. Sharpened the two Consulting-shape
    watchpoint rows and added upstream question 6 on cross-workflow
    sequencing.

### August 14, 2026

- **AG2 1.0.1 task lifecycle evidence adopted for task batches**:
  `AG2TaskBatchRunner` creates an AG2 `Task` for each deterministic
  task-batch work item, subscribes to the task stream, executes the
  preselected worker `Agent.ask(...)`, and records normalized lifecycle
  evidence. Mozaiks still owns task id selection, worker selection,
  dependencies, concurrency, prompts, context, tool profile, owned paths,
  timeout, retries, structured-output validation, output destination, and merge
  behavior.
- **TaskMirror remains deferred for standalone task-batch workers**: AG2 1.0.1
  `TaskMirror` requires a `HubClient` or `Hub`. The current task-batch runner
  does not restructure workers into real Hub clients in this change, so it uses
  authentic standalone Task stream events and leaves `channel_id` unset. This
  path does not claim AG2 channel WAL or durable AG2 task/channel resume.

### July 28, 2026

- **AG2 stable baseline adopted**: Mozaiks now pins `ag2==1.0.0`
  instead of `1.0.0b0`.
- **Multimodal media boundary added**: `mozaiksai.core.media` owns
  provider-neutral media refs, generated media metadata/storage, and AG2 adapter
  helpers. Workflow product logic must use those primitives rather than storing
  generated images in chat text or duplicating provider-specific AG2 setup.
- **OpenAI image generation config boundary**: image-generation agents use
  `OpenAIResponsesConfig` through Mozaiks config conversion because AG2
  `ImageGenerationTool` requires the Responses API. Non-image-generation OpenAI
  agents keep the existing `OpenAIConfig` path.

### July 8, 2026

- **AG2 package namespace migrated**: Mozaiks targeted `ag2==1.0.0b0` and
  imports active beta APIs from `ag2.*`.
- **Mozaiks expression compatibility gap**: AG2 1.0.0b0 did not expose a
  native expression helper for Mozaiks `${context_variable}` workflow
  contracts. Mozaiks keeps a small source-scoped evaluator in
  `ag2_transition_conditions.py` for the declarative `context_expression`
  contract.
- **Retired cache/logger hooks**: AG2 1.0.0b0 no longer exposed the earlier
  cache/logger hook modules; Mozaiks preserves cache seeds for AG2 config
  conversion and removed the file-logger monkeypatch.
- **A2A follow-up**: importing `ag2.a2a.client` currently requires the gRPC
  dependencies from `a2a-sdk[grpc]` in this environment. Validate the new AG2
  A2A remote-agent API before re-enabling that optional workflow surface.

### June 12, 2026

- **Live AgentGenerator pack smoke passed with real AG2/model calls**:
  `scripts/smoke_agentgenerator_live_pack.py --timeout-seconds 900` generated
  two workflow bundles in parallel, confirmed task overlap through
  `AG2TaskBatchRunner`, exported the bundle zip, promoted the generated
  workflows into an isolated active root, and loaded both workflows through
  `UnifiedWorkflowManager`.
- **Coordinator/metadata one-shot calls use AG2 task primitive in the smoke**:
  single-agent AG2 Network channel execution timed out before packet emission
  during the approved-generation coordinator/metadata setup. Direct AG2 task
  calls completed reliably, so the smoke uses the task primitive for those
  one-shot setup calls while keeping the generated workflow workers on the real
  task-batch path. This is a smoke-harness watchpoint, not a new runtime
  orchestration abstraction.

### June 11, 2026 (update 2)

- **Context expression routing used upstream evaluators in 0.13.x**:
  `network_graph.py` compiles deterministic routing into AG2 1.0
  `TransitionCondition` objects registered from `mozaiksai.core.adapters`.
  Simple equality uses AG2 `ContextEquals`; tool routes use AG2 `ToolCalled`;
  composite context routes use the canonical `condition_type: context_expression`
  field and Mozaiks' local `${context_variable}` evaluator. The remaining
  divergence is source-scoping those AG2 conditions to Mozaiks' declarative
  `source_agent` field and bridging `WorkflowState.context_vars` into the local
  expression evaluator.
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
- Keep the Refinement Engine as deterministic Mozaiks policy. Use AG2 only for
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
6. Does the Action-Driven Network roadmap intend to model cross-workflow
   sequencing with human-in-the-loop steps between channels (for example a
   channel-of-channels or journey primitive), or is the intended pattern
   application-owned sequencing that opens one Workflow channel per step, as
   Mozaiks does today through `extension_registry.json` workflow sequences?
