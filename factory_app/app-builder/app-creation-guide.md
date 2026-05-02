# App Creation Guide

This guide explains how Mozaiks should turn raw user intent into a coherent app
bundle.

The builder must decompose intent into separate reviewed models before it
writes files.

## Core Rule

Do not jump from:

- user prompt

directly to:

- workflows
- modules
- React files
- handlers

First produce a typed architecture and a bounded build plan.

## The Correct Pipeline

```text
User intent
  -> ConceptBlueprint
  -> IntentBrief
  -> CapabilityMap
  -> PlatformProvisionPlan
  -> app substrate model
  -> automation model
  -> workflow model
  -> bundle plan
  -> build graph
  -> compiled app bundle
```

For change flows, insert `ImpactSet` before compile so the builder knows what
must actually be touched.

## Step 1: Produce a `ConceptBlueprint`

Start by deciding:

- what the app is
- who it serves
- why it is valuable
- what is in v1 scope
- what is deferred

This is the approval-facing artifact for the builder wizard.

It should prevent requests like "build me Facebook" from immediately turning
into unbounded technical generation.

## Step 2: Normalize `IntentBrief`

Identify:

- business objects
- user roles
- important relationships
- external systems
- constraints
- non-goals

This stops the builder from building a shell before it understands the app.

## Step 3: Build a `CapabilityMap`

Translate the request into concrete capabilities.

Examples:

- create deal
- review booking request
- publish room brief
- summarize meeting notes
- escalate overdue task

Use product language first. Do not assign everything to workflows yet.

## Step 4: Classify Platform Provisions First

Before generating app-specific work, ask what the enterprise core already
provides.

For each recurring SaaS concern, classify it as:

- `core_provided`
- `core_configured`
- `app_stub`
- `external_integration`
- `disabled`

Examples:

- auth is usually `core_configured`
- websocket delivery is usually `core_provided`
- notifications are usually `core_configured`
- a custom CRM adapter may be `external_integration`

This is the point where the builder decides what does not need to be invented
again.

## Step 5: Execute Capability Classification

For each capability, answer these questions.

| Question | If yes | Primary output |
| --- | --- | --- |
| Does it need durable business state? | yes | entity |
| Does it need a persistent product surface? | yes | module and view |
| Is it a deterministic mutation or service call? | yes | action |
| Does it need reasoning, orchestration, or HITL? | yes | workflow |

Then ask one more question:

| Question | If yes | Output |
| --- | --- | --- |
| Should this happen because of a domain event rather than direct user intent? | yes | automation route |

This is the missing cause-and-effect layer.

## Step 6: Build the App Substrate Model

Define:

- `EntitySpec`
- `ViewSpec`
- `ActionSpec`
- `PolicySpec`
- `ModuleSpec`

This is the non-AI app.

If the app cannot stand on its own without workflows, the substrate model is
still incomplete.

## Step 7: Build the Automation Model

Define:

- domain events the app emits
- predicates that matter
- automation effects
- correlation keys
- surface decisions

Example:

```yaml
event_type: booking.request.approved
when:
  priority: high
effect:
  kind: workflow.run
  workflow: ApprovalConcierge
  correlation: booking_id
  surface: existing_chat_or_background
```

This route belongs to the app bundle and executes on the AI side.

## Step 8: Build the Workflow Model

Only now decide which workflows exist.

A workflow should exist when the system needs:

- reasoning
- multi-turn conversation
- orchestration across agents
- structured HITL checkpoints
- AI-mediated tool use

Examples that are not automatically workflows:

- save form
- update record
- list records
- fetch dashboard data

## Step 9: Produce the `BundlePlan`

The plan should answer:

- which declaratives must exist
- which runtime projections must exist
- which generated files must exist
- which surfaces are pure substrate versus workflow-enabled

The bundle plan is the first step that should talk in file paths.

## Step 10: Produce the `BuildGraph`

Break the approved architecture into bounded `BuildTask`s.

Each task should declare:

- one builder workflow owner
- dependencies
- capability refs
- provision refs
- owned bundle paths

This is where planning becomes executable authoring work.

## Create Versus Change

Use the same pipeline for both, but with different entry points.

### Create flow

```text
ConceptBlueprint
  -> IntentBrief
  -> CapabilityMap
  -> PlatformProvisionPlan
  -> DecompositionPackage
  -> BuildGraph
```

### Change flow

```text
ChangeIntent
  -> ImpactSet
  -> ConceptBlueprint if value or scope changed
  -> replanned BuildGraph only where needed
```

Not every change should restart the whole app build.

## Practical Classification Examples

| Capability | Primary model | Secondary model |
| --- | --- | --- |
| Create lead | entity + action + form view | module |
| Browse pipeline | view + module | none |
| Auto-triage new lead | workflow | automation route |
| Approve booking | action or workflow depending on rules | automation route |
| Generate room concept | workflow | optional artifact view |
| Send notification after status change | core-configured provision or integration | domain event |

## Flagship Runtime Example

The current greenfield target is the canonical app workspace contract.

Builder output should compile toward:

- `generated/apps/{app_id}/{build_id}/app/app.json`
- `generated/apps/{app_id}/{build_id}/app/config/*`
- `generated/apps/{app_id}/{build_id}/app/ui/pages/*`
- `generated/apps/{app_id}/{build_id}/app/modules/*`
- `generated/apps/{app_id}/{build_id}/app/workflows/*`
- `generated/apps/{app_id}/{build_id}/app/brand/*`

After validation and promotion, the same bundle shape is consumed from the
active app root such as `app/` or `mozaiks-platform/app/`.

## Generator Discipline

The builder must keep these outputs separate:

- concept and value review
- platform provision planning
- app structure
- automation policy
- workflow behavior
- shell composition

If one artifact tries to do all six jobs, the model will collapse again.

## Cross References

- [app-planning-contracts.md](app-planning-contracts.md)
- [app-bundle-declaratives.md](../../architecture/foundations/app-bundle-declaratives.md)
- [builder-execution-model.md](builder-execution-model.md)
