# Process and Event Map

This document maps the main runtime processes and how events move between them.

It is the operational view of the architecture.

## Runtime Processes

### Process 1: Frontend shell

Owns:

- shell state
- navigation and layout
- module surfaces
- workflow transcript and artifact rendering

Consumes:

- workflow runtime stream
- shell push events
- HTTP APIs from substrate and AI runtime

### Process 2: App substrate

Owns:

- entities, views, actions, modules, policies
- shared platform services such as settings and notifications
- post-commit domain event emission

Current implementation zone:

- `mozaikscore/`

### Process 3: Broker or ingress boundary

Owns transport for domain facts between the substrate and AI runtime.

Target:

- NATS with FastStream

Transitional option:

- HTTP ingress to the AI runtime

### Process 4: AI runtime

Owns:

- workflow execution
- automation route matching
- runtime control plane
- artifacts
- runtime stream delivery

Current implementation zone:

- `mozaiksai/`

### Process 5: Persistence and external integrations

Owns:

- durable workflow state
- app data persistence
- third-party systems

## Transport Map

| Transport | Direction | Purpose |
| --- | --- | --- |
| HTTP | frontend to substrate | app and module APIs |
| HTTP | frontend to AI runtime | chat and workflow APIs |
| WebSocket | frontend to AI runtime | workflow runtime stream |
| WebSocket push | substrate to frontend | shell push events |
| NATS | substrate to AI runtime and subscribers | domain event mesh |
| HTTP ingress | substrate to AI runtime | transitional domain event transport |

## Flow A: Plain App Interaction

```text
user
  -> module or view
  -> substrate action
  -> database commit
  -> response to frontend
```

This path may end here if no automation route applies.

## Flow B: Domain Event Triggers Automation

```text
user or integration
  -> substrate mutation
  -> commit
  -> domain event emitted
  -> broker or HTTP ingress
  -> automation route matched
  -> workflow run or resume
  -> runtime stream and artifacts
```

This is the key hybrid path.

## Flow C: Direct Workflow Interaction

```text
user
  -> workflow entrypoint
  -> AI runtime
  -> engine execution
  -> runtime stream
  -> optional artifacts or actions
```

This path does not require a substrate event to exist first.

## Flow D: Artifact Returns to App Surface

```text
workflow
  -> artifact created or updated
  -> persistence
  -> module or view reads artifact
  -> user continues through app substrate
```

Artifacts are one of the clean bridges between the two halves of the system.

## Guardrails

Do not:

- use the workflow stream as the substrate event mesh
- let the substrate choose workflow names directly
- assume every substrate mutation must open a chat

The architecture only stays modular if those paths remain optional and explicit.

## Cross References

- [event-system-architecture.md](event-system-architecture.md)
- [workflow-architecture.md](workflow-architecture.md)
- [ui-surface-and-layout-architecture.md](ui-surface-and-layout-architecture.md)
