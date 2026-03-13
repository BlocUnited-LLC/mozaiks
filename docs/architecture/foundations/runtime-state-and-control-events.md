# Runtime State And Control Events

This document defines the generic runtime state and typed control-event contracts that belong in `mozaiks core`.

It answers these questions:

- Which session and routing concepts are generic enough for core?
- Which typed control events belong in core instead of in a first-party product?
- How should products such as the Mozaiks app builder specialize these contracts?

When event and state discussions drift into product-specific language, this document is the reference for the core layer.

---

## Core Rule

Core owns generic capabilities.

Core does not own first-party product vocabulary.

That means `core` may define:

- session and routing state
- control-event kinds
- transfer contracts
- feedback/impact contracts
- generic plan/execution/prerequisite artifacts

But `core` should not define:

- `AppSpec`
- `ActionPlan`
- `BuildIteration`
- builder-specific event kinds like `builder.wave_started`
- first-party builder workflow names

Those belong to the first-party product layer.

---

## Runtime Contract Module

The concrete runtime contracts live in:

- [contracts.py](C:/Users/mbari/OneDrive/Desktop/BlocUnited/BlocUnited%20Code/mozaiks/mozaiksai/core/control_plane/contracts.py)

This module defines:

- `ControlPlaneState`
- `ControlPlaneEventKind`
- typed payload models
- `build_control_plane_event(...)`
- `parse_control_plane_event(...)`
- `infer_control_plane_state(...)`

These are the canonical runtime state and control-event contracts for `mozaiks core`.

---

## Generic State Taxonomy

The generic visible-or-hidden session states are:

- `intake`
- `planning`
- `approval_pending`
- `prerequisites_pending`
- `executing`
- `review`
- `rerouting`
- `completed`
- `failed`

These are generic enough to support:

- app building
- onboarding/configuration flows
- research/synthesis systems
- commerce/configuration journeys
- incident-response systems

They are intentionally broader than any one product UX.

---

## Generic Control-Event Taxonomy

The generic core control-event kinds are:

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

These are low-frequency, durable control facts.

They are the correct use of `DomainEvent` in core.

---

## Generic Payload Taxonomy

The generic payload families are:

- `CanonicalStateCreatedPayload`
- `CanonicalStateRevisedPayload`
- `PlanCreatedPayload`
- `PlanApprovedPayload`
- `PlanRejectedPayload`
- `PrerequisitesRequiredPayload`
- `PrerequisitesSatisfiedPayload`
- `PrerequisitesBlockedPayload`
- `ExecutionBatchStartedPayload`
- `ExecutionBatchCompletedPayload`
- `ExecutionBatchFailedPayload`
- `ExecutionCompletedPayload`
- `ExecutionFailedPayload`
- `ArtifactReadyPayload`
- `FeedbackReceivedPayload`
- `ImpactComputedPayload`
- `TransferRequestedPayload`
- `IterationStartedPayload`

These are generic runtime nouns.

Products may map them to richer domain-specific artifacts.

---

## What Products Specialize

The first-party app builder specializes the generic contracts like this:

| Core Contract | Builder Specialization |
|---|---|
| `canonical_state` | `AppSpec` |
| `plan` | `ActionPlan` + `TaskGraph` |
| `prerequisites` | API keys, provider config, deploy choices |
| `execution_batch` | MFJ wave |
| `artifact_ready` | preview ready / plan artifact ready |
| `feedback_received` | user change request after preview |
| `impact_computed` | scoped rebuild impact |
| `transfer_requested` | route `BuildApp -> ValueEngine` |
| `iteration_started` | new build iteration |

This is the correct relationship:

- core defines the generic shape
- product defines the business meaning

---

## Why This Belongs In Core

These contracts are valuable beyond the builder.

Any Mozaiks app that needs:

- one coherent session
- hidden workflow switching
- typed approvals/prerequisites
- parallel execution with later review
- scoped rerouting

can use these contracts.

This makes them platform capabilities, not just builder features.

---

## How Core Should Use These Contracts

Core should use these contracts for:

- state transitions
- durable control events
- UI surface coordination
- replay/recovery of user-visible session state

Core should not use them for:

- raw AG2 text/tool stream events
- first-party builder-only nouns
- product-specific workflow names

That is the boundary that preserves modularity.

---

## Relationship To Builder Docs

Read the docs in this order:

1. `RUNTIME_STATE_AND_CONTROL_EVENTS`
2. `APP_BUILDER_STATE_AND_ROUTING`
3. `BUILDER_EXECUTION_MODEL`
4. `APP_BUILDER_ARCHITECTURE`

That order keeps the core runtime contracts separate from the first-party builder specialization.

---

## Bottom Line

The modular `mozaiks core` should expose:

- generic runtime states
- generic control-event kinds
- generic control-event payloads
- generic transfer/feedback/impact contracts

The builder should specialize those contracts, not redefine the core around builder vocabulary.

