# Event Taxonomy

This document names the distinct event families in Mozaiks and the job of each
one.

The main rule is simple:

- one transport may carry many event families
- one envelope style may describe many durable events
- those families are still different contracts

Do not collapse them just because they share plumbing.

## The Five Event Families

### 1. App domain events

Owner:
app backend and app bundle

Purpose:
describe durable business facts after deterministic mutations

Examples:

- `crm.lead.created`
- `booking.request.approved`
- `catalog.item.archived`

Use these for:

- workflow triggers
- integrations
- projections
- notifications derived from app facts

### 2. Shared app-backend service events

Owner:
shared app-backend capabilities

Purpose:
describe reusable product services rather than app-specific business objects

Examples:

- `settings.updated`
- `notification.created`
- `subscription.changed`

These are still app-side domain facts.

### 3. Runtime control events

Owner:
AI runtime

Purpose:
describe durable session-routing and orchestration posture

Examples:

- `control.plan_created`
- `control.prerequisites_required`
- `control.transfer_requested`

These are not business facts. They describe runtime control state.

### 4. Workflow runtime stream events

Owner:
AI runtime

Purpose:
describe what is happening inside a live run right now

Examples:

- `process.started`
- `task.completed`
- `chat.message_appended`
- `artifact.updated`
- `ui.tool.requested`

These are for the live experience. They are not the automation policy model.

### 5. Telemetry and observability events

Owner:
platform and operations

Purpose:
measure health, usage, performance, and diagnostics

Examples:

- token accounting
- latency metrics
- connector health signals

These support operations. They should not be overloaded as app facts.

## The Separation Test

Ask these questions in order:

1. Did a business fact become true after a deterministic mutation?
2. Did the runtime change orchestration posture?
3. Is this just a live run update for UX?

If the answer is:

- 1: app domain event
- 2: runtime control event
- 3: workflow runtime stream event

## Generator Rule

The generator should only create app events for facts that matter outside the
local transaction.

It should not:

- generate workflow names as event types
- model chat stream messages as app backend events
- use one giant generic event type for unrelated meanings

## Cross References

- [event-system.md](event-system.md)
- [event-system-architecture.md](event-system-architecture.md)
- [runtime-state-and-control-events.md](runtime-state-and-control-events.md)