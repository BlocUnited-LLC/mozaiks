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
- HTTP APIs from app backend and AI runtime

### Process 2: App backend

Owns:

- entities, views, actions, modules, policies
- shared platform services such as settings and notifications
- post-commit domain event emission

Current implementation zone:

- `mozaikscore/`

### Process 3: Automation ingress boundary

Owns transport for domain facts between the app backend and AI runtime.

Current transport:

- in-process ingress in unified deployment
- HTTP ingress to the AI runtime in split local development

### Process 4: AI runtime

Owns:

- workflow execution
- workflow trigger matching
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
| HTTP | frontend to app backend | app and module APIs |
| HTTP | frontend to AI runtime | chat and workflow APIs |
| WebSocket | frontend to AI runtime | workflow runtime stream |
| WebSocket push | app backend to frontend | shell push events |
| In-process ingress | app backend to AI runtime | domain event mesh in unified deployment |
| HTTP ingress | app backend to AI runtime | domain event mesh in split local development |

## Flow A: Plain App Interaction

```text
user
  -> module or view
  -> app backend action
  -> database commit
  -> response to frontend
```

This path may end here if no workflow trigger applies.

## Flow B: Domain Event Triggers Automation

```text
user or integration
  -> app backend mutation
  -> commit
  -> domain event emitted
  -> in-process or HTTP ingress
  -> workflow trigger matched
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

This path does not require a app backend event to exist first.

## Flow D: Artifact Returns to App Surface

```text
workflow
  -> artifact created or updated
  -> persistence
  -> module or view reads artifact
  -> user continues through app backend
```

Artifacts are one of the clean bridges between the two halves of the system.

## Guardrails

Do not:

- use the workflow stream as the app backend event mesh
- let the app backend choose workflow names directly
- assume every app backend mutation must open a chat

The architecture only stays modular if those paths remain optional and explicit.

## Cross References

- [event-system-architecture.md](event-system-architecture.md)
- [workflow-architecture.md](workflow-architecture.md)
- [ui-surface-and-layout-architecture.md](ui-surface-and-layout-architecture.md)

