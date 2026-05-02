# Builder Orchestration Taxonomy

This document defines the canonical nouns for the first-party Mozaiks NL app
generator.

The point of this taxonomy is to stop builder language from drifting into:

- runtime orchestration nouns
- workflow-local AG2 nouns
- substrate business nouns
- vague "task" language

## Core Builder Nouns

### `ConceptBlueprint`

The approval-facing concept artifact.

It explains:

- what the app or requested change is
- why it is valuable
- what is in scope
- what is intentionally deferred

This is the artifact the user should approve in the wizard before expensive
build work starts.

### `IntentBrief`

The normalized technical brief derived from user intent.

It captures the problem space cleanly enough for architecture work to start.

### `CapabilityMap`

The list of concrete app capabilities stated in product language.

It is the bridge between user intent and technical decomposition.

### `PlatformProvisionPlan`

The plan for how the app will use the enterprise-grade platform core.

It classifies capabilities such as auth, tenancy, notifications, subscriptions,
shell chrome, workflow transport, and observability as:

- `core_provided`
- `core_configured`
- `app_stub`
- `external_integration`
- `disabled`

This is the noun that keeps the builder from trying to "build auth" every time.

### `AppModel`

The non-AI application model:

- entities
- views
- actions
- policies
- modules

### `AutomationModel`

The event-driven model:

- domain events
- automation routes
- correlation rules
- automation surfaces

### `WorkflowModel`

The set of workflows, their purposes, and their entry modes.

### `BundlePlan`

The file-level plan for the compiled app bundle.

### `BuildTask`

The bounded authoring unit executed by a builder workflow.

A `BuildTask` is not a generic TODO. It is a constrained unit of build work
with:

- one workflow owner
- dependency edges
- capability refs
- provision refs
- explicit bundle path ownership

### `BuildGraph`

The dependency-aware graph of `BuildTask`s.

This is the builder's execution graph. It is not the same thing as:

- a runtime workflow graph
- a workflow-local DAG
- AG2 groupchat turn order

### `BuilderBlueprint`

The validated package that binds:

- concept
- intent
- provisions
- decomposition
- bundle plan
- task ownership

### `ChangeIntent`

The classified meaning of a user-requested change.

Typical outcomes:

- substrate-only change
- automation change
- workflow change
- foundational architecture change

### `ImpactSet`

The bounded surface area affected by a change:

- affected capabilities
- affected provisions
- affected workflows
- affected bundle paths
- required replanning depth

### `BuildSession`

The user-visible builder session that persists across hidden internal workflow
switches.

The user should experience one build session even if the system uses many AG2
groupchats behind the scenes.

## Core Distinctions

### Concept versus intent

`ConceptBlueprint` says what is worth building.

`IntentBrief` says what the user asked for in technical terms.

### Provision plan versus app model

`PlatformProvisionPlan` says what the platform core already supplies.

`AppModel` says what this specific app still needs to declare.

### Automation model versus workflow model

`AutomationModel` says when AI behavior should start.

`WorkflowModel` says what the workflow does after it starts.

### Bundle plan versus build graph

`BundlePlan` says what outputs must exist.

`BuildGraph` says how bounded authoring work will produce them.

### Build task versus file edit

A `BuildTask` may produce multiple paths.

The file edit is an implementation detail of the task, not the task itself.

## Why This Taxonomy Exists

The previous failure mode was letting one noun stand in for too many things.

If the builder says "task" when it really means "feature," "spec," "workflow,"
or "file edit," the architecture will drift again.

These terms exist so the builder can stay reviewable, debuggable, and safe to
parallelize.

## Cross References

- [app-planning-contracts.md](app-planning-contracts.md)
- [builder-execution-model.md](builder-execution-model.md)
- [app-builder-architecture.md](app-builder-architecture.md)
