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
| `platform.*` | product/platform layer | hosted product facts | no |
| `hosted.*` | hosted-only capability packs | paid hosted product capabilities | no |

The runtime may transport many event families. Transport is not ownership.

## Current frontend event model

The frontend currently has three canonical event lanes:

| Lane | Current producer | Current consumer | Purpose |
| --- | --- | --- | --- |
| `chat.*` | runtime transport | `ChatPage` | transcript, lifecycle, tool-call transport |
| `chat.tool_call` | runtime UI tool transport | `ChatPage` + workflow UI renderer | interactive or artifact workflow surfaces |
| `ui.*` | workflow/page/platform UI emitters | `useAppEventBus` primitives | typed primitive reactions such as refresh, modal open, stat update |
New interactive workflow UI should follow the `chat.tool_call` transport path.

Response-required AG2 input interactions normalize onto `chat.tool_call` with
`interaction_type=input_request`. That is the only canonical browser-facing
lane for runtime-managed interactive input. In `chat-ui`, generic text input
requests use `display=composer` at the wire level, but the "Awaiting Workflow
Reply" banner is suppressed for bare conversational turns — those with no
explicit prompt and component type `UserInputRequest`. The banner only renders
when the agent explicitly provides a prompt or requests a named component other
than `UserInputRequest`. The underlying `tool_call_response` routing path
remains active regardless of banner visibility.

Native AG2 user handoffs can also pause without emitting a dedicated
`InputRequestEvent`. When that happens, the runtime now emits `chat.awaiting_reply`
to mark that the next composer message should resume the workflow. This is a
composer-turn pause signal, not a second workflow UI renderer lane.

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

`chat.run_complete` is slice-scoped. Clients and orchestrators must read its
`status` field:

- `1` means terminal workflow completion
- `0` means the workflow paused awaiting user input and remains resumable

When the pause came from a raw AG2 handoff rather than a response-bearing
`chat.tool_call`, the runtime also emits `chat.awaiting_reply`.

The runtime must not define app business events such as invoices, bookings,
campaigns, payments, or app-specific status changes.

### Platform App

`mozaiksai/hosts/platform.py` owns app-host event integration:

- module action endpoints
- module event validation
- event ingress from app modules or external app backends
- workflow trigger resolution
- notification/reaction derivation
- page and admin surfaces that react to app state

The platform host connects app facts to workflow execution. It does not make
AI workflow stream events into app facts.

Current implementation:

- `ModuleExecutor` injects `ModuleContext.emit(...)` into module handlers.
- Module handlers emit canonical envelopes through the runtime dispatcher.
- `ModuleEventRouter` is registered by `mozaiksai/hosts/platform.py` after app modules are
  loaded.
- `ModuleEventRouter` reads each loaded module's `contracts/reactions.yaml` and
  `notifications.yaml`.
- Notification targets create platform notification intents and emit
  `notification.created`.
- Capability targets resolve against workflow `orchestrator.yaml` trigger
  declarations by matching `capability_id`.
- `mozaiksai/hosts/platform.py` indexes capability-triggered workflows from
  `workflows/*/orchestrator.yaml`, creates the routed chat session, and
  auto-starts background execution when transport is available.
- Capability resolution emits `platform.workflow_capability_started`.
- Handler targets route the event payload directly to a named module action
  method, enabling module-to-module event-driven calls without HTTP indirection.

### Module Event/Reaction Contract

- `contracts/events.yaml` declares the event types a module may emit.
  Emitted event types in `module.yaml.actions[].emits` must be declared there.
- `contracts/reactions.yaml` is the canonical reaction contract. It uses
  `schema_version: mozaiks.reactions.v1`, root key `reactions`, `event_type`,
  and nested `target.kind`.
- `contracts/notifications.yaml` declares notification rules derived from
  events; it is not a reaction-routing file.
- `contracts/subscriptions.yaml` is not supported. Runtime rejects it so
  modules have one reaction-routing source of truth.

### Reaction target reference

```yaml
# contracts/reactions.yaml
schema_version: mozaiks.reactions.v1
reactions:
  - id: task_created_notify_owner
    event_type: domain.tasks.task_created

    # Target kind 1: notification
    # Creates a platform notification intent from notifications.yaml rule.
    target:
      kind: notification
      notification_id: task_created

  - id: my_workflow_trigger
    event_type: domain.tasks.task_created

    # Target kind 2: capability
    # Resolves capability_id against workflow orchestrator.yaml triggers.
    # Starts a workflow session when a matching trigger is found.
    target:
      kind: capability
      capability_id: tasks.review

  - id: my_handler_call
    event_type: domain.other_module.something_happened

    # Target kind 3: handler (module-to-module event routing)
    # Routes payload fields as keyword arguments to the target handler method.
    # The receiving handler method must live on this module's handler class.
    target:
      kind: handler
      handler_method: handle_task_created
```

**Handler target rules:**

- Use `target.kind: handler` with `target.handler_method`.
- `target.handler_method` names a method on this module's `backend/handler.py` class.
- The event payload is unpacked as keyword arguments into the handler method.
- The receiving method must be declared as an action in its `module.yaml` or be
  a documented reaction handler on the handler class.
- Handler targets are for deterministic module-to-module reactions. For
  AI-driven reactions, use a capability target to trigger a workflow instead.
- `/api/notifications/count` reads unread platform notification intents for the
  current principal.

### Provider-Neutral Example

```yaml
# app/modules/tasks/contracts/reactions.yaml
schema_version: mozaiks.reactions.v1
reactions:
  - id: update_project_progress
    event_type: domain.tasks.task.completed
    description: Update project progress when a task completes.
    target:
      kind: handler
      handler_method: update_project_progress
```

Workflow-trigger resolution stays platform-owned: modules publish validated
`domain.*`, `platform.*`, or `hosted.*` events, and the platform host resolves them to
workflow sessions without module code importing workflow internals.

### Studio And Mozaiks Product

`mozaiksai/hosts/studio.py` and `mozaiksai/hosts/mozaiks.py` may own product-layer events for hosted-only concerns such as build lifecycle, marketplace, collaboration, and billing.

These are product facts, not universal runtime assumptions. Product-layer modules publish `hosted.*` events and stay out of generated app bundles unless the hosted product explicitly includes them.

### Modules

Modules own deterministic app facts. A module action may publish `domain.*`
events only after it commits the corresponding state change.

Example:

```text
POST /api/modules/tasks/create
  -> tasks handler validates and saves task
  -> emits domain.tasks.task_created
  -> platform host resolves reactions and workflow triggers
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

Interactive workflow-owned UI is a `chat.tool_call` transport concern.

Current canonical runtime path:

```text
workflow tool
  -> transport.send_tool_call_event(...)
  -> runtime emits kind=tool_call
  -> transport maps to chat.tool_call
  -> ChatPage inserts `toolCall` artifact/inline message state
  -> WorkflowUIRouter resolves workflow-scoped component
```

AG2 `InputRequestEvent` now follows the same transport lane, using
`interaction_type=input_request`. Generic text reply defaults to
`display=composer`; `UserInputRequest` remains the fallback component identity
when a workflow explicitly wants inline input or the request cannot use the
composer (for example, password entry).

Key implications:

- workflow-owned artifact cards and interactive panels are not `ui.*` primitive
  events
- they are chat-session-scoped UI surfaces
- their display mode (`composer`, `inline`, `artifact`, `view`) influences the frontend
  surface state machine
- shipped shared workflow components should receive manifest-derived `workflow_primitive`
  and `ui_contract` metadata in the `chat.tool_call` payload

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
  -> reactions / notifications / workflow triggers resolve
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
| Module-published domain events | `modules/{module}/contracts/events.yaml` |
| Module event reactions | `modules/{module}/contracts/reactions.yaml` |
| Notification derivation | `modules/{module}/contracts/notifications.yaml` |
| Workflow trigger policy | `workflows/{workflow}/orchestrator.yaml` |
| Workflow runtime stream and AG2 custom event types | runtime code in `mozaiksai/core/events/` |
| Page primitive UI reactions | `ui/pages/*.yaml` and primitive component contracts |
| Hosted-only product events | hosted capability pack contracts |

Persistent pages may also launch workflow sessions through declarative page
actions, but that is a page-action contract, not an event-stream contract.
Those launches use runtime workflow ids and session routing, not `chat.tool_call`.

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
- [../../workflows/workflow-architecture.md](../../workflows/workflow-architecture.md)
- [../../app/canonical-app-structure.md](../../app/canonical-app-structure.md)
