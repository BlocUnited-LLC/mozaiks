# AG2 Update Watchpoints

This is the active maintenance ledger for Mozaiks' AG2 integration. Use it for
every AG2 version update and before adding agentic runtime behavior. Its purpose
is to identify where Mozaiks can delete or shrink code as AG2 gains native
capabilities; it is not a second framework or package manager.

Markdown owns rationale, review procedure, and history. The compact
[machine-readable index](ag2-watchpoints.yaml) exists only for baseline, ID,
status, private-access, and dependency-update checks. Broad ownership rules
belong in [AG2 Ownership Boundary](ag2-ownership-boundary.md); the current
replacement plan lives in
[AG2 Execution Alignment Plan](ag2-execution-alignment-plan.md).

## Current Baseline

Source-reviewed on November 6, 2026 against:

- installed package: `ag2==1.3.0` from the `ag2` import package
- declared dependencies: `ag2[a2a,openai,tracing]==1.3.0`,
  `ag2[gemini]==1.3.0`, `ag2[anthropic]==1.3.0`, and `ag2[acp]==1.3.0`
- AG2 docs:
  - <https://docs.ag2.ai/docs/user-guide/network/overview/>
  - <https://docs.ag2.ai/docs/user-guide/network/hub_and_identity/>
  - <https://docs.ag2.ai/docs/user-guide/network/agent_clients/>
  - <https://docs.ag2.ai/docs/user-guide/network/adapters_overview/>
  - <https://docs.ag2.ai/docs/user-guide/network/migration_from_group_chat/>
  - <https://docs.ag2.ai/docs/user-guide/network/task_observation/>
  - <https://docs.ag2.ai/docs/user-guide/tasks/>
  - <https://docs.ag2.ai/docs/blog/2026/05/14/AG2-Action-Driven-Network/>
  - <https://docs.ag2.ai/docs/blog/2026/05/16/AG2-Network-What-Survives/>
  - <https://docs.ag2.ai/docs/blog/2026/06/16/AG2-Network-Networks-You-Can-Deploy/>
  - <https://docs.ag2.ai/docs/blog/2026/06/17/AG2-Agent-Harness/>

Every current watchpoint and approved private/internal access was last verified
against AG2 1.3.0. A verification stamp means the upstream trigger was rechecked
against that version's source. It does not mean a test reached the Mozaiks
surface: the "Required verification" column names only tests that do, and says
so plainly where none does. The exact dependency pin remains authoritative in
`pyproject.toml`, `requirements.txt`, and
`tests/test_ag2_dependency_contract.py`.

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

## Finite Status and Upgrade Outcomes

Watchpoint status is one of:

- `ACTIVE`: a current Mozaiks seam or workaround must be rechecked on upgrades.
- `WATCH`: AG2 may eventually shrink a valid Mozaiks-owned boundary.
- `RESOLVED_UPSTREAM`: AG2 supplied the missing primitive; migration or deletion
  is ready.
- `RESOLVED_IN_MOZAIKS`: Mozaiks removed or replaced the affected mechanism.
- `DEFERRED`: the trigger is known, but adoption belongs to a named future lane.
- `RETIRED`: the watchpoint no longer describes a current supported surface.

Classify each AG2 update with exactly one primary outcome:

- `NO_IMPACT`: no watched usage changed.
- `TEST_ONLY`: behavior remains compatible but needs refreshed proof.
- `ADOPT`: use a new AG2 primitive without a contract migration.
- `MIGRATE`: move an existing integration to a changed AG2 contract.
- `DELETE_WORKAROUND`: an AG2-native primitive makes local machinery obsolete.
- `BREAKING`: the pinned update invalidates a required Mozaiks contract.
- `DEFER`: a valid migration is intentionally assigned to a later lane.
- `WATCH`: upstream movement is relevant but not yet usable.

## Active Watchpoint Ledger

For `AG2-WP-001` and `AG2-WP-002`, also verify the workflow tool boundary and
continuation deadlines. The runner uses `attach_plugin=False` without replacing
AG2's handler; only declared tools and adapter-owned protocol tools reach the
model. A deadline reached before polling or inside `wait_for_channel_event`
must return `FAILED` and close the live run, never `PAUSED` or an uncaught
`TimeoutError`. The HTTP-provider fixture in
`test_ag2_network_tool_routing.py` and both deadline cases in
`test_ag2_network_execution_alignment.py` cover these contracts.

The YAML index repeats only the fields used by automation. The reason and test
intent below remain authoritative for maintainers.

| ID | AG2 surface | Mozaiks surface and current reason | Trigger and deletion/migration condition | Status | Last verified | Required verification |
| --- | --- | --- | --- | --- | --- | --- |
| `AG2-WP-001` | `Hub`/`AgentClient` workflow execution | `AG2NetworkRunner` adapts workflow YAML, app/session identity, artifact validation, and `RunResult` semantics. | When AG2 ships a stable high-level workflow runner, shrink this to request/result conversion. | `ACTIVE` | 1.3.0 | `test_ag2_network_execution_alignment.py` |
| `AG2-WP-002` | Workflow turn-failure policy | The runner maps `HubListener.on_turn_failed` to a failed `RunResult` because the channel otherwise remains alive. | Delete the listener mapping when AG2 exposes a failed channel result or native close policy. | `ACTIVE` | 1.3.0 | `test_ag2_network_execution_alignment.py` |
| `AG2-WP-003` | Round-end packet context updates | `_install_context_update_handler` wraps AG2's default handler so tool updates reach `EV_PACKET` before `WorkflowAdapter.fold(...)`. | Delete the wrapper when AG2 exposes a public packet transform or context-update hook. | `ACTIVE` | 1.3.0 | `test_ag2_network_execution_alignment.py`, `test_ag2_network_tool_routing.py` |
| `AG2-WP-004` | Source-scoped transition composition | Local conditions preserve YAML `source_agent` semantics; `SourceScopedToolCalled` remains a native `ToolCalled` subtype for packet recognition. | Replace them when `FromSpeaker` composes natively with context, tool, or expression conditions and native packet construction recognizes that composition. | `ACTIVE` | 1.3.0 | `test_workflow_network_graph.py`, `test_ag2_network_tool_routing.py` |
| `AG2-WP-005` | Long-lived code environments | `SandboxPort` owns generated-app boot, preview URLs, session lifecycle, and budgets; AG2 tools currently execute snippets/processes. | Re-evaluate as a thin AG2 binding if `CodeEnvironment` gains long-lived servers and exposed ports. | `WATCH` | 1.3.0 | `test_sandbox_boundary_and_persistence.py`, `test_sandbox_shell_contract.py` |
| `AG2-WP-006` | Workflow startup target | `BootstrapInitialDispatch` performs one initial human-to-agent dispatch without creating a reusable author transition. | Delete it when AG2 channels accept a native initial target. | `ACTIVE` | 1.3.0 | `test_ag2_network_execution_alignment.py` |
| `AG2-WP-007` | Channel-turn schema correction | The factory gives each agent its provider `response_schema`, and AG2's channel turn uses it (the notify handler asks without one, so `Agent` falls back to its own). AG2 then packs the raw `reply.body`, so `AgentReply.content()` validation and correction never run on channel turns; `_validate_wal_structured_outputs` validates each packet and fails the run closed. | When channel turns validate and correct against the agent's schema before packing, rely on AG2 for correction; keep Mozaiks packet validation. | `WATCH` | 1.3.0 | `test_structured_output_fail_closed.py`, `test_ag2_network_tool_routing.py` (the two channel-turn response-schema tests) |
| `AG2-WP-008` | Deterministic task graph | `AG2TaskBatchRunner` wraps pre-authorized turns in AG2 `Task`; Mozaiks still dependency-sorts, scopes paths, and merges artifacts. | Move lifecycle execution to AG2 when it supplies deterministic scheduling and lineage; keep product ownership checks. | `DEFERRED` | 1.3.0 | `test_task_batch_contracts.py`, `test_runtime_task_batch_smoke.py` |
| `AG2-WP-009` | Parent/child workflow lineage | Parent planning and continuation share one AG2 channel; task workers still use standalone AG2 Task lifecycle streams. | Replace standalone task observation when AG2 supplies parent/child context, WAL lineage, cancellation, and observation. | `DEFERRED` | 1.3.0 | `test_runtime_task_batch_smoke.py`, `test_ag2_network_execution_alignment.py` |
| `AG2-WP-010` | Typed one-shot Consulting | The approved-generation smoke uses a direct AG2 task call because the single-agent Network coordinator did not close deterministically. | Move to Consulting when typed response, packet emission, and hard-close behavior are stable. | `DEFERRED` | 1.3.0 | None offline. Only the paid live `test_live_agentgenerator_pack_smoke` reaches the direct calls; CI skips it, and it was not run for 1.3.0. The stamp is a source review of Consulting only. |
| `AG2-WP-011` | Typed one-shot Consulting | `AG2StructuredAgentRunner` performs refinement LLM checkpoints while Mozaiks retains artifact policy. | Adopt Consulting when it supports a typed one-question/one-response contract. | `WATCH` | 1.3.0 | `test_ag2_agent_runner.py` |
| `AG2-WP-012` | App-scoped channel events | Mozaiks projects AG2 WAL events into app-scoped websocket and chat persistence contracts. | Shrink WAL polling when native subscriptions preserve those product boundaries. | `WATCH` | 1.3.0 | `test_ag2_network_execution_alignment.py` |
| `AG2-WP-013` | Durable human attachment | `_attach_human_client` reconnects hydrated human identity because AG2 lacks public `HubClient.attach_human(...)`. | Delete the private fallback when AG2 exposes public human reattachment. | `ACTIVE` | 1.3.0 | `test_ag2_network_execution_alignment.py` |
| `AG2-WP-016` | Close decided by an accepted packet | `_accepted_packet_closes_channel` calls the channel adapter's `on_accepted` a second time, outside the hub's accept, for every packet that hands the turn to the user. AG2 dispatches a packet before it applies the close its accept decided. | Delete the second call when AG2 reports that close before dispatch, or settles it some other public way. | `ACTIVE` | 1.3.0 | `test_ag2_on_accepted_recall.py` |

For `AG2-WP-003`, also recheck explicit broadcast audiences for declared
self-edges. AG2 1.3.0 `Hub._dispatch` excludes the sender when `audience=None`,
while the default handler can process explicit self-delivery through `can_send`.
The existing packet adapter includes all participants only for agents with a
declared self-edge. `test_declared_self_transition_runs_again_and_then_terminates`
reproduces the timeout without that adaptation and verifies two real AG2 turns
with it. Upstream automatic self-edge notification should remove this adaptation.

For `AG2-WP-003`, preserve the trusted callable invocation and retained mutation
attribution described in [Declarative Config to AG2 Mapping](declarative-ag2-mapping.md#context_variablesyaml)
when replacing the packet hook. Ordinary packet updates must not acquire
deterministic-tool authority merely because a variable permits that writer.

For `AG2-WP-004`, AG2 1.3.0 recognizes static routing tools through a top-level
`isinstance(condition, ToolCalled)` check in its packet builder. A standalone
condition that delegates only `evaluate()` is insufficient. The registered
subclass preserves source checking at native graph selection and survives
`TransitionGraph.to_dict()` / `loads()`. On upgrades, verify actual callable
execution through native tool events, empty-text turns, wrong-source same-name
calls, graph rehydration, deterministic precedence, and HITL pause/resume.
Mozaiks does not replace AG2's tool-event interpretation or packet builder.

For `AG2-WP-016`, the second `on_accepted` call decides every pause, not only
a rare close. Calling it again is safe only because `on_accepted` has no side
effects. That is how AG2 1.3.0 implements it, not something its
`ChannelAdapter` protocol promises. The hub accepts a packet, dispatches it, runs its
`on_envelope_posted` listeners, and only then applies the close. Ordinary runs
therefore settle through the `is_terminal()` early return, and no test reached
the outcome where the second call itself reports the close.
`test_ag2_on_accepted_recall.py` holds `on_envelope_posted` open until the
runner has decided, which forces that outcome through both the initial run and
a continuation. It also checks that the call leaves the adapter state unchanged
on every pause. On upgrades, recheck the accept order (fold, `on_accepted`,
dispatch, listeners, transition) and that `on_accepted` stays side-effect free.

AG2 1.3.0 materially advances `AG2-WP-009`: native in-process delegations now
inherit explicitly scoped variables, forward descendant events and usage, and
propagate cancellation through the invocation tree. These are supported AG2
mechanisms, not reasons for a parallel Mozaiks observer or delegation engine.
The remaining deferred work is durable parent/child workflow-channel lineage
and its integration with authorized task-batch continuation. The network channel
and workflow adapter contracts are unchanged; live invocation handles do not
establish durable replay or grant Mozaiks module authority.

## Private and Internal API Register

Only verified current reliance belongs here. New reliance requires an explicit
register entry, a protecting test, and an upstream replacement trigger.

| ID | AG2 symbol/surface | Mozaiks caller | Why the public API is insufficient | Risk | Protecting test | Upstream replacement trigger | Last verified |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `AG2-PRI-001` | `HubClient._ensure_connected_async`, `HubClient._cache_passport`, `HubClient._clients` | `_attach_human_client` in `ag2_network_runner.py` | `HubClient.attach(...)` supports agents, but 1.3.0 has no public human-identity reattachment API. | `HIGH` | `test_ag2_network_runner_hydrates_and_continues_same_channel_after_restart` | Public `attach_human(...)` or equivalent durable identity reconnection. | 1.3.0 |
| `AG2-PRI-002` | `ag2.network.client.handlers.default_handler` | `_install_context_update_handler` in `ag2_network_runner.py` | There is no public round-end packet transform before workflow routing folds context updates. | `HIGH` | `test_ag2_network_execution_alignment.py`, `test_ag2_network_tool_routing.py` | Public packet-transform or context-update hook. | 1.3.0 |
| `AG2-PRI-003` | `ag2.network.policies.CHANNEL_STATE_DEP` | `AG2TaskBatchRunner` | Standalone `Agent.ask(...)` does not expose a typed public injection surface for the channel-state context expected by the worker. | `MEDIUM` | `test_task_batch_contracts.py` | Public typed task/ask context injection. | 1.3.0 |

## Supersession and Deletion Queue

Every AG2 update must answer: **Did this release make any Mozaiks-owned runtime
mechanism unnecessary?** A `RESOLVED_UPSTREAM` trigger creates deletion work;
it does not justify retaining both paths.

| Watchpoint | Mozaiks mechanism | Why it exists / AG2 gap | Watched primitive and deletion trigger | Owning future lane |
| --- | --- | --- | --- | --- |
| `AG2-WP-001` | `AG2NetworkRunner` orchestration surface | Mozaiks currently compiles product contracts into lower-level Hub operations. | Stable high-level workflow runner; shrink to semantic request/result binding. | AG2 alignment |
| `AG2-WP-002` | Turn-failure listener mapping | Workflow turn crashes do not produce a terminal channel result. | Native failure/close policy; delete listener mapping. | AG2 compatibility |
| `AG2-WP-003` | Default-handler context bridge | No public pre-fold packet-update hook exists. | Public packet transform; delete handler wrapping. | AG2 alignment |
| `AG2-WP-006` | `BootstrapInitialDispatch` | Workflow channels have no declared startup target. | Native initial target; delete bootstrap condition. | AG2 alignment |
| `AG2-WP-008` / `AG2-WP-009` | Standalone task-batch worker turns | Parent transitions remain in AG2; worker dependency contracts and cross-channel lineage still need a narrow Mozaiks adapter. | Native scheduling plus parent/child lineage; migrate workers and delete standalone lifecycle plumbing. | Slice 5B alignment |
| `AG2-WP-010` / `AG2-WP-011` | Direct one-shot agent runners | Consulting lacks the verified typed response and close contract needed here. | Stable typed Consulting primitive; replace direct one-shot calls. | Slice 5B / control-plane alignment |
| `AG2-WP-012` | WAL polling for product event projection | Native subscriptions do not carry Mozaiks app/transport persistence scope. | App-scoped listener contract; shrink to event conversion. | Runtime alignment |
| `AG2-WP-013` | Private human-client reattachment fallback | No public durable human attach API exists. | Public `attach_human(...)`; delete all three private-member accesses together. | AG2 compatibility |
| `AG2-WP-016` | Second `on_accepted` call for user-bound packets | The hub dispatches a packet before applying the close its accept decided. | AG2 reports that close before dispatch; delete the second call. | AG2 compatibility |

## Auxiliary Usage Provenance

`AG2-WP-014` (`WATCH`, source-reverified against installed `ag2==1.3.0` on
November 6, 2026): `ag2/agent.py` implements schema correction in
`AgentReply.content` via `AgentReply.ask`. Per-ask middleware is not
inherited, but agent-level middleware is reapplied. The auxiliary runner
therefore registers existing Mozaiks usage middleware and native AG2
`RetryMiddleware` on the agent. Mozaiks does not implement a correction loop.

The evidence is the real AG2/OpenAI adapter exercised by
`tests/test_auxiliary_usage_accounting.py` with HTTP MockTransport, together
with `tests/test_ag2_usage_middleware.py` and
`tests/test_ag2_model_call_limits.py`. Source baseline for this bounded
Mozaiks change: `d5e7e13fab23449fa9399db76e6f8a62d9ec101f`.
Recheck native correction inheritance, guard ordering, provider controls,
receipt count, and collector/debit identity on upgrades.

`ag2/events/types.py` permits absent usage fields; `ModelResponse` is not a
guaranteed provider-attempt identity. Middleware sees returned responses, not
SDK-hidden attempts or exhausted provider exceptions. The September 1 history
below describes an earlier observer integration, not current complete runtime
coverage. Current ownership is the existing usage middleware and collector.
Do not add a second TokenMonitor/UsageEvent collector that duplicates receipts.
If AG2 supplies complete attempt provenance, require a coverage and
deduplication proof before replacing this response-only projection.

AG2 1.1.0 changed Anthropic usage normalization: a cache count the provider
measured as zero is now `0` instead of `None`, and `thinking_tokens` is filled
from `output_tokens_details` as a part of `output_tokens`, not an addition to
it. The usage middleware reads only prompt, completion, total, and cache-read
counts and treats a missing and a zero cache read alike, so receipts are
unchanged; it does not read `thinking_tokens`, so reasoning is not counted
twice. The zero-versus-absent behavior is pinned by
`test_ag2_usage_middleware_receipt_for_ag2_anthropic_usage_shape`, which gives
the middleware the `Usage` that mapper returns, with the cache read both absent
and measured as zero.

In 1.1.2, `AgentReply.content()` correction and the `Usage` type are unchanged.
`Agent` now applies a continuation's dependencies, variables, and prompt only
after taking the stream's turn lock, and accepts per-call plugins; agent-level
middleware is still applied on every turn, correction turns included.
`ModelResponse.response_id` (new in 1.1.0) identifies a returned response, not
each provider attempt, so it does not meet the attempt-provenance condition
above.

In 1.2.2, native correction still reapplies agent-level middleware. Anthropic
now aggregates usage across its internal `pause_turn` continuations before
returning the response. Gemini includes thinking tokens in completion tokens
and tool-use prompt tokens in prompt tokens; xAI includes reasoning tokens in
completion tokens. OpenAI fills `cache_creation_input_tokens` from the SDK's
cache-write count. Mozaiks consumes the normalized prompt/completion totals
without adding reasoning again. These corrections improve returned-response
accounting, but do not expose every provider attempt or exhausted exception;
the attempt-provenance trigger remains open.

`test_native_provider_usage_keeps_thinking_and_cache_writes_distinct` feeds
native OpenAI Chat, OpenAI Responses, and Gemini mapper output through the
usage middleware. `test_native_anthropic_continuation_emits_one_aggregated_receipt`
uses the native Anthropic client with an HTTP mock to check that two requests
produce one receipt containing both requests' usage. Gemini and Anthropic
checks require their optional SDK extras and skip when those are absent; the
1.2.2 upgrade environment installs both. The xAI change remains source-reviewed
because Mozaiks does not configure that provider.

In 1.3.0, native delegation forwards original `UsageEvent` publications while
work runs, preserving source identity and delegation provenance. It no longer
emits a parent `kind="subtask"` rollup. `UsageReport` and the native token monitor
deduplicate source event IDs; terminal task/reply usage is a cumulative snapshot,
not another additive receipt. `test_ag2_descendant_usage_contract.py` exercises
the Mozaiks watchdog on a real nested fake-backed delegation and a failed leaf.
The existing response middleware remains the single Mozaiks receipt path;
native observation is not silently added as a second collector. Agent-level
middleware still applies on schema correction, so the original correction,
limits and provider-accounting regressions remain required.

## Workflow Provider History

`AG2-WP-015` (`ACTIVE`, source-reverified November 6, 2026): native workflow views in the
installed AG2 1.3.0 pin project an agent's own earlier packets as bare `ModelMessage` events.
The OpenAI Chat, OpenAI Responses, and Anthropic mappers consume assistant
history through `ModelResponse`; bare messages are omitted. A real discovery
run consequently repeated its opening after human confirmation. An isolated
AG2 1.1.1 wheel reproduced the same event-to-provider conversion gap. On 1.1.2,
real Agent/Hub continuation and disk reopen tests pass with the adapter, while
the strict provider-neutral native-history retirement gate still fails as
expected. The dependency upgrade alone does not close this watchpoint.

AG2 1.2.1 broadened channel rendering and turn input from text to typed input
parts. In 1.2.2, native views still project the agent's own text as bare
`ModelMessage`; the local wrapper preserves the new parts and remains needed.

`ag2_workflow_view.py` subclasses the existing `WorkflowAdapter` and wraps its
native view through public `Hub.register_adapter`. It converts only projected
bare `ModelMessage` events to `ModelResponse`, preserving message metadata.
AG2 continues to own WAL, visibility, peer labels, bounded history, turn
selection, and channel lifecycle. Existing responses and tool events remain
unchanged; the wrapper adds no usage or tool-execution events.

`tests/test_ag2_workflow_provider_history.py` captures actual provider mapper
payloads through real Agent/Hub execution, human continuation, and disk-backed
reopen without paid model calls. It also covers self-edges and privacy/window
preservation, including peer messages retaining their user role. The strict
expected-failure native projection test checks the provider-neutral event shape:
own assistant messages must be preserved as `ModelResponse`, with no bare
`ModelMessage` left for any provider mapper to drop. It is the
retirement signal: when they unexpectedly pass on an AG2 upgrade, remove the
local adapter and registration, remove those expected-failure markers, and
keep continuation/reopen tests passing on the native adapter. No dependency
baseline is changed by this correction.

## AG2 Ownership Guard

Before adding any Mozaiks abstraction involving agents, `Task`, Network,
Skills, middleware, context assembly, history/views, channels/messages,
retries, HITL, or runtime identity, inspect the currently pinned AG2 version.
If AG2 owns the runtime concern, use the AG2 primitive directly or add only the
thinnest Mozaiks semantic binding required for product, tenant, artifact, or
validation contracts.

Do not add a Mozaiks turn-selection loop, task observation stream, agent
registry, channel runtime, retry engine, HITL lifecycle, or workflow state store
that duplicates AG2. A claimed exception must become an `ACTIVE` watchpoint
with a deletion condition before implementation.

## Version-Bump Flow

Dependabot isolates `ag2` and `agent-client-protocol` from the general Python
minor/patch group. Detection opens one AG2-runtime PR; it is not merge-ready
until this flow is complete:

The impact audit must answer:

1. What changed upstream?
2. Which Mozaiks AG2 usages are affected?
3. Which private/internal assumptions are affected?
4. Which Mozaiks workarounds can now be deleted?
5. Which AG2-native primitives should replace parallel Mozaiks logic?
6. Which watchpoint verification tests must run?
7. Which docs, watchpoints, or verification baselines became stale?
8. Which single finite upgrade outcome applies?

```text
new AG2 version detected
→ upgrade PR
→ watchpoint impact audit
→ compatibility suites
→ delete superseded workarounds
→ update last_verified_version
→ independent review
→ merge
```

For the update, review only the release notes, imported API diff,
migration/deprecation guidance, and watched surfaces relevant to Mozaiks. Give
the update one primary outcome from the finite list above. Do not copy the
upstream changelog into this document.

## Reusable Version-Review Checklist

- [ ] AG2 release notes reviewed.
- [ ] Imported API and dependency metadata diff reviewed.
- [ ] Migration and deprecation documentation reviewed.
- [ ] Every active/watch/deferred upstream surface reviewed.
- [ ] Private/internal API assumptions rechecked.
- [ ] Supersession and deletion queue evaluated.
- [ ] Required compatibility tests from affected watchpoints run.
- [ ] YAML and Markdown `last_verified_version` values updated.
- [ ] One finite upgrade outcome recorded.
- [ ] Mozaiks changelog updated only for actual product or architecture impact.
- [ ] Independent review requested before merge.

## History

### November 6, 2026

- **AG2 1.3.0 update, outcome `ADOPT`**: exact runtime, optional-extra and
  ACP image pins advance together. Python 3.11 is already the package minimum.
  The [release](https://github.com/ag2ai/ag2/releases/tag/v1.3.0) introduces
  native per-call status context, scoped delegation variables and descendant
  observation. Mozaiks keeps deterministic workflow and artifact authority.
- All 16 watchpoint triggers and three registered private seams were checked
  against installed 1.3.0 source and compared with 1.2.2. No existing network
  workaround met its deletion trigger. These stamps record source review;
  the listed tests remain the runtime evidence, and paid live acceptance is
  not claimed. Consulting's direct live-only path was not executed.
- Native status context is an opt-in workflow contract backed by AG2
  `statusbar_middleware`, separate from the existing browser chat status strip.
  It projects declared model-visible state without creating a second plan,
  changing authority or replacing enforced tool/model-call limits. See
  [declarative mapping](declarative-ag2-mapping.md#middlewareyaml).
- The descendant-usage contract test exercises a real three-level AG2 tree
  using fake model responses, including a failed leaf after usage. It verifies
  the existing Mozaiks watchdog sees source events once and ignores repeated
  observation. This does not add a second billing collector or certify every
  provider's hidden attempts.
- `Agent.task()` still works in 1.3 with a deprecation warning. The existing
  task-batch adapter must use supported `Task(owner_id=..., spec=...)`
  construction before 1.4; its currently claimed runner work is separate.
  `Task`, `TaskSpec` and the injected `KnowledgeStore` protocol themselves
  remain supported. OpenAI config selection already uses explicit provider
  classes. This update does not enable unrestricted network delegation.


### October 31, 2026

- **AG2 1.2.2 compatibility upgrade, outcome `MIGRATE`**: reviewed the
  [1.2.0](https://github.com/ag2ai/ag2/releases/tag/v1.2.0),
  [1.2.1](https://github.com/ag2ai/ag2/releases/tag/v1.2.1), and
  [1.2.2](https://github.com/ag2ai/ag2/releases/tag/v1.2.2) release and migration
  notes plus the [source comparison from 1.1.2](https://github.com/ag2ai/ag2/compare/v1.1.2...v1.2.2).
  All AG2 extras and the ACP proof images use the exact 1.2.2 pin.
  `OpenAICompletionsConfig` replaces the `OpenAIConfig` name so
  ordinary OpenAI workflows keep Chat Completions after AG2 changes the old
  name's meaning in 1.4.0. Explicit Responses selection remains unchanged.
  Anthropic no longer receives Mozaiks' shared temperature default because
  Anthropic SDK 2.x rejects that sampling option.
- **Ollama typing gap**: AG2 removed `OllamaConfig.create_files_client`,
  inheriting the Files API rejection stub from `ModelConfig`. Mypy treats that
  protocol stub as abstract even though the config remains instantiable at
  runtime. The config adapter suppresses only `abstract` at construction;
  no replacement class or duplicate stub is introduced. Remove this suppression
  when AG2 restores a concrete typing contract.
- **No workaround became deletable**: Hub acceptance and dispatch ordering,
  private human attachment, default-handler packet construction, transition
  condition composition, and `CHANNEL_STATE_DEP` keep their existing contracts.
  Code environments still expose per-call execution; Task does not add a
  deterministic dependency scheduler or cross-workflow lineage; Consulting
  gains no typed correction contract. The existing source, packet, history,
  and lifecycle adapters remain required. Every watchpoint and registered
  private seam was rechecked against 1.2.2 source; the paid live one-shot smoke
  remains outside this offline verification.
- **Network input parts are preserved upstream**: `render_envelope` and
  `extract_turn_input` now accept typed input sequences. Mozaiks' workflow view
  wrapper passes those through, while still repairing the native assistant
  history gap (`AG2-WP-015`). ACP's new content-block transport is compatible
  with the provider's single text prompt. Its terminal handler still does not
  enforce `allow_terminal=False`, so the existing local ACP execution gate
  remains necessary.
- **Usage corrections stay upstream-owned**: Anthropic continuation totals,
  Gemini thinking/tool-use tokens, xAI reasoning tokens, and OpenAI cache-write
  metadata are covered by the `AG2-WP-014` review above. Tool and subscriber
  timeouts default to `None`; this upgrade does not introduce new workflow
  timeout settings or change existing provider limits.
- **Provider SDK requirements follow AG2**: the OpenAI extra requires
  `openai>=3.37.0,<4`; Anthropic requires `anthropic>=1.15.0,<3`. Mozaiks does
  not duplicate those pins. New toolkits, OpenAI Decisions, A2UI changes, and
  provider features outside the existing runtime adapters are not adopted by
  this dependency migration.

### October 4, 2026

- **AG2 1.1.2 security upgrade, outcome `TEST_ONLY`**: all AG2 extras now share
  the exact 1.1.2 pin, replacing 1.0.5. AG2 1.0.5 is affected by eight ag2ai
  security advisories: GHSA-hf3x-42qp-4rq6, GHSA-qrjx-72xx-xhmh,
  GHSA-fxhh-6rm2-v2pp, and GHSA-2h3q-9p6m-4hm4 (fixed in 1.1.0),
  GHSA-2h4w-29cv-2cmv and GHSA-rppm-w5cw-6qcg (fixed in 1.1.1), and
  GHSA-42mf-vpmr-gw5r and GHSA-3ccw-g7ph-hrcr (fixed only in 1.1.2, so 1.1.1 is
  also affected). Mozaiks code does not use skill installation,
  `DiskKnowledgeStore`, `FilesystemToolkit`, subtasks, approval middleware, or a
  wire-served Hub (it uses only the in-process `LocalLink`). It attaches
  `SandboxShellTool()` with no `allowed`, `readonly`, `blocked`, or `ignore`
  filter, so there is no filter to bypass: that tool is an unrestricted shell
  on the host by design. The exact pin still has to move, because
  installations cannot override it.
- **No watched surface changed in a way Mozaiks depends on**: every AG2 symbol
  Mozaiks imports resolves in 1.1.2. Between 1.1.1 and 1.1.2 the hub methods
  Mozaiks calls (`get_channel`, `adapter_for`, `adapter_state`, `_dispatch`,
  `_fan_out`, `_transition_channel`, `bind_endpoint`) are unchanged, and so are
  the default notify handler, Consulting, transition conditions, and
  `CHANNEL_STATE_DEP`. The hub now rejects sends from non-participants; every
  Mozaiks send comes from the channel's initiator or an invited agent. A
  re-attaching `HubClient` now presents an empty credential unless it passes a
  passport; Mozaiks identities use the `none` scheme and re-bind in process.
  `WorkflowAdapter` handoffs now prefer an exact agent id over a name. No
  workaround became deletable.
- **Compatible behavior changes since 1.0.5**: one agent turn keeps one tool per
  callable name (the later declaration wins, with a warning); no shipped
  workflow binds two tools with one name. `SandboxShellTool` runs commands
  without a shell only when a filter is set; the default tool Mozaiks attaches
  keeps `sh -c`. The Anthropic usage change and the 1.1.2 continuation change
  are recorded under `AG2-WP-014`. `HumanInputRequest.timeout` and `.task_id`,
  `ToolApprovalRequest`, `MessageEnqueued`, `ModelResponse.response_id`,
  `UsageEvent.parts`, and the OpenAI prompt-cache options are additive and not
  adopted. AG2 drops context keys prefixed `ag:` or `a2a:` from A2A, AG-UI,
  A2UI, and NLIP transports; Mozaiks' declarative A2A remote agents send
  context variables, so every context declaration surface now rejects those
  prefixes (`reserved_context_keys.py`). `BedrockConfig` now requires
  aiobotocore; Mozaiks does not use Bedrock.
- **Dependency floors moved with the extras** (OpenAI SDK 3.9, Anthropic SDK
  1.6, MCP 2.2). Mozaiks pins none of them; the `a2a-sdk` and
  `agent-client-protocol` ranges are unchanged.
- **Ledger reach corrected**: for `AG2-WP-003`, `AG2-WP-006`, `AG2-WP-007`,
  `AG2-WP-009`, `AG2-WP-010`, `AG2-WP-014`, `AG2-PRI-002`, and the new
  `AG2-WP-016`, every listed verification test was checked by breaking the
  watched Mozaiks code and confirming the test fails. Tests that did not reach
  their watched code were removed: `test_workflow_network_graph.py` from
  `AG2-WP-003`, `AG2-WP-006`, and `AG2-PRI-002`;
  `test_refinement_task_batch_smoke.py` from `AG2-WP-009`;
  `test_structured_output_runtime_contracts.py` from `AG2-WP-007`; and
  `test_task_batch_contracts.py` from `AG2-WP-010`, which has no offline test.
  `AG2-WP-007`'s trigger had already fired before 1.0.5: AG2 uses the agent's
  own `response_schema` on channel turns. The entry now watches the remaining
  gap, correction on channel turns, with tests that send a real channel turn
  through an HTTP provider. `AG2-WP-016` records the second `on_accepted` call.

### September 1, 2026

- **Update governance made actionable**: stable watchpoint IDs, finite statuses,
  the private/internal API register, a supersession queue, and the minimal YAML
  automation index now make each AG2 bump answer what to test, migrate, shrink,
  or delete. Dependabot isolates AG2/ACP updates for that review flow.
- **AG2 1.0.3 compatibility upgrade**: all AG2 extras now share the exact
  1.0.3 pin; ACP uses 0.12.1; AG2 owns the transitive MCP 2.x contract.
- **Usage accounting follows `UsageEvent`**: the upstream `TokenMonitor` proof
  now covers ordinary turns, failed-subtask rollups, compaction, aggregation,
  and rejection of duplicate `ModelResponse` accounting. Mozaiks only projects
  the resulting observer alert into app/run-scoped events.
- **No persistent-subagent workaround existed in Mozaiks**: AG2 1.0.3's stream
  identity fix required no local deletion or replacement.
- **Task plus direct ask remains a narrow valid adapter composition**: AG2
  `Task` owns lifecycle evidence while public `Agent.ask(...)` performs one
  preselected worker turn. This standalone path remains intentionally distinct
  from Hub channels, `TaskMirror`, and LLM-directed delegation.
- **HITL failure semantics required no replacement framework**: unanswerable
  input now fails upstream; the private durable-human reattachment seam remains
  because AG2 still has no public equivalent.

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
  by six AppGenerator agents). Scoped coding now calls the same candidate
  acceptance/build facade; Docker/E2B build evidence comes from the existing
  `SandboxPort`, never from a model's strategy label. Session identity/metadata persists (validation results
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
