# App Planning Contracts

This document defines the typed planning contracts that should exist before the
builder writes an app bundle.

The core rule is simple: the builder must turn intent into reviewed contracts
before it turns anything into file edits.

## Contract Order

The builder should materialize these contracts in order:

1. `ConceptBlueprint`
2. `IntentBrief`
3. `CapabilityMap`
4. `PlatformProvisionPlan`
5. `DecompositionPackage`
6. `BuildGraph`
7. `BuilderBlueprint`

`ImpactSet` is the parallel contract for change flows. It is required when the
builder is adjusting an existing app rather than creating one from scratch.

## Why This Order Exists

Each contract answers a different question:

- `ConceptBlueprint`: what is worth building
- `IntentBrief`: what the user actually asked for
- `CapabilityMap`: what the app must be able to do
- `PlatformProvisionPlan`: what the enterprise core already provides
- `DecompositionPackage`: what the app itself must declare
- `BuildGraph`: what bounded authoring work must run
- `BuilderBlueprint`: whether the whole plan is aligned enough to build

Without this split, the builder collapses back into "one workflow that writes
files."

## `ConceptBlueprint`

`ConceptBlueprint` is the approval-facing artifact produced by the builder's
value and discovery stage.

It should make these things explicit:

- `intent_mode`: `create` or `change`
- `app_name`
- `product_summary`
- `value_proposition`
- `primary_users`
- `approved_scope`
- `deferred_scope`
- `core_outcomes`
- `success_signals`
- `change_summary` for change flows

This is the first artifact a user should approve in the wizard.

## `IntentBrief`

`IntentBrief` is the normalized technical summary of the user's request.

It should capture:

- source request
- user personas
- bounded contexts
- business entities
- constraints
- non-goals
- success criteria
- open questions

`IntentBrief` is not yet a build plan. It is the clean input to architecture.

## `CapabilityMap`

`CapabilityMap` translates the request into discrete product capabilities in
plain language.

Each capability should declare:

- `capability_id`
- `label`
- `summary`
- `actor`
- `primary_surface`
- whether it requires durable state
- whether it requires reasoning
- whether it can be event-triggered
- candidate entities, actions, modules, or workflows

`primary_surface` should stay explicit:

- `module`
- `action`
- `workflow`

This is where the builder decides whether a feature is primarily a durable app
surface, a deterministic action, or a reasoning surface.

## `PlatformProvisionPlan`

`PlatformProvisionPlan` is the contract that stops the generator from trying to
reinvent the platform core every time it builds an app.

Each `PlatformProvisionSpec` should declare:

- `provision_id`
- `label`
- `category`
- `runtime_owner`
- `mode`
- `summary`
- `depends_on`
- `config_paths`
- `stub_paths`

Supported modes:

- `core_provided`: already handled by the platform core
- `core_configured`: provided by core but requires app-specific configuration
- `app_stub`: requires thin app-authored code or declarative stubs on top of core
- `external_integration`: core boundary exists but the app must wire an external system
- `disabled`: intentionally not used by this app

This is the contract that lets Mozaiks behave like an enterprise framework
instead of a prompt-driven code generator.

Examples of provisions:

- auth
- tenancy
- policy enforcement
- notifications
- subscriptions
- shell chrome
- websocket delivery
- automation transport
- workflow runtime
- observability

## `DecompositionPackage`

`DecompositionPackage` is the app-specific architecture package.

It should contain:

- `AppSpec`
- `Capability`
- `EntitySpec`
- `ViewSpec`
- `ActionSpec`
- `PolicySpec`
- `ModuleSpec`
- `DomainEventSpec`
- `AutomationRouteSpec`
- `WorkflowSpec`
- `BundlePlan`

This is the first contract that says what one app actually is.

## `BundlePlan`

`BundlePlan` defines the paths and declarative families the compiled app bundle
must contain.

Recommended families:

- `manifest_paths`
- `shell_paths`
- `substrate_paths`
- `module_paths`
- `automation_paths`
- `workflow_paths`

The target canonical families are still:

- `platform/app.json`
- `platform/shell/*`
- `platform/data/*`
- `platform/modules/*`
- `platform/automations/*`
- `platform/workflows/*`

The current flagship `platform/` example in this repo still includes
transitional projections such as `platform/config/*.json`. Those are valid
runtime outputs today, but they should be understood as shell or substrate
projections rather than the long-term authoring model.

## `BuildTask`

`BuildTask` is the precise meaning of `task` in the Mozaiks NL app generator.

It is not a vague TODO. It is a bounded authoring unit with:

- `task_id`
- `title`
- `builder_workflow`
- `depends_on`
- `capability_refs`
- `provision_refs`
- `declarative_families`
- `bundle_paths`
- `report_paths`

Important rule:

- tasks with `bundle_paths` must reference at least one capability or provision
- tasks that plan do not write bundle paths
- tasks that write bundle paths must declare which declarative family they own

This is what lets AG2 groupchats operate as constrained implementation workers
instead of becoming the architecture themselves.

## `BuildGraph`

`BuildGraph` is the dependency-aware execution graph made of `BuildTask`s.

Its job is to answer:

- what can run in parallel
- what must run first
- which workflow owns which bundle paths
- where fan-in happens before validation

`BuildGraph` should be the builder's conveyor belt. It should not be confused
with workflow-local DAGs or runtime execution graphs.

## `BuilderBlueprint`

`BuilderBlueprint` is the fully aligned build package.

It binds together:

- `ConceptBlueprint`
- `IntentBrief`
- `CapabilityMap`
- `PlatformProvisionPlan`
- `DecompositionPackage`
- `BuildGraph`
- optional `ImpactSet`

This is the contract the builder should approve before actual compile work
starts.

## `ImpactSet`

`ImpactSet` is the bounded change contract for refactors and adjustments.

It should capture:

- `change_summary`
- affected capability ids
- affected provision ids
- affected workflows
- affected bundle paths
- affected declarative families
- whether the change requires concept revision
- whether the change requires replanning
- whether the change requires rebuild

This is what keeps a change request from turning into an unbounded rebuild.

## Integrity Rules

### Cross-reference integrity

Every capability ref must point to a declared spec in the decomposition.

### Event integrity

Every `AutomationRouteSpec.event_type` must point to a declared
`DomainEventSpec.event_type`.

### Capability coverage

Every declared capability must be covered by at least one `BuildTask`.

### Provision coverage

Every active platform provision must be covered by at least one `BuildTask`.

### Ownership integrity

Every path in `BundlePlan` must be owned by exactly one `BuildTask`.

### Write-boundary integrity

Each `BuildTask.builder_workflow` may only write the declarative families its
role is allowed to own.

## Practical Meaning

The builder should prefer these in order:

1. configure a core provision
2. declare app-specific substrate or automation
3. author a thin stub on top of core
4. only then create bespoke code

That is how Mozaiks becomes a production-grade framework with a generator on
top, rather than a generator pretending to be a framework.

## Cross References

- [builder-orchestration-taxonomy.md](builder-orchestration-taxonomy.md)
- [builder-execution-model.md](builder-execution-model.md)
- [app-creation-guide.md](app-creation-guide.md)
