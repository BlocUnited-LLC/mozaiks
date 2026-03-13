# Builder And Orchestration Taxonomy

This document defines the canonical taxonomy for the Mozaiks app builder and the orchestration primitives it depends on.

It exists to answer one practical question:

`When we say plan, task, iteration, reroute, approval, or rebuild, what exact thing do we mean?`

When builder, workflow, or UX docs use these terms, they must align with this document.

---

## Scope

This document covers:

- user-facing builder phases
- internal workflow roles
- canonical builder artifacts
- change and routing classes
- task and dependency classes
- human checkpoint classes
- setup/API-key classes
- visualization classes

This document does not replace:

- [event-taxonomy.md](event-taxonomy.md) for runtime event names
- [workflow-authoring-contracts.md](workflow-authoring-contracts.md) for workflow-writing rules
- [builder-execution-model.md](builder-execution-model.md) for execution flow
- [app-builder-architecture.md](app-builder-architecture.md) for product UX

---

## Core Rule

Mozaiks should use a small number of stable nouns.

The builder becomes confusing when the same thing is called:

- a plan in one doc
- a workflow output in another
- a DAG in another
- an action list in another

The canonical terms below should be used consistently.

---

## Layer Taxonomy

### User-Facing Product Layer

The user experiences one system:

- `AppBuilder`

This is the only top-level product noun the user needs to understand.

### Internal Workflow Layer

The product may route across these internal workflows:

- `ValueEngine`
- `SystemPlanner`
- `BuildApp`
- `ValidationEngine`

These are workflow nouns, not user-facing product nouns.

### Runtime Kernel Layer

Core runtime primitives are:

- `UniversalOrchestrator`
- `MidFlightJourney` (`MFJ`)
- `TaskGraph` scheduling
- pause / resume
- UI tool round-trips
- persistence

These are execution nouns, not workflow ideas.

---

## Builder Workflow Taxonomy

### `AppBuilder`

The visible builder session.

Purpose:

- hold one coherent conversation
- present plan/build/preview surfaces
- hide internal workflow switching

### `ValueEngine`

The workflow that defines and protects product meaning.

Purpose:

- interview the user
- create or revise `AppSpec`
- classify foundational changes

### `SystemPlanner`

The workflow that turns `AppSpec` into a buildable plan.

Purpose:

- create `ActionPlan`
- create `TaskGraph`
- identify setup requirements

### `BuildApp`

The workflow that executes build work.

Purpose:

- schedule MFJ waves
- run child builders
- stream file writes
- integrate outputs

### `ValidationEngine`

The workflow that decides whether the current build is preview-ready.

Purpose:

- run sanity checks
- surface blockers
- hand off to preview

---

## Canonical Artifact Taxonomy

### `AppSpec`

The canonical product definition.

Contains:

- app identity
- target users
- core jobs
- constraints
- non-goals
- guardrails

Use when:

- deciding whether a request changes the product itself

Do not use as:

- a task list
- a file manifest

### `ActionPlan`

The user-facing explanation of what the system intends to build.

Contains:

- app summary
- major modules/features
- workflow/data/UI/integration overview
- first build wave
- setup requirements

Use when:

- presenting the plan for approval

### `SystemPlan`

The internal architectural plan that bridges `AppSpec` and executable tasks.

Contains:

- module plan
- workflow plan
- entity/data plan
- UI surface plan
- integration plan

Use when:

- the system needs to reason about what kinds of assets must exist

### `TaskGraph`

The executable dependency-aware build graph.

Contains:

- task nodes
- dependency edges
- owned paths
- execution metadata

Use when:

- scheduling actual build work

### `BuildWave`

One execution batch of ready tasks from the `TaskGraph`.

Contains:

- all tasks whose dependencies are currently satisfied
- the current wave index
- aggregate wave status

Use when:

- describing one MFJ fan-out / fan-in cycle inside `BuildApp`

### `TaskSpec`

One bounded executable task inside a `TaskGraph`.

Contains:

- `task_id`
- `goal`
- `owned_paths`
- `depends_on`
- `initial_agent`
- `initial_message`

Use when:

- spawning one child build run

### `TaskResult`

The structured output of one completed child task.

Contains:

- `task_id`
- status
- file manifest
- write batches or applied writes
- validation signals

Use when:

- fan-in merges child results

### `ImpactSet`

The bounded change-impact result for an edit request.

Contains:

- affected modules
- affected tasks
- read paths
- write paths
- rebuild scope

Use when:

- the user asks for changes after preview

### `WorkflowTransferRequest`

The explicit request to leave one workflow and start or resume another.

Contains:

- target workflow
- transfer mode
- carry-forward state

Use when:

- leaving `BuildApp` for `ValueEngine`
- leaving `ValueEngine` back to `BuildApp`

### `BuildIteration`

One versioned build attempt tied to a specific `AppSpec` revision and plan state.

Use when:

- tracking “current build” vs “replanned build”

Rule:

- a new `BuildIteration` does not necessarily mean a new user-visible chat

---

## Change Taxonomy

### `SURFACE`

Small localized changes to presentation or interaction.

Examples:

- add a filter to a section
- rename a label
- adjust copy
- tweak layout

Default routing:

- stay in `BuildApp`
- scoped rebuild
- no `AppSpec` revision

### `FEATURE`

Adds or expands a bounded capability without changing product identity.

Examples:

- add saved searches
- add seller ratings
- add notifications

Default routing:

- stay in `BuildApp`
- update `SystemPlan`
- replan affected tasks

### `STRUCTURAL`

Changes the app’s internal architecture, module breakdown, or system shape.

Examples:

- split one module into several
- introduce a new workflow type
- change data model shape significantly

Default routing:

- usually stay in `BuildApp`
- replan substantially
- may consult `ValueEngine` if the request challenges guardrails

### `FOUNDATIONAL`

Changes product identity, audience, business model, or core loop.

Examples:

- pivot from ecommerce app to creator marketplace
- switch from consumer to enterprise product
- add a concept that contradicts stated non-goals

Default routing:

- route to `ValueEngine`
- revise `AppSpec`
- create a new plan/build iteration

### `UNKNOWN`

The system cannot safely classify the request yet.

Default routing:

- hold
- clarify with the user
- do not start rebuild work yet

---

## Routing Taxonomy

### `stay_in_build`

Remain in `BuildApp` and apply a local or scoped rebuild.

### `replan_in_build`

Remain in `BuildApp`, regenerate part of the `SystemPlan` or `TaskGraph`, then rebuild.

### `revise_appspec`

Route to `ValueEngine` to revise product definition before planning/building continues.

### `new_iteration`

Create a new versioned build path because the current plan is no longer the right baseline.

Rule:

- `new_iteration` is an internal versioning event
- it does not require a new user-visible session

---

## Human Checkpoint Taxonomy

### `text_checkpoint`

Plain conversational checkpoint.

Primitive:

- AG2 `InputRequestEvent` / handoff-to-user

Use when:

- the user can answer with normal chat text

### `structured_ui_checkpoint`

Typed UI round-trip checkpoint.

Primitive:

- `use_ui_tool(...)`

Use when:

- the user must return structured data

### `approval_checkpoint`

Explicit yes/no or approve/revise checkpoint on the current plan.

Usually visualized through:

- `ActionPlan`
- inline summary
- approval component

### `setup_checkpoint`

Checkpoint that collects external prerequisites.

Examples:

- API keys
- provider choice
- deployment target

### `preview_feedback_checkpoint`

Checkpoint after preview where the user requests edits.

Output:

- usually a `ChangeIntent` or an input that leads to one

---

## Setup Requirement Taxonomy

### `required_now`

Must be collected before the next build wave can run.

Example:

- Stripe key when the next wave is generating billing integration stubs

### `required_later`

Known to be needed eventually, but not needed for the next wave.

Example:

- deployment credential not needed until deploy step

### `optional`

Useful but not necessary to continue.

Example:

- analytics provider selection for an optional integration

### `not_required`

No user setup is needed for current plan/build scope.

Rule:

- do not ask for keys or credentials in this case

---

## Visualization Taxonomy

### `inline_summary`

Compact chat-stream card.

Use for:

- canon summaries
- approval summaries
- change review summaries

### `plan_artifact`

Primary artifact panel for planning.

Use for:

- `ActionPlan`
- Mermaid execution view

### `build_board`

Primary artifact panel for build execution.

Use for:

- current wave
- running tasks
- completed tasks
- file activity

### `sequence_view`

Planning/execution sequence visualization.

Use for:

- Mermaid plan diagram
- phase sequencing
- explaining review and setup gates without exposing runtime internals

### `preview_surface`

The runnable or inspectable result surface.

Use for:

- app preview
- artifact inspection

### `review_card`

Scoped artifact or inline card for a change request.

Use for:

- impact summary
- reroute notice
- rebuild scope

---

## Task Taxonomy

### `foundation_task`

Shared prerequisite work that other tasks depend on.

Examples:

- auth base
- core entities
- route registry skeleton

### `module_task`

Builds one module or page bundle.

Examples:

- feed module
- profile module
- admin page

### `workflow_task`

Builds one agentic workflow bundle.

Examples:

- support workflow
- roast/demo workflow
- generator workflow

### `integration_task`

Updates cross-cutting config and registry files.

Examples:

- navigation config
- module registry
- integration registry

### `validation_task`

Verifies preview readiness or schema consistency.

Examples:

- config consistency check
- route validation
- workflow pack validation

### `patch_task`

A narrow post-preview edit task.

Examples:

- add filter
- update a form
- fix one screen

---

## Code Context Taxonomy

### `canon_context`

The minimum product-definition context required to keep work aligned with the app’s intent.

Contains:

- current `AppSpec` summary
- guardrails
- non-goals

### `plan_context`

The minimum planning context required to keep a task aligned with the approved plan.

Contains:

- relevant `ActionPlan` slice
- relevant `SystemPlan` slice
- setup assumptions

### `task_context`

The minimum execution context required for one worker task.

Contains:

- `TaskSpec`
- owned paths
- direct dependencies
- current file contents for owned paths

Rule:

- this should be the default build-agent context

### `impact_context`

The minimum change-review context required after preview.

Contains:

- user request
- `ImpactSet`
- affected files/modules/tasks
- current relevant file contents

Rule:

- do not inject the whole workspace when `impact_context` is enough

---

## Dependency Taxonomy

### `hard_dependency`

The downstream task cannot run until the upstream task completes.

Example:

- module task depends on auth foundation

### `integration_dependency`

A task must wait for a set of sibling tasks so it can integrate them.

Example:

- navigation integration waits for all module tasks

### `review_dependency`

Execution should pause until the user approves or provides missing setup.

Example:

- build waves wait for plan approval

---

## Agent Role Taxonomy

### `interview_agent`

Collects seed intent.

### `canon_agent`

Produces or revises canonical product meaning.

### `change_classifier_agent`

Produces `ChangeIntent`.

### `decomposition_agent`

Produces `TaskGraph` or child specs.

### `worker_agent`

Performs one bounded task.

### `integration_agent`

Merges and validates outputs.

### `transfer_agent`

Produces `WorkflowTransferRequest`.

### `host_agent`

Explains the state of the system to the user.

Rule:

- only classifier or transfer roles should emit routing contracts

---

## State Taxonomy

The visible builder session should move through these states:

- `discovery`
- `plan_review`
- `setup_gate`
- `building`
- `preview`
- `change_review`
- `replanning`

These are product states, not necessarily one-to-one workflow names.

---

## Bottom Line

The canonical nouns for Mozaiks builder architecture are:

- `AppSpec`
- `ActionPlan`
- `SystemPlan`
- `TaskGraph`
- `TaskSpec`
- `TaskResult`
- `ImpactSet`
- `ChangeIntent`
- `WorkflowTransferRequest`
- `BuildIteration`

The canonical routing classes are:

- `SURFACE`
- `FEATURE`
- `STRUCTURAL`
- `FOUNDATIONAL`
- `UNKNOWN`

The canonical visible states are:

- `discovery`
- `plan_review`
- `setup_gate`
- `building`
- `preview`
- `change_review`
- `replanning`

Use these terms consistently across prompts, workflow docs, runtime docs, and builder UX.

