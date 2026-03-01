# Process and Event Map

**Status:** Source of truth  
**Last updated:** 2026-02-26

## Purpose

This document maps runtime processes, transports, and event flow for mozaiks.

It answers:

- what processes run
- which transports connect them
- where event categories originate and terminate
- how the three execution modes use different runtime paths

## Runtime Processes

### Process 1: Browser frontend

- Runtime: React (`packages/frontend/chat-ui/src`)
- State owner: `uiSurfaceReducer.js`
- Transports: WebSocket (stream), HTTP (REST)
- Primary consumer of streamed run events

### Process 2: Core runtime API server

- Factory: `build_runtime()` in `src/mozaiks/core/api/app.py`
- Hosts API routes, WebSocket stream endpoint, persistence calls, event publication
- Uses `RunStreamHub` for per-run pub/sub (`src/mozaiks/core/streaming/hub.py`)
- Uses orchestration runner through `AIEngineFacade`

### Process 3: Persistence store

- Event store and checkpoints via `src/mozaiks/core/persistence/`
- Stores runs, events, artifacts, checkpoint data

### Process 4: Graph store (optional)

- FalkorDB for graph injection/query use cases
- Accessed by runtime hooks defined by workflow configuration

### Process 5: External integrations (optional)

- Any third-party APIs or app-owned services called by tools or app routes

## Transport Map

| Transport | Direction | Typical payloads |
|---|---|---|
| WebSocket | Browser <-> Core runtime | run events, replay boundary, UI tool messages |
| HTTP | Browser <-> Core runtime | run create/resume, artifact/run queries, action endpoints |
| SQL/ORM | Core runtime -> Persistence | event append, checkpoint read/write, run/artifact reads |
| Graph query | Core runtime <-> Graph store | pre-turn injection and post-event mutation queries |
| Outbound HTTP | Core/runtime tools -> external services | app/tool specific integrations |

## Event Categories

### A) Run stream events (frontend-bound)

Examples:

- `process.*`
- `task.*`
- `chat.*`
- `artifact.*`
- `ui.tool.*`
- `transport.snapshot`
- `transport.replay_boundary`

Delivery path:

1. emitted during run lifecycle
2. persisted in event store
3. published via `RunStreamHub`
4. delivered over WebSocket

### B) Business dispatch events (backend-local)

Examples:

- `subscription.*`
- `notification.*`
- `settings.*`
- `entitlement.*`

Handled in-process by:

- `BusinessEventDispatcher`
- `EventRouter`
- YAML-driven dispatchers in `src/mozaiks/core/events/`

### C) Telemetry events (cross-cutting)

- Namespace: `telemetry.*`
- Can be persisted and consumed by scoring/analytics workflows
- Production wiring is app-dependent; contract is runtime-visible

## Core Runtime Entry Points

From `src/mozaiks/core/api/app.py`:

- `POST /v1/runs/create`
- `POST /v1/runs/{run_id}/resume`
- `POST /v1/runs/{run_id}/ui-tools/{ui_tool_id}/submit`
- `GET /v1/runs/{run_id}`
- `GET /v1/runs/{run_id}/events`
- `GET /v1/artifacts/{artifact_id}`
- `GET /v1/artifacts?uri=...&checksum=...`
- `WS /v1/runs/{run_id}/stream`

## End-to-End Flow (Mode 1)

1. Client calls `POST /v1/runs/create`
2. Core runtime creates run and emits `process.created`
3. Runner emits lifecycle/domain events (`process.*`, `task.*`, etc.)
4. Core persists each event and publishes to stream hub
5. Browser receives live stream over WebSocket
6. Resume path replays persisted events and sends `transport.replay_boundary`

## Artifact Bridge Across Modes

- Mode 1 (AI workflow) creates or updates artifacts.
- Mode 2 (triggered action) may update artifacts directly or via mini-run.
- Mode 3 (plain app) reads persisted artifacts through REST without AI orchestration.

This is the core cross-mode contract.

## Execution Modes and Process Usage

| Mode | Uses orchestration runtime | Uses stream WebSocket | Typical stores |
|---|---|---|---|
| Mode 1: AI Workflow | Yes | Yes | events, checkpoints, artifacts |
| Mode 2: Triggered Action | Optional | Optional | app state and artifacts |
| Mode 3: Plain App | No | No | app state and artifacts |

## Guardrails

1. Do not conflate execution modes with runtime layers.
2. `view` is a UI surface state, not a process or sandbox runtime.
3. `core` owns API, persistence, streaming, and dispatch; `orchestration` owns execution logic.

## Cross References

- [WORKFLOW_ARCHITECTURE.md](WORKFLOW_ARCHITECTURE.md)
- [EVENT_SYSTEM_ARCHITECTURE.md](EVENT_SYSTEM_ARCHITECTURE.md)
- [EVENT_TAXONOMY.md](EVENT_TAXONOMY.md)
- [UI_SURFACE_AND_LAYOUT_ARCHITECTURE.md](UI_SURFACE_AND_LAYOUT_ARCHITECTURE.md)
