# Event System Architecture

This document defines the event architecture that connects the app substrate,
automation boundary, and AI runtime.

It is the most important seam in the system.

## Core Rules

1. Events are immutable facts.
2. Domain events and workflow runtime events are different channels.
3. `mozaikscore` emits business facts, never workflow names.
4. `mozaiksai` owns mapping from domain event to automation effect.
5. WebSocket is a client transport, not the backbone for substrate-to-AI
   automation.

## The Four Event Channels

### Channel 1: Workflow runtime stream

Purpose:

- run lifecycle
- chat stream
- task progress
- artifacts
- UI tool requests and responses

Typical consumers:

- frontend chat and workflow surfaces

Typical transport:

- WebSocket

Typical families:

- `process.*`
- `task.*`
- `chat.*`
- `artifact.*`
- `ui.tool.*`

### Channel 2: Domain event mesh

Purpose:

- communicate post-commit business facts
- bridge substrate behavior to automation policy
- support app-to-app or integration-driven triggers

Typical producers:

- `mozaikscore`
- external integrations
- app actions

Typical consumers:

- automation router
- non-AI subscribers
- telemetry sinks

Target transport:

- NATS through FastStream

Transitional transport:

- HTTP ingress is acceptable until the broker layer is in place

Typical families:

- app-owned domain events such as `crm.lead.created`
- shared substrate events such as `settings.updated`

### Channel 3: Shell push channel

Purpose:

- push substrate-side user updates to connected clients

Examples:

- notifications
- settings refresh
- module-level UI refresh hints

Typical transport:

- WebSocket push from substrate services

This is separate from the workflow runtime stream even if the same frontend
renders both.

### Channel 4: Telemetry and analytics

Purpose:

- observability
- audit
- usage analytics
- learning and improvement loops

This channel should remain downstream of the other channels. It should not be
the place where business behavior is decided.

## Canonical Domain Event Flow

```text
user or integration action
  -> substrate mutation commits
  -> domain event emitted
  -> broker or ingress transport
  -> automation router validates and matches
  -> workflow route selected or ignored
  -> AI runtime executes run or resume
  -> runtime stream reaches frontend if needed
```

## Canonical User-Driven Workflow Flow

```text
user invokes workflow
  -> AI runtime loads workflow
  -> workflow executes through engine adapter
  -> runtime events stream to frontend
  -> artifacts and explicit actions update durable state
```

## Ownership Model

### `mozaikscore`

Owns:

- app and substrate event production
- post-commit fact emission
- substrate-side push events

Does not own:

- workflow selection
- workflow resume policy
- groupchat meaning

### `mozaiksai`

Owns:

- event validation at the automation boundary
- automation route matching
- run and resume decisions
- workflow runtime stream

### Frontend

Consumes:

- runtime stream events
- shell push events

The frontend may merge these into one user experience, but the backend should
not pretend they are the same channel.

## FastStream and NATS Guidance

Use FastStream and NATS first for Channel 2, the domain event mesh.

Do not start by moving:

- the frontend runtime stream
- UI tool round-trips
- direct browser push

onto the broker.

That would blur the boundary again.

## Guardrails

Do not:

- emit commands disguised as events
- put workflow names in domain event types
- use WebSocket as the backend-to-backend automation backbone
- collapse substrate events and workflow events into one bus

## Cross References

- [event-taxonomy.md](event-taxonomy.md)
- [process-and-event-map.md](process-and-event-map.md)
- [workflow-architecture.md](workflow-architecture.md)
