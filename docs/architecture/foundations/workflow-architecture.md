# Mozaiks Core: Workflow Architecture

**Last updated:** 2026-03-12  
**Status:** Current architecture reference

Mozaiks is a runtime for hybrid applications where:

- AI workflows
- triggered actions
- modules/pages
- artifacts

coexist inside one app bundle.

---

## Scope

This document defines the workflow/runtime architecture in the current unified
repo.

Two axes are intentionally separate:

1. runtime layers in this repo
2. execution modes in consuming apps

Do not treat those as the same thing.

---

## Repository Mapping

| Architecture concept | Primary implementation paths |
|---|---|
| Engine-neutral runtime contracts | `mozaiksai/core/ports/` |
| Workflow runtime and orchestration support | `mozaiksai/core/workflow/`, `mozaiksai/core/orchestration/`, `mozaiksai/core/adapters/` |
| Transport and runtime event handling | `mozaiksai/core/transport/`, `mozaiksai/core/events/`, `shared_app.py` |
| Substrate/module/settings/notifications layer | `mozaikscore/core/` |
| Shared frontend runtime/surfaces | `chat-ui/src/` |
| App bundle inputs | `platform/` |

Import/ownership direction:

```text
platform bundle inputs -> runtime loaders -> execution/transport/substrate
```

And at the core engine boundary:

```text
runtime -> OrchestrationPort -> AG2 adapter
```

---

## Runtime Structure

### AI Runtime Layer (`mozaiksai`)

Owns:

- workflow execution
- workflow config loading
- AG2 adapter boundary
- workflow transport
- runtime event routing
- MFJ and journey orchestration support
- artifact attachments and workflow persistence support

Representative paths:

- `mozaiksai/core/ports/orchestration.py`
- `mozaiksai/core/adapters/ag2_orchestration.py`
- `mozaiksai/core/workflow/`
- `mozaiksai/core/transport/`
- `mozaiksai/core/events/`
- `shared_app.py`

### Substrate Layer (`mozaikscore`)

Owns:

- module management
- navigation/theme/settings/subscription config loading
- substrate event bus
- websocket push bridge for substrate events

Representative paths:

- `mozaikscore/core/director.py`
- `mozaikscore/core/module_manager.py`
- `mozaikscore/core/config_loader.py`
- `mozaikscore/core/event_bus.py`
- `mozaikscore/core/websocket_event_bridge.py`

### Shared Frontend Runtime (`chat-ui`)

Owns:

- chat page and UI surfaces
- mode/layout state
- navigation and shell state
- workflow selection
- rendering of inline/artifact UI tool components

Representative paths:

- `chat-ui/src/pages/ChatPage.js`
- `chat-ui/src/state/uiSurfaceReducer.js`
- `chat-ui/src/providers/NavigationProvider.jsx`
- `chat-ui/src/config/workflowConfig.js`
- `chat-ui/src/utils/resolveWorkflow.js`

---

## Workflow Inputs

Workflows are runtime inputs under `platform/workflows/`.

Per-workflow files:

- `orchestrator.yaml`
- `agents.yaml`
- `handoffs.yaml`
- `context_variables.yaml`
- `structured_outputs.yaml`
- `tools.yaml`
- `ui_config.yaml`
- `hooks.yaml`
- `_pack/workflow_graph.json`
- `tools/*.py`
- `ui/*`

Global workflow graph:

- `platform/workflows/_pack/workflow_graph.json`

Loader:

- `mozaiksai/core/workflow/workflow_manager.py`

---

## App-Level AI Bootstrap

Workflow selection and chat boot are now split cleanly:

- app-level AI bootstrap: `platform/config/ai.json`
- workflow-local execution startup: `orchestrator.yaml`

### `platform/config/ai.json`

Owns:

- `engine.framework`
- `chat.startup_mode`
- `workflows.entry_point`

### `orchestrator.yaml`

Owns workflow-local execution settings such as:

- `workflow_name`
- `max_turns`
- `startup_mode` (`AgentDriven`, `UserDriven`, `BackendOnly`)
- `initial_message`
- `initial_agent`

These are different concepts and should not be conflated.

---

## Entry Point Resolution

Each app may designate at most one default workflow in
`platform/config/ai.json` under `workflows.entry_point`.

The backend projects that into `/api/workflows` as `entry_point: true` for the
matching workflow.

Frontend resolution chain:

1. explicit workflow
2. backend-projected entry point
3. singleton workflow auto-select
4. null

This is frontend boot metadata only.
It does not affect runtime orchestration semantics like MFJ or universal routing.

---

## Three Execution Modes

### Mode 1: AI Workflow

- full workflow execution
- uses orchestration runtime
- usually uses WebSocket stream

### Mode 2: Triggered Action

- bounded operation or mini-run
- may or may not use the full workflow runtime
- may use HTTP only or HTTP + stream

### Mode 3: Plain App

- module/page interaction without workflow execution
- typically uses substrate/module APIs

Artifacts bridge these modes.

---

## AG-UI Boundary

AG-UI, when used, is a protocol concern for frontend/workflow stream
interoperability.

It is not the place to model:

- subscriptions
- settings
- notifications
- app shell config
- business/commercial policy

Those remain runtime or substrate concerns.

---

## Artifact Bridge

Artifacts are the shared contract between AI and non-AI surfaces.

Typical lifecycle:

1. workflow creates or updates artifact
2. artifact is persisted
3. module/page reads artifact
4. triggered action or follow-up workflow mutates artifact
5. UI re-renders from persisted state

---

## Guardrails

1. Do not treat workflows as the whole app.
2. Do not treat app-level AI config as workflow-local execution config.
3. Do not reimplement AG2-native semantics in core without a proven gap.
4. Do not put product-specific builder meaning into the runtime just because the
   builder is the current hard use case.

---

## Cross References

- [core-product-app-bundle-boundary.md](core-product-app-bundle-boundary.md)
- [event-system-architecture.md](event-system-architecture.md)
- [process-and-event-map.md](process-and-event-map.md)
- [app-bundle-declaratives.md](app-bundle-declaratives.md)

