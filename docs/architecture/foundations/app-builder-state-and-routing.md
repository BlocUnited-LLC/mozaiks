# App Builder State And Routing

This document defines the missing layer between:

- the visible `AppBuilder` user experience
- the execution primitives in core (`UniversalOrchestrator`, `WorkflowPackCoordinator`, `JourneyOrchestrator`, `OrchestrationPort`)

It answers these questions:

- What does `WorkflowPackCoordinator` actually solve?
- What did the old kernel solve that MFJ alone does not solve?
- What is the minimal runtime state-and-routing layer we should restore?
- How should we build it?
- Is this useful beyond the Mozaiks app builder?

When builder state and routing discussions conflict with looser notes, this document wins.

This document is a first-party builder specialization of the generic core runtime state and control-event contracts.

Read the generic contracts first:

- [runtime-state-and-control-events.md](runtime-state-and-control-events.md)

The concrete generic runtime contracts live in:

- [contracts.py](C:/Users/mbari/OneDrive/Desktop/BlocUnited/BlocUnited%20Code/mozaiks/mozaiksai/core/control_plane/contracts.py)
- [planning_contracts.py](C:/Users/mbari/OneDrive/Desktop/BlocUnited/BlocUnited%20Code/mozaiks/mozaiksai/core/orchestration/planning_contracts.py)

---

## Core Point

`WorkflowPackCoordinator` is not the builder kernel.

It is one execution primitive inside the builder kernel.

Specifically:

- `WorkflowPackCoordinator` solves workflow-level `MFJ`
- it does not solve the whole user experience
- it does not solve cross-workflow rerouting
- it does not solve plan/setup/preview/build-state continuity

What the old kernel was trying to do was valid.

What was wrong was not the idea of a runtime state-and-routing layer.
What was wrong was that one abstraction started doing too many jobs.

The fix is not to bring back a giant kernel.
The fix is to restore a thin typed layer for state, routing, and durable product-facing facts.

---

## The Five Runtime Layers

### 1. Engine Layer

This is AG2.

Responsibilities:

- run agent conversations
- stream AG2 events
- handle handoffs inside one workflow
- pause for human input

Mozaiks boundary:

- [AG2OrchestrationAdapter](C:/Users/mbari/OneDrive/Desktop/BlocUnited/BlocUnited%20Code/mozaiks/mozaiksai/core/adapters/ag2_orchestration.py)
- [OrchestrationPort](C:/Users/mbari/OneDrive/Desktop/BlocUnited/BlocUnited%20Code/mozaiks/mozaiksai/core/ports/orchestration.py)

### 2. Execution Primitive Layer

This is the runtime machinery that executes workflow patterns.

Responsibilities:

- workflow run / resume / cancel
- workflow-level `MFJ`
- global sequential journeys
- UI round-trips

Mozaiks components:

- [WorkflowPackCoordinator](C:/Users/mbari/OneDrive/Desktop/BlocUnited/BlocUnited%20Code/mozaiks/mozaiksai/core/workflow/pack/workflow_pack_coordinator.py)
- [JourneyOrchestrator](C:/Users/mbari/OneDrive/Desktop/BlocUnited/BlocUnited%20Code/mozaiks/mozaiksai/core/workflow/pack/journey_orchestrator.py)
- [UniversalOrchestrator](C:/Users/mbari/OneDrive/Desktop/BlocUnited/BlocUnited%20Code/mozaiks/mozaiksai/core/orchestration/universal_orchestrator.py)

### 3. State And Routing Layer

This is the missing builder-state layer.

Responsibilities:

- maintain builder session state
- convert runtime facts into product-facing states
- persist plan/build/preview iteration facts
- drive UI surfaces from typed control events
- decide whether the session stays in build, replans, or returns to value/spec work

This layer should be typed and event-driven.

This is where `DomainEvent` is valuable.

This layer also depends on typed planning outputs (`DecompositionPackage`)
instead of freeform feature prose.

### 4. Product UX Layer

This is the user-facing builder session.

Responsibilities:

- discovery chat
- `ActionPlan`
- setup gate
- live build board
- preview
- change review

The user should feel one product experience here, not multiple hidden workflows.

### 5. Generated App Layer

This is the app-bundle output produced by the builder.

Responsibilities:

- declarative files
- registered modules
- workflows
- pages
- integrations

The builder writes this layer.

It does not modify `mozaiks core`.

---

## What WFC Solves

`WorkflowPackCoordinator` should remain narrow.

It solves:

- detect MFJ trigger agent output
- extract child specs/tasks
- pause parent
- spawn child runs
- wait for children
- merge results
- resume parent

That is all.

This is valuable and should stay in core.

But WFC should not own:

- builder session state
- `AppSpec` revision logic
- setup/API-key decisions
- visible plan state
- preview state
- change-review UX
- cross-workflow intent classification

So WFC is not obsolete.
It is just not the control plane.

---

## What MFJ Did Not Solve

MFJ solves execution parallelism.

It does not solve:

- when to show `ActionPlan`
- when to ask for API keys
- whether a user edit is local or foundational
- whether the builder should stay in `BuildApp` or route back to `ValueEngine`
- how the UI knows the session is in `plan_review`, `setup_gate`, or `preview`
- how the system keeps one coherent session while internally switching workflows

That is why MFJ alone never answered the full builder UX problem.

---

## The Thin State And Routing Layer We Should Keep

This layer should be a small typed layer above the execution primitives.

It should consume:

- `ChangeIntent`
- `WorkflowTransferRequest`
- `TaskGraph`
- `TaskResult`
- `ImpactSet`
- low-frequency `DomainEvent` facts

It should produce:

- builder session state
- visible UI surface state
- reroute decisions
- build iteration lineage

It should not consume raw AG2 event noise directly unless it has already been normalized into a stable runtime fact.

---

## Canonical State And Routing Facts

These are the kinds of events that deserve `DomainEvent` status.

They are low-frequency, durable facts about the builder state.

### Value and Planning

- `builder.appspec_created`
- `builder.appspec_revised`
- `builder.action_plan_created`
- `builder.action_plan_approved`
- `builder.action_plan_rejected`
- `builder.task_graph_created`
- `builder.task_graph_accepted`

### Setup

- `builder.setup_required`
- `builder.setup_completed`
- `builder.setup_blocked`

### Build Execution

- `builder.wave_started`
- `builder.wave_completed`
- `builder.wave_failed`
- `builder.build_completed`
- `builder.build_failed`

### Preview and Iteration

- `builder.preview_ready`
- `builder.preview_feedback_received`
- `builder.impact_set_computed`
- `builder.workflow_transfer_requested`
- `builder.iteration_started`

These are the events that make the builder feel coherent.

They are not AG2 events.
They are runtime/product control facts.

---

## Builder Session State Machine

The visible builder session should move through these states:

- `discovery`
- `plan_review`
- `setup_gate`
- `building`
- `preview`
- `change_review`
- `replanning`

These states should be driven by typed state-and-routing facts, not by scraping chat text.

### Example state transitions

`discovery -> plan_review`

- when `builder.action_plan_created` is emitted

`plan_review -> setup_gate`

- when the plan is approved and setup is required

`plan_review -> building`

- when the plan is approved and no setup is required

`setup_gate -> building`

- when `builder.setup_completed` is emitted

`building -> preview`

- when `builder.preview_ready` is emitted

`preview -> change_review`

- when user submits a change request

`change_review -> building`

- when `ChangeIntent` stays inside `BuildApp`

`change_review -> replanning`

- when `ChangeIntent` requires `ValueEngine` or substantial replanning

---

## Where ChangeIntent Fits

`ChangeIntent` is the routing contract that decides whether the visible session stays in build or returns to value/spec work.

Examples:

### Local change

User:

- "add a filter to this section"

Result:

- `change_type = SURFACE`
- stay in `BuildApp`
- compute `ImpactSet`
- run scoped patch tasks

### Foundational change

User:

- "actually make this a creator marketplace"

Result:

- `change_type = FOUNDATIONAL`
- route to `ValueEngine`
- revise `AppSpec`
- create a new build iteration

This is how the user stays in one session while the system does the right internal routing.

---

## How We Should Build It

Do this in phases.

### Phase 1. Keep the current core execution boundaries

Keep:

- `OrchestrationPort`
- `AG2OrchestrationAdapter`
- `WorkflowPackCoordinator`
- `JourneyOrchestrator`
- `UniversalOrchestrator`

Do not collapse them into one abstraction again.

### Phase 2. Make DomainEvent the typed state-and-routing envelope

Use `DomainEvent` only for low-frequency builder/runtime facts.

Do not route every AG2 text/tool/print event through it.

This preserves:

- durability
- replayability
- stable UI semantics

without rebuilding the old giant kernel.

### Phase 3. Add a small BuilderControlPlane service

This should be a runtime service that subscribes to typed builder events and maintains session state.

Responsibilities:

- persist current builder state
- persist current `AppSpec` version
- persist current `BuildIteration`
- map state-and-routing facts to visible UI surfaces
- emit state updates for the frontend

This service should not run AG2 directly.

It should react to typed runtime facts.

### Phase 4. Drive the UI from builder state, not agent prose

The frontend should respond to builder session state such as:

- `plan_review` -> show `ActionPlan`
- `setup_gate` -> show provider/API key UI
- `building` -> show live build board
- `preview` -> show preview
- `change_review` -> show impact/rebuild summary

### Phase 5. Add builder-specific DomainEvent emissions at orchestration boundaries

Examples:

- `ValueEngine` finalizes `AppSpec` -> emit `builder.appspec_created`
- `SystemPlanner` emits plan -> emit `builder.action_plan_created`
- plan approved -> emit `builder.action_plan_approved`
- setup required -> emit `builder.setup_required`
- WFC starts a wave -> emit `builder.wave_started`
- WFC completes a wave -> emit `builder.wave_completed`
- validation passes -> emit `builder.preview_ready`
- reroute requested -> emit `builder.workflow_transfer_requested`

### Phase 6. Add iteration lineage

The runtime should persist:

- `appspec_version`
- `build_iteration_id`
- `previous_iteration_id`

This is how you avoid “did we restart?” confusion while still supporting drastic pivots cleanly.

---

## What This Looks Like In Practice

```text
User -> AppBuilder session
          |
          v
   ChangeIntent / approvals / setup decisions
          |
          v
   BuilderControlPlane
          |
          +--> UniversalOrchestrator
          +--> JourneyOrchestrator
          +--> WorkflowPackCoordinator
          |
          v
   AG2 via OrchestrationPort
```

And on the UI side:

```text
BuilderControlPlane facts
    -> visible builder state
    -> ActionPlan
    -> setup gate
    -> build board
    -> preview
```

---

## Why This Matters Beyond The Builder

Yes, there is value far beyond the app builder.

The reusable value prop is:

- one visible session
- hidden workflow switching
- deterministic rerouting
- typed control facts
- parallel work when needed
- durable user checkpoints

That is useful for any app that needs to feel like a coherent adaptive system instead of a loose collection of chats.

Examples:

### 1. Guided onboarding applications

Flow:

- interview user
- build plan
- collect setup info
- run background tasks
- preview resulting workspace

### 2. Research and synthesis applications

Flow:

- gather question
- decompose into research lanes
- fan out analysis
- fan in synthesis
- reroute if the question changes scope

### 3. Incident-response or operations copilots

Flow:

- classify issue
- gather logs and service state
- fan out checks
- merge findings
- escalate or continue based on typed reroute decisions

### 4. Commerce or configuration flows

Flow:

- discover intent
- create proposal/configuration plan
- collect missing requirements
- run validations
- preview outcome
- revise without restarting the whole flow

### 5. Multi-step creative or editorial systems

Flow:

- canonize concept
- plan assets/lanes
- run parallel creation/review
- present merged artifact
- reroute back to concept or keep iterating locally

So the value prop for Mozaiks is not “we have MFJ.”

The stronger value prop is:

- Mozaiks can power next-generation apps that keep one coherent session while internally orchestrating multiple workflows, parallel lanes, typed checkpoints, and scoped reroutes.

That is a platform capability, not just a builder trick.

---

## Bottom Line

The right answer is:

- keep `WFC`
- keep `JourneyOrchestrator`
- keep `UniversalOrchestrator`
- keep `OrchestrationPort`
- restore a thin typed state-and-routing layer above them

`WFC` is not the kernel.
It is one primitive inside the kernel.

The kernel you actually want is:

- small
- typed
- event-driven
- session-aware
- UI-facing

That is what gives you the better user experience you originally wanted.

