# Pipeline Architecture Refactor Plan

> **Status:** Phases 1-4 complete; Phase 5 next  
> **Date:** 2026-03-05  
> **Scope:** `orchestration.py` (2448→1353 lines), `handler.py` (2742 lines), `shared_app.py` (1985→22 lines)  
> **Goal:** Three god-files → ~20 focused modules with a clean pipeline architecture  
> **Constraint:** Zero behavioral regressions. Every phase leaves the system runnable.

---

## 1. Why

| Problem | Impact |
|---|---|
| `_stream_events()` is 1073 lines with 14 responsibilities | Untestable, unreviewable, every change risks side effects |
| `SimpleTransport` is 2596 lines doing WS lifecycle + routing + workflow execution + artifacts + token logging | Can't change transport without risking orchestration |
| `shared_app.py` has 24 routes mixed with startup/shutdown/middleware | Route changes require reading 2000 lines of context |
| Circular dependency: `orchestration.py ↔ handler.py` | Both use deferred imports to avoid import-time cycles |
| `transport.connections[chat_id]` is a mutable dict written by the engine and read by transport | Tightest non-contractual coupling; invisible shared state |
| AG2 event types leak into kernel and transport layers | Future engine swap requires touching every layer |

---

## 2. Target Architecture

```
Engine (AG2-specific)
   ↓  yields DomainEvents
Engine Adapter (AG2 → DomainEvent translation)
   ↓  DomainEvent stream
Kernel Pipeline (middleware chain)
   ↓  enriched / persisted / filtered events
Runtime Services (persistence, sessions, artifacts)
   ↓
Transport (WebSocket delivery)
```

### Target directory structure

```
engine/
├─ executor/
│  ├─ groupchat_executor.py    # Builds agents + pattern, runs AG2, yields raw events
│  └─ pattern_factory.py       # YAML config → AG2 Pattern object
├─ agents/
│  ├─ factory.py               # (exists) YAML → ConversableAgent instances
│  └─ ...
├─ streaming/
│  ├─ ag2_event_adapter.py     # AG2 event → DomainEvent (pure translation)
│  └─ iostream_bridge.py       # (exists) IOStream → transport fast-path
├─ a2a/
│  └─ remote.py                # (exists) A2A remote agent factory

kernel/
├─ pipeline/
│  ├─ event_pipeline.py        # Middleware chain runner
│  ├─ persistence_middleware.py # Save messages to Mongo
│  ├─ transport_middleware.py   # Forward events to SimpleTransport
│  ├─ lifecycle_middleware.py   # before_agent / after_agent / on_context_change triggers
│  ├─ structured_output_middleware.py  # Auto-tool JSON extraction + schema validation retry
│  └─ observability_middleware.py      # Perf recording, context diffs, logging
├─ orchestration/
│  └─ ...                      # (exists) decomposition strategies

runtime/
├─ persistence/                # (exists)
├─ sessions/                   # (exists)
└─ artifacts/                  # (exists)

transport/
├─ websocket/
│  ├─ server.py                # FastAPI WS endpoint accept + delegate
│  ├─ connection_manager.py    # Connection state tracking, broadcast
│  ├─ message_router.py        # Inbound WS message type dispatch
│  ├─ input_handler.py         # AG2 input-request callback wiring
│  └─ handler.py               # (slim) backward-compat re-exports during migration
├─ routes/
│  ├─ chat_routes.py           # /api/chats/*, /chat/*/input, /api/user-input/submit
│  ├─ workflow_routes.py       # /api/workflows/*, /api/workflows/config
│  ├─ session_routes.py        # /api/sessions/*
│  ├─ metrics_routes.py        # /metrics/perf/*, /health/active-runs, /api/events/metrics
│  ├─ upload_routes.py         # /api/chat/upload*
│  └─ health_routes.py         # /api/health
├─ app_factory.py              # create_app() → FastAPI, mounts routers + middleware
```

---

## 3. Phased Execution Plan

Each phase is independently shippable. The system must pass `python run_server.py` + WebSocket flow after each phase.

---

### Phase 1 — Split `shared_app.py` into route modules

**Risk: Low** — Mechanical extraction, no behavioral change.

**What changes:**
- `shared_app.py` (1985 lines) → `transport/app_factory.py` (~80 lines) + 6 route modules (~200 lines each)
- `shared_app.py` becomes a thin shim that imports from `app_factory.py` (backward compat for `run_server.py`)

**Checklist:**

- [ ] Create `transport/routes/__init__.py`
- [ ] Extract health/metrics routes → `transport/routes/health_routes.py`
  - `/api/health`
  - `/health/active-runs`
  - `/metrics/perf/aggregate`
  - `/metrics/perf/chats`
  - `/metrics/perf/chats/{chat_id}`
  - `/api/events/metrics`
- [ ] Extract chat routes → `transport/routes/chat_routes.py`
  - `/api/chats/{app_id}/{workflow_name}/start`
  - `/api/chats/{app_id}/{workflow_name}` (list)
  - `/api/chats/exists/...`
  - `/api/chats/meta/...`
  - `/api/general_chats/list/...` (stub)
  - `/api/general_chats/transcript/...` (stub)
  - `/chat/{app_id}/{chat_id}/{user_id}/input`
  - `/api/user-input/submit`
  - `/chat/{app_id}/{chat_id}/component_action`
  - `/api/ui-tool/submit`
- [ ] Extract session routes → `transport/routes/session_routes.py`
  - `/api/sessions/list/{app_id}/{user_id}`
  - `/api/sessions/recent/{app_id}/{user_id}`
- [ ] Extract workflow routes → `transport/routes/workflow_routes.py`
  - `/api/workflows`
  - `/api/workflows/config`
  - `/api/workflows/{workflow_name}/transport`
  - `/api/workflows/{workflow_name}/tools`
  - `/api/workflows/{workflow_name}/ui-tools`
  - `/api/download/workflow-file`
- [ ] Extract upload routes → `transport/routes/upload_routes.py`
  - `/api/chat/upload`
  - `/api/chat/upload/{app_id}/{user_id}`
  - `_handle_chat_upload()` helper
- [ ] Extract WebSocket endpoint → `transport/routes/ws_routes.py`
  - `/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}`
  - `_auto_start_if_needed()` helper
- [ ] Create `transport/app_factory.py` with `create_app()` function
  - CORS middleware setup
  - Principal header middleware
  - Router mounting
  - Startup/shutdown lifecycle
- [ ] Update `shared_app.py` to thin shim: `app = create_app()`
- [ ] Verify `run_server.py` still works
- [ ] Verify all 24 endpoints still respond

---

### Phase 2 — Extract `ConnectionState` from `SimpleTransport.connections`

**Risk: Low** — Replaces untyped `dict` with typed dataclass. No behavior change.

**What changes:**
- New `transport/websocket/connection_state.py` with a `ConnectionState` dataclass
- `SimpleTransport.connections` values become `ConnectionState` instances instead of raw dicts
- `orchestration.py` accesses `state.agents`, `state.context`, `state.frontend_context` instead of `transport.connections[chat_id]['agents']`

**Checklist:**

- [ ] Create `transport/websocket/connection_state.py`
  - `ConnectionState` dataclass with: `websocket`, `workflow_name`, `app_id`, `user_id`, `agents`, `context`, `frontend_context`, `ws_id`, `metadata`
- [ ] Update `SimpleTransport.__init__` — `self.connections: Dict[str, ConnectionState]`
- [ ] Update `SimpleTransport.handle_websocket()` — create `ConnectionState` on connect
- [ ] Update all `self.connections[chat_id][key]` accesses in `handler.py` → attribute access
- [ ] Update `orchestration.py` `transport.connections[chat_id]['agents']` → `transport.connections[chat_id].agents` (3 locations)
- [ ] Update `shared_app.py` `simple_transport.connections` reads → attribute access
- [ ] Verify WebSocket flow works end-to-end

---

### Phase 3 — Extract `AG2EventAdapter` (AG2 → DomainEvent translation)

**Risk: Medium** — Changes the event processing boundary. Must preserve every field mapping.

**What changes:**
- New `engine/streaming/ag2_event_adapter.py` — pure function that maps AG2 event objects to normalized dicts/dataclasses
- `_stream_events()` event loop replaces inline type checks with `adapter.translate(ev)` calls
- AG2 event imports (`TextEvent`, `SelectSpeakerEvent`, etc.) move out of `_stream_events` into the adapter

**Checklist:**

- [ ] Define `DomainEvent` envelope (typed dict or dataclass):
  ```python
  @dataclass
  class DomainEvent:
      kind: str           # "text", "select_speaker", "input_request", "tool_call", "usage", "stream_chunk", "run_complete"
      agent: str | None
      content: Any
      metadata: dict
      raw_ag2_event: Any  # preserved for middleware that needs AG2-specific fields
  ```
- [ ] Create `engine/streaming/ag2_event_adapter.py`
  - `translate(ag2_event) -> DomainEvent` — covers all 7 event types:
    - `TextEvent` → `kind="text"`
    - `SelectSpeakerEvent` → `kind="select_speaker"`
    - `InputRequestEvent` → `kind="input_request"`
    - `FunctionCallEvent`/`ToolCallEvent` → `kind="tool_call"`
    - `UsageSummaryEvent` → `kind="usage"`
    - `StreamEvent` → `kind="stream_chunk"`
    - `RunCompletionEvent` → `kind="run_complete"`
  - All AG2 imports contained in this one file
- [ ] Update `_stream_events()` event loop to use `adapter.translate(ev)` then switch on `domain_event.kind`
- [ ] Verify: same payloads reach the frontend for every event type
- [ ] Verify: auto-tool structured output interception still works (receives translated events)

---

### Phase 4 — Extract `GroupChatExecutor` (AG2 launch + iteration) ✅ COMPLETE

**Risk: Medium** — Moves the AG2 execution call site. Must preserve resume vs. new-run logic.

**What changed:**
- New `engine/executor/__init__.py` — exports `GroupChatExecutor`, `PreparedRun`
- New `engine/executor/groupchat_executor.py` (893 lines) — owns agent building, pattern creation, AG2 invocation, context wiring, lifecycle before_chat
- `PreparedRun` dataclass (26 fields) holds everything the event pipeline needs post-launch
- `run_workflow_orchestration()` now creates `GroupChatExecutor`, calls `prepare_and_launch()`, then calls `_stream_events(run: PreparedRun)` for event iteration
- `_stream_events()` signature reduced from 16 parameters to `(run: PreparedRun)`; AG2 launch code removed
- `orchestration.py` shrank from 2427 → 1353 lines; no `autogen` agent imports remain

**Completed checklist:**

- [x] Create `engine/executor/__init__.py`
- [x] Create `engine/executor/groupchat_executor.py`
  - `GroupChatExecutor` class with `prepare_and_launch() -> PreparedRun`
  - `PreparedRun` dataclass as the boundary contract between executor and event pipeline
  - `_launch_ag2()` handles resume path (`prepare_group_chat → a_resume`) and new-run path (`a_run_group_chat`)
  - IOStream bridge setup contained here
  - All AG2 imports (`ConversableAgent`, `UserProxyAgent`, `a_run_group_chat`, `IOStream`, `ContextVariables`) contained here
- [x] Move 11 helper functions → executor: `_normalize_human_in_the_loop`, `_load_workflow_config`, `_resume_or_initialize_chat`, `_load_llm_config`, `_build_context_blocking`, `_create_agents`, `_ensure_user_proxy`, `_resolve_initiating_agent`, `_filter_agents_for_pattern`, `_convert_to_ag2_context`, `_create_ag2_pattern`
- [x] `_wire_derived_context()` static method for AG2 context provider registration
- [x] Update `run_workflow_orchestration()` — executor creates infra, `_stream_events(run)` replaces 16-param call
- [x] Clean up unused imports from orchestration.py (removed autogen, AG2PersistenceManager, DerivedContextManager, etc.)
- [x] Fixed pre-existing bug: `domain_ev.kind` referenced before assignment in event loop
- [x] Validated startup: `App created: MozaiksAI Runtime v5.0.0, Routes: 31`

---

### Phase 5 — Split `SimpleTransport` into focused modules

**Risk: Medium** — Changes class boundaries but preserves all behavior behind the same singleton.

**What changes:**
- `SimpleTransport` (2596 lines) → 4 focused classes composed inside a slim `SimpleTransport` facade
- External callers still use `SimpleTransport.get_instance()` — internal delegation is invisible

**Checklist:**

- [ ] Create `transport/websocket/connection_manager.py`
  - `ConnectionManager` class owns `connections: Dict[str, ConnectionState]`
  - Methods: `connect()`, `disconnect()`, `get_state()`, `broadcast()`, `_broadcast_to_websockets()`
  - Heartbeat task management
  - Pre-connection buffering
  - Backpressure / message queue logic
- [ ] Create `transport/websocket/message_router.py`
  - `MessageRouter` class handles inbound WS message type dispatch
  - All the `if msg_type == "..."` branches from `handle_websocket()` move here
  - Methods: `route(message, chat_id, ws_id)`
- [ ] Create `transport/websocket/input_handler.py`
  - `InputRequestHandler` owns `_input_request_registries`
  - Methods: `register_input_request()`, `submit_user_input()`, `register_orchestration_input_registry()`
  - Resume signal building
- [ ] Create `transport/websocket/event_sender.py`
  - `EventSender` owns `send_event_to_ui()` logic
  - Agent visibility filtering (`should_show_to_user`)
  - Trace downgrading
  - Event dispatcher envelope building
  - UI tool event bypass
- [ ] Refactor `SimpleTransport` to compose these 4 classes
  - Singleton pattern preserved
  - Public API unchanged: `send_event_to_ui()`, `handle_websocket()`, `submit_user_input()`, etc.
  - Each method delegates to the appropriate composed class
- [ ] Preserve `_handle_general_agent_exchange()` — stays in transport (it's a non-AG2 capability)
- [ ] Preserve `_handle_artifact_action()` — stays in transport
- [ ] Verify: WebSocket connect/disconnect cycle works
- [ ] Verify: Agent messages appear in UI
- [ ] Verify: Input requests round-trip (AG2 → frontend → submit → AG2 callback)
- [ ] Verify: UI tool events render and respond

---

### Phase 6 — Build Kernel Event Pipeline

**Risk: High** — Replaces the core event processing loop. This is the final structural change.

**What changes:**
- New `kernel/pipeline/` module with middleware chain
- `_stream_events()`'s 14 inline responsibilities become 5 middleware modules
- `run_workflow_orchestration()` creates a pipeline, feeds it `DomainEvent`s from the executor

**Checklist:**

- [ ] Create `kernel/pipeline/__init__.py`
- [ ] Create `kernel/pipeline/event_pipeline.py`
  - `EventPipeline` class:
    ```python
    class EventPipeline:
        def __init__(self, middleware: list[Middleware]):
            ...
        async def process(self, events: AsyncIterator[DomainEvent]) -> PipelineResult:
            async for event in events:
                for mw in self.middleware:
                    event = await mw.handle(event, context)
                    if event is None:  # middleware consumed it
                        break
    ```
- [ ] Create `kernel/pipeline/persistence_middleware.py`
  - Saves `text` and `tool_call` events to Mongo via persistence_manager
  - Handles seed message deduplication (initial message suppression)
- [ ] Create `kernel/pipeline/transport_middleware.py`
  - Forwards events to `transport.send_event_to_ui()` / `transport.send_chat_message()`
  - Synthetic `select_speaker` emission for UI thinking bubbles
- [ ] Create `kernel/pipeline/lifecycle_middleware.py`
  - `before_agent` / `after_agent` / `after_chat` / `on_context_change` trigger execution
  - Context snapshot diffing
- [ ] Create `kernel/pipeline/structured_output_middleware.py`
  - Auto-tool JSON extraction from text events
  - Pydantic schema validation
  - Schema retry via `gm.a_send` feedback injection
  - `structured_output_ready` event emission
- [ ] Create `kernel/pipeline/observability_middleware.py`
  - Agent turn perf recording
  - Verbose context diff logging
  - Token usage summary logging
- [ ] Update `run_workflow_orchestration()`:
  ```python
  executor = GroupChatExecutor(...)
  pipeline = EventPipeline([
      PersistenceMiddleware(persistence_manager),
      StructuredOutputMiddleware(structured_outputs),
      LifecycleMiddleware(lifecycle_manager),
      ObservabilityMiddleware(perf_mgr),
      TransportMiddleware(transport),
  ])
  result = await pipeline.process(executor.run(run_context))
  ```
- [ ] Delete `_stream_events()` function
- [ ] Verify: full HelloWorld workflow runs end-to-end
- [ ] Verify: structured output workflows validate correctly
- [ ] Verify: input request workflows pause and resume
- [ ] Verify: multi-agent handoff with SelectSpeakerEvent displays correctly
- [ ] Verify: tool calls render UI tools and receive responses
- [ ] Verify: token streaming (stream_chunk/stream_end) works

---

## 4. What Does NOT Change

- **Workflow YAML contracts** — No changes to `agents.yaml`, `orchestrator.yaml`, etc.
- **Frontend** — No changes to ChatPage.js or any React code
- **Database schema** — No changes to Mongo collections or persistence format
- **Auth system** — JWT dependencies, middleware, principals unchanged
- **Kernel dispatcher** — `UnifiedEventDispatcher` and ns_map unchanged
- **Public API surface** — All 24 HTTP endpoints keep same paths, params, responses
- **WebSocket protocol** — Same message types, same event shapes over the wire
- **AG2 version / config** — No AG2 library changes

---

## 5. Risk Mitigation

| Risk | Mitigation |
|---|---|
| Breaking the circular dependency too early | Phase 3 (adapter) and Phase 4 (executor) naturally eliminate it — orchestration no longer imports transport directly |
| `SimpleTransport.connections` shared state | Phase 2 types it first; Phase 5 moves it behind `ConnectionManager` methods |
| Losing event fields during translation | Phase 3 adapter preserves `raw_ag2_event` on every `DomainEvent`; middleware can access original fields |
| Schema retry loop needs AG2 `gm.a_send` | `StructuredOutputMiddleware` receives a `group_manager` reference via pipeline context — AG2 call contained in engine layer |
| Regression in auto-tool structured output flow | Phase 6 is last precisely because it's highest risk; all prior phases leave `_stream_events` working |

---

## 6. Measurement

After all 6 phases:

| Metric | Before | After |
|---|---|---|
| `orchestration.py` | 2448 lines | ~200 lines (thin `run_workflow_orchestration` that wires executor + pipeline) |
| `handler.py` | 2742 lines | ~300 lines (facade composing 4 modules) |
| `shared_app.py` | 1985 lines | ~50 lines (shim importing app factory) |
| Largest single module | 1073 lines (`_stream_events`) | ~200 lines (any single middleware) |
| AG2 imports in non-engine code | 3 files | 0 files (contained in `engine/`) |
| Circular imports | 1 (orchestration ↔ handler) | 0 (unidirectional flow) |
| Testable units | ~3 (integration only) | ~15 (each middleware, adapter, executor independently testable) |
