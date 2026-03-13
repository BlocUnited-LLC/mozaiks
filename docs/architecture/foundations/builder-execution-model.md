# Builder Execution Model

This document defines the builder architecture for Mozaiks.

It answers one concrete question:

`How should mozaiks.ai turn user intent into app-bundle files using decomposition, MFJ, DAG dependencies, user checkpoints, and code context without putting orchestration logic in prose?`

When build-specific docs disagree with this document, this document wins.

For canonical builder/orchestration terminology, also see
[builder-orchestration-taxonomy.md](builder-orchestration-taxonomy.md).

For the layer that sits above MFJ and below the visible builder UX, also see
[app-builder-state-and-routing.md](app-builder-state-and-routing.md).

For the generic runtime state and control-event contracts that stay in core regardless of use case, also see
[runtime-state-and-control-events.md](runtime-state-and-control-events.md).

---

## Non-Negotiable Rules

- The builder writes app-bundle files only. It does not modify `mozaiks core`.
- The runtime control plane stays deterministic.
- Workflow graphs do not contain natural-language `logic`.
- Decomposition belongs to agents.
- Scheduling belongs to the runtime.
- UI checkpoints belong to explicit pauses and UI tools.
- Parallel execution must operate on bounded task ownership.
- Code context must be scoped. Do not dump the whole workspace into every agent turn.

---

## Core Mental Model

The builder is not a single giant agent.

It is a layered system:

1. `ValueEngine` defines the canonical app idea.
2. `BuildPlanner` decomposes that idea into a dependency-aware `TaskGraph`.
3. The runtime executes the `TaskGraph` in `MFJ waves`.
4. Each wave runs the same child build workflow in parallel for all ready tasks.
5. Fan-in updates task status, unblocks dependents, and starts the next wave.
6. The user sees the plan before build, then sees files appear in real time during execution.

The clean framing is:

- `UniversalOrchestrator` handles macro workflow routing.
- `DAG` models build dependencies.
- `MFJ` executes each ready DAG layer in parallel.

This is not `MFJ vs DAG`.

It is `DAG scheduled through MFJ waves`.

---

## The Three Build Layers

### 1. Value Layer

Owned by `ValueEngine`.

Responsibilities:

- interview the user
- research and sharpen the app idea
- maintain the canonical `AppSpec`
- reject or reframe requests that break the app's coherence
- classify user changes as spec changes vs build changes

Outputs:

- `AppSpec`
- `ChangeIntent`

This layer is where "build me fb" becomes a real product spec instead of a vague sentence.

### 2. Planning Layer

Owned by `BuildPlanner` or `DecompositionAgent`.

Responsibilities:

- turn `AppSpec` into a `TaskGraph`
- identify shared foundation work
- identify parallelizable feature/module work
- identify integration work
- assign path ownership
- assign dependency edges

Outputs:

- `TaskGraph`
- user-facing `ActionPlan`
- optional sequence/flow visualization

This layer prepares the conveyor belt.

### 3. Execution Layer

Owned by runtime + child workflow template.

Responsibilities:

- compute ready tasks
- spawn one child workflow run per ready task
- stream writes into the workspace live
- collect results
- unblock dependents
- repeat until complete

Outputs:

- generated app-bundle files
- task execution telemetry
- merged build results
- preview-ready app bundle

---

## Universal Orchestrator Role

The global orchestrator should stay coarse.

Use it for phases such as:

- `ValueEngine -> BuildApp -> ValidationEngine`
- `ValueEngine -> BuildApp`
- `ValueEngine -> BuildApp -> Review`

Do not use the global orchestrator for per-feature scheduling.

Per-feature scheduling belongs inside `BuildApp`.

That means:

- global layer decides which workflow runs next
- workflow layer decides how internal parallel build work is executed

---

## BuildApp Pattern

`BuildApp` should be one workflow with an internal MFJ scheduler.

Recommended high-level flow:

1. `BuildHostAgent`
2. `BuildPlannerAgent`
3. `ActionPlanPresenter`
4. user approval checkpoint
5. environment/API-key checkpoint if needed
6. `TaskSchedulerAgent` or runtime scheduler
7. MFJ wave for ready tasks
8. `IntegrationAgent`
9. preview handoff
10. follow-up iteration or change request

Important:

- `BuildPlannerAgent` does not write files
- child builders write files
- `IntegrationAgent` handles merge validation and final assembly

---

## Canonical Schemas

### AppSpec

`AppSpec` is the canonical product definition.

Minimum fields:

```json
{
  "name": "Campus Marketplace",
  "summary": "A university-only marketplace for buying and selling",
  "user_personas": ["students", "student sellers", "campus admins"],
  "core_jobs": ["list items", "browse items", "message sellers", "report abuse"],
  "constraints": ["mobile first", "university email auth"],
  "non_goals": ["crypto payments", "blockchain collectibles"]
}
```

### ChangeIntent

`ChangeIntent` decides whether a request stays inside build or goes back to value/spec work.

Minimum fields:

```json
{
  "change_scope": "foundational",
  "requires_appspec_revision": true,
  "requires_new_iteration": true,
  "target_workflow": "ValueEngine",
  "reason": "request changes product identity and architecture"
}
```

### TaskGraph

`TaskGraph` is the planning artifact that powers the conveyor belt.

Minimum fields:

```json
{
  "shared_foundation": [
    {
      "task_id": "foundation_auth",
      "goal": "Define auth entities and base routes",
      "owned_paths": [
        "entities/user.entity.json",
        "routes/auth.routes.json"
      ],
      "depends_on": [],
      "initial_agent": "EntityAgent",
      "initial_message": "Create the shared auth foundation."
    }
  ],
  "tasks": [
    {
      "task_id": "feature_feed",
      "goal": "Build feed module bundle files",
      "owned_paths": [
        "modules/feed/module.json",
        "modules/feed/ui/FeedPage.jsx",
        "routes/feed.routes.json"
      ],
      "depends_on": ["foundation_auth"],
      "initial_agent": "WorkflowStrategyAgent",
      "initial_message": "Build the feed module using the approved app spec."
    },
    {
      "task_id": "feature_profile",
      "goal": "Build profile module bundle files",
      "owned_paths": [
        "modules/profile/module.json",
        "modules/profile/ui/ProfilePage.jsx",
        "routes/profile.routes.json"
      ],
      "depends_on": ["foundation_auth"],
      "initial_agent": "WorkflowStrategyAgent",
      "initial_message": "Build the profile module using the approved app spec."
    }
  ],
  "integration_tasks": [
    {
      "task_id": "integration_nav",
      "goal": "Update navigation and registry files",
      "owned_paths": [
        "config/navigation_config.json",
        "config/module_registry.json"
      ],
      "depends_on": ["feature_feed", "feature_profile"],
      "initial_agent": "PackMetadataAgent",
      "initial_message": "Integrate all completed modules into the bundle."
    }
  ]
}
```

### Task Result

Each child workflow should return a typed result.

Minimum fields:

```json
{
  "task_id": "feature_feed",
  "status": "completed",
  "written_paths": [
    "modules/feed/module.json",
    "modules/feed/ui/FeedPage.jsx",
    "routes/feed.routes.json"
  ],
  "blocked": [],
  "notes": "Feed bundle generated successfully",
  "preview_ready": false
}
```

---

## Minimal BuildApp MFJ Graph

The authored workflow graph should stay tiny.

Example:

```json
{
  "version": 3,
  "mid_flight_journeys": [
    {
      "id": "build_wave",
      "trigger_agent": "TaskSchedulerAgent",
      "fan_out": {
        "spawn_mode": "generator_subrun"
      },
      "fan_in": {
        "resume_entry_agent": "IntegrationAgent",
        "resume_agent": "IntegrationAgent",
        "inject_as": "mfj_wave_results"
      }
    }
  ]
}
```

Meaning:

- `TaskSchedulerAgent` emits the ready task specs
- runtime fans out one child run per ready task
- runtime fans in to `IntegrationAgent`
- `IntegrationAgent` updates task state and determines the next wave

The graph should not explain the logic in prose.

The trigger agent output and scheduler rules carry the meaning.

---

## Child Workflow Contract

The conveyor belt works because every child task runs the same workflow template.

That template might contain agents like:

- `WorkflowStrategyAgent`
- `ContextVariablesAgent`
- `ToolsManagerAgent`
- `UIFileGenerator`
- `StructuredOutputsAgent`
- `PackMetadataAgent`

The important point is:

- the roster is reused
- the input payload changes per task
- each child owns only its assigned paths

So the pipeline is "the same agents in parallel," but implemented as:

- same workflow template
- many isolated child runs

Not:

- one mutable shared agent instance doing everything at once

---

## Scheduler Rules

The runtime scheduler should operate with simple deterministic rules.

### Ready Task Rule

A task is `ready` when:

- it is not completed
- it is not currently running
- all `depends_on` tasks are completed

### Wave Rule

One MFJ wave contains all ready tasks for the current layer.

### Fan-Out Rule

For each ready task:

- create one child run
- pass `task_id`, `goal`, `owned_paths`, `initial_agent`, `initial_message`
- seed child context with relevant `AppSpec`, module scope, and approved plan state

### Fan-In Rule

When all child runs complete:

- aggregate child results
- mark successful tasks completed
- surface failed tasks
- unblock dependents
- compute next ready wave

### Termination Rule

Stop when:

- all tasks are completed
- or a blocking failure requires user intervention

---

## User Experience Contract

This is where your existing `ActionPlan` and Mermaid work becomes valuable.

The build UX should not hide decomposition. It should present it.

Recommended UX flow:

1. user describes the app
2. `ValueEngine` interviews and sharpens intent
3. `BuildPlanner` creates the `ActionPlan`
4. show the `ActionPlan` artifact
5. show the sequence/flow diagram artifact
6. user approves or revises
7. if external setup is needed, collect API keys or environment choices
8. start build execution
9. show live task progress and file writes
10. show preview-ready message and invite iteration

This preserves:

- comprehension
- trust
- approval before expensive work
- live feedback during build

The user should feel:

- "I understand the plan"
- "I approved the plan"
- "I can see the build happening"
- "I can ask for changes without losing the thread"

The user should not feel:

- "the agents disappeared for ten minutes"
- "I do not know what they are building"
- "I cannot tell why the build is slow"

---

## ActionPlan and Diagram Role

Your existing generator artifacts already map well to the build UX.

### ActionPlan

Use the Action Plan as the pre-build contract presentation.

It should show:

- workflow/module breakdown
- agent lanes
- tool families
- lifecycle hooks
- UI components
- database capability
- context variables

For the builder specifically, extend it to also show:

- task groups
- dependency groups
- owned path ranges
- which tasks will run in the first wave

### Mermaid Diagram

Use the Mermaid diagram as the "how the plan will execute" view.

It should visualize:

- decomposition
- approval checkpoint
- build waves
- integration
- preview

Important:

The `ActionPlan` is not the execution graph.

It is the user-facing explanation of the planned build.

---

## Human Checkpoints

The builder should support three checkpoint types.

### 1. Plan Approval

Use `use_ui_tool(...)` with a structured response.

Use this before the first build wave.

### 2. Environment Setup

Use structured UI when the user must provide:

- API keys
- provider choices
- deployment/environment options

### 3. Mid-Build Intervention

Use only when necessary.

Examples:

- a blocking dependency failed
- a required secret is missing
- a spec-level inconsistency was discovered

Do not interrupt the user for normal internal agent coordination.

---

## Code Context Contract

Your `code_context` tooling is still useful.

It should be treated as a build-discipline system, not as random extra context.

From the current generator-side tool design, the right contract is:

- `index_codebase`
- `get_code_context`
- `get_code_diff`

### When to Use It

Use code context in modification or iterative builds when:

- the workspace already contains generated files
- the user requests targeted changes
- the planner needs to understand existing modules before generating new ones

### What It Should Do

- index the current workspace
- retrieve intent-scoped context only
- compare previous and current versions
- provide targeted file/symbol slices to the next build agent

### What It Should Not Do

- dump the whole workspace into every agent prompt
- replace ownership boundaries
- become a hidden second orchestration system

### Required Discipline

For each task, the planner should decide:

- `read_paths`
- `write_paths`
- `owned_paths`
- `intent`

Then the child workflow should receive:

- current `AppSpec`
- task payload
- targeted code context
- relevant diffs if this is a modification pass

That keeps coding agents in line without drowning them in noise.

---

## Real-Time Workspace Streaming

If the build writes into Monaco/E2B in real time, the runtime should expose:

- task start
- file write
- file patch
- task complete
- wave complete

That means the experience is:

- approve plan
- watch files appear
- see task progress by module/feature
- try the app
- request next iteration

This is the correct place for "wow" in the product.

The wow should come from:

- visible structured planning
- live execution
- fast parallel waves
- easy follow-up iteration

Not from hiding the system behind a chat box.

---

## Iteration Model

When the user asks for a change after preview:

- classify the request with `ChangeIntent`
- if it changes product meaning, route back to `ValueEngine`
- if it is a bounded build change, stay in `BuildApp`

Examples:

- "Add blockchain to this ecommerce app" -> `ValueEngine`
- "Add a filter to the feed page" -> `BuildApp`

For bounded build changes:

1. index current bundle
2. compute code diff and impact set
3. produce a small `TaskGraph`
4. execute only the affected wave(s)

So iteration is not a total rebuild.

It is a scoped rebuild.

---

## What AG2 Can Replace

AG2 can likely help with:

- task semantics
- dependency bookkeeping
- team/task style inner planning concepts

AG2 does not currently replace:

- universal cross-workflow routing
- workflow-level MFJ with persisted child runs
- frontend UI round-trips
- live workspace streaming contracts
- code-context scoping policy

So the correct strategy is:

- borrow good task/DAG concepts from AG2
- keep Mozaiks runtime where you need persisted orchestration and UI/runtime contracts

---

## Recommended Implementation Order

1. Lock `AppSpec`, `ChangeIntent`, `TaskGraph`, and `TaskResult` schemas.
2. Keep authored MFJ graphs minimal.
3. Make `BuildPlanner` emit ready-task child specs directly.
4. Execute the `TaskGraph` in MFJ waves.
5. Reuse one child workflow template for all build tasks.
6. Use `ActionPlan` and Mermaid as approval artifacts before build starts.
7. Use `code_context` only for scoped modification and continuity.
8. Stream task and file events live to the frontend.

---

## Bottom Line

The builder should work like this:

- `ValueEngine` defines what the app should be
- `BuildPlanner` decomposes that into a dependency-aware `TaskGraph`
- runtime executes the `TaskGraph` in MFJ waves
- each wave runs the same child build workflow in parallel
- `ActionPlan` and Mermaid explain the build before it starts
- code context keeps iterative coding agents disciplined
- the user watches files appear live and then asks for the next change

That is the clean version of the system you were trying to build.

