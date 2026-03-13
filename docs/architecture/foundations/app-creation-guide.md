# App Creation Guide

**Last updated:** 2026-03-12  
**Status:** Current architecture reference  
**Audience:** App-bundle authors, generator authors, and platform maintainers

---

## Purpose

This guide explains how Mozaiks should turn user intent into a structured app
bundle.

The important shift is:

- do not think in terms of `backend/` and `frontend/` first
- do not start by generating loose code files
- start from app intent
- decompose that intent into typed app concerns
- compile those concerns into the `platform/` bundle

If another doc is vague about how to move from a user idea to concrete app
files, this doc wins.

---

## The Core Problem

Most app generators fail here:

1. user gives a broad idea
2. the system jumps straight into code generation
3. one agent tries to build everything
4. the result is flimsy, shallow, or structurally inconsistent

Mozaiks should do the opposite.

It should decompose app intent before it writes bundle files.

---

## The Correct Mental Model

Mozaiks is not compiling intent directly into:

```text
backend/
frontend/
```

That tree is too implementation-specific and too unconstrained for scalable
generation.

Mozaiks should compile intent into a structured app bundle under `platform/`.

That means the builder should reason in these families:

- `app`
- `ai`
- `shell`
- `theme`
- `modules`
- `entities`
- `views`
- `actions`
- `policies`
- `workflows`

Then the runtime and frontend consume those declaratives.

See also:

- [Canonical App Structure](canonical-app-structure.md)
- [App Bundle Declaratives](app-bundle-declaratives.md)
- [App Planning Contracts](app-planning-contracts.md)

---

## The Decomposition Pipeline

The intended path is:

```text
User Intent
  -> Capability Map
  -> App Model
  -> Execution Model
  -> Bundle Plan
  -> Compiled platform/ files
```

Each stage answers a different question.

### 1. User Intent

Start with the raw user request.

Example:

- `build me a campus marketplace`
- `I want an app for comedy club bookings and lineup management`
- `build a creator drop platform with launch rooms`

This is too vague to build directly.

### 2. Capability Map

Turn the request into concrete capabilities.

Use verb+noun statements.

Example:

- browse listings
- create listing
- save favorites
- message seller
- moderate reports
- manage event schedule
- generate launch copy

At this stage, do not decide files yet.

### 3. App Model

Now classify capabilities into app-bundle concerns.

This is the step that has been missing.

For each capability, decide:

- does it require a durable data entity?
- does it require a persistent page or module?
- does it require a triggered action?
- does it require an AI workflow?
- does it require a policy or permission rule?

That produces:

- `EntitySpec`
- `ViewSpec`
- `ActionSpec`
- `ModuleSpec`
- `WorkflowSpec`
- `PolicySpec`

### 4. Execution Model

After the app model is defined, decide how the capability should execute.

Use these buckets:

- `workflow`
  - conversational, multi-step, HITL, agentic
- `action`
  - deterministic mutation, button/API trigger, service call
- `module`
  - durable page or application surface

This prevents the common failure mode where everything gets forced into chat.

### 5. Bundle Plan

Now define which files must exist in `platform/`.

Examples:

- `platform/entities/listing.json`
- `platform/views/listings_list.json`
- `platform/actions/save_favorite.json`
- `platform/modules/marketplace_home/module.json`
- `platform/workflows/Concierge/orchestrator.yaml`

This is the first stage where file generation becomes appropriate.

### 6. Compile

Finally, compile the plan into the real bundle.

This is where code generation happens.

The generated code should be constrained by the declaratives, not invented from
scratch without structure.

---

## The Classification Matrix

This is the practical rule Mozaiks needs when decomposing capabilities.

| Question | If yes | Output |
|---|---|---|
| Does the app need to persist this as business data? | yes | `EntitySpec` |
| Does the user need a durable page/screen for it? | yes | `ModuleSpec` + `ViewSpec` |
| Is it a list/detail/create/edit/filter/search concern? | yes | `ViewSpec` |
| Is it a deterministic mutation or service call? | yes | `ActionSpec` |
| Does it require reasoning, orchestration, or multi-turn conversation? | yes | `WorkflowSpec` |
| Does access differ by role, plan, or tenant? | yes | `PolicySpec` |

Examples:

| Capability | Result |
|---|---|
| `create listing` | entity + action + form/detail view |
| `browse listings` | list view + module |
| `generate pitch options` | workflow |
| `approve final plan` | workflow checkpoint or action, depending on complexity |
| `message seller` | entity + module + optional workflow if AI mediation is involved |
| `regenerate product description` | action or workflow depending on whether conversational iteration is required |

---

## What Counts As CRUD In Mozaiks

CRUD is not a separate world outside the platform.

In Mozaiks, CRUD should come from:

- entities
- views
- actions
- modules
- policies

That means:

- data structure is typed
- pages are generated from view contracts
- actions are declared
- modules surface the behavior

This is how CRUD becomes dynamic and scalable without every app turning into a
one-off hand-built codebase.

---

## What Counts As AI In Mozaiks

AI behavior should come from workflows.

Use workflows when you need:

- multi-turn reasoning
- handoffs
- human checkpoints
- orchestration
- decomposition
- tool use with contextual reasoning

Do not use a workflow just because a feature sounds advanced.

Examples:

- `generate 5 pitch directions` -> workflow
- `summarize user requirements` -> workflow
- `classify a change request` -> workflow or structured action
- `save profile form` -> not a workflow

---

## A Simple Example

Intent:

- `Build a comedy club operating system`

Capability map:

- manage lineup
- archive sets
- generate roast directions
- review performer brief
- show crowd scoreboard

App model:

- entities:
  - `Performer`
  - `Set`
  - `Show`
- views:
  - lineup board
  - archive list
  - performer detail
- actions:
  - save performer
  - publish lineup
  - archive set
- modules:
  - `lineup_board`
  - `show_archive`
- workflows:
  - `GreenRoom`
  - `WritersRoom`
  - `MainStage`

Compiled bundle output:

- `platform/modules/lineup_board/*`
- `platform/modules/show_archive/*`
- `platform/workflows/GreenRoom/*`
- `platform/workflows/WritersRoom/*`
- `platform/workflows/MainStage/*`
- future:
  - `platform/entities/*.json`
  - `platform/views/*.json`
  - `platform/actions/*.json`

---

## The Planning Artifacts Mozaiks Needs

To make this scalable, generators should not jump straight to files.

They should emit typed planning artifacts first.

Recommended planning artifacts:

- `AppSpec`
- `CapabilityMap`
- `EntitySpec[]`
- `ViewSpec[]`
- `ActionSpec[]`
- `ModuleSpec[]`
- `WorkflowSpec[]`
- `PolicySpec[]`
- `BundlePlan`

Then the compiler/generator turns those into the `platform/` files.

This is what keeps the system opinionated.

These contracts are now typed in runtime at:

- `mozaiksai/core/orchestration/planning_contracts.py`

---

## What The Builder Should Actually Do

The builder should follow this sequence:

1. interview and refine intent
2. create `AppSpec`
3. derive a capability map
4. classify capabilities into:
   - entities
   - views
   - actions
   - modules
   - workflows
   - policies
5. create a `BundlePlan`
6. decompose the plan into a build `TaskGraph`
7. generate the actual files

This is how Mozaiks can scale beyond workflow-only demos.

---

## What Not To Do

Do not:

- treat every capability as a workflow
- generate raw backend/frontend file trees first
- let one agent freestyle all app structure
- collapse CRUD, actions, and workflows into one vague “feature” bucket
- ask the runtime to infer app shape from prose after generation starts

---

## Recommended AI-Agent Workflow

When an AI coding agent is involved, do not ask it to “build the app” in one
shot.

Instead:

1. use a decomposition prompt pack
2. have it emit typed planning artifacts
3. review the resulting plan
4. then let it compile the plan into `platform/` files

See:

- [Prompt Packs For AI Coding Agents](../../instruction-prompts/prompt-packs.md)
- [Prompt Pack: Decompose App Intent](../../instruction-prompts/app-planning/decompose-app-intent.md)

---

## Next Reading

- [App Bundle Declaratives](app-bundle-declaratives.md)
- [Canonical App Structure](canonical-app-structure.md)
- [App Planning Contracts](app-planning-contracts.md)
- [Builder Execution Model](builder-execution-model.md)
- [App Builder Architecture](app-builder-architecture.md)
