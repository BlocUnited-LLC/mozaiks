# Event Contracts

This document defines the canonical Mozaiks event contract.

Use this for generated apps, module contracts, workflow triggers, UI primitive
events, hosted capability packs, and runtime transport.

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

## Namespace Rules

### `domain.*`

Owner:
module or app backend.

Allowed publishers:
module handlers and external app backends after deterministic state commits.

Allowed subscribers:
platform host, module subscriptions, notification service, workflow trigger
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
App Zero, platform host services, Mozaiks product services.

Allowed subscribers:
platform host, Studio, product analytics, hosted services.

Allowed in generic modules:
no.

Hosted-only:
sometimes. App Zero product modules may use this namespace; generated generic
apps should prefer `domain.*`.

Example:

```yaml
type: platform.app.created
```

### `hosted.*`

Owner:
hosted capability packs.

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

## Module Event Files

### `events.yaml`

Declares events the module may publish.

```yaml
schema_version: mozaiks.events.v1
events:
  - type: domain.tasks.task_created
    version: 1
    producer: tasks
    subject:
      type: task
      id_path: payload.task_id
    payload_schema:
      type: object
      required: [task_id, title]
      properties:
        task_id: { type: string }
        title: { type: string }
```

### `subscriptions.yaml`

Declares module-owned reactions.

```yaml
schema_version: mozaiks.subscriptions.v1
subscriptions:
  - id: task_created_notify_owner
    event_type: domain.tasks.task_created
    target:
      kind: notification
      notification_id: task_created
```

Targets may be:

- `handler`
- `capability`
- `notification`

Workflow starts must go through capability resolution or workflow trigger
resolution. Do not hardcode workflow internals in module code.

### `notifications.yaml`

Declares notification derivation rules.

```yaml
schema_version: mozaiks.notifications.v1
notifications:
  - id: task_created
    event_type: domain.tasks.task_created
    channels: [in_app]
    audience:
      roles: [owner, admin]
    template:
      title: "Task created"
      body: "{payload.title}"
```

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
- `notifications.yaml` and `subscriptions.yaml` must reference declared or
  imported event types.
- Generic generated modules may not publish `platform.*`, `hosted.*`,
  `runtime.*`, `workflow.*`, or `chat.*`.
- Runtime stream events must not be routed as app-domain facts.
- UI events must not be required for durable state correctness.
- Hosted-only events must not be loaded by runtime-only hosts.

## Runtime Mapping

Current runtime stream names may use existing transport payloads such as
`kind: text` or `type: chat.text`. These are transport representations. At the
contract boundary they map to the namespace rules above.

The canonical contract is the event envelope and namespace ownership, not a
specific Python class.
