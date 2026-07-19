# Event Contracts

This document defines the canonical Mozaiks event contract.

Use this for generated apps, module contracts, workflow triggers, UI primitive
events, managed capability packs, and runtime transport.

## Canonical Envelope

Every durable or cross-layer event must normalize to this shape before it is
routed outside its owner:

```yaml
id: evt_01H...
type: domain.tasks.task_created
version: 1
occurred_at: "2026-04-23T18:00:00Z"
source:
  layer: module
  app_id: app_123
  module_id: tasks
  workflow_id: null
  capability_id: tasks.create
subject:
  type: task
  id: task_123
actor:
  type: user
  id: user_123
tenant:
  app_id: app_123
  tenant_id: tenant_123
correlation:
  correlation_id: corr_123
  causation_id: evt_previous
payload:
  task_id: task_123
  title: Draft contract
visibility: internal
```

## Required Fields

| Field | Required | Notes |
| --- | --- | --- |
| `id` | yes | globally unique event id |
| `type` | yes | namespaced event type |
| `version` | yes | integer schema version for this event type |
| `occurred_at` | yes | ISO timestamp |
| `source.layer` | yes | `module`, `workflow`, `runtime`, `platform`, `hosted`, or `ui` |
| `tenant.app_id` | yes | app scope |
| `payload` | yes | JSON-serializable event data |

Optional fields should be present when known: `subject`, `actor`,
`correlation`, `visibility`, and owner-specific source fields.

## Frontend transport note

The canonical event contract is about ownership and meaning, not one exact
frontend payload class.

Current frontend transport uses two distinct shapes:

- runtime/websocket envelopes such as `chat.text` and `chat.tool_call`
- direct typed envelopes such as `ui.datatable.refresh`

The transport shape may differ from the canonical durable envelope, but the
namespace ownership rules still apply.

## Namespace Rules

### `domain.*`

Owner:
module or app backend.

Allowed publishers:
module handlers and external app backends after deterministic state commits.

Allowed subscribers:
platform host, module reactions, notification service, workflow trigger
resolver, external integrations.

Allowed in generic modules:
yes.

Hosted-only:
no.

Example:

```yaml
type: domain.tasks.task_created
```

### `workflow.*`

Owner:
workflow runtime.

Allowed publishers:
workflow runtime and workflow tools.

Allowed subscribers:
runtime observers, workflow UI, platform host telemetry, workflow-local
automation.

Allowed in generic modules:
no.

Hosted-only:
no.

Example:

```yaml
type: workflow.app_generator.plan_ready
```

### `runtime.*`

Owner:
runtime substrate.

Allowed publishers:
runtime internals only.

Allowed subscribers:
runtime handlers, platform host observers, internal orchestration coordinators.

Allowed in generic modules:
no.

Hosted-only:
no.

Example:

```yaml
type: runtime.agent_output_validated
```

### `chat.*`

Owner:
runtime transport.

Allowed publishers:
runtime transport and AG2 stream handlers.

Allowed subscribers:
frontend chat UI and runtime observers.

Allowed in generic modules:
no.

Hosted-only:
no.

Example:

```yaml
type: chat.text
```

### `artifact.*`

Owner:
runtime or generator workflow.

Allowed publishers:
runtime artifact manager, generator workflows, promotion/build services.

Allowed subscribers:
Studio, artifact panels, build lifecycle services.

Allowed in generic modules:
only when the module owns an artifact domain.

Hosted-only:
no.

Example:

```yaml
type: artifact.ready
```

### `ui.*`

Owner:
app UI contract.

Allowed publishers:
workflow tools, page actions, platform host UI bridge.

Allowed subscribers:
browser primitives and shell components.

Allowed in generic modules:
modules may declare UI affordances, but module backend handlers should not
depend on UI events for correctness.

Hosted-only:
no.

Example:

```yaml
type: ui.datatable.refresh
payload:
  component_id: tasks_table
```

Current frontend delivery:

- typed websocket envelope with `type: ui.datatable.refresh`
- payload scoped by `component_id` or `modal_id`
- consumed by `chat-ui/src/ui/hooks/useAppEventBus.js`

These are primitive update commands, not workflow artifact surfaces.
Persistent page launches of workflow sessions are not `ui.*` events either; they
use declarative page actions with `action_type: workflow`.

### `notification.*`

Owner:
platform notification service.

Allowed publishers:
notification service after deriving notification lifecycle from domain events.

Allowed subscribers:
notification UI, delivery adapters, audit/observability.

Allowed in generic modules:
modules declare notification rules in `notifications.yaml`; they do not publish
`notification.*` directly.

Hosted-only:
no.

Example:

```yaml
type: notification.created
```

### `platform.*`

Owner:
platform/product layer.

Allowed publishers:
hosted product workspaces, platform host services, and Mozaiks product
services.

Allowed subscribers:
platform host, Studio, product analytics, hosted services.

Allowed in generic modules:
no.

Hosted-only:
sometimes. Hosted product modules may use this namespace; generated generic
apps should prefer `domain.*`.

Example:

```yaml
type: platform.app.created
```

### `hosted.*`

Owner:
managed capability packs.

Allowed publishers:
hosted-only product services such as MozaiksPay.

Allowed subscribers:
hosted platform services and product workflows.

Allowed in generic modules:
no.

Hosted-only:
yes.

Example:

```yaml
type: hosted.mozaikspay.revenue_share_recorded
```

Hosted product examples:

```yaml
type: hosted.marketplace.interest.recorded
source:
  layer: hosted
  module_id: investor_marketplace
```

### `mozaikspay.*`

Owner:
MozaiksPay managed capability.

Allowed publishers:
MozaiksPay-owned hosted modules that expose public MozaiksPay capability
lifecycle facts.

Allowed subscribers:
MozaiksPay hosted modules and product services.

Allowed in generic modules:
no.

Hosted-only:
yes.

Example:

```yaml
type: mozaikspay.self_hosted_app_registered
```

## Module Event Files

### `contracts/events.yaml`

Declares events the module may publish. Event types must use a valid namespace
owned by the emitting layer such as `domain.*`, `platform.*`, `hosted.*`, or
a bounded first-party managed-capability namespace such as `mozaikspay.*`.
For app modules, `module.yaml.actions[].emits` must reference event types
declared here and those emitted types are normally `domain.*`.

```yaml
schema_version: mozaiks.events.v1
events:
  - type: domain.tasks.task.completed
    version: 1
    description: Emitted when a task is completed.
    producer: tasks
```

### `contracts/reactions.yaml`

Declares module-owned reactions. This is the canonical reaction contract.
It uses `schema_version: mozaiks.reactions.v1`, root key `reactions`,
`event_type` for the incoming event, and nested `target.kind` for routing.

```yaml
schema_version: mozaiks.reactions.v1
reactions:
  - id: update_project_progress
    event_type: domain.tasks.task.completed
    description: Update project progress when a task completes.
    target:
      kind: handler
      handler_method: update_project_progress
```

Targets may be:

- `handler`
- `capability`
- `notification`
- `service_adapter`

Target field mapping:

- `target.kind: handler` uses `target.handler_method`
- `target.kind: capability` uses `target.capability_id`
- `target.kind: notification` uses `target.notification_id`
- `target.kind: service_adapter` uses `target.adapter` and
  `target.adapter_method`

Current platform support:

- `notification` targets are active. The platform host creates a notification
  intent and emits `notification.created`.
- `capability` targets are active. The platform host resolves
  `target.capability_id` against workflow `orchestrator.yaml` trigger
  declarations, creates the routed workflow session, and emits
  `platform.workflow_capability_started`.
- `handler` targets are active. `ModuleEventRouter` invokes
  `target.handler_method` on the module handler class and passes event payload
  fields as keyword arguments.
- `service_adapter` targets are active. `ModuleEventRouter` imports the
  app-owned adapter class declared by `target.adapter`, instantiates it, and
  calls `target.adapter_method` with the event payload and optional event
  metadata parameters.

Workflow starts must go through capability resolution or workflow trigger
resolution. Do not hardcode workflow internals in module code.

Service adapter starts must stay app-owned. Use them for provider or
hosted-product mechanics that belong under `app/services/adapters/...`; do not
use them for durable module state changes, public user actions, permissions,
subscription or wallet authority, or hosted-product entitlement policy.

### `contracts/notifications.yaml`

Declares notification derivation rules derived from events. It should not be
confused with `contracts/reactions.yaml`: notifications define delivery rules,
while reactions define event routing.

```yaml
schema_version: mozaiks.notifications.v1
notifications:
  - id: task_completed
    event_type: domain.tasks.task.completed
    channels: [in_app]
    audience:
      roles: [owner, admin]
    template:
      title: "Task completed"
      body: "{{task_id}}"
```

### Removed Subscription Contract

`contracts/subscriptions.yaml` is not a supported module contract.

- Do not author modules with `contracts/subscriptions.yaml`.
- Runtime rejects it so reaction routing has one source of truth.
- Generator and CLI output must use `contracts/reactions.yaml`.

### `orchestrator.yaml`

Declares workflow trigger policy only.

```yaml
triggers:
  - event: domain.tasks.task_created
    action: run
    capability_id: tasks.review
```

## Validation Rules

- `domain.*` events must be declared in the publishing module's `events.yaml`.
- `module.yaml.actions[].emits` must reference declared event types.
- `notifications.yaml` and `reactions.yaml` must reference declared or
  imported event types.
- Generic generated modules may not publish `platform.*`, `hosted.*`,
  `mozaikspay.*`, `runtime.*`, `workflow.*`, or `chat.*`.
- `service_adapter` reactions must declare both `target.adapter` and
  `target.adapter_method`, and must not also declare `handler_method`,
  `capability_id`, or `notification_id`.
- Runtime stream events must not be routed as app-domain facts.
- UI events must not be required for durable state correctness.
- Hosted-only events must not be loaded by runtime-only hosts.

## Runtime Mapping

Current runtime stream names may use existing transport payloads such as
`kind: text` or `type: chat.text`. These are transport representations. At the
contract boundary they map to the namespace rules above.

The canonical contract is the event envelope and namespace ownership, not a
specific Python class.

## Current frontend mappings

### Chat/runtime stream

Runtime transport currently maps `kind` values to websocket event names such as:

- `text` -> `chat.text`
- `run_complete` -> `chat.run_complete`
- `tool_call` -> `chat.tool_call`

This mapping is built in `UnifiedEventDispatcher.build_outbound_event_envelope(...)`.

### Workflow UI tool transport

Interactive workflow UI currently travels through `chat.tool_call`, not through
the typed `ui.*` primitive lane.

Current payload shape:

```yaml
type: chat.tool_call
data:
  tool_call_id: evt_123
  corr: evt_123
  tool_name: save_plan
  component_type: AppWorkbench
  workflow_name: AppGenerator
  interaction_type: ui_tool
  awaiting_response: true
  display: artifact
  display_type: artifact
  payload:
    workflow_name: AppGenerator
    interaction_type: ui_tool
    structured_output: {}
```

Key rules:

- `component_type` selects the workflow UI component
- `tool_call_id` is the primary UI interaction identifier
- `corr` is the wire-level response correlation key
- `workflow_name` should be available at the envelope level and inside `payload`
- `interaction_type` distinguishes `ui_tool`, `ui_surface`, `auto_tool`, and `input_request`
- `display` controls whether the surface is `composer`, `inline`, `artifact`, or `view`
- `corr` / event id is the response correlation key
- this lane is session-scoped and may persist artifact state for resume
- generic `chat.tool_call` events without `component_type` are not workflow UI
  mount requests

The frontend stores render metadata in `toolCall` message/artifact state, but
the transport contract is `chat.tool_call`.

For the exact runtime-to-frontend lifecycle and the AG-UI/CopilotKit mapping,
see [Tool Event Lifecycle](../../frontend/chat-ui/tool-event-lifecycle.md).

### Primitive UI transport

Primitive UI events remain direct typed `ui.*` envelopes:

```yaml
type: ui.modal.open
payload:
  modal_id: create-task
```

Those are ingested by the app event bus and routed by `component_id` or
`modal_id`, not by workflow UI component registry lookup.
