# Kernel Integration Roadmap — UniversalOrchestrator

> **Purpose Statement:** Every piece in this plan exists to enable the **UniversalOrchestrator** —
> the ability to pause a running GroupChat mid-stream, decompose into N parallel sub-GroupChats,
> merge results, and resume the parent. If a piece doesn't serve that goal, it doesn't belong here.

**Date:** 2026-02-28  
**Source:** Archived `ARCHIVED_MozaiksKernel/` repo (contracts-only, zero runtime logic)  
**Target:** `mozaiksai/core/` inside the `mozaiks` repo  

---

## North Star: Future Agentic Streaming Alignment

The AG2 framework is evolving toward an **async-first, streaming-native** API redesign. The exact
timeline and API surface are not finalized — what follows is our architectural alignment with the
*direction* of that evolution, not with any specific unreleased version. Our contracts/ports layer
ensures we can adopt these capabilities rapidly when they ship, without rewriting orchestrator logic.

### AG2 Evolution Direction (async-first, A2A / MCP / AG-UI influenced)

| Emerging Concept | What It Means | Our Alignment |
|---|---|---|
| **Async-first API** | `agent.ask()`, `agent.process()`, `agent.stream()` replace sync `initiate_chat` | Our `OrchestrationPort.run()` is already `async → AsyncIterator[DomainEvent]`. Zero conflict. |
| **Immutable Model Config** | Frozen model objects with `.update()` returning new instances | Our `RunRequest`/`ResumeRequest` are already frozen Pydantic. LLM config managed per-workflow in YAML. |
| **Conversation object** | `conversation.ask()` chains turns; `.history` is explicit | Maps to our `LedgerPort.read()` — event history per run_id. Conversation state = ledger cursor. |
| **Bi-directional streaming** | Writer + listener with pause/continue/break controls (API shape TBD) | **This IS the UniversalOrchestrator's control plane.** Pause = decompose. Continue = resume after merge. Our `ControlPlanePort.notify_run_state(PAUSED/RUNNING)` maps to this concept. |
| **`InputRequired` event** | Stream yields `InputRequired` when HITL needed; writer sends user input | Maps to our `SessionPausedEvent` + existing `SimpleTransport.submit_user_input()`. |
| **Response schema per-call** | `agent.ask("...", response_schema=Answer)` at conversation level | Maps to our existing `structured_outputs` in workflow YAML. Per-call override = agent signal. |
| **Restore conversation from history** | Resume from persisted state | **This is `OrchestrationPort.resume(ResumeRequest)`.** Our cursor-based replay protocol handles this. |
| **ModelComposition (fallback)** | Primary + fallback models | Our `llm_config.py` already supports this via YAML `models:` list. |
| **Delayed configuration / DI** | Model/agent config injected at call time | Our `RuntimeContext` protocol already provides per-run config injection. |

### What This Means Concretely

1. **We build on current AG2 now.** Phases 1-4 use current AG2 (`a_run_group_chat`, `a_resume`).
2. **When AG2 ships streaming-native APIs**, our migration surface is contained:
   - `orchestration_patterns.py` swaps its AG2 calls to the new streaming primitives
   - `groupchat_pool.py` (Phase 4) swaps to native streaming orchestration APIs
   - **Everything else stays the same** — contracts, ports, orchestrator logic, event pipeline, schemas
3. **The ports/contracts layer we're building IS the migration firewall.** When AG2's API changes, only the code behind `OrchestrationPort` changes. Consumers (platform, frontend, workflows) see zero disruption.

### Streaming ↔ UniversalOrchestrator Mapping (Conceptual)

```
Future Streaming Primitives             UniversalOrchestrator
───────────────────────────             ─────────────────────
send(message)                      ──►  OrchestrationPort.run(RunRequest)
listen() yields events             ──►  AsyncIterator[DomainEvent]
pause()                            ──►  ControlPlanePort.notify(PAUSED) → decompose
writer.send(input)                 ──►  ResumeRequest (sub-GroupChat initial_message)
continue/resume                    ──►  OrchestrationPort.resume(ResumeRequest)
InputRequired event                ──►  SessionPausedEvent → UI prompt
conversation.history               ──►  LedgerPort.read(run_id) → event replay
restore_conversation(history)      ──►  OrchestrationPort.resume(last_seq cursor)
```

**Note:** The exact API names above (pause/continue/break) are from early design discussions
and may change. Our alignment is with the *concept* (bidirectional streaming with lifecycle
controls), not specific method names.

### REST/CRUD Awareness

While the future is chat-centric, workflows also expose **REST/CRUD endpoints** via optional
`router.py` files. These endpoints:
- May trigger workflow events (e.g., a REST `POST /orders` creates an order and emits a
  `resource.created` DomainEvent that an agent can react to)
- Live alongside the WebSocket-based chat transport
- Use the same `app_id` scoping and auth as agentic flows
- Can participate in the event pipeline: REST actions → DomainEvent → agent reaction → DomainEvent → REST response

The orchestration layer must remain aware that **not all app logic flows through GroupChats**.
CRUD operations, webhook handlers, and scheduled tasks are valid event sources. The `DomainEvent`
envelope is the unifying contract — whether an event originates from an agent turn, a REST call,
or a background job.

### GroupChat Pattern Selection (Two-Phase Rule)

**At runtime → always declarative, never LLM-driven.**
The UniversalOrchestrator reads `groupchat_pattern` from `orchestrator.yaml` or
`_pack/workflow_graph.json`. This is deterministic and fast. No LLM call happens at runtime
to decide how to orchestrate.

**At generation time → LLM-assisted, one time, output baked into config.**
During app generation (AppGenerator or OSS builder), an LLM may examine the workflow
description + tools needed to propose the appropriate pattern. The result is written
permanently into the config files. The LLM is never consulted again for this decision.

**Future roadmap (opt-in only):** A workflow may declare `pattern: adaptive` in its config,
signaling the runtime to perform an LLM routing call. This is **not the default** and must be
explicitly opted into per-workflow.

### The Business Case

When AG2 ships streaming-native APIs with A2A/MCP/AG-UI support:
- **Our workflows become A2A-compatible agents** — each workflow behind `OrchestrationPort` IS an A2A service
- **Our platform users get agent-to-agent interop for free** — their apps can participate in the broader agentic ecosystem
- **The generator's output format doesn't change** — because contracts (RunRequest, DomainEvent) are stable

> When our users monetize, we monetize. Next-gen agentic apps require next-gen orchestration.
> Our architecture ensures users can offer A2A-compatible, MCP-integrated, streaming-first
> applications that participate in the agentic economy — the moment the underlying framework
> supports it.

---

## Why Each Piece Exists (Dependency Chain)

Every item below has a **direct line** to the UniversalOrchestrator. If you can't trace the line, the item shouldn't be integrated.

```
UniversalOrchestrator
├── needs OrchestrationPort ← to define its own contract (run/resume/cancel)
│   └── needs RunRequest, ResumeRequest ← typed input to run() and resume()
│       └── needs DomainEvent, EventEnvelope ← typed output from run() (AsyncIterator[DomainEvent])
│           └── needs Event Taxonomy ← canonical event_type names (process.*, task.*, tool.*)
│           └── needs Replay/Resume Protocol ← cursor-based resume when sub-GroupChat pauses/fails
├── needs RuntimeContext ← each spawned sub-GroupChat gets its own context
│   └── needs LedgerPort ← event log per sub-chat (append/read)
│   └── needs ControlPlanePort ← run state tracking (RUNNING/PAUSED/COMPLETED/FAILED)
│   └── needs ArtifactPort ← sub-chats produce artifacts that parent must merge
│   └── needs ClockPort, LoggerPort ← observable execution
├── needs WorkflowRegistry Protocol ← look up sub-workflow handlers by name
├── needs PluginRegistry Protocol ← load decomposition strategies / merge strategies
├── needs ToolExecutionPort ← sub-chats may invoke tools
├── needs SandboxPort, SecretsPort ← sub-chats may run sandboxed code or access secrets
└── needs Frozen JSON Schemas ← contract enforcement prevents drift between core and platform
```

**What does NOT serve this goal (and is NOT being integrated):**
- Kernel's SQLAlchemy/Alembic DB layer → Core uses MongoDB. Different backend.
- Kernel's auth subsystem → Core's auth is already a superset (FastAPI deps, WebSocket, etc.)
- Kernel's generic logging utils → Core's AG2-specific observability is more relevant.

---

## Architecture: Where Everything Lives

```
mozaiksai/
  core/
    contracts/         ← Phase 1: Typed Pydantic models (from kernel)
      schemas/v1/      ← Phase 3: Frozen JSON schemas (Pydantic model shape)
        transport/     ← Phase 3: Wire protocol schemas (x-contract-frozen)
        fixtures/      ← Phase 3: NDJSON reference event streams
    ports/             ← Phase 1: @runtime_checkable Protocols (from kernel)
    events/            ← Phase 2: Wire DomainEvent into existing dispatcher
    workflow/          ← Phase 2: Type-annotate existing code against ports
    data/              ← Unchanged
    auth/              ← Unchanged
    transport/         ← Unchanged
    ...
  orchestration/
    __init__.py        ← Already exists (create_ai_workflow_runner)
    universal.py       ← Phase 4: UniversalOrchestrator (implements OrchestrationPort)
    groupchat_pool.py  ← Phase 4: Parallel sub-GroupChat spawning (streaming migration boundary)
    decomposition.py   ← Phase 4: Decomposition strategies (detect split points)
    merge.py           ← Phase 4: Result merging across sub-GroupChats
scripts/
  check_event_envelope_protocol_guard.py  ← Phase 3: CI guard for frozen schemas
tests/
  test_event_schemas.py  ← Phase 3: 17 tests (envelope, seq, aliases, jsonschema)
```

---

## Phase 1: Contracts + Ports (Pure Additive)  ✅ COMPLETED

**Goal:** Drop typed contracts and protocol interfaces into core. Zero behavioral changes. Zero existing code modified.

**Why this matters for UniversalOrchestrator:**
The orchestrator's `run()` method signature is `run(request: RunRequest) -> AsyncIterator[DomainEvent]`.
Without `RunRequest` and `DomainEvent`, the orchestrator has no typed API.
Without `OrchestrationPort`, the orchestrator has no formal contract to implement.

### Checklist

#### 1.1 — Create `mozaiksai/core/contracts/`  ✅

- [x] `__init__.py` — Aggregate re-exports (35 symbols in `__all__`)
- [x] `events.py` — `DomainEvent`, `EventEnvelope`, `EVENT_SCHEMA_VERSION`, `EVENT_TYPE_PATTERN`
  - Pydantic v2 models, extra=forbid, UTC normalization, regex-validated event_type
- [x] `runner.py` — `RunRequest`, `ResumeRequest`, `AI_RUNNER_PROTOCOL_VERSION`
  - Legacy field coercion (`input`→`payload`, `checkpoint_key`→`checkpoint_id`)
- [x] `replay.py` — `ReplayBoundaryPayload`, `SnapshotEventPayload`, protocol version + event type constants
- [x] `artifacts.py` — `ArtifactRef`, `ArtifactCreatedPayload`, `ArtifactUpdatedPayload`, `ArtifactStateReplacedPayload`, `ArtifactStatePatchedPayload`, `LARGE_ARTIFACT_INLINE_THRESHOLD_BYTES` (256KB)
- [x] `sandbox.py` — `SandboxExecutionResult`
- [x] `secrets.py` — `SecretRef`
- [x] `taxonomy.py` — `CANONICAL_EVENT_TAXONOMY`, `PROCESS_STARTED/COMPLETED/FAILED_EVENT_TYPE`
- [x] `tools.py` — `ToolExecutionRequest`, `ToolExecutionResult`, `TOOL_EXECUTION_SCHEMA_VERSION`

#### 1.2 — Create `mozaiksai/core/ports/`  ✅

- [x] `__init__.py` — Aggregate re-exports (26 symbols in `__all__`)
- [x] `orchestration.py` — `OrchestrationPort` (`run`, `resume`, `cancel`, `capabilities`)
- [x] `ai_runner.py` — `AIWorkflowRunnerPort` (extends OrchestrationPort, marker protocol)
- [x] `runtime.py` — `LedgerPort`, `ControlPlanePort`, `ArtifactPort`, `ClockPort`, `LoggerPort`
- [x] `sandbox.py` — `SandboxPort`
- [x] `secrets.py` — `SecretsPort`
- [x] `tool_execution.py` — `ToolExecutionPort`
- [x] `context.py` — `RuntimeContext` Protocol (properties for run_id, tenant_id, ledger, control_plane, etc.)
- [x] `registry.py` — `WorkflowRegistry` Protocol, `PluginRegistry` Protocol, `InMemoryWorkflowRegistry`, `InMemoryPluginRegistry`, `@workflow` decorator, `@plugin` decorator
- [x] `ag2_adapter.py` — `AG2OrchestrationAdapter` (implements OrchestrationPort, wraps `run_workflow_orchestration`)

#### 1.3 — Update Exports  ✅

- [x] Add `contracts` and `ports` to `mozaiksai/core/__init__.py` re-exports
- [x] Add new packages to `pyproject.toml` packages list (both `mozaiks.*` and `mozaiksai.*` namespaces)
- [x] Verify: `from mozaiks.core.contracts import RunRequest, DomainEvent` works
- [x] Verify: `from mozaiks.core.ports import OrchestrationPort` works

#### 1.4 — Validation  ✅

- [x] All existing tests still pass (zero regression)
- [x] New import smoke test passes
- [x] No existing file was modified (except `__init__.py` exports + `pyproject.toml`)

---

## Phase 2: Wire Protocols to Existing Code  ✅ COMPLETED

**Goal:** Type-annotate existing runtime code to implement the kernel protocols. Makes existing code formally satisfy the port contracts without changing behavior.

**Why this matters for UniversalOrchestrator:**
The orchestrator delegates to existing code. If `UnifiedWorkflowManager` implements `WorkflowRegistry`, the orchestrator can look up sub-workflows through the protocol. If `run_workflow_orchestration` matches `OrchestrationPort.run()`, the orchestrator can wrap it.

### Checklist

#### 2.1 — Workflow Manager → WorkflowRegistry  ✅

- [x] `UnifiedWorkflowManager` gains `register()`, `get()`, `list()` that delegate to its existing `_handlers`
- [x] Type-annotate return types to match protocol
- [x] `isinstance(workflow_manager, WorkflowRegistry)` → `True`
- [x] `WorkflowNotFoundError` / `WorkflowAlreadyRegisteredError` raised correctly
- [x] Existing `register_workflow_handler()` / `get_workflow_handler()` untouched (legacy API preserved)

#### 2.2 — Event Dispatcher → DomainEvent Support  ✅

- [x] `UnifiedEventDispatcher.dispatch()` accepts `DomainEvent` alongside existing `BusinessLogEvent`/`UIToolEvent`
- [x] `DomainEventHandler` registered in default handlers
- [x] `emit_domain_event()` convenience method dispatches + fans out to event_type listeners
- [x] `domain_event_to_envelope()` converts DomainEvent to transport dict
- [x] `build_outbound_event_envelope()` accepts `DomainEvent` (fast-path) AND legacy dict-with-kind

#### 2.3 — Orchestration Runner → OrchestrationPort  ✅

- [x] `AG2OrchestrationAdapter` created in `ports/ag2_adapter.py`
- [x] `run()` unpacks `RunRequest` → calls `run_workflow_orchestration` → yields `DomainEvent`
- [x] `resume()` unpacks `ResumeRequest` → same engine → yields `DomainEvent`
- [x] `cancel()` placeholder (AG2 0.x has no native cancel)
- [x] `capabilities()` returns engine metadata
- [x] `isinstance(adapter, OrchestrationPort)` → `True`
- [x] `get_ag2_orchestration_adapter()` singleton accessor

#### 2.4 — Validation  ✅

- [x] `isinstance(workflow_manager, WorkflowRegistry)` → `True` (runtime_checkable)
- [x] `isinstance(adapter, OrchestrationPort)` → `True`
- [x] Both `mozaiks.*` and `mozaiksai.*` namespace imports resolve identically
- [x] Legacy dict-with-kind event path unbroken
- [x] No existing behavior modified

---

## Phase 3: Frozen JSON Schemas + CI Guard  ✅ COMPLETED

**Goal:** Copy the frozen event schemas into core and add CI enforcement so no one silently breaks the event contract.

**Why this matters for UniversalOrchestrator:**
The orchestrator emits `DomainEvent`s as it decomposes and merges. Platform, frontend, and any consumer depend on the envelope shape being stable. Without frozen schemas, a typo in Phase 4 breaks the entire event pipeline.

### Checklist

#### 3.1 — Copy Schemas ✅

- [x] Create `mozaiksai/core/contracts/schemas/v1/`
- [x] Copy all 9 JSON schema files from archived kernel (kernel payload schemas)
- [x] Copy 5 transport-level frozen schemas to `schemas/v1/transport/`
- [x] Copy 3 reference data files (aliases, family guidelines, replay spec)
- [x] Copy `contracts/events/v1/` reference fixtures (3 NDJSON/JSON files → `schemas/v1/fixtures/`)
- [x] Copy `validate_fixture_example.py` as a standalone validation script

#### 3.2 — CI Enforcement ✅

- [x] Port `check_event_envelope_protocol_guard.py` → `scripts/check_event_envelope_protocol_guard.py`
- [x] Guard detects 1 frozen schema (`event_envelope.schema.json` with `x-contract-frozen: true`)
- [x] Guard exits 0 on clean state, would exit 1 if schema modified without version bump
- [x] `tests/test_event_schemas.py` — 17 tests covering envelope shape, seq monotonicity, alias rejection, jsonschema Draft2020-12 validation

#### 3.3 — Validation ✅

- [x] `pytest tests/test_event_schemas.py` — **17/17 passed** (0.18s)
- [x] CI guard script passes on current state — **✅ All frozen schemas OK**
- [x] Standalone validator passes — **✅ All 36 events valid against event_envelope.schema.json**

---

## Phase 4: UniversalOrchestrator (The Actual Goal)  ✅ COMPLETED

**Goal:** Build the Layer 1.5 orchestrator that sits between single-GroupChat execution and cross-workflow pack orchestration. It controls GroupChats — pause, decompose, spawn parallel sub-GroupChats, merge, resume.

**Why this IS the goal:** Everything above exists to make this possible with typed contracts, formal protocols, and contract enforcement.

**Completed:** 6 files created, `__init__.py` updated, 49/49 tests passing.

### Checklist

#### 4.1 — UniversalOrchestrator Core

- [x] `mozaiksai/orchestration/universal.py`
  - Implements `OrchestrationPort` (run, resume, cancel, capabilities)
  - `run()`: wraps existing `run_workflow_orchestration` for the happy path (single GroupChat)
  - Detects decomposition points (via config or agent signal)
  - On decomposition: pause parent → create N sub-run descriptors → delegate to pool → merge → resume parent
- [x] Internal state model:
  - `OrchestratorRun` — tracks parent run_id, child run_ids, state (RUNNING/DECOMPOSING/MERGING/COMPLETED)
  - `RunState` enum: INITIALIZING → RUNNING → DECOMPOSING → MERGING → COMPLETED/FAILED/CANCELLED

#### 4.2 — GroupChat Pool (AG2 Integration)

- [x] `mozaiksai/orchestration/groupchat_pool.py`
  - Takes N sub-run descriptors (each with workflow_name + initial_message)
  - Executes sub-GroupChats through `AG2OrchestrationAdapter` (which wraps `run_workflow_orchestration`)
  - Supports parallel execution (asyncio tasks + queue drain) and sequential execution
  - Emits `DomainEvent`s for each sub-chat lifecycle (process.started, process.completed/failed)
- [x] **Streaming migration boundary:** All AG2-specific calls are isolated in this file.
  When the framework evolves to native async streaming, ONLY this file swaps to new streaming primitives.
  The `OrchestrationPort` contract above it stays unchanged.

#### 4.3 — Decomposition Strategy

- [x] `mozaiksai/orchestration/decomposition.py`
  - Interface: `DecompositionStrategy` Protocol — `detect(context) -> Optional[DecompositionPlan]`
  - `DecompositionPlan`: tuple of `SubTask`s, execution_mode, resume_agent
  - Built-in strategies:
    - `ConfigDrivenDecomposition` — reads from workflow YAML (`decomposition:` block) or pack graph `nested_chats`
    - `AgentSignalDecomposition` — triggered by `process.decompose_requested` event or `PatternSelection` structured output

#### 4.4 — Merge Strategy

- [x] `mozaiksai/orchestration/merge.py`
  - Interface: `MergeStrategy` Protocol — `merge(MergeContext) -> MergeResult`
  - `MergeResult`: combined summary_message + merged_data + per-child results
  - Built-in strategies:
    - `ConcatenateMerge` — markdown summary with per-child sections (✅/❌)
    - `StructuredMerge` — JSON-keyed merge by task_id

#### 4.5 — Event Integration

- [x] `mozaiksai/orchestration/events.py`
  - Event emission helpers: `emit_decomposition_started()`, `emit_subtask_spawned()`, `emit_decomposition_completed()`, `emit_merge_completed()`, `emit_parent_resuming()`
  - Routes through existing `emit_handoff_event()` for full pipeline compatibility
- [x] `mozaiksai/orchestration/__init__.py` updated with 24 new exports

#### 4.6 — Validation

- [x] Unit tests: 49 tests covering all Phase 4 components (`tests/test_universal_orchestrator.py`)
  - Data model construction (SubTask, DecompositionPlan, ChildResult, MergeResult, OrchestratorRun)
  - ConfigDrivenDecomposition: 7 tests (no config, empty, YAML parallel/sequential, pack graph, priority, invalid)
  - AgentSignalDecomposition: 5 tests (no event, domain event, PatternSelection, negative cases)
  - ConcatenateMerge: 3 tests (success, partial failure, structured output)
  - StructuredMerge: 4 tests (merge, text fallback, no fallback, failure)
  - Protocol conformance: 3 tests
  - UniversalOrchestrator happy path: 2 tests (delegates to adapter, run_completed payload)
  - UniversalOrchestrator decomposition: 2 tests (lifecycle events, decomposed flag)
  - UniversalOrchestrator errors: 1 test (adapter failure → run_failed)
  - GroupChatPool: 4 tests (sequential, parallel, failure, pool_completed payload)
  - Events: 3 tests (constants, emit helpers)
  - Package exports: 2 tests (__all__ completeness, symbol resolution)
- [x] Zero regression: Phase 3 tests (17/17) still pass
- [x] Protocol conformance verified

---

## Phase 5: Future Streaming Evolution (When Framework Ships Native Async Streaming)

**Goal:** When the AG2 framework ships async-first / streaming-native APIs, swap the AG2 call
sites while keeping everything above `OrchestrationPort` completely unchanged. This is NOT active
work — it's a readiness checklist for when the next-gen AG2 APIs become available.

**Why this matters:** Our users' apps are built on workflows that go through our orchestrator.
If the framework evolves to native A2A/MCP/AG-UI support and we can adopt it in days (not months),
our users get next-gen capabilities before any competitor.

### Checklist (Activate When Next-Gen AG2 Streaming APIs Are Available)

#### 5.1 — Framework Assessment

- [ ] Read release notes / migration guide for the new AG2 async streaming APIs
- [ ] Map new Agent / Model classes to our LLM config YAML
- [ ] Verify the new streaming API supports our event types or can be wrapped into `DomainEvent`
- [ ] Check if native streaming lifecycle controls (pause/resume/break) provide the control we need for decomposition
- [ ] Assess new model composition / fallback mechanisms vs our existing `llm_config.py`

#### 5.2 — Migration (Contained to 2-3 files)

- [ ] `orchestration_patterns.py` — replace `a_run_group_chat()` / `group_manager.a_resume()` with new streaming API
- [ ] `groupchat_pool.py` — replace current orchestration calls with native streaming multi-agent orchestration
- [ ] `core/workflow/execution/patterns.py` — update `create_ag2_pattern()` if pattern API shape changed
- [ ] Validate: all new streaming calls still produce `DomainEvent`s that match frozen schemas

#### 5.3 — A2A / MCP / AG-UI Integration Points

- [ ] Each workflow behind `OrchestrationPort` can be exposed as an **A2A service endpoint**
  - RunRequest maps to A2A task request
  - AsyncIterator[DomainEvent] maps to A2A streaming response
- [ ] MCP tool integration: `ToolExecutionPort` can wrap MCP tool servers
  - Agent tools from `tools.yaml` can reference MCP URIs
  - `ToolExecutionRequest` → MCP tool call, `ToolExecutionResult` ← MCP response
- [ ] AG-UI streaming: `DomainEvent` stream ↔ AG-UI event protocol
  - `DomainEvent.event_type` taxonomy maps to AG-UI event types
  - `SimpleTransport` adapts to AG-UI client protocol

#### 5.4 — Generator Update

- [ ] Workflow generator produces agent definitions compatible with new streaming APIs
- [ ] Generated `agents.yaml` uses latest model config syntax if available
- [ ] Generated workflows can declare A2A service exposure

#### 5.5 — Validation

- [ ] All existing workflows pass on new AG2 streaming APIs (backward compat or one-time migration)
- [ ] UniversalOrchestrator decomposition still works with native streaming lifecycle controls
- [ ] A2A endpoint responds correctly to external agent requests
- [ ] No contract breakage: `DomainEvent`s validate against frozen schemas

---

## What We Are NOT Doing

These are explicitly out of scope to prevent drift:

| Item | Why Not |
|---|---|
| Replacing MongoDB with PostgreSQL | Kernel's DB layer is for a different backend. Core stays on Mongo. |
| Replacing core's auth with kernel's auth | Core's auth is a superset. Kernel's is simpler but less capable. |
| Creating a separate `mozaiks-kernel` package | User directive: "i dont want this as a separate repo i want this as part of the mozaiks core." |
| Adding abstract layers without consumers | Every protocol added in Phase 1-2 has a concrete consumer in Phase 4. |
| Building the full 4-repo split from AGENTS.md | The archived kernel envisioned kernel/core/ai/platform as 4 repos. We're merging kernel INTO core. Two repos remain: `mozaiks` (core) + `mozaiks-platform`. |
| Touching `orchestration_patterns.py` internals | Phase 4 wraps it; doesn't rewrite it. The 2408-line file stays as-is. |
| Pre-emptively adopting unreleased AG2 APIs | Next-gen streaming APIs aren't released yet. We build on current AG2 now. Phase 5 activates when they ship. |
| Building our own A2A/MCP runtime | The framework will provide this. We prepare the integration points (ports + contracts) so adoption is fast. |

---

## Dependency Graph (Phase Ordering)

```
Phase 1 ──────► Phase 2 ──────► Phase 4 ─ ─ ─ ► Phase 5
(contracts)     (wire)           (orchestrator)   (streaming evolution — when available)
    │                               ▲                  ▲
    └──► Phase 3 ───────────────────┘                  │
         (schemas + CI)                                │
                                                       │
                          AG2 streaming APIs ship ─────┘
```

- Phase 1 is prerequisite for everything
- Phase 2 and 3 can run in parallel after Phase 1
- Phase 4 requires Phase 2 (wired protocols) and benefits from Phase 3 (schema enforcement)
- Phase 5 is **event-triggered** — activates only when AG2 ships next-gen streaming APIs
- Phases 1-4 are designed so Phase 5 only touches AG2 call sites (2-3 files), not contracts/ports/orchestrator logic

---

## Success Criteria

When this roadmap is complete:

1. `from mozaiks.core.contracts import RunRequest, DomainEvent, EventEnvelope` ← works
2. `from mozaiks.core.ports import OrchestrationPort, RuntimeContext` ← works
3. `isinstance(universal_orchestrator, OrchestrationPort)` ← `True`
4. A workflow can be configured with `decomposition:` in its YAML
5. Mid-stream: orchestrator detects decomposition point → pauses parent GroupChat
6. N sub-GroupChats spawn via orchestration adapter → execute in parallel
7. Results merge → parent GroupChat resumes with merged context
8. All existing single-GroupChat workflows work unchanged (zero regression)
9. Event stream is typed `DomainEvent` throughout, validated against frozen JSON schemas
10. Platform can register custom `DecompositionStrategy` and `MergeStrategy` via `PluginRegistry`
11. All AG2-specific code is behind `OrchestrationPort` — when streaming APIs evolve, only 2-3 files change
12. `OrchestrationPort` contract is A2A-compatible: `run(RunRequest) → AsyncIterator[DomainEvent]` maps 1:1 to A2A task lifecycle
13. `ToolExecutionPort` is MCP-ready: tool calls can be routed to MCP servers without changing the contract
14. Platform users can expose their workflows as A2A agents, enabling interop with the broader agentic ecosystem
