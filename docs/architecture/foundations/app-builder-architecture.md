# App Builder Architecture

This document defines the user-facing architecture of the first-party Mozaiks
NL app generator.

The builder should feel like one coherent product even if it is powered by many
internal AG2 workflows.

## The User Should See One Guided Build

The builder should feel like:

- one guided wizard
- one evolving app concept
- one build session
- one preview and iteration loop

The user should not need to understand:

- hidden groupchat switches
- build graph internals
- runtime substrate boundaries

Those are internal implementation details.

## The Six User-Facing Surfaces

### 1. Discovery

Purpose:

- capture the app idea or requested change
- identify actors, domain objects, and constraints
- understand what outcome the user actually wants

### 2. Value review

Purpose:

- show the `ConceptBlueprint`
- explain the app's value proposition
- show approved scope versus deferred scope
- get approval before deeper architecture work

This is where the builder should prevent "build me Facebook" from becoming an
unbounded technical request with no scoped value proposition.

### 3. Architecture review

Purpose:

- show the app model
- show the automation routes
- show the workflow inventory
- show which concerns are handled by the core platform
- show which thin stubs still need to be authored

The user should be able to see cause and effect here, not just a pile of
tasks.

### 4. Prerequisites and setup

Purpose:

- collect keys, providers, or deployment inputs only when the plan actually
  needs them

Do not ask for secrets during ideation.

### 5. Build and validation

Purpose:

- show generation progress
- show which bundle assets are being created
- show validation and repair work

This is where `BuildGraph` becomes visible as progress, not as architecture.

### 6. Preview and iteration

Purpose:

- let the user inspect the generated app
- request changes
- understand whether a change affects concept, provisions, substrate,
  automation, or workflows

This is where `ImpactSet` becomes important.

## Hidden Internal Responsibilities

Internally, the builder may still use specialized workflows for:

- concept modeling
- intent normalization
- architecture planning
- automation planning
- workflow authoring
- bundle compilation
- validation

In practice, those internal workflows may be AG2 groupchats.

That is acceptable only if they stay behind typed builder contracts such as:

- `ConceptBlueprint`
- `IntentBrief`
- `CapabilityMap`
- `PlatformProvisionPlan`
- `DecompositionPackage`
- `BuildGraph`
- `ImpactSet`

## Required Review Artifacts

Before a build starts, the builder should be able to show:

- concept blueprint
- architecture summary
- provision plan summary
- automation route summary
- workflow inventory
- build graph summary

Without these artifacts, the builder cannot explain what it is about to build
or change.

## Why This Matters

The builder becomes trustworthy only when it can explain:

- what is a core platform provision
- what is an app-authored stub
- what is a normal app surface
- what is an AI workflow
- what business facts trigger automation

That is the same separation the runtime architecture depends on.

## Current Flagship Example

The current `platform/` directory should be treated as the flagship runtime
output example used to test this architecture.

It already demonstrates:

- app manifest configuration
- shell projections in `platform/config/*`
- durable modules in `platform/modules/*`
- automation contracts in `platform/automations/*`
- workflow authoring in `platform/workflows/*`

The builder should understand that example as a runtime target while still
guiding new authoring toward the canonical bundle families.

## Cross References

- [builder-execution-model.md](builder-execution-model.md)
- [app-planning-contracts.md](app-planning-contracts.md)
- [canonical-app-structure.md](canonical-app-structure.md)
