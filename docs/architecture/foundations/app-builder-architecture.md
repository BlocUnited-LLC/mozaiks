# App Builder Architecture

This document defines the exact architecture for the Mozaiks app builder as a product.

It answers these questions:

- What does the user actually experience?
- Which parts are visible vs hidden?
- When do `ActionPlan`, Mermaid, API-key collection, and live build surfaces appear?
- How does `ChangeIntent` drive routing without turning the experience into a visible workflow maze?

When builder UX docs conflict with this document, this document wins.

For canonical builder/orchestration terminology, also see
[builder-orchestration-taxonomy.md](builder-orchestration-taxonomy.md).

For the runtime control layer that keeps one builder session coherent across hidden workflow switches, also see
[app-builder-state-and-routing.md](app-builder-state-and-routing.md).

For the generic core contracts that builder-specific semantics sit on top of, also see
[runtime-state-and-control-events.md](runtime-state-and-control-events.md).

---

## Core Rule

The user experiences **one builder session**.

The runtime may route across many workflows internally, but the user should not feel like they are inside a wizard of separate systems.

So the product contract is:

- one conversation
- one evolving plan
- one live build surface
- one preview surface
- one iteration loop

Not:

- one visible workflow for value
- one visible workflow for agents
- one visible workflow for CRUD
- one visible workflow for UI

Those may exist internally, but they are not the user-facing model.

---

## Product Layers

### 1. User-Facing Session

The user thinks they are interacting with one thing:

- `AppBuilder`

This is the branded product workflow.

### 2. Internal Build Workflows

Behind the scenes, `AppBuilder` may route across:

- `ValueEngine`
- `SystemPlanner`
- `BuildApp`
- `ValidationEngine`

The user does not need to know those names unless they inspect advanced details.

### 3. Core Runtime Primitives

Core provides:

- universal orchestration
- workflow-level MFJ
- pause / resume
- typed routing contracts
- event transport
- UI tool round-trips
- persistence

The builder product consumes those primitives.

---

## Canonical Internal Flow

The internal architecture should look like this:

```text
User Intent
   |
   v
AppBuilder Session
   |
   +--> ValueEngine
   |      |
   |      +--> AppSpec
   |
   +--> SystemPlanner
   |      |
   |      +--> ActionPlan
   |      +--> TaskGraph
   |
   +--> Approval / Setup Gate
   |      |
   |      +--> API keys / provider config / deployment choices
   |
   +--> BuildApp
   |      |
   |      +--> MFJ Wave 1
   |      +--> Fan-in
   |      +--> MFJ Wave 2
   |      +--> Fan-in
   |      +--> ...
   |
   +--> ValidationEngine
   |
   +--> Preview
   |
   +--> Change Request
          |
          +--> ChangeIntent
                 |
                 +--> ValueEngine or BuildApp
```

---

## What the User Sees

The user should only see five major surfaces.

### 1. Discovery Chat

Purpose:

- capture the app idea
- clarify intent
- refine the app concept

Visible UI:

- normal conversational chat
- optional lightweight inline cards summarizing canon as it evolves

Hidden internals:

- `ValueEngine`
- `AppSpec` creation

### 2. Plan Review

Purpose:

- explain what the system intends to build
- let the user approve before expensive work starts

Visible UI:

- `ActionPlan` artifact
- Mermaid execution diagram artifact
- optional compact inline summary card in chat

What the user should understand here:

- what this app is
- what modules/features will be built
- what the first build wave will do
- whether any external setup is required

### 3. Setup Gate

Purpose:

- collect missing prerequisites only after the plan is real

Visible UI:

- API key collection component
- provider/environment chooser
- optional deployment options

Important timing rule:

Do not ask for API keys at the start of the conversation.

Ask for them only when:

- the `ActionPlan` is approved
- the planner has discovered required integrations
- those integrations are actually needed for the next execution wave

Examples:

- Stripe key only if billing/payments are in scope
- OpenAI/Anthropic provider key only if the generated app itself needs external model access
- deployment credentials only at deploy/setup time, not at ideation time

### 4. Live Build Surface

Purpose:

- make execution visible
- reduce anxiety
- show real progress

Visible UI:

- task board
- wave progress
- file write feed
- Monaco/E2B file updates
- module/feature completion markers

This is the primary execution surface.

The user should see:

- current wave
- tasks in progress
- completed tasks
- failed or blocked tasks
- files appearing in real time

### 5. Preview and Iteration

Purpose:

- let the user try the app
- collect bounded feedback
- classify changes

Visible UI:

- preview panel
- change request prompt
- optional “this change affects X modules / Y files” review card

Hidden internals:

- `ChangeIntent`
- route to `ValueEngine` or `BuildApp`
- impact analysis
- scoped rebuild waves

---

## What Gets Visualized and When

### ActionPlan

Show:

- after `AppSpec` is good enough
- before any build wave starts

Do not show:

- too early during ideation
- after build has already started unless the plan materially changes

The `ActionPlan` should visualize:

- app summary
- modules/features
- AI workflow components
- non-AI CRUD/data entities
- UI surface plan
- integration requirements
- first-wave build tasks
- dependency highlights

### Mermaid Diagram

Show:

- alongside the `ActionPlan`
- after the plan is stable enough to explain execution

It should visualize:

- plan phases
- approval checkpoint
- setup gate
- build waves
- integration
- preview

It should not dump every low-level runtime concept onto the user.

### API Key UI

Show:

- after plan approval
- only for integrations actually required by the approved plan
- before the first wave that depends on them

Not before.

This avoids the “give me keys before I even know what we’re building” anti-pattern.

### Live Build Board

Show:

- immediately after setup gate is satisfied
- during every build wave

It should visualize:

- current wave index
- ready/running/completed/blocked tasks
- recent file writes
- errors that need intervention

### Change Review Card

Show:

- after preview
- when the user asks for a change

It should visualize:

- whether this is a spec change or build change
- which modules/files are likely affected
- whether this will trigger a scoped rebuild or a return to planning

---

## Internal Workflow Map

### ValueEngine

Owns:

- ideation
- canonization
- `AppSpec`
- foundational change review

Should visualize to the user:

- discovery chat
- lightweight canon summaries

Should not visualize:

- raw routing objects
- runtime orchestration details

### SystemPlanner

Owns:

- decomposition
- `ActionPlan`
- `TaskGraph`
- dependency grouping
- required integrations discovery

Should visualize to the user:

- `ActionPlan`
- Mermaid diagram
- “required setup” list

### BuildApp

Owns:

- task scheduling
- MFJ waves
- live writes
- integration and assembly

Should visualize to the user:

- task board
- file activity
- wave completion

### ValidationEngine

Owns:

- sanity checks
- preview readiness
- optional test/health validation

Should visualize to the user:

- concise preview-ready summary
- blockers only when they matter

---

## Kernel Fix: ChangeIntent

`ChangeIntent` should be the first-class routing contract in core.

This is the kernel fix because it replaces weak prose-based routing with a reusable typed object.

Minimum canonical fields:

```json
{
  "change_type": "FOUNDATIONAL",
  "change_scope": "foundational",
  "requires_appspec_revision": true,
  "requires_replan": true,
  "requires_new_iteration": true,
  "target_workflow": "ValueEngine",
  "rationale": "request changes product identity and architecture",
  "confidence": 0.9
}
```

This object is what the universal orchestrator should consume.

### Important rule

Not every agent should emit `ChangeIntent`.

Only these roles should:

- `ChangeClassifierAgent`
- `WorkflowTransferAgent`
- optional review/host agents whose explicit job is escalation or rerouting

Normal builder agents should not carry `ChangeIntent` instructions in their prompts.

Do not pollute every prompt with routing responsibilities.

### Practical rule

Treat `ChangeIntent` like a reusable agent contract pack:

- one shared schema
- one shared prompt contract
- only attached to the agents that need it

This is the right analogue to a `SKILLS.md` idea.

It should not be:

- ad hoc prompt text copied into every agent
- a global instruction all agents are forced to care about

---

## How Agents Should Use ChangeIntent

There are two valid ways.

### 1. Free-text classification path

Used when the user says something in chat and no explicit typed routing object exists yet.

Flow:

1. user writes free text
2. `ChangeClassifierAgent` or core classifier produces `ChangeIntent`
3. universal orchestrator routes from `ChangeIntent`

### 2. Structured agent-output path

Used when an agent explicitly decides that the next action is a reroute.

Flow:

1. agent emits structured `ChangeIntent`
2. runtime validates it
3. universal orchestrator routes from it directly

This is the cleanest path for advanced workflows.

---

## Agent Contract Packs

You should create reusable contract references for agents.

These are not runtime primitives. They are developer/generator aids.

Recommended contract packs:

- `ChangeIntent`
- `WorkflowTransferRequest`
- `AppSpec`
- `TaskGraph`
- `TaskResult`
- `ImpactSet`

Think of these as:

- reusable schema definitions
- reusable prompt fragments
- reusable structured-output targets

This is the Mozaiks equivalent of a skill/reference pack.

The generator agents should reference these packs when authoring workflows.

---

## Prompting Rule

Do not tell every agent:

- “you may emit ChangeIntent”

Tell only the correct role agents that:

- when your job is classification or escalation, emit `ChangeIntent`

This keeps prompts tight and avoids hallucinated routing behavior.

So the contract should be attached to:

- classifier agents
- transfer agents
- integration/host agents only if they truly own reroute decisions

Not:

- decomposers
- file writers
- page builders
- CRUD builders
- UI builders

---

## Exact User-Facing Timeline

### Phase 1: Discovery

User sees:

- chat only

System does:

- `ValueEngine`
- produce `AppSpec`

### Phase 2: Plan Review

User sees:

- `ActionPlan`
- Mermaid
- concise approval CTA

System does:

- `SystemPlanner`
- produce `TaskGraph`
- detect required integrations

### Phase 3: Setup

User sees:

- API-key / provider gate only if required

System does:

- collect missing setup state

### Phase 4: Build

User sees:

- live task board
- file activity
- Monaco/E2B updates

System does:

- execute MFJ waves from `TaskGraph`

### Phase 5: Preview

User sees:

- preview
- “tell us what to change”

System does:

- validation
- preview handoff

### Phase 6: Iteration

User sees:

- change review summary
- scoped rebuild if appropriate

System does:

- classify to `ChangeIntent`
- route to `ValueEngine` or `BuildApp`

---

## Bottom Line

The exact architecture should be:

- one visible `AppBuilder` session
- many hidden internal workflows
- `ActionPlan` and Mermaid before build
- API keys only after plan approval and only when actually required
- live build board during MFJ waves
- `ChangeIntent` as the first-class routing contract in core
- reusable contract packs for the few agents that truly need routing authority

That gives you:

- first-class decomposition / universal orchestration / MFJ in core
- a coherent user experience
- disciplined routing instead of prose chaos
- a builder that is bigger than “just building groupchats”

