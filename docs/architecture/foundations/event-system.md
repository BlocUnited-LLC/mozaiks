# Event System

Mozaiks is event-driven, but not every event means the same thing.

The event system exists to let deterministic app behavior, AI workflow
orchestration, UI reactivity, notifications, and hosted product capabilities
cooperate without collapsing into one control plane.

## Core Rule

Event ownership follows the layer that owns the fact.

| Event family | Owner | Purpose | Kernel concern |
| --- | --- | --- | --- |
| `domain.*` | app module / app backend | durable business facts after deterministic mutations | no |
| `workflow.*` | workflow runtime | workflow lifecycle facts and workflow-level checkpoints | yes, as execution metadata |
| `runtime.*` | runtime substrate | internal orchestration, validation, fan-out, resume, and control state | yes |
| `chat.*` | runtime transport | live chat transcript and tool execution stream | yes |
| `artifact.*` | runtime or generator workflow | artifact lifecycle facts | yes for transport, no for business meaning |
| `ui.*` | app UI contract | primitive updates and client-side UI reactions | no |
| `notification.*` | platform host notification service | notification lifecycle | no |
| `platform.*` | product/platform layer | App Zero or hosted-platform product facts | no |
| `hosted.*` | hosted-only capability packs | paid hosted product capabilities | no |

The runtime may transport many event families. Transport is not ownership.

## Current frontend event model

The frontend currently has three canonical event lanes plus one compatibility
bridge:

| Lane | Current producer | Current consumer | Purpose |
| --- | --- | --- | --- |
| `chat.*` | runtime transport | `ChatPage` | transcript, lifecycle, tool-call transport |
| `chat.tool_call` | runtime UI tool transport | `ChatPage` + workflow UI renderer | interactive or artifact workflow surfaces |
| `ui.*` | workflow/page/platform UI emitters | `useAppEventBus` primitives | typed primitive reactions such as refresh, modal open, stat update |
| legacy `ui_tool_event` | older runtime emitters | `dynamicUIHandler` bridge | compatibility during migration to `chat.tool_call` |

The compatibility path still exists in `ChatPage`, but new interactive workflow
UI should follow the `chat.tool_call` transport path.

## Layer Responsibilities

### Runtime App

`mozaiksai/hosts/runtime.py` and `mozaiksai` own execution transport:

- WebSocket delivery
- chat stream events
- AG2 event stream handling
- runtime control events
- workflow execution checkpoints
- persistence of runtime session state
- transport envelopes such as `chat.text`, `chat.run_complete`, and
  `chat.tool_call`

The runtime must not define app business events such as invoices, bookings,
campaigns, payments, or app-specific status changes.

### Platform App

`mozaiksai/hosts/platform.py` owns app-host event integration:

- module action endpoints
- module event validation
- event ingress from app modules or external app backends
- workflow trigger resolution
- notification/subscription derivation
- page and admin surfaces that react to app state

The platform host connects app facts to workflow execution. It does not make
AI workflow stream events into app facts.

Current implementation:

- `ModuleExecutor` injects `ModuleContext.emit(...)` into module handlers.
- Module handlers emit canonical envelopes through the runtime dispatcher.
- `ModuleEventRouter` is registered by `mozaiksai/hosts/platform.py` after app modules are
  loaded.
- `ModuleEventRouter` reads each loaded module's `subscriptions.yaml` and
  `notifications.yaml`.
- Notification targets create platform notification intents and emit
  `notification.created`.
- Capability targets resolve against workflow `orchestrator.yaml` trigger
  declarations by matching `capability_id`.
- `mozaiksai/hosts/platform.py` indexes capability-triggered workflows from
  `workflows/*/orchestrator.yaml`, creates the routed chat session, and
  auto-starts background execution when transport is available.
- Capability resolution emits `platform.workflow_capability_started`.
- `/api/notifications/count` reads unread platform notification intents for the
  current principal.

Workflow-trigger resolution stays platform-owned: modules publish validated
`domain.*` or `hosted.*` events, and the platform host resolves them to
workflow sessions without module code importing workflow internals.

### Studio And Mozaiks Product

`mozaiksai/hosts/studio.py` and `mozaiksai/hosts/mozaiks.py` own product-layer events:

- build lifecycle
- app project lifecycle
- hosted collaboration
- marketplace
- hosted billing and revenue-share capabilities

These are product facts, not universal runtime assumptions.

App Zero's deterministic hosted product modules currently center on:

- `investor_marketplace`, which publishes `hosted.marketplace.*`
- `communications`, which publishes `hosted.communication.*`

Those events can drive Studio, admin, marketplace, notification, and hosted
collaboration behavior. They must not be treated as generic generated-app
events.

### Modules

Modules own deterministic app facts. A module action may publish `domain.*`
events only after it commits the corresponding state change.

Example:

```text
POST /api/modules/tasks/create
  -> tasks handler validates and saves task
  -> emits domain.tasks.task_created
  -> platform host resolves subscriptions and workflow triggers
```

### Workflows

Workflows own AI orchestration events, not app business facts.

A workflow may:

- emit `workflow.*` checkpoints
- emit `runtime.*` events through AG2 custom-event handling
- emit `ui.*` events to update primitive UI
- emit interactive UI surfaces through workflow UI tool transport, which the
  runtime currently serializes as `chat.tool_call`
- call a module action that then emits `domain.*`

A workflow should not directly invent durable domain facts unless it is calling
the app/module contract that owns that fact.

## Frontend handling

### 1. `chat.*` transport stream

`ChatPage` is the primary consumer of runtime transport events.

It handles:

- transcript stream events such as `chat.text`
- lifecycle events
- tool-call envelopes
- AG-UI state replay/snapshot events

The surface reducer observes that stream to keep layout and workflow state in
sync.

### 2. Workflow UI tool lane

Interactive workflow-owned UI is no longer just a conceptual `ui_tool_event`.

Current canonical runtime path:

```text
workflow tool
  -> transport.send_ui_tool_event(...)
  -> runtime emits kind=tool_call
  -> transport maps to chat.tool_call
  -> ChatPage inserts artifact/inline message state
  -> WorkflowUIRouter resolves workflow-scoped component
```

Key implications:

- workflow-owned artifact cards and interactive panels are not `ui.*` primitive
  events
- they are chat-session-scoped UI surfaces
- their display mode (`inline`, `artifact`, `view`) influences the frontend
  surface state machine

### 3. Typed `ui.*` primitive lane

Primitive UI events are a separate lane from workflow UI tools.

Current canonical frontend path:

```text
typed websocket envelope with type=ui.*
  -> useCoreWebSocket.ingestEvent(...)
  -> useAppEventBus
  -> primitive subscription by component_id or modal_id
```

This lane is for lightweight browser reactions:

- `ui.datatable.refresh`
- `ui.form.set_field`
- `ui.form.reset`
- `ui.stat.update`
- `ui.modal.open`
- `ui.modal.close`
- `ui.alert.show`

These events update existing surfaces. They do not mount workflow-owned React
artifact components.

### 4. Client-only routing lane

`routing.transition.*` is a local shell bus, not a runtime/domain event family.

It is used for transition UI:

- transition component emits `routing.transition.resolve`
- shell resolves navigation or chained transition state

Keep it separate from business and runtime events.

## Canonical Event Loop

```text
user action or integration
  -> module action
  -> deterministic state commit
  -> domain.* event
  -> platform host validates event
  -> subscriptions / notifications / workflow triggers resolve
  -> notification.created emitted when a notification rule matches
  -> workflow starts or resumes when configured
  -> runtime emits chat.*, runtime.*, workflow.*, artifact.* stream events
  -> UI renders stream and primitive updates
  -> workflow saves results through module actions when app state changes
  -> module emits new domain.* event if another durable fact became true
```

## Where Events Are Declared

| Contract | File |
| --- | --- |
| Module-published domain events | `modules/{module}/events.yaml` |
| Module event reactions | `modules/{module}/subscriptions.yaml` |
| Notification derivation | `modules/{module}/notifications.yaml` |
| Workflow trigger policy | `workflows/{workflow}/orchestrator.yaml` |
| Workflow runtime stream and AG2 custom event types | runtime code in `mozaiksai/core/events/` |
| Page primitive UI reactions | `ui/pages/*.yaml` and primitive component contracts |
| Hosted-only product events | hosted capability pack contracts |

## `orchestrator.yaml`

`orchestrator.yaml` is not the global event catalog.

It may declare:

```yaml
triggers:
  - event: domain.tasks.task_created
    action: run
    capability_id: tasks.review
```

That means the workflow wants to react to a domain event. The event itself is
owned by the module that publishes it.

## AG2 Custom Events

AG2 custom events are runtime execution events. Use them for AI/runtime facts:

- agent produced validated structured output
- decomposition was planned
- artifact became ready
- handoff was requested
- user input is required

Do not use AG2 custom events as app business events. If the AI result changes
app state, the workflow must call a module action and let the module publish the
domain event after the state commit.

## UI Events

`ui.*` events are client reaction commands, not durable app facts.

Examples:

- `ui.datatable.refresh`
- `ui.form.set_field`
- `ui.stat.update`
- `ui.modal.open`

These events may be emitted by a workflow or a page action. They update the live
browser surface and should not be used for business correctness.

`ui.*` is also distinct from workflow UI tool transport:

- use `ui.*` for primitive refresh/open/update actions
- use workflow UI tools for rendered interactive cards, forms, artifact panels,
  and workflow-specific React surfaces

## Hosted Capability Events

Hosted-only capabilities such as MozaiksPay use `hosted.*` or product-scoped
`platform.*` events. They plug into the platform/product layer as capability
packs. They must not become kernel assumptions.

## Collapse Rules

Do not:

- encode workflow names into domain event names
- use `chat.*` messages as durable business facts
- let modules publish `runtime.*` or `workflow.*`
- let hosted billing/revenue-share events leak into the runtime kernel
- let `orchestrator.yaml` become the app event catalog
- let UI events substitute for committed app state
- treat workflow UI tools and primitive `ui.*` events as the same lane
- let workflows publish notification records directly when a module event and
  notification rule should own the notification

## Related Documents

- [event-contracts.md](event-contracts.md)
- [workflow-architecture.md](workflow-architecture.md)
- [canonical-app-structure.md](canonical-app-structure.md)
