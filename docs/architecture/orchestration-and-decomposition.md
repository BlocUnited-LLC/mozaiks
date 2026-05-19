# Orchestration and Decomposition

**Status**: Active  
**Date**: March 11, 2026  
**Purpose**: Define the deterministic control-plane model for Mozaiks runtime orchestration and the authoring contract for decomposition.

This document is about graph/config artifacts used by orchestration.
For the execution ownership model across workflow runs, builder sessions, and
scoped refinement workers, see
[Orchestration Control Loops](workflows/orchestration-control-loops.md).

## Non-Negotiable Rules

- The runtime control plane is deterministic.
- `mfj_extension.json` is a compiled execution artifact, not a place for prose reasoning.
- Natural-language reasoning does not belong in runtime graphs.
- LLMs may produce plans, classifications, and structured outputs inside workflows.
- The runtime may execute those outputs, but it must not interpret vague prose to decide control flow.
- There is no single global orchestration prompt over every user request.
  Builder-context free-text analysis belongs in the builder-session harness
  (`OrchestrationControlHarness`), not in pack graphs.
- The builder-session harness is configured through `app/config/ai.json ->
  control_plane`, not through `extension_registry.json`, `mfj_extension.json`,
  or workflow-local handoff prompts.

## The Three Graph Scopes

These are graph or config scopes, not the same thing as Mozaiks' three control
loops.

### 1. Global workflow sequence graph

The global pack graph in `factory_app/workflows/extended_orchestration/extension_registry.json` is for sequencing across workflows.

Sequence key: `workflow_sequences[]`.

It answers:

- which workflows exist
- which workflows belong to the same journey
- which workflows run sequentially vs in parallel groups

It does not answer:

- how a workflow decomposes a task internally
- how a child fan-out is generated
- how an LLM should reason about branching

Use the global layer for coarse journey phases such as:

- `ValueEngine -> BuildApp`
- `GreenRoom -> WritersRoom -> MainStage`
- `Review -> Publish`

### 2. Workflow-level MFJ graph

The per-workflow pack graph in `app/workflows/<workflow>/extended_orchestration/mfj_extension.json` is for mid-flight journeys inside one workflow. Builder workflows use the same contract under `factory_app/workflows/<workflow>/extended_orchestration/mfj_extension.json`.

It answers:

- which `decomposition_agent` triggers the MFJ
- what the child spawn mode is
- which context fields must be present
- how fan-in resumes the parent
- where merged child results are injected

It does not contain business prose. It only contains executable runtime config.

### 3. Builder task graph / DAG

This is optional and separate from MFJ.

A DAG only exists when a planner emits explicit dependency edges such as `depends_on`.

That means:

- a `decomposition_agent` output is not automatically a DAG
- an MFJ is not automatically a DAG
- a DAG is a structured task plan plus dependency edges plus a scheduler

For most Mozaiks workflows, a layered execution model is enough:

1. foundation
2. parallel child work
3. integration
4. summary / preview

## Runtime Contract

### Global Pack Graph

Global pack graphs should stay minimal:

```json
{
  "version": 3,
  "workflows": [
    { "id": "GreenRoom" },
    { "id": "WritersRoom" },
    { "id": "MainStage" }
  ],
  "workflow_sequences": [
    {
      "id": "backstage_showcase",
      "steps": [
        { "workflows": ["GreenRoom"] },
        { "workflows": ["WritersRoom"] },
        { "workflows": ["MainStage"] }
      ]
    }
  ]
}
```

Meaning:

- `GreenRoom` runs first
- then `WritersRoom` starts
- then `MainStage` finishes the journey
- the runtime does not guess intent from prose

### Workflow MFJ Graph

Workflow MFJ graphs should stay as small as possible.

**Single-phase form:**

```json
{
  "version": 3,
  "mid_flight_journeys": [
    {
      "id": "writers_room_cycle",
      "description": "Fan out to 3 writer children, fan in to host.",
      "decomposition_agent": "DecompositionAgent",
      "fan_out": { "spawn_mode": "workflow", "max_children": 3 },
      "fan_in": {
        "resume_agent": "WritersHostAgent",
        "inject_as": "mfj_writers_room_results"
      }
    }
  ]
}
```

Meaning:

- `decomposition_agent` must emit the child specs in its structured output
- runtime fans out deterministically
- runtime fans in deterministically
- parent resumes at the configured `resume_agent`
- `aggregation_strategy` defaults to `collect_all` — no need to author it
- `resume_entry_agent` defaults to `resume_agent` when omitted

**Multi-stage form:**

Use `stages` when one decomposition agent powers multiple sequential fan-out → fan-in
phases. Each stage after the first requires a `gate_agent` that serves as the
fan-in resume point of the prior stage and the decomposition trigger for the next fan-out.

```json
{
  "version": 3,
  "mid_flight_journeys": [
    {
      "id": "generation_journey",
      "description": "Stage 1 plans all workflows in parallel, user approves, stage 2 implements.",
      "decomposition_agent": "DecompositionAgent",
      "fan_out": { "spawn_mode": "workflow", "max_children": 10 },
      "stages": [
        {
          "id": "plan",
          "child_initial_agent": "PlanningAgent",
          "resume_agent": "ReviewAgent",
          "inject_as": "mfj_plan_results"
        },
        {
          "id": "implement",
          "gate_agent": "ApprovalAgent",
          "child_initial_agent": "ImplementationAgent",
          "resume_agent": "PackagingAgent",
          "inject_as": "mfj_impl_results"
        }
      ]
    }
  ]
}
```

The schema expands stages to flat journeys at load time. The coordinator never
sees the staged format — it sees one journey per stage, with the gate agent as
the decomposition trigger of the next stage.

Advanced fields like `trigger_on`, `input_contract`, `output_contract`,
`child_context_seed`, and timeout settings are optional override knobs. They
exist for stricter validation or special cases, but they should not be the
default authored experience. Keep those advanced knobs in roadmap profiles
until the baseline authoring flow needs them:
internal MFJ authoring roadmap notes.

## Decomposition Contract

If a workflow needs productive fan-out, a dedicated decomposition step should prepare it.

That means:

- do not put reasoning in `mfj_extension.json`
- do put reasoning in a `decomposition_agent`
- require structured outputs from that agent

### MFJ context variable auto-synthesis

The runtime reads `extended_orchestration/mfj_extension.json` at plan-load time and
auto-registers context variables for:

- every `inject_as` key — type `object`, default `null`, scoped to the
  corresponding `resume_agent`
- the runtime `_mfj_resume_*` handshake keys — scoped to every `resume_agent`

Workflow authors do not declare these in `context_variables.yaml`. The only
authoring obligation is in the `resume_agent`'s `[CONTEXT]` prompt section:
name the `inject_as` key and describe the shape of the injected value so the
agent knows what it is reading.

The decomposition agent is responsible for producing:

- bounded child work units
- child workflow specs
- any lane/task metadata needed for fan-in

For build-style workflows, the output should include ownership and dependency information such as:

- `task_id`
- `goal`
- `owned_paths`
- `depends_on`
- `acceptance_criteria`
- `integration_needs` when a child task expects third-party credentials or
  connector configuration

## Cross-Workflow Data Transfer

Global journeys do not magically share workflow-local context.

Cross-workflow carry must be explicit:

1. workflow A persists canonical fields to its `ChatSessions` document
2. workflow B loads them in a `before_chat` lifecycle tool
3. workflow B seeds its own context variables from those persisted fields

This is the current Mozaiks contract.

Use it for:

- `ValueEngine` canonical app spec
- `GreenRoom` set brief
- any other workflow-to-workflow carry

## Runtime Event Flow

The runtime emits `chat.agent_output_validated` for any agent with a registered structured-output model. Two downstream handlers react:

- **`handle_tool_dispatch`** — invoked only when the agent has an `auto_tool_call: true` tool in tools.yaml. Runs the mapped tool function deterministically.
- **`handle_journey_triggered`** — invoked only when the agent matches a `decomposition_agent` in the workflow's MFJ pack graph. Starts fan-out.

That means:

- MFJ decomposition agents do not need fake auto-tool bindings
- UI tool or side-effect automation should use `auto_tool_call: true` on the tool in tools.yaml

## Showcase Pattern

The canonical demo in this repo is:

1. `GreenRoom`
2. `WritersRoom`
3. `MainStage`

### GreenRoom

Purpose:

- capture a comedy premise and performer boundaries
- convert it into a canonical set brief
- persist that brief for the next workflow

### WritersRoom

Purpose:

- load the persisted set brief
- decompose it into three parallel evaluation lanes
- fan out to three child runs inside the same workflow
- fan in to the host
- render both inline and artifact UI surfaces

### MainStage

Purpose:

- load the writers-room summary
- package the strongest material into a final stage-ready set
- render the final artifact for presentation

This demonstrates:

- global universal orchestration
- workflow-level MFJ
- lifecycle-tool carry between workflows
- inline UI tools
- artifact UI tools

## BuildApp Guidance

For real application generation, the pattern should be:

1. `ValueEngine` produces `ProductSpec`, or `ExistingAppDiscovery` produces `ExistingProductSpec`
2. downstream planning resolves `CapabilitySpec[]`
3. `ExperienceSpec` and `AgentAugmentationPlan` are derived from that product model
4. compilers turn those artifacts into a typed `BuildGraph` and concrete bundles
5. major changes emit a typed `ChangeIntent`
6. the universal orchestrator routes deterministically from typed refinement state rather than raw prose

Do not route from raw prose.

Use:

- typed `AppSpec`
- typed `ProductSpec`
- typed `CapabilitySpec`
- typed `ExperienceSpec`
- typed `AgentAugmentationPlan`
- typed `ChangeIntent`
- typed `BuildGraph`
- typed `BuildTaskSpec`

Decompose into product artifacts first, not workflows first.

- modules and persistent pages come from deterministic product planning
- workflows are attached only when a capability requires agentic behavior
- refinements route by artifact boundary (`ProductSpec`, `CapabilitySpec[]`, `ExperienceSpec`, `AgentAugmentationPlan`, `BuildGraph`)
- third-party credentials are requested agentically at the point of need. Child
  tasks may declare or record `integration_needs`; the parent fan-in checkpoint
  aggregates them, reuses ready app connectors, prompts inline for missing
  required credentials, and persists results to the app-scoped
  `/apps/{appId}/integrations` surface.

## Summary

- Global pack graphs sequence workflows.
- Workflow pack graphs handle MFJ inside a workflow.
- Workflow execution, builder-session routing, and scoped refinement workers are
  separate control loops above those graph artifacts.
- Decomposition belongs to agents, not runtime graph prose.
- DAG scheduling is optional and separate from MFJ.
- Cross-workflow carry is explicit persistence plus lifecycle loading.
- The runtime executes compiled contracts, not natural-language logic.
