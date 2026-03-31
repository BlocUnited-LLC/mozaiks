# Event System

This document defines the event model and taxonomy for Mozaiks.

## Core Rule

The event system should be simple:

1. normal app logic emits app events
2. workflow triggers decide whether a workflow should run
3. workflows do the agentic work
4. pages reflect the result

That is the event story.

## Design Principles

- events are immutable facts
- event names describe what happened, not what should happen
- event ownership follows layer boundaries
- workflow names do not appear inside domain event types
- tenant scope and causation must be explicit

## Two Important Event Kinds

### App events

These come from normal app behavior.

Examples:

- `set.brief_confirmed`
- `set.direction_selected`
- `set.finalized`
- `subscription.changed`

App events are facts. They should never encode workflow names.

### Workflow runtime events

These come from live workflow execution.

Examples:

- chat messages
- progress updates
- artifact updates
- UI tool requests

These are for the live experience, not for deciding app automation policy.

## Automation Flow

```text
page or backend action
  -> app event emitted
  -> workflow trigger checked
  -> workflow runs or resumes
  -> workflow output is saved or streamed
  -> pages update
```

Simple rule:

- runtime events say what facts exist
- workflow `triggers:` say what workflow behavior those facts can trigger

## Event Families

| Family | Owner | Purpose |
| --- | --- | --- |
| App domain events | app bundle and app backend | business facts such as entity changes |
| Shared app backend events | app backend core | settings, notifications, subscriptions, module activity |
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
- owned by the app backend
- never include workflow names
- safe to route through internal or HTTP ingress

## 2. Shared App Backend Events

These are reusable app-backend facts owned by the platform.

Examples:

- `settings.updated`
- `notification.created`
- `notification.read`
- `subscription.changed`
- `module.executed`

These are still domain events, but they describe shared platform services rather than app-specific business objects.

## 3. Automation Control Events

These are low-frequency facts about automation and session control.

Examples:

- `control.plan_created`
- `control.prerequisites_required`
- `control.execution_batch_started`
- `control.transfer_requested`

These do not replace domain events. They describe what the AI-side control plane decided after domain events were processed.

## 4. Workflow Runtime Events

These are the live workflow and chat stream families.

Examples:

- `process.started`
- `process.completed`
- `task.failed`
- `chat.message_appended`
- `artifact.updated`
- `ui.tool.requested`

These are not the right contract for app backend workflow triggers.

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
    "layer": "app backend|automation|runtime|frontend|integration",
    "component": "component_name",
    "transport": "http|ws|internal"
  },
  "payload": {},
  "causation_id": null,
  "correlation_id": "stable-business-or-run-correlation-key"
}
```

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

For app backend events, prefer:

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
- workflow triggers describe policy
- workflows execute the resulting effect

Event taxonomies should never encode all three in one artifact.

## What Not To Do

Do not:

- emit workflow names as app events
- use frontend UI events as your backend automation model
- make every CRUD action call a workflow directly
- treat live chat events as the same thing as app facts

## Key Files

- `platform/workflows/{workflow}/orchestrator.yaml`

## Current Implementation Note

The backend currently uses one app server and handles automation inside that same backend.

That is the right default model for Mozaiks.

## Cross References

- [architecture-overview.md](architecture-overview.md)
- [workflow-architecture.md](workflow-architecture.md)
- [process-and-event-map.md](process-and-event-map.md)
- [runtime-state-and-control-events.md](runtime-state-and-control-events.md)
