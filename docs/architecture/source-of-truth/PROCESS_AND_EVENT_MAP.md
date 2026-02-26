# Process & Event Map

**Status**: Architectural Reference  
**Date**: February 23, 2026  
**Goal**: One document that shows every process, every transport, and every event — so you can point to any event and know exactly where it starts, where it goes, and what consumes it.

---

## Why This Document Exists

The system has multiple processes, multiple event transports, and multiple event categories. Other docs describe slices:

| Document | What It Covers | What It Leaves Out |
|----------|---------------|--------------------|
| [WORKFLOW_ARCHITECTURE.md](WORKFLOW_ARCHITECTURE.md) | YAML structure, MEP standard events, dispatchers | Process boundaries, transport mechanisms |
| [LEARNING_LOOP_ARCHITECTURE.md](LEARNING_LOOP_ARCHITECTURE.md) | Telemetry events, scoring, feedback loop | How telemetry physically flows between processes |
| [EVENT_SYSTEM_ARCHITECTURE.md](EVENT_SYSTEM_ARCHITECTURE.md) | Target event dispatch design (ChatDispatcher, BusinessDispatcher, Metering) | Which OS processes host which dispatcher |
| [EVENT_TAXONOMY.md](EVENT_TAXONOMY.md) | Canonical event domain types (Orchestration.RunStarted, etc.) | How they map to runtime event names (chat.*, subscription.*) |
| [DECLARATIVE_RUNTIME_SYSTEM.md](../events/DECLARATIVE_RUNTIME_SYSTEM.md) | How YAML drives notification/subscription/settings dispatchers | The full picture of what else runs in the same process |

This document provides the **process-level view**: the boxes, the arrows, and which events travel on which arrows.

---

## The 5 Processes (What Actually Runs)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│  ┌─────────────┐   WebSocket    ┌──────────────────────────────────────┐   │
│  │  PROCESS 1   │◄═════════════►│  PROCESS 2                           │   │
│  │  Browser     │               │  Core API Server (FastAPI)           │   │
│  │  (Frontend)  │   HTTP/REST   │                                      │   │
│  │              │◄─────────────►│  Contains:                           │   │
│  └─────────────┘               │  • WebSocket hub (RunStreamHub)      │   │
│                                 │  • Event dispatchers (all of them)   │   │
│                                 │  • AI Runner (in-process, async)     │   │
│                                 │  • Declarative loaders (YAML)        │   │
│                                 │  • REST endpoints                    │   │
│                                 └──────┬─────────┬────────┬───────────┘   │
│                                        │ SQL     │ Graph  │ HTTP          │
│                                        │         │        │               │
│                                        ▼         ▼        ▼               │
│                                 ┌──────────┐ ┌────────┐ ┌────────────┐   │
│                                 │PROCESS 3  │ │PROC 4  │ │ PROCESS 5  │   │
│                                 │PostgreSQL │ │FalkorDB│ │ Platform   │   │
│                                 │(DB)       │ │(Graph) │ │ API        │   │
│                                 └──────────┘ └────────┘ └────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Process 1: Browser (Frontend)

| Aspect | Details |
|--------|---------|
| **Runtime** | React app, single page |
| **State machine** | `uiSurfaceReducer.js` — manages layout mode, surface mode, artifact status |
| **Connection** | One WebSocket per run, HTTP for REST calls |
| **Receives events** | `chat.*`, `agui.*`, `artifact.*`, `transport.*`, `notification.toast` |
| **Sends events** | `user.input.submit`, `ui.tool.response`, `artifact.action` |
| **Key files** | `packages/frontend/chat-ui/src/state/uiSurfaceReducer.js`, `packages/frontend/shell/src/context/ChatUIContext.js`, `packages/frontend/shell/src/pages/ChatPage.js` |

### Process 2: Core API Server (FastAPI)

This is the **big one**. Almost everything runs here. It is a single Python process running FastAPI with async support.

| Aspect | Details |
|--------|---------|
| **Runtime** | FastAPI + uvicorn, single process, async |
| **Factory** | `create_app()` in `mozaiks_core/api/app.py` — only one |
| **Contains** | REST endpoints, WebSocket hub, event dispatchers, AI runner (async task), YAML loaders |
| **Outbound connections** | WebSocket → Browser, SQL → Postgres, Graph → FalkorDB, HTTP → Platform API |

**What lives inside this process:**

```
Core API Server (single Python process)
│
├── REST Endpoints
│   ├── POST /runs/create          → Create a new workflow run
│   ├── POST /runs/{id}/resume     → Resume from checkpoint
│   ├── GET  /runs/{id}            → Get run status/metadata
│   └── POST /tools/submit         → Submit UI tool response
│
├── WebSocket Endpoints
│   └── WS /runs/{id}/events       → Stream events for a run
│
├── RunStreamHub (WebSocket manager)
│   ├── Manages per-run WebSocket connections
│   ├── Sends outbound events to connected clients
│   └── Receives inbound messages (user input, tool responses)
│
├── Event Dispatch Layer (all in-memory, same process)
│   ├── ChatEventDispatcher      → AG2 events → chat.* → WebSocket
│   ├── AGUIEventAdapter         → chat.* → agui.* (optional dual emission)
│   ├── BusinessEventDispatcher  → subscription.*/notification.*/settings.*/entitlement.*
│   ├── MeteringEventCollector   → usage events → batched → Platform API
│   ├── EventRouter              → Routes events to correct handler/transport
│   └── TelemetryCollector       → telemetry.* events → persistence + scoring
│
├── Declarative Loaders (YAML → runtime config)
│   ├── ModularYAMLLoader        → Loads all YAML (monolithic or modular)
│   ├── EventDeclarationLoader   → events.yaml → validates event references
│   ├── NotificationDispatcher   → notifications.yaml → trigger/template/deliver
│   ├── SubscriptionDispatcher   → subscription.yaml → plans/limits/metering
│   ├── SettingsDispatcher       → settings.yaml → validate/store/emit
│   └── GraphInjectionLoader     → graph_injection.yaml → injection + mutation rules
│
├── AI Runner (AG2, in-process async task)
│   ├── Spawned as asyncio.Task when run starts
│   ├── AG2 GroupChat → run_iter() / a_run_iter()
│   ├── Emits AG2 native events
│   ├── AG2EventAdapter converts → chat.* events
│   └── Access to tools, FalkorDB, structured outputs
│
├── Persistence Layer
│   ├── EventStore          → Append events to Postgres
│   ├── CheckpointStore     → Save/load checkpoints to Postgres
│   └── ArtifactStore       → Save/load artifacts
│
└── AIEngineFacade (dynamic bridge to mozaiks_ai)
    ├── Dynamic import (no static import of mozaiks_ai)
    └── Resolves KernelAIWorkflowRunner at runtime
```

### Process 3: PostgreSQL

| Aspect | Details |
|--------|---------|
| **Role** | Persistent storage (passive — receives queries, never initiates) |
| **Contains** | Event log (append-only), checkpoints, run metadata, workflow registry |
| **Accessed by** | Core API Server via SQLAlchemy (TCP) |
| **Event relevance** | Stores events, does not emit or consume them |

### Process 4: FalkorDB

| Aspect | Details |
|--------|---------|
| **Role** | Graph database for agent memory / knowledge injection |
| **Contains** | Per-tenant graphs (`mozaiks_{app_id}`), pattern scores, agent config scores |
| **Accessed by** | Core API Server via FalkorDB client (TCP) |
| **When queried** | Before-turn (injection): read context for agents. After-event (mutation): update graph based on events. |
| **Event relevance** | Graph injection happens IN RESPONSE to events, not via events. The `GraphInjectionLoader` hooks into the agent turn cycle. |

### Process 5: Platform API (External Consumer Layer)

| Aspect | Details |
|--------|---------|
| **Role** | Commercial layer — billing, provisioning, generation |
| **Relationship** | Consumer of Core API, not part of Core |
| **Sends to Core** | HTTP: create runs, resume runs, submit tool responses |
| **Receives from Core** | HTTP: metering batches, telemetry summaries |
| **Event relevance** | Platform consumes `telemetry.run.summary` for generation quality scoring (Doll 2 in learning loop) |

---

## The 4 Transport Mechanisms (The Arrows)

Events move between processes using exactly **4 mechanisms**. If you know which transport an event uses, you know its delivery guarantees, latency, and failure modes.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        TRANSPORT MAP                                         │
│                                                                              │
│  Transport 1: WebSocket (bidirectional, real-time)                           │
│  ════════════════════════════════════════════════                             │
│  Browser ◄══════════════════════════════════════════► Core API               │
│  • Outbound: chat.*, agui.*, artifact.*, transport.*, notification.toast     │
│  • Inbound:  user.input.submit, ui.tool.response, artifact.action            │
│  • Lifetime: One connection per active run                                   │
│  • Failure:  Client reconnects → replay from checkpoint                      │
│                                                                              │
│  Transport 2: In-Memory Async (within single Python process)                 │
│  ═══════════════════════════════════════════════════════════                  │
│  AI Runner ──→ ChatEventDispatcher ──→ BusinessEventDispatcher               │
│  • All dispatchers, loaders, router live in same process                     │
│  • subscription.*, settings.*, notification.*, entitlement.*, system.*       │
│  • telemetry.* (collected, then persisted or forwarded)                      │
│  • Zero network latency, but coupled to process lifecycle                    │
│  • Failure: Process crash = all in-flight events lost (→ replay)             │
│                                                                              │
│  Transport 3: SQL (Core → PostgreSQL, synchronous writes)                    │
│  ═══════════════════════════════════════════════════════                      │
│  EventStore.append(), CheckpointStore.save()                                 │
│  • Events persisted as rows (append-only)                                    │
│  • Checkpoints persisted as blobs                                            │
│  • Transactional (ACID)                                                      │
│  • Failure: DB down = run cannot persist = run pauses                        │
│                                                                              │
│  Transport 4: HTTP (Core ↔ Platform API, batch/async)                        │
│  ═══════════════════════════════════════════════════                          │
│  MeteringEventCollector → Platform billing API                               │
│  TelemetryCollector → Platform scoring API                                   │
│  Platform → Core: POST /runs/create, POST /runs/{id}/resume                 │
│  • Batched metering: every 60s or 100 events                                 │
│  • Failure: Platform down = metering buffered, retried                       │
│                                                                              │
│  (Graph queries are NOT events — see §Note on FalkorDB below)                │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Note on FalkorDB

FalkorDB communication is **not event-based**. It's query-based:

- **Before-turn injection**: The `GraphInjectionLoader` runs a Cypher query before each agent turn and injects the result into the agent's context window. This is a synchronous read, not an event.
- **After-event mutation**: When certain events fire (e.g., `telemetry.run.summary`), a handler runs a Cypher mutation to update the graph. The trigger is an event, but the graph write is a query.

The distinction matters because FalkorDB is not an event consumer — it's a data store that gets queried in response to events.

---

## The 4 Event Categories (What Flows)

Every event in the system belongs to exactly one of these categories. If you know the category, you know the transport, the lifetime, and who cares about it.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      EVENT CATEGORY MAP                                      │
│                                                                              │
│  CATEGORY A: Chat Events (Frontend-bound, real-time)                         │
│  ─────────────────────────────────────────────                               │
│  Transport: WebSocket (outbound to browser)                                  │
│  Lifetime: Ephemeral (streamed, optionally persisted)                        │
│  Source: AI Runner (AG2) → AG2EventAdapter → ChatEventDispatcher             │
│  Consumer: Browser (uiSurfaceReducer, ChatPage)                              │
│  Events: chat.*, agui.* (derived), transport.*                               │
│                                                                              │
│  CATEGORY B: Business Events (Backend-only, in-memory)                       │
│  ─────────────────────────────────────────────────                            │
│  Transport: In-memory async (BusinessEventDispatcher)                        │
│  Lifetime: Handled immediately, optionally logged                            │
│  Source: Declarative dispatchers (Subscription, Notification, Settings)       │
│  Consumer: EventRouter → handlers (notification delivery, feature gates)     │
│  Events: subscription.*, notification.*, settings.*, entitlement.*, system.* │
│                                                                              │
│  CATEGORY C: Metering Events (Backend-only, batched to platform)             │
│  ──────────────────────────────────────────────────────                       │
│  Transport: In-memory → HTTP (batched to Platform API)                       │
│  Lifetime: Buffered, then flushed to platform                                │
│  Source: AI Runner + tool execution + artifact operations                     │
│  Consumer: MeteringEventCollector → Platform billing                         │
│  Events: chat.usage_*, artifact.*, storage.*, tool.*, workflow.*, api.*      │
│                                                                              │
│  CATEGORY D: Telemetry Events (Core-emitted, multi-destination)              │
│  ───────────────────────────────────────────────────────                      │
│  Transport: In-memory → SQL (persisted) + HTTP (to platform, batched)        │
│  Lifetime: Persisted permanently (source of truth for learning loop)         │
│  Source: Core runtime (post-run aggregation)                                 │
│  Consumer: EventStore (persistence), Platform (scoring), FalkorDB (mutation) │
│  Events: telemetry.*                                                         │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Category Quick Reference

| | A: Chat | B: Business | C: Metering | D: Telemetry |
|---|---|---|---|---|
| **Goes to browser?** | Yes | No (except notification.toast) | No | No |
| **Persisted?** | Optionally (EventStore) | Optionally (audit log) | Yes (platform) | Yes (EventStore + platform) |
| **Real-time?** | Yes (streaming) | Yes (in-process) | No (batched) | No (post-run) |
| **Survives crash?** | Only if persisted | No | Only if flushed | Only if persisted |
| **Who declares?** | Core (hardcoded) | YAML (declarative) | Core (hardcoded) | Core (hardcoded) |
| **Cross-reference** | [EVENT_SYSTEM_ARCHITECTURE §2.1-2.2](EVENT_SYSTEM_ARCHITECTURE.md) | [EVENT_SYSTEM_ARCHITECTURE §2.3](EVENT_SYSTEM_ARCHITECTURE.md), [DECLARATIVE_RUNTIME_SYSTEM](../events/DECLARATIVE_RUNTIME_SYSTEM.md) | [EVENT_SYSTEM_ARCHITECTURE §2.4](EVENT_SYSTEM_ARCHITECTURE.md) | [LEARNING_LOOP_ARCHITECTURE](LEARNING_LOOP_ARCHITECTURE.md) |

---

## Complete Event Catalog (Every Event, One Table)

This is the single source of truth for "what does this event name mean and where does it go?" Cross-references the taxonomy in [EVENT_TAXONOMY.md](EVENT_TAXONOMY.md) and the standard events in [WORKFLOW_ARCHITECTURE.md](WORKFLOW_ARCHITECTURE.md).

### A: Chat Events

| Event | Description | Source | Transport | Consumer |
|-------|-------------|--------|-----------|----------|
| `chat.text` | Full agent message | AG2 → AG2EventAdapter | WebSocket | Browser |
| `chat.print` | Streaming text chunk | AG2 → AG2EventAdapter | WebSocket | Browser |
| `chat.tool_call` | Tool invocation started | AG2 → AG2EventAdapter | WebSocket | Browser |
| `chat.tool_response` | Tool result returned | AG2 → AG2EventAdapter | WebSocket | Browser |
| `chat.input_request` | Awaiting user input (HITL) | AG2 → AG2EventAdapter | WebSocket | Browser |
| `chat.input_ack` | User input received | Core (on user submit) | WebSocket | Browser |
| `chat.input_timeout` | User input timed out | Core (timeout handler) | WebSocket | Browser |
| `chat.select_speaker` | Multi-agent speaker selected | AG2 → AG2EventAdapter | WebSocket | Browser |
| `chat.handoff` | Agent handoff | AG2 → AG2EventAdapter | WebSocket | Browser |
| `chat.structured_output_ready` | Structured output emitted | AG2 → AG2EventAdapter | WebSocket | Browser + AutoToolHandler |
| `chat.run_start` | Workflow started | Core (run lifecycle) | WebSocket | Browser |
| `chat.run_complete` | Workflow finished | AG2 → AG2EventAdapter | WebSocket | Browser + PackCoordinator |
| `chat.error` | Error occurred | Core (error handler) | WebSocket | Browser |
| `chat.orchestration.run_started` | Run lifecycle start | Core | WebSocket | Browser |
| `chat.orchestration.run_completed` | Run lifecycle complete | Core | WebSocket | Browser |
| `chat.orchestration.run_failed` | Run lifecycle failed | Core | WebSocket | Browser |
| `chat.orchestration.agent_started` | Agent turn start | Core | WebSocket | Browser |
| `chat.orchestration.agent_completed` | Agent turn complete | Core | WebSocket | Browser |

### A (derived): AG-UI Events

| Event | Description | Derived From | Transport | Consumer |
|-------|-------------|-------------|-----------|----------|
| `agui.lifecycle.RunStarted` | Run started | `chat.orchestration.run_started` | WebSocket | AG-UI clients |
| `agui.lifecycle.RunFinished` | Run finished | `chat.orchestration.run_completed` | WebSocket | AG-UI clients |
| `agui.lifecycle.RunError` | Run error | `chat.orchestration.run_failed` | WebSocket | AG-UI clients |
| `agui.lifecycle.StepStarted` | Agent step start | `chat.orchestration.agent_started` | WebSocket | AG-UI clients |
| `agui.lifecycle.StepFinished` | Agent step end | `chat.orchestration.agent_completed` | WebSocket | AG-UI clients |
| `agui.text.TextMessageStart` | Text stream start | `chat.print` (first chunk) | WebSocket | AG-UI clients |
| `agui.text.TextMessageContent` | Text stream chunk | `chat.print` | WebSocket | AG-UI clients |
| `agui.text.TextMessageEnd` | Text stream end | `chat.text` | WebSocket | AG-UI clients |
| `agui.tool.ToolCallStart` | Tool call start | `chat.tool_call` | WebSocket | AG-UI clients |
| `agui.tool.ToolCallEnd` | Tool call end | `chat.tool_response` | WebSocket | AG-UI clients |
| `agui.tool.ToolCallResult` | Tool result | `chat.tool_response` | WebSocket | AG-UI clients |

### A (transport): Transport Events

| Event | Description | Source | Transport | Consumer |
|-------|-------------|--------|-----------|----------|
| `transport.snapshot` | Full state snapshot for resume | Core (checkpoint) | WebSocket | Browser |
| `transport.replay_boundary` | End of replay, start of live | Core (resume path) | WebSocket | Browser |

### A (inbound): Browser → Core Events

| Event | Description | Source | Transport | Consumer |
|-------|-------------|--------|-----------|----------|
| `user.input.submit` | User message during HITL | Browser | WebSocket | Core → AI Runner |
| `ui.tool.response` | User answered UI tool | Browser | WebSocket | Core → AutoToolHandler |
| `artifact.action` | User clicked artifact action | Browser | WebSocket | Core → ArtifactHandler |

### B: Business Events

| Event | Description | Source | Transport | Consumer |
|-------|-------------|--------|-----------|----------|
| `subscription.plan_changed` | User upgraded/downgraded | SubscriptionDispatcher | In-memory | EventRouter → NotificationDispatcher |
| `subscription.limit_warning` | 80/90% of limit | SubscriptionDispatcher | In-memory | EventRouter → NotificationDispatcher |
| `subscription.limit_reached` | 100% of limit | SubscriptionDispatcher | In-memory | EventRouter → NotificationDispatcher + feature gate |
| `subscription.limit_exceeded` | Over limit (grace period) | SubscriptionDispatcher | In-memory | EventRouter → NotificationDispatcher + feature gate |
| `subscription.renewed` | Subscription renewed | SubscriptionDispatcher | In-memory | EventRouter |
| `subscription.canceled` | User canceled | SubscriptionDispatcher | In-memory | EventRouter → NotificationDispatcher |
| `subscription.trial_started` | Trial began | SubscriptionDispatcher | In-memory | EventRouter |
| `subscription.trial_ending` | Trial ending soon | SubscriptionDispatcher | In-memory | EventRouter → NotificationDispatcher |
| `subscription.trial_ended` | Trial expired | SubscriptionDispatcher | In-memory | EventRouter → feature gate |
| `subscription.payment_failed` | Payment issue | SubscriptionDispatcher | In-memory | EventRouter → NotificationDispatcher |
| `subscription.payment_success` | Payment succeeded | SubscriptionDispatcher | In-memory | EventRouter |
| `settings.updated` | User changed settings | SettingsDispatcher | In-memory | EventRouter |
| `settings.reset` | Settings reset to default | SettingsDispatcher | In-memory | EventRouter |
| `settings.imported` | Settings imported | SettingsDispatcher | In-memory | EventRouter |
| `settings.exported` | Settings exported | SettingsDispatcher | In-memory | EventRouter |
| `notification.sent` | Notification dispatched | NotificationDispatcher | In-memory | Delivery handlers |
| `notification.delivered` | Confirmed delivered | NotificationDispatcher | In-memory | Analytics |
| `notification.clicked` | User clicked/opened | NotificationDispatcher | In-memory | Analytics |
| `notification.dismissed` | User dismissed | NotificationDispatcher | In-memory | Analytics |
| `notification.failed` | Delivery failed | NotificationDispatcher | In-memory | Error handler |
| `entitlement.granted` | Feature access granted | SubscriptionDispatcher | In-memory | Feature gates |
| `entitlement.revoked` | Feature access revoked | SubscriptionDispatcher | In-memory | Feature gates |
| `entitlement.expired` | Time-limited access ended | SubscriptionDispatcher | In-memory | Feature gates |
| `system.error` | Runtime error | Error handlers | In-memory | Monitoring |
| `system.warning` | Runtime warning | Health checks | In-memory | Monitoring |
| `system.health_check` | Health check result | Health checks | In-memory | Monitoring |
| `system.rate_limited` | Rate limit triggered | Rate limiter | In-memory | Monitoring |

### C: Metering Events

| Event | Description | Source | Transport | Consumer |
|-------|-------------|--------|-----------|----------|
| `chat.usage_delta` | Token usage per LLM call | AG2 runtime | In-memory → HTTP (batched) | MeteringCollector → Platform billing |
| `chat.usage_summary` | Session token total | AG2 runtime | In-memory → HTTP (batched) | MeteringCollector → Platform billing |
| `artifact.created` | Artifact generated | Artifact tools | In-memory → HTTP (batched) | MeteringCollector → Platform billing |
| `artifact.downloaded` | Artifact downloaded | Artifact tools | In-memory → HTTP (batched) | MeteringCollector → Platform billing |
| `artifact.exported` | Artifact exported | Artifact tools | In-memory → HTTP (batched) | MeteringCollector → Platform billing |
| `storage.uploaded` | File uploaded | Storage tools | In-memory → HTTP (batched) | MeteringCollector → Platform billing |
| `storage.deleted` | File deleted | Storage tools | In-memory → HTTP (batched) | MeteringCollector → Platform billing |
| `tool.executed` | Tool/function called | Tool execution | In-memory → HTTP (batched) | MeteringCollector → Platform billing |
| `tool.failed` | Tool execution failed | Tool execution | In-memory → HTTP (batched) | MeteringCollector → Platform billing |
| `workflow.started` | Workflow session started | Core (run lifecycle) | In-memory → HTTP (batched) | MeteringCollector → Platform billing |
| `workflow.completed` | Workflow finished | Core (run lifecycle) | In-memory → HTTP (batched) | MeteringCollector → Platform billing |
| `workflow.failed` | Workflow failed | Core (run lifecycle) | In-memory → HTTP (batched) | MeteringCollector → Platform billing |
| `api.request` | External API call made | External API tools | In-memory → HTTP (batched) | MeteringCollector → Platform billing |

### D: Telemetry Events

| Event | Description | Source | Transport | Consumer |
|-------|-------------|--------|-----------|----------|
| `telemetry.run.started` | Run started (timing) | Core | In-memory → SQL | EventStore |
| `telemetry.run.completed` | Run finished (timing) | Core | In-memory → SQL | EventStore |
| `telemetry.run.failed` | Run failed (error info) | Core | In-memory → SQL | EventStore |
| `telemetry.run.abandoned` | User left mid-run | Core | In-memory → SQL | EventStore |
| `telemetry.run.summary` | **Key event**: post-run aggregation with outcome, timing, agent turns, tool results, HITL counts | Core (post-run) | In-memory → SQL + HTTP | EventStore + Platform scoring + FalkorDB mutation |
| `telemetry.agent.turn_summary` | Per-agent stats for a run | Core (post-run) | In-memory → SQL | EventStore |
| `telemetry.tool.outcome` | Per-tool stats for a run | Core (post-run) | In-memory → SQL | EventStore |
| `telemetry.hitl.requested` | Human-in-the-loop requested | Core | In-memory → SQL | EventStore |
| `telemetry.hitl.resolved` | HITL resolved (by user or timeout) | Core | In-memory → SQL | EventStore |
| `telemetry.journey.step_completed` | Pack orchestration journey step | Core | In-memory → SQL | EventStore |
| `telemetry.gate.evaluated` | Pack orchestration gate check | Core | In-memory → SQL | EventStore |
| `telemetry.injection.executed` | Graph injection query ran | GraphInjectionLoader | In-memory → SQL | EventStore |
| `telemetry.mutation.executed` | Graph mutation rule ran | GraphInjectionLoader | In-memory → SQL | EventStore |

---

## End-to-End Event Traces

These traces show what happens at every process boundary for common scenarios. Read top-to-bottom: each line is an event or action, indented lines happen inside a process.

### Trace 1: User Sends a Message (Happy Path)

```
BROWSER                          CORE API SERVER                      POSTGRES    FALKORDB
───────                          ───────────────                      ────────    ────────
                                
user.input.submit ──────────────►
                                 WebSocket receives message
                                 │
                                 ├─ GraphInjectionLoader:
                                 │  before-turn query ─────────────────────────────► Cypher READ
                                 │  ◄─────────────────────────────────────────────── context result
                                 │  inject context into agent prompt
                                 │
                                 ├─ AI Runner (AG2):
                                 │  agent processes message
                                 │  AG2 emits TextEvent
                                 │  AG2EventAdapter → chat.print
                                 │
◄──────────────────────────────── chat.print (streaming chunk)
                                 │
                                 │  AG2 emits TerminationEvent
                                 │  AG2EventAdapter → chat.text
                                 │
◄──────────────────────────────── chat.text (full message)
                                 │
                                 ├─ EventStore.append() ──────────────────────────► INSERT events
                                 │
                                 ├─ MeteringCollector:
                                 │  buffer chat.usage_delta
                                 │  (flushed later to Platform API)
                                 │
                                 ├─ GraphInjectionLoader:
                                 │  after-event mutation rules ────────────────────► Cypher WRITE
                                 │
                                 done
```

### Trace 2: Tool Call (Agent Uses a Tool)

```
BROWSER                          CORE API SERVER                      EXTERNAL API
───────                          ───────────────                      ────────────

                                 AI Runner (AG2):
                                 agent decides to call tool
                                 AG2 emits ToolCallEvent
                                 AG2EventAdapter → chat.tool_call
                                 │
◄──────────────────────────────── chat.tool_call
                                 │
                                 Tool executor runs tool function
                                 Tool calls external API (if needed) ──────────────► HTTP request
                                 ◄──────────────────────────────────────────────── response
                                 │
                                 AG2EventAdapter → chat.tool_response
                                 │
◄──────────────────────────────── chat.tool_response
                                 │
                                 MeteringCollector:
                                 buffer tool.executed
                                 │
                                 Telemetry:
                                 record telemetry.tool.outcome
```

### Trace 3: HITL (Human-in-the-Loop Input Request)

```
BROWSER                          CORE API SERVER                      POSTGRES
───────                          ───────────────                      ────────

                                 AI Runner (AG2):
                                 AG2 emits InputRequestEvent
                                 AG2EventAdapter → chat.input_request
                                 │
◄──────────────────────────────── chat.input_request
                                 │
                                 AI Runner suspends (await)
                                 CheckpointStore.save() ──────────────────────────► INSERT checkpoint
                                 │
                                 Telemetry:
                                 telemetry.hitl.requested
                                 │
[user types response]
                                 
user.input.submit ──────────────►
                                 WebSocket receives
                                 chat.input_ack → AG2 Runner
                                 │
◄──────────────────────────────── chat.input_ack
                                 │
                                 AI Runner resumes
                                 Telemetry:
                                 telemetry.hitl.resolved
```

### Trace 4: Subscription Limit Hit (Business Event Chain)

```
BROWSER                          CORE API SERVER
───────                          ───────────────

                                 MeteringCollector detects usage threshold
                                 │
                                 SubscriptionDispatcher:
                                 ├─ emit subscription.limit_warning
                                 │  │
                                 │  ├─ BusinessEventDispatcher routes to EventRouter
                                 │  │
                                 │  ├─ EventRouter → NotificationDispatcher
                                 │  │  │
                                 │  │  ├─ NotificationDispatcher:
                                 │  │  │  loads notifications.yaml
                                 │  │  │  matches trigger: subscription.limit_warning
                                 │  │  │  templates message
                                 │  │  │  emit notification.sent
                                 │  │  │  │
                                 │  │  │  └─ Delivery handler: push to browser
                                 │  │  │
◄──────────────────────────────── notification.toast (limit warning)
                                 │
                                 (no events cross to Postgres or FalkorDB for this flow)
```

### Trace 5: Run Completion + Telemetry (Learning Loop Feed)

```
BROWSER                          CORE API SERVER                      POSTGRES    FALKORDB    PLATFORM
───────                          ───────────────                      ────────    ────────    ────────

                                 AI Runner (AG2):
                                 AG2 emits TerminationEvent
                                 AG2EventAdapter → chat.run_complete
                                 │
◄──────────────────────────────── chat.run_complete
                                 │
                                 Run lifecycle:
                                 ├─ emit chat.orchestration.run_completed
◄──────────────────────────────── chat.orchestration.run_completed
                                 │
                                 ├─ agui (if enabled):
◄──────────────────────────────── agui.lifecycle.RunFinished
                                 │
                                 Post-run aggregation:
                                 ├─ Compute telemetry.run.summary
                                 │  (outcome, timing, agents, tools, hitl)
                                 │
                                 ├─ EventStore.append() ──────────────────────────► INSERT telemetry
                                 │
                                 ├─ TelemetryCollector:
                                 │  forward to Platform  ──────────────────────────────────────────► scoring API
                                 │
                                 ├─ GraphInjectionLoader:
                                 │  after-event mutation:
                                 │  "on telemetry.run.summary → update PatternScore" ─────► Cypher MERGE
                                 │
                                 ├─ MeteringCollector:
                                 │  flush buffered events ─────────────────────────────────────────► billing API
                                 │
                                 done
```

### Trace 6: Resume from Checkpoint (Replay + Live)

```
BROWSER                          CORE API SERVER                      POSTGRES
───────                          ───────────────                      ────────

POST /runs/{id}/resume ─────────►
                                 │
                                 CheckpointStore.load() ──────────────────────────► SELECT checkpoint
                                 ◄──────────────────────────────────────────────── checkpoint data
                                 │
                                 EventStore.get_events(after=checkpoint_seq) ──────► SELECT events
                                 ◄──────────────────────────────────────────────── missed events
                                 │
                                 Replay phase:
                                 ├─ Send all missed events to browser
◄──────────────────────────────── chat.print, chat.text, ... (replayed)
                                 │
                                 ├─ Send boundary marker
◄──────────────────────────────── transport.replay_boundary
                                 │
                                 Live phase:
                                 ├─ AI Runner resumes from checkpoint
                                 │  (new events stream normally)
◄──────────────────────────────── chat.print (live)
```

---

## Three Execution Modes and How They Map to Processes

The 6 traces above all describe **Mode 1: AI Workflow** — the full WebSocket-connected, event-streaming path. But a real app built on mozaiks has three execution modes, each using a different subset of the 5 processes and 4 transports.

### Mode Map

| Aspect | Mode 1: AI Workflow | Mode 2: Triggered Action | Mode 3: Plain App |
|--------|--------------------|--------------------------|--------------------|
| **Entry point** | Chat input (user types message) | Button click / API call | Page navigation |
| **Processes involved** | 1 + 2 + 3 + 4 + 5 (all) | 2 + 3 (± 4) | 2 + 3 (app DB) |
| **Transports used** | WebSocket + In-memory + SQL + HTTP | HTTP + (optional WS) + SQL | HTTP + SQL |
| **Event categories** | A + B + C + D (all) | A (if WS) + B (maybe) + C | None (or app-level) |
| **Duration** | Seconds to minutes | Sub-second to seconds | Instant (request/response) |
| **AI runner involved** | Yes (full AG2 orchestration) | Maybe (mini-run) or No (direct call) | No |
| **Artifact produced** | Often (new artifact) | Sometimes (artifact update) | Never (reads existing) |

### Process Usage by Mode

```
              Process 1     Process 2         Process 3      Process 4     Process 5
              (Browser)     (Core API)        (Postgres)     (FalkorDB)    (Platform)
              ─────────     ──────────        ──────────     ──────────    ──────────
Mode 1 (AI)    ◄══WS══►     Full stack         Events +       Graph         Metering +
               Chat panel   orchestration      Checkpoints    Injection     Telemetry
                            all dispatchers    Artifacts

Mode 2 (Trig)  Button       REST endpoint      Update DB      Maybe query   Maybe billing
               click ──►    or mini-run        Update artifact

Mode 3 (Plain) Page nav     REST endpoint      Read DB        ✗             ✗
               ──────────►  Serves data        Read artifacts
```

### How Mode 3 Reads Mode 1's Output

The most common cross-mode interaction: an AI workflow (Mode 1) creates an artifact, and a plain app page (Mode 3) reads it later.

```
MODE 1 (earlier):
    Browser ══WS══► Core API ──SQL──► PostgreSQL
         chat.text           EventStore.append(artifact.created, payload)
         artifact in panel

MODE 3 (later):
    Browser ──HTTP──► Core API ──SQL──► PostgreSQL
         GET /app/calendar    SELECT * FROM run_artifacts WHERE type='calendar'
         Renders table
```

The **artifact** is the data boundary between the two modes. Mode 1 writes it (via event persistence). Mode 3 reads it (via REST query). No AI is involved when Mode 3 runs — it's a standard database read surfaced by a standard REST endpoint.

### What This Means for Core API Server (Process 2)

Process 2 serves all three modes. Its internal components split by mode:

```
Core API Server (Process 2)
│
├── AI Workflow Subsystem (Mode 1)
│   ├── WS /runs/{id}/events
│   ├── POST /runs/create, POST /runs/{id}/resume
│   ├── RunStreamHub (WebSocket manager)
│   ├── Event dispatchers (Chat, Business, Metering, Telemetry)
│   ├── AI Runner (AG2 async task)
│   └── YAML Loaders (declarative config)
│
├── Data Access Subsystem (Mode 2 + Mode 3)
│   ├── GET /artifacts/{id}
│   ├── GET /artifacts?run_id=...&type=...
│   ├── GET /runs?workflow=...&status=...
│   └── Persistence ports (EventStorePort → read operations)
│
├── App Extension Points (Mode 3)
│   ├── App-owned REST endpoints (registered by the app, not core)
│   ├── App-owned database access (domain tables: posts, API keys, schedules)
│   └── Standard middleware (auth, CORS, logging)
│
└── Shared Infrastructure (All modes)
    ├── Auth (JWT validation, OIDC)
    ├── Persistence layer (EventStore, CheckpointStore)
    └── AIEngineFacade (dynamic bridge, only activated by Mode 1/2)
```

---

## Naming Convention Reconciliation

There are two naming systems in the docs. They are **not competing** — they serve different purposes.

### Runtime Event Names (What Code Uses)

```
chat.text, chat.tool_call, subscription.plan_changed, telemetry.run.summary
```

- Lowercase, dot-separated
- Used in: WebSocket payloads, dispatcher routing, YAML trigger declarations, metering
- Defined in: [EVENT_SYSTEM_ARCHITECTURE.md](EVENT_SYSTEM_ARCHITECTURE.md), [WORKFLOW_ARCHITECTURE.md](WORKFLOW_ARCHITECTURE.md)
- **This is what code emits and consumes.**

### Canonical Taxonomy Names (What the Domain Model Uses)

```
Orchestration.RunCompleted, Integration.ToolCallSent, Commerce.CreditsPurchased
```

- PascalCase, domain-prefixed
- Used in: [EVENT_TAXONOMY.md](EVENT_TAXONOMY.md), architectural reasoning, cross-domain analysis
- **This is the conceptual model for understanding what events mean.**

### How They Map

The canonical taxonomy is the **"what happened in the domain"** view. Runtime events are the **"what the code emits"** view. Both describe the same facts.

| Canonical (Domain) | Runtime (Code) | Category |
|---------------------|----------------|----------|
| `Orchestration.RunStarted` | `chat.orchestration.run_started` | A: Chat |
| `Orchestration.RunCompleted` | `chat.orchestration.run_completed` | A: Chat |
| `Orchestration.RunFailed` | `chat.orchestration.run_failed` | A: Chat |
| `Orchestration.TaskStarted` | `chat.orchestration.agent_started` | A: Chat |
| `Integration.ToolCallSent` | `chat.tool_call` | A: Chat |
| `Integration.ToolCallSucceeded` | `chat.tool_response` | A: Chat |
| `Perception.UserInputReceived` | `user.input.submit` | A: Chat (inbound) |
| `Commerce.CreditsConsumed` | `chat.usage_delta` | C: Metering |
| `Commerce.SubscriptionStarted` | `subscription.trial_started` | B: Business |
| `Commerce.SubscriptionCanceled` | `subscription.canceled` | B: Business |
| `Entitlement.Granted` | `entitlement.granted` | B: Business |
| `Settings.PreferenceSet` | `settings.updated` | B: Business |
| `Notification.Generated` | `notification.sent` | B: Business |
| `Evaluation.OutcomeVerified` | `telemetry.run.summary` | D: Telemetry |
| `Learning.PatternFound` | (future — P5) | D: Telemetry |
| `WorldState.AttributeSet` | (FalkorDB mutation — not an event, it's a query) | N/A |

**Rule**: Code always uses runtime names. Docs can use either, but must be explicit about which.

---

## Where Each Event Category Is Declared vs Consumed

This answers: "I'm writing a YAML file / Python handler / React component — which events do I care about?"

### If You're Writing a Workflow YAML

You declare and consume events in YAML. You only touch **Category B** events (business) and **custom events** you define.

```yaml
# events.yaml — declare custom events for your workflow
events:
  app.generated: ...         # custom event
  app.deployed: ...          # custom event

# notifications.yaml — consume events as triggers
triggers:
  - event: subscription.limit_warning  # Category B (standard)
  - event: app.generated               # custom (declared in events.yaml)

# subscription.yaml — consume events for metering
consumes:
  - event: app.generated               # custom
    resource: apps_generated
```

You **never** declare or consume Category A (chat), C (metering), or D (telemetry) events in YAML. Those are core-owned.

### If You're Writing a Python Handler (Core)

You deal with in-memory events within Process 2.

```python
# Consuming business events (Category B)
business_dispatcher.register("subscription.limit_reached", handle_limit)

# Emitting business events (Category B)
await business_dispatcher.emit("subscription.plan_changed", {...})

# Emitting telemetry (Category D) — core-owned
await telemetry_collector.emit("telemetry.run.summary", {...})

# You NEVER emit chat.* events directly — AG2EventAdapter does that
# You NEVER emit metering events directly — MeteringCollector does that
```

### If You're Writing a React Component (Frontend)

You consume **Category A** events from the WebSocket and dispatch actions to the reducer.

```javascript
// Consuming chat events (Category A)
ws.onmessage = (event) => {
  const { type, data } = JSON.parse(event.data);
  
  switch (type) {
    case 'chat.text':           dispatch({ type: 'APPEND_MESSAGE', ... }); break;
    case 'chat.print':          dispatch({ type: 'STREAM_CHUNK', ... }); break;
    case 'chat.input_request':  dispatch({ type: 'SHOW_INPUT', ... }); break;
    case 'transport.snapshot':  dispatch({ type: 'RESTORE_STATE', ... }); break;
    case 'notification.toast':  dispatch({ type: 'SHOW_TOAST', ... }); break;
    // ...
  }
};

// Sending events (inbound Category A)
ws.send(JSON.stringify({ type: 'user.input.submit', data: { text: userInput } }));

// You NEVER see Category B, C, or D events — they don't cross the WebSocket
```

---

## Connection to Other Architecture Docs

### This Doc → Workflow Architecture

[WORKFLOW_ARCHITECTURE.md](WORKFLOW_ARCHITECTURE.md) defines:
- The YAML files that declare and consume **Category B** events
- The **MEP** (Mozaiks Event Protocol) — the standard event namespaces
- The **dispatchers** that read YAML and emit events
- The **EventRouter** that wires events to handlers

This doc adds: which process those dispatchers run in (Process 2), what transport they use (in-memory), and how they connect to the other processes.

### This Doc → Learning Loop Architecture

[LEARNING_LOOP_ARCHITECTURE.md](LEARNING_LOOP_ARCHITECTURE.md) defines:
- The **telemetry.*** events (Category D) and their payloads
- How `telemetry.run.summary` feeds scoring and the feedback loop
- The 3 telemetry layers (Runtime, Generation, Quality Scoring)
- The nested-loop problem (which layer generates which events)

This doc adds: the physical flow of telemetry events — they're emitted in Process 2, persisted to Process 3 (Postgres), forwarded via HTTP to Process 5 (Platform), and used to trigger graph mutations in Process 4 (FalkorDB). See Trace 5.

### This Doc → Graph Injection

The graph injection system is **not event-based**. It operates as:
1. **Before-turn hook**: GraphInjectionLoader runs a Cypher query against FalkorDB (Process 4) and injects the result into the agent's context — this happens inside Process 2 as a synchronous operation within the agent turn cycle.
2. **After-event hook**: When specific events fire (e.g., `telemetry.run.summary`), a handler runs a Cypher mutation against FalkorDB — the trigger is an event, but the mutation is a query.

Graph injection sits at the intersection of Process 2 (where the hooks execute) and Process 4 (where the data lives).

### This Doc → Event Taxonomy

[EVENT_TAXONOMY.md](EVENT_TAXONOMY.md) defines the **canonical domain model** for events — the conceptual categories (Perception, Orchestration, Integration, etc.) and the envelope schema. See the naming reconciliation section above for how canonical names map to runtime names.

---

## Summary: The 4 Things to Remember

If the whole document is too much, remember exactly this:

**1. Five processes, one is the center.**  
Browser, **Core API Server**, PostgreSQL, FalkorDB, Platform. Almost everything happens in the Core API Server (Process 2). The others are storage or consumers.

**2. Four event categories, four transports.**  
Chat (WebSocket), Business (in-memory), Metering (in-memory → HTTP batch), Telemetry (in-memory → SQL + HTTP). If you know the category, you know the transport.

**3. Events don't cross categories.**  
A `chat.text` event never becomes a `subscription.plan_changed` event. A `telemetry.run.summary` is a different fact than `chat.run_complete`, even though they both relate to a run finishing. Categories are boundaries.

**4. Three execution modes, not just AI workflows.**  
Mode 1 (AI Workflow) uses all 5 processes and all 4 transports — it's the full path. Mode 2 (Triggered Action) uses Process 2 + 3 via HTTP — it's a button click that may or may not involve AI. Mode 3 (Plain App) uses Process 2 + 3 via HTTP with no AI at all — it reads artifacts and app data. Artifacts bridge the modes.

---

## Related Documents

| Document | Relationship |
|----------|-------------|
| [WORKFLOW_ARCHITECTURE.md](WORKFLOW_ARCHITECTURE.md) | How YAML declares and consumes events |
| [LEARNING_LOOP_ARCHITECTURE.md](LEARNING_LOOP_ARCHITECTURE.md) | How telemetry feeds quality scoring |
| [EVENT_SYSTEM_ARCHITECTURE.md](EVENT_SYSTEM_ARCHITECTURE.md) | Target event dispatch layer design |
| [EVENT_TAXONOMY.md](EVENT_TAXONOMY.md) | Canonical domain event types and envelope |
| [EVENT_SYSTEM_INVENTORY.md](../events/EVENT_SYSTEM_INVENTORY.md) | Current state (5+ overlapping systems) |
| [DECLARATIVE_RUNTIME_SYSTEM.md](../events/DECLARATIVE_RUNTIME_SYSTEM.md) | YAML-driven notification/subscription/settings |

