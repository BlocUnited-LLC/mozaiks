# Process and Event Map

**Status:** Current architecture reference  
**Last updated:** 2026-03-12

---

## Purpose

This document maps the live runtime processes, transports, and event flows in
the current repo.

It answers:

- what processes are actually running
- what transports connect them
- where the event channels originate and terminate

---

## Runtime Processes

### Process 1: Frontend shell

- Runtime: `chat-ui/src/`
- Primary state owners:
  - `chat-ui/src/state/uiSurfaceReducer.js`
  - `chat-ui/src/providers/NavigationProvider.jsx`
- Consumes:
  - workflow/runtime stream events
  - substrate websocket push events

### Process 2: AI runtime server

- Primary entrypoint: `shared_app.py`
- Key runtime paths:
  - `mozaiksai/core/transport/`
  - `mozaiksai/core/events/`
  - `mozaiksai/core/workflow/`
  - `mozaiksai/core/adapters/`
- Owns:
  - workflow execution
  - workflow transport
  - runtime event dispatch
  - workflow/session APIs

### Process 3: Substrate/core server components

- Primary paths:
  - `mozaikscore/core/director.py`
  - `mozaikscore/core/module_manager.py`
  - `mozaikscore/core/event_bus.py`
  - `mozaikscore/core/websocket_event_bridge.py`
- Owns:
  - module routes
  - settings/notifications/subscriptions
  - substrate event bus and websocket push

### Process 4: Persistence layer

- Workflow/chat persistence:
  - `mozaiksai/core/data/persistence/`
- Substrate config/state/data:
  - `mozaikscore/core/database.py`
  - manager-specific persistence paths

### Process 5: External integrations

- third-party APIs called by tools, module handlers, or app routes

---

## Transport Map

| Transport | Direction | Typical payloads |
|---|---|---|
| WebSocket | Frontend <-> AI runtime | workflow stream events, UI tool events, replay-safe runtime envelopes |
| HTTP | Frontend <-> AI runtime | chat/session/workflow APIs, input submission, component actions |
| HTTP | Frontend <-> mozaikscore | navigation/theme/module/settings/subscription APIs |
| WebSocket push | mozaikscore -> frontend | notifications, settings, subscription, module-level substrate events |
| DB/persistence | runtime/substrate -> persistence | chat sessions, event history, artifacts, module/state data |
| Outbound HTTP | runtime/tools -> external services | tool/service integrations |

---

## Runtime Entry Surfaces

### AI runtime

Representative endpoints in `shared_app.py`:

- `POST /api/chats/{app_id}/{workflow_name}/start`
- `GET /api/chats/{app_id}/{workflow_name}`
- `GET /api/sessions/list/{app_id}/{user_id}`
- `GET /api/sessions/recent/{app_id}/{user_id}`
- `POST /chat/{app_id}/{chat_id}/{user_id}/input`
- `POST /chat/{app_id}/{chat_id}/component_action`
- `GET /api/workflows`
- `GET /api/workflows/config`
- `GET /api/workflows/{workflow_name}/transport`
- `GET /api/workflows/{workflow_name}/tools`
- `GET /api/workflows/{workflow_name}/ui-tools`
- `WS /ws/{workflow_name}/{app_id}/{chat_id}/{user_id}`

### Substrate/core

Representative endpoints in `mozaikscore/core/director.py`:

- `/api/navigation-config`
- `/api/navigation`
- `/api/theme-config`
- `/api/settings-config`
- module and subscription-related substrate routes

---

## Event Categories

### A. Workflow/runtime stream events

Examples:

- `process.*`
- `task.*`
- `chat.*`
- `artifact.*`
- `ui.tool.*`
- `transport.*`
- `runtime.*`

Flow:

1. workflow execution emits normalized runtime facts
2. dispatcher routes to listeners
3. transport sends replay-safe frontend envelopes

### B. Substrate/business events

Examples in current code:

- `subscription_updated`
- `notification_created`
- `settings_updated`
- `module_executed`
- `theme_changed`

Flow:

1. mozaikscore manager publishes to `event_bus`
2. in-process subscribers react
3. websocket bridge optionally pushes to users

### C. Cross-cutting telemetry/usage signals

Examples:

- usage summary events
- observability/business log events

These may flow through runtime dispatch, logging, or ingest paths depending on
the subsystem.

---

## Mode Usage Across Processes

| Mode | Uses AI runtime | Uses workflow WebSocket | Uses substrate/module APIs |
|---|---|---|---|
| Mode 1: AI Workflow | Yes | Yes | Often |
| Mode 2: Triggered Action | Sometimes | Sometimes | Often |
| Mode 3: Plain App | No | No | Yes |

Artifacts and module pages bridge these modes.

---

## Guardrails

1. Do not equate the frontend shell with the workflow runtime.
2. Do not equate mozaikscore event bus traffic with workflow stream traffic.
3. Do not document retired endpoints or old repo paths as current.
4. Keep workflow-local execution config separate from app-level shell/AI config.

---

## Cross References

- [workflow-architecture.md](workflow-architecture.md)
- [event-system-architecture.md](event-system-architecture.md)
- [event-taxonomy.md](event-taxonomy.md)
- [app-bundle-declaratives.md](app-bundle-declaratives.md)

