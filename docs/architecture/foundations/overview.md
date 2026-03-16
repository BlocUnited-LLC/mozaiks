# Architecture Foundations

This directory defines the target architecture for Mozaiks as a modular,
dynamic, agentic application platform.

The central shift in this rewrite is simple:

- the non-AI app substrate is first-class
- the AI workflow runtime is first-class
- the boundary between them is event-driven and declarative
- the first-party builder is a product on top of that boundary, not the
  architecture itself
- the enterprise-grade core should absorb recurring SaaS concerns so the
  generator mostly configures core and authors thin app-specific stubs

## The Four-Layer Model

### 1. App substrate

Owns durable application behavior:

- entities
- views
- actions
- modules
- policies
- settings, notifications, subscriptions
- post-commit domain event emission

Primary implementation zone today:

- `mozaikscore/`

### 2. Event and automation boundary

Owns the contract between business facts and AI automation:

- canonical event envelope
- domain event taxonomy
- automation routing rules
- transport between substrate and AI runtime

Key rule:

- `mozaikscore` emits domain facts, never workflow names
- `mozaiksai` owns mapping from domain event to automation effect

### 3. AI runtime

Owns workflow execution and orchestration:

- workflow loading
- engine adapters
- chat and run transport
- artifacts
- run lifecycle
- workflow routing and resume semantics

Primary implementation zone today:

- `mozaiksai/`

### 4. Product and generator

Owns intent decomposition and bundle generation:

- turns user intent into a typed app model
- turns concept review into a bounded build plan
- decides which capabilities are core provisions versus app-authored stubs
- defines automation routes
- authors workflows
- compiles app bundles

The builder is a first-party app on the platform. It is not the platform.

## Core Thesis

Mozaiks should not force all application behavior into chat or groupchat.

It should support three equally real execution paths:

- plain app behavior through modules, views, and actions
- event-driven automation triggered by domain facts
- user-facing workflows for reasoning, orchestration, and HITL

The architecture is sound only if those paths remain distinct and composable.

## Enterprise Core Thesis

Mozaiks core should try to cover as many recurring SaaS infrastructure concerns
as possible without collapsing app-specific behavior into runtime internals.

That means the generator should usually be doing one of three things:

- enabling or configuring a core provision
- declaring app-specific substrate or automation
- authoring thin stubs on top of core

It should not repeatedly reinvent auth, tenancy, notifications, shell chrome,
event transport, or workflow streaming for each app.

## Current Runtime Example

The current `platform/` directory in this repo should be treated as the flagship
runtime-output example used to test the architecture.

It already demonstrates:

- app manifest output
- shell projections in `platform/config/*`
- modules in `platform/modules/*`
- automation contracts in `platform/automations/*`
- workflows in `platform/workflows/*`

That runtime example is important for the builder because it shows the concrete
shape the generated bundle still has to satisfy today.

## Reading Order

Read these first:

1. [canonical-app-structure.md](canonical-app-structure.md)
2. [app-bundle-declaratives.md](app-bundle-declaratives.md)
3. [core-product-app-bundle-boundary.md](core-product-app-bundle-boundary.md)
4. [event-taxonomy.md](event-taxonomy.md)
5. [event-system-architecture.md](event-system-architecture.md)
6. [workflow-architecture.md](workflow-architecture.md)

Then read the generator and builder references:

1. [app-creation-guide.md](app-creation-guide.md)
2. [app-planning-contracts.md](app-planning-contracts.md)
3. [builder-orchestration-taxonomy.md](builder-orchestration-taxonomy.md)
4. [builder-execution-model.md](builder-execution-model.md)
5. [app-builder-state-and-routing.md](app-builder-state-and-routing.md)
6. [app-builder-architecture.md](app-builder-architecture.md)

## What Changed

These docs now assume:

- workflows stay real and important
- the workflow file contract remains stable
- modules are not the same thing as entities, views, or actions
- config files are not the architecture
- NATS or FastStream belongs on the substrate event mesh, not on the frontend
  run stream
- chat is one surface, not the default answer to every feature

## Cross References

- [app-bundle-declaratives.md](app-bundle-declaratives.md)
- [event-system-architecture.md](event-system-architecture.md)
- [workflow-architecture.md](workflow-architecture.md)
