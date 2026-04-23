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

## Layer Responsibilities

### Runtime App

`runtime_app.py` and `mozaiksai` own execution transport:

- WebSocket delivery
- chat stream events
- AG2 event stream handling
- runtime control events
- workflow execution checkpoints
- persistence of runtime session state

The runtime must not define app business events such as invoices, bookings,
campaigns, payments, or app-specific status changes.

### Platform App

`platform_app.py` owns app-host event integration:

- module action endpoints
- module event validation
- event ingress from app modules or external app backends
- workflow trigger resolution
- notification/subscription derivation
- page and admin surfaces that react to app state

The platform host connects app facts to workflow execution. It does not make
AI workflow stream events into app facts.

### Studio And Mozaiks Product

`studio_app.py` and `mozaiks_app.py` own product-layer events:

- build lifecycle
- app project lifecycle
- hosted collaboration
- marketplace
- hosted billing and revenue-share capabilities

These are product facts, not universal runtime assumptions.

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
- call a module action that then emits `domain.*`

A workflow should not directly invent durable domain facts unless it is calling
the app/module contract that owns that fact.

## Canonical Event Loop

```text
user action or integration
  -> module action
  -> deterministic state commit
  -> domain.* event
  -> platform host validates event
  -> subscriptions / notifications / workflow triggers resolve
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
| Page primitive UI reactions | `pages/*.yaml` and primitive component contracts |
| Hosted-only product events | hosted capability pack contracts |

## `orchestrator.yaml`

`orchestrator.yaml` is not the global event catalog.

It may declare:

```yaml
triggers:
  - event: domain.tasks.task_created
    action: run
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

## Related Documents

- [event-contracts.md](event-contracts.md)
- [workflow-architecture.md](workflow-architecture.md)
- [canonical-app-structure.md](canonical-app-structure.md)
