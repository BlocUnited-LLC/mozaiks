# Core vs Product vs App Bundle Boundary

**Last updated:** 2026-03-10  
**Status:** Current architecture reference  
**Audience:** Runtime maintainers, generator authors, and first-party product builders

---

## Purpose

This document defines the ownership boundary between:

1. **Mozaiks Core** - the reusable runtime substrate
2. **mozaiks.ai Product / Platform** - the first-party managed product running on that substrate
3. **App Bundles** - generated or hand-authored applications consumed by the runtime

This boundary exists to stop three common failure modes:

- product logic leaking into runtime internals
- generated app definitions depending on private runtime implementation details
- AG2-native behavior being reimplemented in the core without a real gap

If another doc conflicts with this one on ownership, this doc wins.

---

## The Boundary Thesis

Mozaiks is not "just a chatbot runtime" and it is not "just an app generator."

It is a **hybrid application runtime** where:

- chat acts as a control surface
- workflows perform reasoning and orchestration
- artifacts bridge AI and non-AI experiences
- modules/pages provide persistent product UX
- services and data models support durable application behavior

The critical mistake to avoid is treating all of that as one layer.

---

## The Four Things

There are really **four** separate concerns in the system, even though this document focuses on three ownership zones.

| Concern | What it is | Primary question it answers |
|---|---|---|
| **Core runtime** | Reusable substrate used by every app | "How does execution, persistence, transport, and surface state work?" |
| **Execution engine** | AG2 today, other engines later | "How do agents reason, hand off, and run conversations?" |
| **Product/platform** | mozaiks.ai as a first-party managed product | "How do we generate, host, monetize, provision, and operate apps?" |
| **App bundle** | Generated or authored app definition consumed by core | "What app should exist, what workflows/pages/modules does it contain, and how should users experience it?" |

Important rule:

- **AG2 is not the product.**
- **The product is not the core.**
- **The app bundle is not the runtime.**

---

## Ownership Model

### 1. Core Owns Generic Runtime Nouns

Core owns capabilities that every app may need, regardless of vertical or product strategy.

Examples:

- run lifecycle and resume semantics
- transport and event streaming
- persistence managers and artifact storage
- multi-tenant isolation by `app_id` / `user_id` / `chat_id` / `run_id`
- orchestration ports and engine adapters
- workflow loading and pack coordination
- UI tool protocol and bidirectional response plumbing
- shared frontend surface semantics (`ask`, `workflow`, `view`)
- auth, observability, metrics, token accounting

Core may define **how** the system executes and renders shared primitives.

Core must not define **what product** gets built on top.

### 2. Product Owns First-Party Opinions and Commercial Features

The mozaiks.ai product is a first-party app running on the core.

Examples:

- app builder workflows
- generator UX and prompt strategy
- managed provisioning and deployment flows
- billing, subscriptions, plans, quotas, entitlements
- branded shells, first-party navigation, admin experiences
- template catalogs, starter kits, vertical-specific accelerators
- hosted onboarding, support workflows, analytics dashboards

Product may consume core APIs and runtime contracts.

Product must not modify the core every time it wants a new business behavior.

### 3. App Bundles Own Application-Specific Behavior

An app bundle is the generated or authored definition of an actual app.

Examples:

- workflows and `_pack/workflow_graph.json`
- tools, hooks, structured outputs, handoffs
- workflow-specific React components
- pages, modules, routes, navigation, themes
- app CRUD models, services, settings, forms, dashboards
- app-specific artifact schemas and module handlers

App bundles answer:

- what workflows exist
- what pages and modules exist
- what artifacts the app produces
- what the user sees and does

App bundles should be treated as **runtime inputs**, not as runtime extensions.

### 4. AG2 Owns Native Agent Semantics Unless Proven Otherwise

If AG2 can already do something natively, prefer AG2-native configuration before inventing new runtime machinery.

Examples that should stay engine-native when possible:

- handoffs
- `context_conditions`
- input requests / handoff-to-user
- groupchat turn progression
- agent reply policies

Core should extend around AG2 where the gap is real:

- persistence
- multi-run fan-out/fan-in
- frontend event streaming
- UI tool correlation and response routing
- artifact persistence and retrieval
- app-level surface management

---

## Current Repo Mapping

In this repo snapshot, the logical layers map approximately like this:

| Logical layer | Current implementation paths |
|---|---|
| **Core runtime** | `mozaiksai/core/`, `mozaiksai/core/transport/`, `mozaiksai/core/events/`, `mozaiksai/core/workflow/`, `mozaiksai/core/adapters/` |
| **Shared frontend runtime** | `chat-ui/src/` |
| **First-party product/platform** | `platform/`, product-facing modules, first-party workflows, hosted/admin concerns |
| **App bundle content** | `platform/workflows/`, `platform/modules/`, workflow UI components, declarative configs |
| **Docs/generator guidance** | `docs/guides/`, `docs/reference/deep-dives/`, architecture docs |

Long-term repo splitting is optional. Logical separation is not optional.

---

## What Belongs in Core

A feature belongs in core if all of the following are true:

1. It is reusable across many apps.
2. It is not specific to mozaiks.ai's commercial model.
3. It defines execution or shared UX semantics, not domain content.
4. It can be described without naming a specific workflow, vertical, or business use case.

Examples:

- `SimpleTransport` and WebSocket protocol concerns
- `OrchestrationPort` and AG2 adapter wiring
- `WorkflowPackCoordinator`
- artifact persistence/query contracts
- `ChatUIContext` / surface reducer semantics
- event dispatch/envelope normalization

If removing the first-party product still leaves the feature necessary, it probably belongs in core.

---

## What Belongs in Product

A feature belongs in the mozaiks.ai product if any of the following are true:

1. It exists to acquire, monetize, provision, or support customers.
2. It reflects first-party UX opinions rather than generic runtime necessity.
3. It is a generator-specific workflow or managed-hosting concern.
4. Self-hosters should not need it to run apps successfully.

Examples:

- app generation flows
- awards/admin/operator dashboards
- provisioning and deployment orchestration
- subscription enforcement and monetization UX
- template marketplace or prompt recipes

If self-hosters would reasonably say "that is your SaaS business, not my runtime," it belongs in product.

---

## What Belongs in an App Bundle

A feature belongs in the app bundle if it answers a domain or UX question for one application.

Examples:

- "What pages does this app expose?"
- "What workflows does this app run?"
- "What modules and CRUD surfaces exist?"
- "What artifact components should render this workflow output?"
- "What settings, forms, and navigation structure exist?"

Generated output should target an app bundle contract, not core internals.

That means the generator should emit things like:

- workflow configs
- module definitions
- page/components
- navigation/theme config
- app data entities and settings definitions
- action handlers and service stubs

The generator should not emit:

- patches to transport internals
- direct dependencies on runtime private classes
- product-only assumptions disguised as kernel contracts

---

## Decision Test

Use this test whenever a new capability is proposed.

### Put it in Core if:

- every app could benefit from it
- it changes execution/runtime behavior
- it belongs to the contract between backend and frontend
- it belongs to the contract between runtime and engine

### Put it in Product if:

- it is a first-party managed-service concern
- it reflects mozaiks.ai business policy
- it exists mainly to help users generate or deploy apps

### Put it in the App Bundle if:

- it defines one app's workflows, modules, pages, or data
- it changes user-facing behavior for one app, not all apps
- it can be expressed declaratively or through app-owned stubs/components

### Keep it in AG2 if:

- AG2 already supports the behavior natively
- the main need is better configuration, documentation, or contract discipline
- replacing it in core would duplicate engine semantics

---

## Anti-Patterns

These are the main architectural traps to avoid.

### 1. Event System as a Second Business Logic Engine

The event system should coordinate runtime state, persistence, and streaming.

It should not become a shadow workflow language where product logic hides in event listeners.

### 2. Everything Becomes a Workflow

Not every feature is a groupchat.

Use:

- **Mode 1** for conversational or multi-step reasoning
- **Mode 2** for targeted triggered actions
- **Mode 3** for normal app pages, CRUD, dashboards, settings, and service interactions

### 3. Everything Becomes a Chat Box

Chat is the control plane, not the only UI surface.

Persistent apps still need:

- pages
- modules
- forms
- dashboards
- artifact views
- navigation

### 4. Product Needs Hard-Code the Kernel

If mozaiks.ai wants a new commercial behavior, do not immediately bake it into core.

First ask whether the need can live in:

- product workflows
- app bundle schema
- platform extensions
- product services

### 5. Reimplementing AG2 Without a Proven Gap

Do not replace AG2-native semantics just because the current prompts/docs are unclear.

Only add core machinery when:

- AG2 cannot support the behavior
- or AG2 can support it, but the runtime still needs a cross-cutting contract around it

---

## Generator Implications

The generator should target a **bundle contract**, not a runtime patch surface.

That means the generator's job is to produce:

- app structure
- workflow definitions
- module/page definitions
- UI components
- data entity definitions
- navigation/theme/settings config
- action and service stubs

The generator's job is not to:

- invent new transport semantics on demand
- create product-specific hacks in the core
- bypass runtime contracts because a feature is urgent

mozaiks.ai is both:

- a first-party product
- and the first major consumer of the core

That is good. It provides pressure on the runtime.

But it also means first-party urgency must not be mistaken for universal kernel truth.

---

## Repo Audit Checklist

Use this checklist when reviewing current or proposed code.

### Runtime audit

- [ ] Does this code introduce app-specific logic into `mozaiksai/core/`?
- [ ] Does this code assume one workflow, one product, or one business domain?
- [ ] Does this code duplicate AG2-native behavior instead of adapting it?
- [ ] Does this code create a new runtime contract that every app must now carry?

### Product audit

- [ ] Is this feature really a managed-platform concern rather than a runtime primitive?
- [ ] Could this live as a product workflow, module, or service instead of in core?
- [ ] Would a self-hoster need this to run a normal app?

### App bundle audit

- [ ] Is this behavior specific to one application or domain?
- [ ] Can it be expressed declaratively or as app-owned stubs/components?
- [ ] Should this be a page/module/action instead of a workflow?

### Frontend surface audit

- [ ] Is the code changing shared surface semantics or just product composition?
- [ ] Does it belong in shared runtime UI (`chat-ui`) or in app-specific components?
- [ ] Is chat being used as the only surface when a module/page/artifact should exist?

---

## Final Rule

When there is ambiguity, use this sentence:

**Core defines how apps execute. Product defines how mozaiks.ai operates. App bundles define what a specific app is.**

If a change cannot be defended clearly against that sentence, it is probably in the wrong layer.

---

## Related Documents

- [workflow-architecture.md](workflow-architecture.md)
- [app-creation-guide.md](app-creation-guide.md)
- [canonical-app-structure.md](canonical-app-structure.md)
- [ui-surface-and-layout-architecture.md](ui-surface-and-layout-architecture.md)

