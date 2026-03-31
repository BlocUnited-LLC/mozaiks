# Runtime State and Control Events

This document defines the generic runtime state and control-event layer that
belongs in Mozaiks core.

It is separate from:

- app backend domain events
- workflow stream events
- first-party builder vocabulary

## Core Rule

Use control events for low-frequency, durable session-routing facts.

Do not use them for:

- raw CRUD mutations
- chat token streams
- product-specific nouns when a generic state exists

## Why This Layer Exists

Mozaiks needs a stable control plane for cases where one user-visible session
may:

- plan
- wait for prerequisites
- execute
- reroute
- review
- continue

That is broader than any single product, including the builder.

## Generic State Taxonomy

Recommended core states:

- `intake`
- `planning`
- `approval_pending`
- `prerequisites_pending`
- `executing`
- `review`
- `rerouting`
- `completed`
- `failed`

These states describe session posture, not business domain facts.

## Generic Control Event Families

### Canonical state

- `control.canonical_state_created`
- `control.canonical_state_revised`

### Planning

- `control.plan_created`
- `control.plan_approved`
- `control.plan_rejected`

### Prerequisites

- `control.prerequisites_required`
- `control.prerequisites_satisfied`
- `control.prerequisites_blocked`

### Execution

- `control.execution_batch_started`
- `control.execution_batch_completed`
- `control.execution_batch_failed`
- `control.execution_completed`
- `control.execution_failed`

### Review and rerouting

- `control.artifact_ready`
- `control.feedback_received`
- `control.impact_computed`
- `control.transfer_requested`
- `control.iteration_started`

## Relation to Domain Events

Domain events answer:

- what happened in the app or business world

Control events answer:

- what the runtime or product session is doing next

That distinction is essential.

Example:

- `booking.request.approved` is a domain event
- `control.transfer_requested` is a control-plane fact

## Relation to Workflow Runtime Events

Workflow runtime events answer:

- what is happening inside a run right now

Examples:

- `process.started`
- `task.completed`
- `ui.tool.requested`

Those are too fine-grained to replace the control plane.

## Product Specialization

Products may specialize generic control events into richer meanings.

Example:

- a builder may interpret `control.plan_created` as an architecture review being
  ready

The generic contract should stay reusable even if the product meaning changes.

## Cross References

- [event-taxonomy.md](event-taxonomy.md)
- Builder state-routing and execution specializations are maintained privately in
  `mozaiks-platform/`.

