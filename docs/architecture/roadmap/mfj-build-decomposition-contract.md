# MFJ Build Decomposition Contract

## Why This Exists

The current SmokeParent and SmokeChild workflows prove that mid-flight journeys can:

- detect a planner decomposition event
- fan out child workflows
- wait for children in parallel
- merge child outputs
- inject merged data back into the parent
- resume the parent into a synthesizer agent

That smoke path is intentionally narrow. It does not represent the intended product case:

> A user says "build me Facebook", a planner decomposes the work into multiple engineering subtasks, the same developer workflow runs those subtasks in parallel, and a parent coordinator fans the results back in.

This note maps the current runtime contract to that product goal.

## What The Current Smoke Graph Means

The workflow graph in [platform/workflows/SmokeParent/extended_orchestration/mfj_extension.json](../platform/workflows/SmokeParent/extended_orchestration/mfj_extension.json) currently means:

- `decomposition_agent: PlannerAgent`
  - the planner is the only agent allowed to start fan-out
- `trigger_on: decomposition_event`
  - fan-out only happens for explicit decomposition payloads
- `spawn_mode: workflow`
  - each child is a workflow run
- `max_children: 1`
  - the smoke harness only allows one child
- `aggregation_strategy: first_success`
  - if multiple children existed, only the first successful one would be used
- `resume_agent: PresenterAgent`
  - the resumed parent lands at the final synthesizer/presenter
- `resume_entry_agent: ResumeRouterAgent`
  - the runtime inserts a resume signal under this speaker name before the actual resume
- `inject_as: mfj_smoke_results`
  - merged child output is injected into parent context under this key

That is a valid MFJ contract, but it is a smoke-test contract, not the target product contract.

## Runtime Semantics That Still Match The Product Goal

The runtime still supports the intended fan-out and fan-in shape.

From the current coordinator behavior:

- the planner can emit a `workflows` list
- each item becomes a child run spec
- the same workflow name can appear more than once
- each child can have its own `initial_message`
- each child can optionally choose a different `initial_agent`
- child outputs are merged after all children finish
- the parent resumes with merged context injected under `inject_as`

That means the platform still supports this pattern:

1. user asks for a large product build
2. planner decomposes into many engineering tasks
3. one reusable developer workflow runs multiple tasks in parallel
4. the parent coordinator resumes with merged results

## Proposed Product Contract

### Parent Workflow Role

The parent workflow should own:

- product decomposition
- orchestration state
- fan-out and fan-in
- final synthesis of child outputs

Suggested parent workflow agents:

- `ProductPlannerAgent`
- `ResumeRouterAgent`
- `BuildCoordinatorAgent`

### Child Workflow Role

The child workflow should be reusable and task-oriented.

Suggested child workflow name:

- `DeveloperWorker`

This same workflow should be spawned multiple times with different `initial_message` values.

## Proposed Parent Graph

Example replacement for a build-oriented parent workflow graph:

```json
{
  "version": 3,
  "mid_flight_journeys": [
    {
      "id": "parallel-build-plan",
      "description": "Decompose a product request into parallel engineering subtasks",
      "decomposition_agent": "ProductPlannerAgent",
      "trigger_on": "decomposition_event",
      "requires": [],
      "fan_out": {
        "spawn_mode": "workflow",
        "max_children": 6,
        "timeout_seconds": 1800,
        "input_contract": {
          "required": [],
          "optional": [
            "product_name",
            "product_brief",
            "product_constraints"
          ]
        },
        "child_context_seed": {
          "execution_mode": "parallel-build"
        }
      },
      "fan_in": {
        "resume_agent": "BuildCoordinatorAgent",
        "resume_entry_agent": "ResumeRouterAgent",
        "aggregation_strategy": "collect_all",
        "inject_as": "mfj_build_results",
        "on_partial_failure": "resume_with_available",
        "timeout_seconds": 300
      },
      "output_contract": {
        "required": [
          "task_id",
          "result",
          "worker_name"
        ],
        "optional": [
          "agent_message",
          "files_changed",
          "artifacts",
          "tests_run",
          "next_dependencies"
        ]
      }
    }
  ]
}
```

## Why These Fields Matter

### `spawn_mode: workflow`

Keep this.

It means each child is an isolated workflow run, which is a better fit than trying to multiplex many engineering tasks through one in-memory group chat.

### `max_children: 6`

This is the practical fan-out control.

For a request like "build me Facebook", the planner might produce tasks such as:

- auth and identity
- profile and social graph
- feed ranking and post rendering
- messaging and notifications
- media upload and storage
- admin and moderation

### `aggregation_strategy: collect_all`

This is the best starting strategy for engineering work.

It preserves each child result independently instead of collapsing them too early.

That lets the resumed parent coordinator reason about:

- which tasks succeeded
- which tasks failed
- what artifacts were created
- what cross-task dependencies remain

### `inject_as: mfj_build_results`

This becomes the parent context key that the resumed coordinator reads.

Example parent context shape after fan-in:

```json
{
  "mfj_build_results": {
    "DeveloperWorker_0": {
      "task_id": "auth",
      "result": "Implemented auth flow",
      "worker_name": "BackendDevAgent",
      "files_changed": ["api/auth.py", "tests/test_auth.py"]
    },
    "DeveloperWorker_1": {
      "task_id": "feed-ui",
      "result": "Built feed shell UI",
      "worker_name": "FrontendDevAgent",
      "files_changed": ["app/feed.jsx"]
    }
  }
}
```

## Proposed Planner Output Schema

The planner should not hardcode a single child like the smoke flow does today.

Instead it should emit many child specs that can reuse the same workflow name.

Suggested parent structured output schema:

```yaml
registry:
  ProductPlannerAgent: BuildPlan
  BuildCoordinatorAgent: BuildSynthesis

models:
  BuildTaskSpec:
    type: model
    fields:
      name:
        type: literal
        values:
          - DeveloperWorker
      task_id:
        type: str
      description:
        type: str
      initial_message:
        type: str
      initial_agent:
        type: str
        required: false

  BuildPlan:
    type: model
    fields:
      agent_message:
        type: str
      product_name:
        type: str
      product_brief:
        type: str
      workflows:
        type: list
        items: BuildTaskSpec

  BuildSynthesis:
    type: model
    fields:
      agent_message:
        type: str
      completed_tasks:
        type: list
        items: str
      blocked_tasks:
        type: list
        items: str
      summary:
        type: str
      next_step:
        type: str
```

### Example Planner Output

```json
{
  "agent_message": "Breaking the request into parallel engineering tasks.",
  "product_name": "Facebook Clone",
  "product_brief": "Social network with auth, profiles, feed, messaging, notifications, and media.",
  "workflows": [
    {
      "name": "DeveloperWorker",
      "task_id": "auth",
      "description": "Implement authentication and session handling.",
      "initial_message": "Build auth and session flows for a Facebook-like product.",
      "initial_agent": "BackendDevAgent"
    },
    {
      "name": "DeveloperWorker",
      "task_id": "feed-ui",
      "description": "Implement feed UI and post rendering.",
      "initial_message": "Build the feed UI, composer, and post cards for a Facebook-like product.",
      "initial_agent": "FrontendDevAgent"
    },
    {
      "name": "DeveloperWorker",
      "task_id": "messaging",
      "description": "Implement direct messaging.",
      "initial_message": "Build direct messaging flows for a Facebook-like product.",
      "initial_agent": "RealtimeDevAgent"
    }
  ]
}
```

## Proposed Child Output Schema

The child workflow should return engineering-friendly output, not just a smoke summary.

Suggested child structured output schema:

```yaml
registry:
  ChildWorkerAgent: DeveloperTaskResult

models:
  DeveloperTaskResult:
    type: model
    fields:
      agent_message:
        type: str
      task_id:
        type: str
      result:
        type: str
      worker_name:
        type: str
      files_changed:
        type: list
        items: str
      artifacts:
        type: list
        items: str
      tests_run:
        type: list
        items: str
      next_dependencies:
        type: list
        items: str
```

## What This Means For The Original Goal

The answer is:

- the runtime still supports fan-out and fan-in in the intended direction
- the smoke workflow drifted into a one-child proof harness
- the product goal is still achievable without changing the MFJ runtime model

The real shift needed is not in the runtime primitives. It is in the workflow contract:

- planner output shape
- child output shape
- merge strategy
- resumed coordinator behavior

## Important Constraint

This design gives workflow-level parallelism.

It does not, by itself, solve concurrent code editing safety in a shared repo. If multiple children are going to edit the same codebase in parallel, the execution model also needs one of these:

- separate worktrees
- separate sandboxes
- authored patch artifacts merged later
- task partitioning by file ownership

So the intended product path should be understood as:

1. planner-driven parallel task decomposition
2. child workflow isolation per task
3. merge and synthesis in the parent
4. a separate strategy for safe parallel code writes

## Recommended Next Implementation Step

Do not mutate the smoke workflow further.

Instead, create a real build-oriented workflow pair:

- `BuildParent`
- `DeveloperWorker`

Start with:

- `aggregation_strategy: collect_all`
- repeated `DeveloperWorker` child specs
- a resumed `BuildCoordinatorAgent` that reads `mfj_build_results`

That will let the product behavior match the original intent without sacrificing the smoke harness.