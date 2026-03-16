# Event Taxonomy

This document defines the canonical event taxonomy for Mozaiks.

It covers both the app substrate and the AI runtime, but it keeps their event
families distinct.

## Design Principles

- events are immutable facts
- event names describe what happened, not what should happen
- event ownership follows layer boundaries
- workflow names do not appear inside domain event types
- tenant scope and causation must be explicit

## Canonical Envelope

All durable events should use this envelope.

```json
{
  "event_id": "uuid",
  "event_type": "domain.fact",
  "timestamp": "ISO8601",
  "tenant": {
    "app_id": "app_123",
    "user_id": "user_123",
    "chat_id": null,
    "run_id": null
  },
  "actor": {
    "id": "system|user|agent|integration",
    "type": "system|user|agent|integration"
  },
  "source": {
    "layer": "substrate|automation|runtime|frontend|integration",
    "component": "component_name",
    "transport": "http|nats|ws|internal"
  },
  "payload": {},
  "causation_id": null,
  "correlation_id": "stable-business-or-run-correlation-key"
}
```

## Event Families

| Family | Owner | Purpose |
| --- | --- | --- |
| App domain events | app bundle and substrate | business facts such as entity changes |
| Shared substrate events | substrate core | settings, notifications, subscriptions, module activity |
| Automation control events | AI runtime | route and session control facts |
| Workflow runtime events | AI runtime | run, task, chat, artifact, UI tool lifecycle |
| Telemetry events | platform and observability | monitoring and learning signals |

## 1. App Domain Events

These are app-specific business facts.

Examples:

- `crm.lead.created`
- `booking.request.approved`
- `writers_room.brief.updated`
- `catalog.item.archived`

Rules:

- emitted after the mutation commits
- owned by the app substrate
- never include workflow names
- safe to route through NATS or HTTP ingress

## 2. Shared Substrate Events

These are reusable substrate facts owned by the platform.

Examples:

- `settings.updated`
- `notification.created`
- `notification.read`
- `subscription.changed`
- `module.executed`

These are still domain events, but they describe shared platform services
rather than app-specific business objects.

## 3. Automation Control Events

These are low-frequency facts about automation and session control.

Examples:

- `control.plan_created`
- `control.prerequisites_required`
- `control.execution_batch_started`
- `control.transfer_requested`

These do not replace domain events. They describe what the AI-side control
plane decided after domain events were processed.

## 4. Workflow Runtime Events

These are the live workflow and chat stream families.

Examples:

- `process.started`
- `process.completed`
- `task.failed`
- `chat.message_appended`
- `artifact.updated`
- `ui.tool.requested`

These are not the right contract for substrate automation triggers.

## Naming Rules

### Lowercase dot notation

Use lowercase dot notation only:

- `crm.lead.created`
- `settings.updated`
- `process.completed`

### Facts, not commands

Good:

- `booking.request.approved`

Bad:

- `workflow.run.approval_concierge`
- `start_booking_workflow`

### Domain-first naming

For substrate events, prefer:

- `<bounded_context>.<aggregate>.<fact>`

Examples:

- `crm.lead.created`
- `writers_room.session.scheduled`

### Shared service naming

For shared platform services, prefer:

- `settings.updated`
- `notification.created`
- `subscription.renewed`

## Correlation Rules

Use `correlation_id` to bind related facts.

Examples:

- business object id
- process id
- external webhook id

Use `tenant` fields to prevent cross-app or cross-user leakage.

## Cause and Effect Rule

This is the rule the generator and runtime must both honor:

- domain events describe facts
- automation routes describe policy
- workflows execute the resulting effect

Event taxonomies should never encode all three in one artifact.

## Cross References

- [event-system-architecture.md](event-system-architecture.md)
- [process-and-event-map.md](process-and-event-map.md)
- [runtime-state-and-control-events.md](runtime-state-and-control-events.md)
