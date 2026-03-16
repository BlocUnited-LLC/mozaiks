# Mozaiks Core — Architecture & Design Brief

> **Scope:** Mozaiks Core as a standalone, enterprise-grade, production runtime for agentic applications.  Platform / NL generator concerns are noted only where they directly touch the core.

---

## 1. Repo Scan & Identification

### 1.1 AG2 Integration Surface

AG2 (autogen ≥ 0.11 / ag2[openai,lmm]) is the sole execution engine today.  Direct AG2 imports are confined to **three files**:

| File | What it imports |
|---|---|
| `mozaiksai/core/adapters/ag2_orchestration.py` | `OrchestrationPort` adapter — calls `a_run_group_chat_iter`, interprets `RunResult` |
| `mozaiksai/core/workflow/orchestration_patterns.py` | The heavy AG2 execution loop — `ConversableAgent`, `UserProxyAgent`, `a_run_group_chat_iter`, native AG2 event types (`TextEvent`, `InputRequestEvent`, `SelectSpeakerEvent`, `RunCompletionEvent`, `AfterWorksTransitionEvent`) |
| `mozaiksai/core/workflow/agents/factory.py` | Agent construction — `ConversableAgent` with prompt composition, tools, handoffs |

Supporting files (context adapter, persistence, event serialization) conditionally import AG2 types with safe fallbacks.

### 1.2 Event Bus / Director

There is **no single "director" process**.  The concept is distributed across three mechanisms:

| Mechanism | Location | Role |
|---|---|---|
| **UnifiedEventDispatcher** | `mozaiksai/core/events/unified_event_dispatcher.py` | In-process async pub/sub.  Dispatches `BusinessLogEvent`, `UIToolEvent`, `DomainEvent`, and string-keyed events (`chat.run_complete`, `chat.structured_output_ready`, etc.) to registered handlers. |
| **NATS / FastStream** | `mozaiksai/core/automation/nats_consumer.py` + `mozaikscore/core/automation_nats.py` | Cross-process substrate events.  `SubstrateEventEnvelope` flows from mozaikscore → NATS → mozaiksai `AutomationNatsConsumer` → `AutomationRouter` → `UniversalOrchestrator`. Optional; transport mode (`http` / `nats` / `dual`) is env-driven. |
| **UniversalOrchestrator** | `mozaiksai/core/orchestration/universal_orchestrator.py` | Event-to-workflow routing.  Receives structured or free-text triggers, classifies via `ChangeClassifier`, resolves to `workflow.run:<name>` or `workflow.resume:<name>` routes, dispatches through `SimpleTransport`. |

The cross-substrate bridge (`mozaikscore/core/cross_substrate_bridge.py`) relays events bidirectionally between mozaikscore's in-process `EventBus` and mozaiksai via HTTP POST or NATS.

### 1.3 Core Runtime vs. Platform / NL Generator Separation

| Directory | Role | Classification |
|---|---|---|
| `mozaiksai/` | Runtime: orchestration, transport, events, persistence, auth, observability | **Core runtime** |
| `mozaikscore/` | Application substrate: users, settings, notifications, subscriptions, analytics, module management | **Integration glue** (non-AI app services) |
| `platform/` | Declarative config: workflow YAML, automation routes, navigation, theme, subscriptions | **App-definition layer** (consumed by core, authored by humans or future NL generator) |
| `app/`, `chat-ui/` | Frontend shells | **UI surface** |
| `_cherry_pick_backup/` | Deprecated/legacy orchestration code | **Dead code** |

---

## 2. Runtime Architecture Description

### 2.1 How Agent Runs are Initiated and Orchestrated

```
User connects WebSocket
→ /ws/{workflow_name}/{app_id}/{chat_id}/{user_id}     (shared_app.py)
→ JWT auth + path binding validation
→ SimpleTransport.connect() registers the connection
→ User sends a message (JSON over WS)
→ MESSAGE_HANDLERS dispatch to correct handler
  ├── "start"   → WorkflowBridgeMixin._run_workflow_background()
  ├── "switch"   → switch active workflow
  ├── "resume"   → resume a paused chat
  └── "input"    → submit input for a human-in-the-loop pause
→ Handler creates an asyncio.Task that calls:
    AG2OrchestrationAdapter.run(RunRequest(...))
    → orchestration_patterns.run_workflow_orchestration()
      → loads workflow config (YAML)
      → builds agents via factory.py (ConversableAgent + tools + prompts)
      → creates AG2 pattern (DefaultPattern / RoundRobin / etc.)
      → calls a_run_group_chat_iter()
      → iterates events, serializes to UI payloads, sends via transport
→ On completion: emit "chat.run_complete" → dispatcher → PackCoordinator / JourneyOrchestrator
```

**REST API path (automation / headless):**
```
SubstrateEvent (NATS or HTTP POST to /api/substrate-events)
→ AutomationRouter.evaluate(envelope)
→ AutomationDecision (matched, ignored, invalid)
→ UniversalOrchestrator.dispatch_route("workflow.run:<name>", payload)
→ SimpleTransport.handle_user_input_from_api()
→ same background task pipeline as WebSocket
```

### 2.2 How the Event Bus is Used

**In-process (UnifiedEventDispatcher):**

| Event type | Producer | Consumer(s) |
|---|---|---|
| `chat.text`, `chat.tool_call`, `chat.input_request`, `chat.select_speaker` | AG2 event loop in orchestration_patterns.py (via `transport.send_event_to_ui()`) | WebSocket → frontend |
| `chat.structured_output_ready` | orchestration_patterns.py when an agent produces structured JSON | `AutoToolEventHandler`, `WorkflowPackCoordinator` |
| `chat.run_complete` | orchestration_patterns.py post-loop | `WorkflowPackCoordinator` (fan-in merge), `JourneyOrchestrator` (auto-advance) |
| `chat.usage_delta`, `chat.usage_summary` | orchestration_patterns.py | `SimpleTransport` (forward to UI), `UsageIngestClient` (control-plane measurement) |
| `runtime.universal_event`, `runtime.universal_text` | Any code that wants routed dispatch | `UniversalOrchestrator` |
| `BusinessLogEvent` | Anywhere via `emit_business_event()` | `BusinessLogHandler` (structured logging) |
| `UIToolEvent` | Agent tools that need UI interaction | `UIToolHandler`, forwarded via WebSocket |

**Cross-process (NATS / HTTP):**

| Subject pattern | Producer | Consumer |
|---|---|---|
| `mozaiks.substrate.events.{app_id}` | `SubstrateEventNatsPublisher` in mozaikscore | `AutomationNatsConsumer` in mozaiksai |
| HTTP `POST /api/substrate-events` | mozaikscore cross_substrate_bridge | shared_app.py route handler |
| HTTP `POST /__mozaiks/internal/relay-event` | mozaiksai | mozaikscore inbound bridge |

**Envelope schema** (SubstrateEventEnvelope):
```
event_id, event_type, timestamp,
tenant: { app_id, user_id, chat_id, run_id },
actor: { id, type },
source: { layer, component, transport, internal_event },
payload: { ... },
causation_id, correlation_id
```

### 2.3 State, Persistence, and CRUD Integration

- **Source of truth**: MongoDB (Motor async client).
  - `chat_sessions` — per-chat state: messages, context_variables, status, workflow_name, usage, structured outputs.
  - `WorkflowStats`, `WorkflowSummaries` — aggregate analytics.
  - `GeneralChatSessions` — non-workflow (general mode) conversations.
  - mozaikscore collections: users, settings, subscriptions, subscription_history, billing_history, enterprises, notifications, user_events.
- **CRUD updates in mozaikscore emit events** via the in-process `EventBus` (e.g., `subscription_updated`, `module_executed`).  The `cross_substrate_bridge` listens for events in the automation catalog and relays them to mozaiksai as `SubstrateEventEnvelope`s.
- **Agents react to non-AI changes** via the automation routing pipeline: event catalog entry → route match → `UniversalOrchestrator` dispatch → workflow run/resume.
- **Context variables** are persisted within the chat session document and reloaded on resume.  Derived context managers apply computed/external variable changes in real time and push updates to the UI via WebSocket.

---

## 3. Key Components & Responsibilities

### 3.1 Core Runtime

| Component | File(s) | Responsibility | Dependencies |
|---|---|---|---|
| **OrchestrationPort** | `ports/orchestration.py` | Engine-agnostic protocol: `run`, `resume`, `cancel`, `capabilities`. Defines `RunRequest`, `ResumeRequest`, `DomainEvent`, `RunResult`, `RunStatus`. | None (pure Python Protocol) |
| **AG2OrchestrationAdapter** | `adapters/ag2_orchestration.py` | AG2 implementation of the port.  Delegates to `run_workflow_orchestration()`. | AG2, `orchestration_patterns` |
| **orchestration_patterns** | `workflow/orchestration_patterns.py` | The AG2 execution loop.  Loads config, builds agents, runs `a_run_group_chat_iter`, iterates events, serializes to UI, emits domain events. | AG2, `agents/factory`, `execution/patterns`, event serialization, persistence, transport |
| **SimpleTransport** | `transport/simple_transport.py` | Singleton WebSocket connection manager + background AG2 task executor.  Mixins: `WebSocketProtocolMixin`, `WorkflowBridgeMixin`, `GeneralModeMixin`, `UIToolsMixin`. | WebSocket, AG2 `BaseEvent`, persistence |
| **UnifiedEventDispatcher** | `events/unified_event_dispatcher.py` | Central in-process event dispatch.  Handler-chain for typed events + string-keyed async listeners for domain events. | `AutoToolEventHandler`, `WorkflowPackCoordinator`, `JourneyOrchestrator`, `UsageIngestClient`, `UniversalOrchestrator` |
| **UniversalOrchestrator** | `orchestration/universal_orchestrator.py` | Event → workflow routing engine.  Resolves structured events and free-text via `ChangeClassifier` to `workflow.run:X` / `workflow.resume:X` targets. | `ChangeClassifier`, `SimpleTransport` |
| **AutomationRouter** | `automation/router.py` | Evaluates `SubstrateEventEnvelope`s against declarative route config (`platform/automations/routes.json`).  Returns `AutomationDecision`. | `automation/config`, `contracts`, `UniversalOrchestrator` |
| **AutomationNatsConsumer** | `automation/nats_consumer.py` | FastStream NATS subscriber.  Deserializes envelope → `AutomationRouter.dispatch()`. | FastStream, NATS, `AutomationRouter` |
| **WorkflowPackCoordinator** | `workflow/pack/workflow_pack_coordinator.py` | Fan-out / fan-in for mid-flight journeys (MFJ).  Spawns child chats, collects structured outputs, merges results, resumes parent. | `OrchestrationPort`, `MergeStrategy`, `MFJCompletionStore`, `MFJObserver` |
| **JourneyOrchestrator** | `workflow/pack/journey_orchestrator.py` | Auto-advance for global journeys (multi-step sequential/parallel workflows). | `WorkflowPackCoordinator`, pack config |
| **AG2PersistenceManager** | `data/persistence/persistence_manager.py` | MongoDB CRUD for chat sessions, real-time usage tracking, context variable persistence. | Motor, MongoDB |
| **WorkflowManager** | `workflow/workflow_manager.py` | YAML config cache, UI tool metadata registry, workflow discovery. | YAML, filesystem |
| **Agent Factory** | `workflow/agents/factory.py` | Builds `ConversableAgent`/`UserProxyAgent` from YAML with prompt composition, tools, hooks. | AG2 |
| **Auth stack** | `auth/` (6 files) | JWT validation, OIDC discovery, JWKS caching, FastAPI dependency injection, WebSocket auth. | PyJWT, HTTPX |
| **Multitenant** | `multitenant/app_ids.py` | `app_id` normalization, scope filter enforcement (`build_app_scope_filter`). | None |
| **Observability** | `observability/` | `PerformanceManager` (in-memory perf snapshots), `AG2RuntimeLogger` (AG2 logging bridge), `RealtimeTokenLogger` (token/cost accounting). | AG2 runtime_logging |

### 3.2 Integration Glue (mozaikscore)

| Component | File(s) | Responsibility |
|---|---|---|
| **core_app** | `mozaikscore/core_app.py` | FastAPI app (port 8001).  CORS, WebSocket, mounts all routes. |
| **EventBus** | `mozaikscore/core/event_bus.py` | Thread-safe in-process pub/sub for substrate-side events. |
| **CrossSubstrateBridge** | `mozaikscore/core/cross_substrate_bridge.py` | Bidirectional event relay (mozaikscore ↔ mozaiksai). |
| **ModuleManager** | `mozaikscore/core/module_manager.py` | Dynamic module discovery, loading, execution dispatch. |
| **NotificationsManager** | `mozaikscore/core/notifications_manager.py` | Multi-channel notification delivery (in-app, email, WebSocket). |
| **SubscriptionManager** | `mozaikscore/core/subscription_manager.py` | Plan-based access control, trial logic, Control Plane sync. |

### 3.3 Platform / Generator-Adjacent

| Component | Location | Responsibility |
|---|---|---|
| **Workflow YAML** | `platform/workflows/{name}/` | Declarative app definition: agents, orchestrator pattern, handoffs, context_variables, structured_outputs, hooks, tools, ui_config, _pack |
| **Automation config** | `platform/automations/` | `event_catalog.json` (known event types) + `routes.json` (event → workflow bindings) |
| **Platform config** | `platform/config/` | `ai.json`, `navigation_config.json`, `theme_config.json`, `module_registry.json`, etc. |
| **PlatformHookRegistry** | `mozaiksai/core/runtime/platform_hooks.py` | Entrypoint-based hook injection (`on_startup`, `chat_prereqs`, `chat_session_fields`, `workflow_ordering`) so platform layers can extend the runtime without modifying `shared_app.py`. |
| **RuntimeExtensions** | `mozaiksai/core/runtime/extensions.py` | Mount workflow-declared `api_router` and `startup_service` entrypoints. |

---

## 4. Event Bus / Director Design

### 4.1 What is the "Director"?

There is no single director process.  The director concept is a **pattern spread across three layers**:

1. **UnifiedEventDispatcher** — in-process pub/sub hub.  Receives events from the AG2 execution loop and dispatches to registered listeners (PackCoordinator, JourneyOrchestrator, AutoToolHandler, UsageIngest, UniversalOrchestrator).
2. **UniversalOrchestrator** — event-to-workflow router.  Classifies events by type or free-text, resolves routes from config, and dispatches to `SimpleTransport`.
3. **AutomationRouter + NATS consumer** — cross-substrate routing.  Evaluates enveloped events against declarative route config and dispatches through the UniversalOrchestrator.

### 4.2 Subscribe / Publish Mechanics

- **Publish**: `dispatcher.emit(event_type_str, payload_dict)` → fire-and-forget `create_task()` for each registered listener.
- **Subscribe**: `dispatcher.register_handler(event_type_str, async_callback)` during initialization.
- **Cross-process**: NATS subject pattern `mozaiks.substrate.events.{app_id}`, queue group `mozaiksai-automation`.  HTTP fallback to `POST /api/substrate-events`.
- **Backpressure**: WebSocket transport implements message queuing (max 100), pre-connection buffering (max 200), and heartbeat (120s).

### 4.3 Multi-Agent Coordination Patterns

| Pattern | Implementation |
|---|---|
| **Group chat (round-robin, auto, random, default)** | AG2 `a_run_group_chat_iter` with pattern factory (`execution/patterns.py`) |
| **Handoff (condition-based agent transitions)** | Declarative `handoffs.yaml` → `AgentTarget`, `RevertToUserTarget`, `TerminateTarget` |
| **Human-in-the-loop** | `RevertToUserTarget` handoff → `InputRequestEvent` → WebSocket → user submits → `resume` |
| **Fan-out / fan-in (MFJ)** | `WorkflowPackCoordinator` spawns child chats via `OrchestrationPort.run()`, collects `chat.structured_output_ready` events, merges via strategy, resumes parent via `OrchestrationPort.resume()` |
| **Sequential multi-step journeys** | `JourneyOrchestrator` listens for `chat.run_complete`, auto-advances to next step in the global pack graph |
| **Event-driven automation** | `SubstrateEventEnvelope` → `AutomationRouter` → `UniversalOrchestrator` → `workflow.run:X` |

### 4.4 Current Gaps

1. **No formal event schema registry.** Event kinds (`chat.text`, `chat.run_complete`, etc.) are string constants scattered across producers and consumers.  There is no central schema definition, making it easy to introduce typos or break contracts.
2. **No retry / idempotency for in-process events.** `UnifiedEventDispatcher.emit()` fires `create_task()` per listener with no retry, no deduplication, and only a `done_callback` for error logging.  If a listener fails, the event is lost.  (The cross-substrate `EventBus` in mozaikscore has 3-retry with exponential backoff, but the runtime dispatcher does not.)
3. **No dead-letter / observability pipeline for failed events.** Failed event deliveries are logged but not collected or alerted on.  There is no dead-letter queue for NATS messages either.
4. **AG2 events flow directly via `transport.send_event_to_ui()`**, bypassing the dispatcher for the hot path (text, tool calls, speaker selection).  This means the dispatcher's metrics undercount total event volume and handlers registered on the dispatcher cannot intercept UI-bound AG2 events.
5. **Tight coupling between orchestration_patterns and SimpleTransport.** The orchestration loop directly calls `transport.send_event_to_ui()` to push events to WebSocket, rather than emitting through the dispatcher and letting the transport subscribe.  This makes it hard to add alternative transports (SSE, gRPC) without modifying the execution loop.

---

## 5. AG2 Integration Model

### 5.1 Where Agents and Tools are Defined

- **Agents**: `platform/workflows/{name}/agents.yaml` — each agent has `prompt_sections`, `max_consecutive_auto_reply`, `auto_tool_mode`, `structured_outputs_required`.
- **Tools (backend)**: `platform/workflows/{name}/tools/` — Python modules discovered by `agents/tools.py`, bound at agent construction via `factory.py`.
- **Tools (UI)**: `platform/workflows/{name}/ui/` — component metadata registered in `WorkflowManager._ui_registry`.
- **Handoffs**: `platform/workflows/{name}/handoffs.yaml` — declarative condition-based transitions.
- **Orchestrator config**: `platform/workflows/{name}/orchestrator.yaml` — `workflow_name`, `max_turns`, `human_in_the_loop`, `startup_mode`, `orchestration_pattern`, `initial_message`, `initial_agent`.
- **Context variables**: `platform/workflows/{name}/context_variables.yaml` — typed declarations with sources (config, data_reference, data_entity, computed, state, external, file).
- **Structured outputs**: `platform/workflows/{name}/structured_outputs.yaml` — JSON schema for agent outputs.
- **Hooks**: `platform/workflows/{name}/hooks.yaml` — lifecycle triggers (`before_chat`, `after_chat`, `before_agent`, `after_agent`).

### 5.2 Multi-Agent Orchestration

AG2 group chat is the primary execution primitive.  The runtime supports four patterns via the pattern factory (`execution/patterns.py`):

- `DefaultPattern` — AG2's default speaker selection
- `AutoPattern` — automatic selection
- `RoundRobinPattern` — round-robin
- `RandomPattern` — random speaker

Handoff-based transitions (condition-based `AgentTarget`, `RevertToUserTarget`, `TerminateTarget`) override the default speaker selection logic and provide human-in-the-loop gating.

The `WorkflowPackCoordinator` layers on top for multi-workflow MFJ (mid-flight journey) patterns: a parent workflow can fan out to child workflows, wait for structured output, merge results, and resume.

### 5.3 AG2 Run ↔ Event Bus Connection

```
AG2 a_run_group_chat_iter()
  → yields native events (TextEvent, ToolCallEvent, InputRequestEvent, ...)
  → orchestration_patterns.py iterates
    → event_serialization.build_ui_event_payload() normalizes to dict
    → transport.send_event_to_ui(evt, chat_id) pushes to WebSocket
    → For select events: dispatcher.emit("chat.structured_output_ready", ...)
    → On loop completion: dispatcher.emit("chat.run_complete", ...)
    → On usage accounting: dispatcher.emit("chat.usage_summary", ...)
```

### 5.4 Anti-Patterns / Encapsulation Opportunities

1. **`orchestration_patterns.py` is too large and coupled.** It directly constructs the AG2 execution loop, iterates events, serializes them, calls the transport, manages persistence, and handles edge cases (resume, handoff, structured output).  This should be split into an event stream processor (iterate + serialize), a runtime event emitter (dispatch), and a persistence finalizer.
2. **AG2 `BaseEvent` import in `simple_transport.py`.** The transport layer imports `autogen.events.BaseEvent` for type checking, breaking the engine-agnostic boundary.  This check should move behind the adapter.
3. **Monkey-patching Autogen's FileLogger in `shared_app.py`.**  This tight coupling to AG2 internals is fragile across AG2 version upgrades.  It should be isolated into the adapter layer or the observability module.

---

## 6. Runtime Boundaries & Extension Points

### 6.1 Public Surface

| Surface | Mechanism | Key endpoints / files |
|---|---|---|
| **WebSocket API** | `/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}` | Real-time bidirectional agent communication |
| **REST API** (mozaiksai) | FastAPI routes in `shared_app.py` | `/api/chats/...` (CRUD), `/api/workflows/...` (config), `/api/substrate-events` (automation ingest), `/health/active-runs`, `/metrics/perf/...` |
| **REST API** (mozaikscore) | FastAPI routes in `mozaikscore/core/routes/` | `/api/notifications`, `/api/events`, `/api/push`, `/__mozaiks/admin/...` |
| **CLI** | `mozaiks build`, `mozaiks dev` | Web app build and Vite dev server |
| **YAML config** | `platform/workflows/{name}/` | Declarative agent, tool, handoff, context, output definitions |
| **JSON config** | `platform/config/`, `platform/automations/` | App config, automation event catalog and routes |
| **Environment variables** | `.env` / shell | Transport mode, NATS URL, auth, API keys, cache TTL, etc. |

### 6.2 What External Systems Talk To

- **UI clients** → WebSocket + REST (mozaiksai port 8000)
- **mozaikscore** → REST + NATS (cross-substrate bridge) → mozaiksai
- **Future NL generator / Mozaiks Platform** → would emit YAML workflow definitions into `platform/workflows/` and JSON config into `platform/config/` and `platform/automations/`
- **Control Plane** → `POST /api/internal/subscription/sync` on mozaikscore

### 6.3 Extension Points

| Extension | How |
|---|---|
| **Add new agents/tools** | Add a workflow directory under `platform/workflows/{name}/` with YAML + tool Python modules |
| **Add new event types** | Add to `platform/automations/event_catalog.json` + create a route in `routes.json` |
| **Add new orchestration patterns** | Implement in `execution/patterns.py` and reference from `orchestrator.yaml` |
| **Plug in different transport** | Implement the `SimpleTransport` interface (or write a new mixin); change env `MOZAIKS_AUTOMATION_TRANSPORT` for cross-substrate |
| **Plug in different model provider** | AG2 supports arbitrary LLM providers via `llm_config`; configured per-agent in YAML or via environment / MongoDB |
| **Add a second execution engine** | Implement `OrchestrationPort` protocol (new adapter alongside `ag2_orchestration.py`) |
| **Platform hooks** | Set `RUNTIME_PLATFORM_EXTENSIONS` env to inject startup, prereqs, session fields, and workflow ordering hooks |
| **Runtime extensions** | Define `runtime_extensions` in workflow YAML to mount API routers and startup services |

---

## 7. Architecture Diagram (Textual)

```
┌────────────────────────────────────────────────────────────────────────────┐
│                        CLIENTS / EXTERNAL                                  │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐ │
│  │ Web UI   │  │ Mobile App   │  │ Control Plane│  │ Future NL Gen    │ │
│  │ (chat-ui)│  │ (RN)         │  │ (subscriptions│  │ (Mozaiks Platform)│ │
│  └────┬─────┘  └──────┬───────┘  └──────┬───────┘  └───────┬───────────┘ │
│       │ WS+REST        │ WS+REST         │ REST              │ YAML/JSON  │
└───────┼────────────────┼─────────────────┼──────────────────┼────────────┘
        │                │                 │                  │
        ▼                ▼                 ▼                  ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                    MOZAIKS CORE (mozaiksai — port 8000)                    │
│                                                                            │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │  INGRESS LAYER                                                       │  │
│  │  ┌─────────────────┐  ┌──────────────────┐  ┌───────────────────┐  │  │
│  │  │ WebSocket        │  │ REST API          │  │ NATS Consumer      │  │  │
│  │  │ /ws/{wf}/{app}/  │  │ /api/chats/...    │  │ (FastStream)       │  │  │
│  │  │     {chat}/{user}│  │ /api/substrate-   │  │ mozaiks.substrate  │  │  │
│  │  │                  │  │   events          │  │   .events.{app_id} │  │  │
│  │  └───────┬──────────┘  └────────┬─────────┘  └────────┬──────────┘  │  │
│  │          │ MSG_HANDLERS          │                      │             │  │
│  └──────────┼───────────────────────┼──────────────────────┼─────────────┘  │
│             ▼                       ▼                      ▼               │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │  ROUTING & DISPATCH                                                  │  │
│  │                                                                      │  │
│  │  ┌──────────────────┐    ┌───────────────────┐                      │  │
│  │  │ SimpleTransport   │    │ AutomationRouter   │                      │  │
│  │  │ (WS + task mgmt)  │◄───│ (event_catalog +   │                      │  │
│  │  └───────┬──────────┘    │  routes.json)      │                      │  │
│  │          │                └────────┬──────────┘                      │  │
│  │          │                         │                                  │  │
│  │          ▼                         ▼                                  │  │
│  │  ┌──────────────────────────────────────────┐                        │  │
│  │  │       UniversalOrchestrator               │                        │  │
│  │  │  event_type → workflow.run:X              │                        │  │
│  │  │  free_text  → ChangeClassifier → route    │                        │  │
│  │  └──────────────────┬───────────────────────┘                        │  │
│  └──────────────────────┼────────────────────────────────────────────────┘  │
│                         ▼                                                  │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │  ORCHESTRATION LAYER                                                 │  │
│  │                                                                      │  │
│  │  ┌─────────────────────────┐                                        │  │
│  │  │  OrchestrationPort       │  ← engine-agnostic protocol            │  │
│  │  │  (run / resume / cancel) │                                        │  │
│  │  └───────────┬─────────────┘                                        │  │
│  │              │                                                        │  │
│  │              ▼                                                        │  │
│  │  ┌─────────────────────────┐    ┌───────────────────────────┐        │  │
│  │  │ AG2OrchestrationAdapter  │───►│ orchestration_patterns.py  │        │  │
│  │  │ (the only AG2-coupled   │    │ (AG2 execution loop)       │        │  │
│  │  │  entry point)           │    │ a_run_group_chat_iter()    │        │  │
│  │  └─────────────────────────┘    └──────────┬────────────────┘        │  │
│  │                                             │ events                   │  │
│  │                                             ▼                         │  │
│  │  ┌─────────────────────────────────────────────────────────────┐    │  │
│  │  │  UnifiedEventDispatcher                                      │    │  │
│  │  │  ├── chat.run_complete      → PackCoordinator, JourneyOrch   │    │  │
│  │  │  ├── chat.structured_output → AutoToolHandler, PackCoord     │    │  │
│  │  │  ├── chat.usage_summary     → UsageIngest, Transport         │    │  │
│  │  │  ├── runtime.universal_*    → UniversalOrchestrator          │    │  │
│  │  │  └── BusinessLogEvent       → Structured logging             │    │  │
│  │  └──────────────────────────────────────────────────────────────┘    │  │
│  │                                                                      │  │
│  │  ┌──────────────────────────────────────────────────────┐           │  │
│  │  │  WorkflowPackCoordinator (fan-out / fan-in / MFJ)     │           │  │
│  │  │  JourneyOrchestrator (global multi-step auto-advance) │           │  │
│  │  └──────────────────────────────────────────────────────┘           │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                            │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │  DATA & INFRASTRUCTURE                                               │  │
│  │  ┌─────────────────┐  ┌──────────────┐  ┌────────────────────────┐  │  │
│  │  │ MongoDB (Motor)  │  │ Auth (JWT/   │  │ Observability          │  │  │
│  │  │ chat_sessions    │  │  OIDC/JWKS)  │  │ PerformanceManager     │  │  │
│  │  │ WorkflowStats    │  │              │  │ AG2RuntimeLogger       │  │  │
│  │  │ GeneralChat...   │  │              │  │ RealtimeTokenLogger    │  │  │
│  │  └─────────────────┘  └──────────────┘  └────────────────────────┘  │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────┘
                         │
                         │ cross-substrate bridge
                         │ (HTTP POST / NATS)
                         ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                MOZAIKSCORE (app substrate — port 8001)                      │
│  ┌──────────┐ ┌──────────────┐ ┌─────────────┐ ┌──────────────────────┐  │
│  │ Director  │ │Notifications │ │Subscriptions│ │ EventBus + Bridge    │  │
│  │ (CRUD)   │ │ Manager      │ │ Manager     │ │ (→ NATS / HTTP)      │  │
│  └──────────┘ └──────────────┘ └─────────────┘ └──────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────┘
```

**Partially implemented / implied elements:**

- **gRPC / SSE transports** — not implemented; the architecture supports them via the port pattern but only WebSocket exists today.
- **Kafka / alternative brokers** — not implemented; NATS + FastStream is the only broker integration.  `faststream[nats]` is an optional dependency.
- **Dead-letter queue** — not implemented for either in-process or NATS events.
- **OpenTelemetry tracing** — declared as optional dependency (`opentelemetry-sdk`, `opentelemetry-exporter-otlp`) but not wired into the runtime.

---

## 8. Critical Feedback & Next Steps

### 8.1 Recommendations (Make Mozaiks Core Cleaner)

#### 1. Introduce a formal Event Schema Registry

Define all event kinds (`chat.text`, `chat.run_complete`, `chat.structured_output_ready`, etc.) in a central schema module — ideally as Pydantic models or TypedDicts with documented fields — rather than scattered string constants.  This enables compile-time validation, auto-documentation, and makes it safe to add new event types without grep-and-pray.

**Concrete step:** Create `mozaiksai/core/events/schemas.py` exporting typed event models per `kind`.  Update producers and consumers to import from it.

#### 2. Route all AG2 events through the UnifiedEventDispatcher

Currently, the hot path in `orchestration_patterns.py` calls `transport.send_event_to_ui()` directly, bypassing the dispatcher.  This creates a second event flow that can't be intercepted, metered, or replayed.

**Concrete step:** Replace direct `transport.send_event_to_ui()` calls with `dispatcher.emit("chat.text", payload)`, then have the transport subscribe as a listener.  This also decouples the execution loop from the transport implementation, enabling alternative transports (SSE, gRPC stream) by adding new subscribers.

#### 3. Split `orchestration_patterns.py` into focused modules

This file is the most complex in the codebase.  It handles AG2 execution setup, event iteration, serialization, persistence updates, performance tracking, resume logic, and structured output handling — all in one module.

**Concrete step:** Extract at least: (a) an `EventStreamProcessor` that iterates AG2 events and emits normalized domain events, (b) a `RunPersistenceFinalizer` that handles post-run DB updates, (c) keep the top-level `run_workflow_orchestration()` as a thin coordinator.

#### 4. Remove AG2 imports from the transport layer

`simple_transport.py` imports `autogen.events.BaseEvent` for `isinstance` checks.  This violates the engine-agnostic boundary established by `OrchestrationPort`.

**Concrete step:** Move type discrimination behind `event_serialization.py` or the adapter.  The transport should only receive dicts or `DomainEvent` objects — never raw AG2 types.

#### 5. Add retry + idempotency to UnifiedEventDispatcher

In-process `emit()` is fire-and-forget with no retry.  For critical events like `chat.run_complete` (which triggers fan-in resume) and `chat.usage_summary` (billing measurement), a listener failure is silent data loss.

**Concrete step:** Add configurable retry (with backoff) to `emit()` for handlers registered with a `critical=True` flag.  Add event-id deduplication for handlers that should be idempotent.

### 8.2 Strengths (Double Down)

#### 1. OrchestrationPort — Clean engine-agnostic boundary

The `OrchestrationPort` protocol with `RunRequest` / `RunResult` / `DomainEvent` is an excellent architectural move.  It enables future engine swaps (LangGraph, CrewAI, custom) without touching transport or orchestration code.  **Double down** by ensuring all AG2 types are fully contained behind this boundary (see recommendations 2 and 4).

#### 2. Declarative YAML workflow definitions

The `platform/workflows/{name}/` convention with `agents.yaml`, `handoffs.yaml`, `orchestrator.yaml`, `context_variables.yaml`, etc. provides a clean declarative layer that separates app definitions from runtime logic.  This is exactly the surface a future NL generator would target.  **Double down** by formalizing the YAML schemas (JSON Schema or Pydantic validators) so the generator and human authors get validation errors early.

#### 3. Multi-tenant isolation discipline

`app_id` scoping via `build_app_scope_filter()`, `coalesce_app_id()`, and `dual_write_app_scope()` is applied consistently across persistence, WebSocket auth, and event envelopes.  The `AutomationTenant` model enforces non-empty `app_id` with validators.  **Double down** by adding integration tests that verify cross-tenant isolation at the persistence and event layers.

#### 4. PlatformHookRegistry — Extension without modification

The hook registry pattern (`RUNTIME_PLATFORM_EXTENSIONS` env → bundle of `on_startup`, `chat_prereqs`, `chat_session_fields`, `workflow_ordering`) is a clean separation between the open-source runtime and proprietary platform layers.  **Double down** by documenting the hook contract as a first-class API and adding more hook points (e.g., `on_event`, `on_run_complete`).

#### 5. Cross-substrate event envelope design

The `SubstrateEventEnvelope` with tenant, actor, source, correlation/causation IDs, and strict Pydantic validation is a well-thought-out event contract.  The `AutomationRouter` with declarative `routes.json` makes event → workflow binding auditable and version-controllable.  **Double down** by extending this pattern to all runtime events (not just cross-substrate ones).
