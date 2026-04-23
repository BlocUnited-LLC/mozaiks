# Agentic App Generation Strategy

**Status:** Canonical pre-implementation strategy
**Purpose:** Define the best way to decompose user intent into a deterministic, customizable, agentic application.

## Why This Document Exists

Mozaiks already has strong runtime pieces:

- workflow orchestration
- session routing
- declarative workflow packs
- app UI primitives
- generator workflows

What is still unsettled is the **product assembly model**:

- how user intent should be decomposed
- what should be deterministic product functionality vs agentic behavior
- how persistent app pages should be represented
- how current builder workflows should fit together without becoming a user-facing maze

This document is the north-star plan for that layer.

For the existing-app track specifically, the companion reference is
[existing-app-augmentation-strategy.md](./existing-app-augmentation-strategy.md).

## Core Principles

1. **One user-facing build journey**
   Users should think in terms of:
   - create app
   - connect existing app
   - refine app

   They should not need to understand `ValueEngine`, `DesignDocs`, `AppGenerator`, `AgentGenerator`, or `ExistingAppDiscovery` as separate product concepts.

2. **Artifacts first, workflows second**
   Decompose intent into typed planning artifacts first.
   Workflows are internal compilers and execution stages, not the primary product model.

3. **Deterministic product surfaces first**
   If a capability still makes sense with AI turned off, it should begin as deterministic product behavior.

4. **Agentic behavior is augmentation**
   AI should be attached where it adds reasoning, review, optimization, summarization, or orchestration value.
   It should not be the default implementation model for normal product features.

5. **Persistent app UI stays declarative**
   Persistent pages should not default to arbitrary raw React generation.
   They should compile from a higher-level experience model into a controlled page system.

6. **Refinement re-enters by artifact layer**
   Follow-up changes should be routed based on which artifact changed, not by rerunning the whole generation stack.

7. **Existing-app augmentation first**
   For existing products, the default path is augment -> bridge -> ecosystem-bind -> migrate selectively.
   Mozaiks should not assume a full rewrite is the first move.

## The Canonical Artifact Model

The correct decomposition chain is:

1. `RequestIntent`
2. `ProductSpec`
3. `CapabilitySpec[]`
4. `ExperienceSpec`
5. `AgentAugmentationPlan`
6. `BuildGraph`

These are the canonical planning artifacts. Everything else should compile from them.

### 1. RequestIntent

Classifies what the user is asking for.

Canonical values:

- `new_app`
- `existing_app`
- `refinement`
- `agent_only`

This is the first routing decision.

### 2. ProductSpec

The canonical product definition.

It should capture:

- app purpose
- target users
- domain summary
- core business objects
- non-goals
- constraints
- integrations
- business model or operating mode
- whether the product is mostly deterministic, mostly agentic, or mixed

`ProductSpec` is the artifact the user is effectively approving at the concept layer.

### 3. CapabilitySpec[]

This replaces the earlier “capability pack” framing as the canonical planning language.

A `CapabilitySpec` is a product capability, not an implementation task.

Examples:

- messaging
- marketplace
- campaigns
- billing
- notifications
- analytics
- admin CRUD

Each `CapabilitySpec` should answer:

- what user-facing capability exists
- which entities it owns
- which actions it supports
- which pages or surfaces it needs
- which integrations it depends on
- whether any agentic augmentation is required

Important:

- this is a product planning unit
- it is not a codegen task
- it is not a workflow definition

### 4. ExperienceSpec

Defines the persistent application experience.

It should include:

- navigation model
- page list
- page archetypes
- section layouts
- theme/branding intent
- design constraints
- custom slots where necessary

`ExperienceSpec` is what persistent app UI compiles from.
In the current Mozaiks workflow chain, that intent should begin as structured
`brand_intent` in `ConceptBlueprint`, survive into `AppBuildPlan`, and only
then compile into `theme_config_patch`, `shell_config`, `asset_manifest`, or page-level primitive composition.

### 5. AgentAugmentationPlan

Defines where AI actually belongs.

It should answer:

- which capabilities need workflows
- which app actions invoke workflows
- which workflows read/write which entities
- which workflow UIs appear in chat, artifacts, or transitions
- where human approval is required

This keeps agentic functionality explicit rather than implicit.

### 6. BuildGraph

The execution artifact.

It should contain:

- deterministic implementation tasks
- owned paths
- dependencies
- validation rules
- artifact outputs

This is where execution planning belongs. Not in `ProductSpec`, not in `CapabilitySpec`.

## Product Capabilities vs Agentic Augmentation

This boundary is the most important one.

### Example: Direct Messaging

Direct messaging is primarily deterministic product behavior:

- conversations
- messages
- inbox page
- thread page
- realtime delivery
- read receipts

Possible agentic augmentation:

- summarize thread
- draft reply
- moderation review

The app should work without the AI layer.

### Example: Marketplace + Campaigns

These are product capabilities first:

- marketplace listings
- discovery/search
- campaign management
- billing and settlement
- analytics

Possible agentic augmentation:

- campaign optimization
- listing recommendations
- fraud/risk review
- growth suggestions

Again: product first, AI second.

## Persistent App UI Strategy

Mozaiks should not choose between only two bad extremes:

- completely freeform page React
- extremely low-level primitive-only planning

The right model is a three-layer persistent UI contract:

1. **Page archetypes**
   - dashboard
   - entity list
   - entity detail
   - form page
   - feed
   - thread
   - marketplace grid
   - analytics overview

2. **Primitive composition**
   The runtime compiles archetypes into the shipped primitive system.

3. **Custom slots**
   Explicit extension points for novel surfaces without making arbitrary page React the default.

This keeps pages deterministic while still allowing real customization.

## Builder Workflow Roles

Current workflows can stay, but they should be understood as **internal compilers**, not first-class user-facing products.

### ValueEngine

Owns early concept synthesis.

Target responsibility:

- produce `ProductSpec`
- help classify high-level `CapabilitySpec[]`
- capture the initial business shape and constraints

### ExistingAppDiscovery

Owns existing-product intake.

Target responsibility:

- produce `ExistingProductSpec`
- map current `CapabilitySpec[]`
- recommend an `AgentAugmentationPlan`
- identify reusable surfaces, integrations, and current architecture constraints
- default to guided plain-language onboarding first, advanced operator inputs second
- support known presets for first-class host products such as the Mozaiks dogfood case

This should feed the same downstream planner as `ValueEngine`, not fork the architecture.

### DesignDocs

Should become an internal design elaboration stage, not a separate user mental model.

Target responsibility:

- elaborate `ExperienceSpec`
- elaborate backend/data design intent where needed
- provide richer guidance to deterministic compilers

### AppGenerator

Owns the deterministic product bundle.

Target responsibility:

- compile `CapabilitySpec[]` + `ExperienceSpec` into:
  - modules/services
  - persistent page schemas
  - navigation
  - `brand/theme_config.json` visual token artifacts
  - `config/shell.json` shell content/behavior artifacts
  - `config/asset_manifest.json` media inventory artifacts
  - auth/integrations
  - deterministic build tasks

Current implementation note:

- `save_app_schema` writes these artifacts to
  `$MOZAIKS_GENERATED_ARTIFACTS_PATH/apps/{app_id}/{build_id}/app/`.
- The generated app bundle is not active until an explicit promotion step copies
  validated files into an active app root.

### AgentGenerator

Owns the agentic bundle.

Target responsibility:

- compile `AgentAugmentationPlan` into:
  - workflows
  - tools
  - handoffs
  - agent UI surfaces
  - transition/session surfaces where needed

Current implementation note:

- `workflow_converter.py` writes generated workflow bundles to
  `$MOZAIKS_GENERATED_ARTIFACTS_PATH/workflows/{app_id}/{build_id}/{workflow_name}/`.
- Generated workflows are not runtime-loaded until explicitly promoted into an
  active app root's `workflows/` directory.

## User-Facing Build Journey

The product experience should feel like one journey:

### New app

`RequestIntent(new_app) -> ProductSpec -> CapabilitySpec[] -> ExperienceSpec + AgentAugmentationPlan -> BuildGraph -> Product bundle + Agent bundle`

### Existing app

`RequestIntent(existing_app) -> ExistingProductSpec -> CapabilitySpec[] -> AgentAugmentationPlan -> same downstream planning path`

### Refinement

`RequestIntent(refinement) -> artifact-layer classification -> targeted re-entry`

The user should not feel like they are hopping between unrelated workflows.

## Refinement Routing Model

Refinements should route by artifact layer:

- product concept change -> `ProductSpec`
- capability change -> `CapabilitySpec[]`
- page/theme/layout change -> `ExperienceSpec`
- AI behavior change -> `AgentAugmentationPlan`
- implementation-only fix -> `BuildGraph`

This is the cleanest way to make revisions persistent without rerunning everything.

## Transitional Mapping to Current Placeholder Logic

Current repository code still contains temporary naming and placeholder contracts from earlier iterations.

For now, interpret them like this:

- `capability_pack_*` -> temporary placeholder for `CapabilitySpec`
- `build_plan` -> temporary placeholder for `BuildGraph`
- `theme_preferences` + `brand_intent` + page planning hints -> partial placeholder for `ExperienceSpec`
- agent workflow planning outputs -> partial placeholder for `AgentAugmentationPlan`

This mapping is temporary. The target language should move toward the artifact model in this document.

## Recommended Functional First Milestone

The first truly functional milestone should not try to solve every app type.

It should prove this path:

1. user requests a new app
2. system produces `ProductSpec`
3. system resolves 1-2 `CapabilitySpec` items
4. system produces `ExperienceSpec` with page archetypes
5. `AppGenerator` emits deterministic app bundle artifacts
6. optionally attach one agentic augmentation through `AgentGenerator`
7. user can refine one artifact layer without rerunning the full stack

Suggested proof scenario:

- a simple social/productivity app with:
  - auth/admin CRUD
  - messaging
  - one agentic augmentation such as thread summary

## Non-Goals

This strategy does not require:

- collapsing all workflows into one runtime file
- generating arbitrary page React by default
- making every capability agentic
- exposing internal workflow topology to the user

## Decision Summary

The best decomposition model for Mozaiks is:

`Intent -> ProductSpec -> CapabilitySpec[] -> ExperienceSpec + AgentAugmentationPlan -> BuildGraph -> Bundles`

That is the strategy future docs, prompts, validators, and runtime changes should align to.
