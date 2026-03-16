# Core, Product, and App Bundle Boundary

This document defines the ownership boundary between the main layers in
Mozaiks.

The rewrite adds one boundary that must stay explicit:

- app substrate behavior
- AI workflow behavior

## The Five Ownership Zones

### 1. App substrate core

Owns generic non-AI application capabilities:

- entities, views, actions, and policies at runtime
- module hosting
- settings, notifications, subscriptions
- app-facing APIs
- post-commit domain event emission

The design target here is broad coverage of recurring SaaS infrastructure
concerns, not bespoke application logic.

Current implementation zone:

- `mozaikscore/`

### 2. AI runtime core

Owns generic workflow and orchestration capabilities:

- workflow loading
- run lifecycle
- engine adapters
- stream transport
- artifacts
- UI tool round-trips
- orchestration and resume semantics

Current implementation zone:

- `mozaiksai/`

### 3. Execution engine

Owns agent-native semantics.

AG2 should continue to own native concepts such as:

- handoffs
- groupchat progression
- reply hooks
- tool calling

Mozaiks should wrap around AG2 where the gap is real, not duplicate AG2 inside
core.

### 4. First-party product

Owns product-specific behavior such as:

- app building
- provisioning
- monetization
- hosted operations
- templates and guided creation flows

The first-party builder is a consumer of the platform, not the platform.

Its main job is to turn intent into:

- a concept the user can approve
- platform provision choices
- app-specific declaratives
- thin stubs on top of core

### 5. App bundle

Owns application-specific declaratives:

- substrate model
- modules
- automation routes
- workflows
- shell composition

The app bundle is input to the runtime. It is not runtime internals.

## The Boundary Rule That Matters Most

`mozaikscore` may emit domain events.

`mozaikscore` must not emit workflow names or directly encode AG2 or groupchat
meaning.

`mozaiksai` owns:

- event validation
- automation routing
- workflow route selection
- run and resume execution

This is the boundary that prevents the non-AI substrate from becoming a hidden
workflow engine.

## What Belongs in the App Substrate

A feature belongs in the substrate if it is primarily about:

- durable business state
- persistent product surfaces
- deterministic mutations
- policies and access rules
- platform services such as notifications or settings

If the feature should still make sense with AI turned off, it probably starts in
the substrate.

## What Belongs in the AI Runtime

A feature belongs in the AI runtime if it is primarily about:

- reasoning
- multi-turn interaction
- orchestration
- human checkpoints
- agent tooling
- run lifecycle

If the feature still matters even when the app changes verticals, it probably
belongs in the AI runtime.

## What Belongs in the App Bundle

A feature belongs in the app bundle if it answers a question about one app:

- what entities exist
- what modules and views exist
- what events matter
- what automations exist
- what workflows exist

Generated output should target these app-bundle contracts, not patches to core.

Important qualification:

- if a concern is already covered by the core runtime, the bundle should prefer
  configuration over fresh implementation
- if a concern only needs a thin extension, the bundle should author a stub on
  top of core rather than invent a new runtime primitive

## What Belongs in the Product

A feature belongs in the first-party product if it is about:

- helping users describe an app
- decomposing intent
- generating bundles
- provisioning hosted apps
- billing and SaaS operations

Self-hosters should not need the first-party product layer to understand the
runtime architecture.

## Decision Test

Put it in the substrate if:

- it is a fact about app state or a deterministic app action

Put it in the AI runtime if:

- it is a fact about workflow execution or orchestration

Put it in the app bundle if:

- it defines one app's model, routes, workflows, or shell

Put it in the product if:

- it exists to build, host, or monetize apps
- it exists to turn user intent into approved specs, provision plans, and build
  graphs

Keep it in AG2 if:

- AG2 already has a native concept for it and Mozaiks only needs to configure or
  adapt it

## Cross References

- [workflow-architecture.md](workflow-architecture.md)
- [event-system-architecture.md](event-system-architecture.md)
- [app-bundle-declaratives.md](app-bundle-declaratives.md)
