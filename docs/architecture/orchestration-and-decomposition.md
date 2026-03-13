# Orchestration and Decomposition

**Status**: Active  
**Date**: March 11, 2026  
**Purpose**: Define the deterministic control-plane model for Mozaiks runtime orchestration and the authoring contract for decomposition.

## Non-Negotiable Rules

- The runtime control plane is deterministic.
- `workflow_graph.json` is a compiled execution artifact, not a place for prose reasoning.
- Natural-language `logic` fields do not belong in runtime graphs.
- LLMs may produce plans, classifications, and structured outputs inside workflows.
- The runtime may execute those outputs, but it must not interpret vague prose to decide control flow.

## The Three Layers

### 1. Global Orchestrator

The global pack graph in `platform/workflows/_pack/workflow_graph.json` is for sequencing across workflows.

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

### 2. Workflow-Level MFJ

The per-workflow pack graph in `platform/workflows/<workflow>/_pack/workflow_graph.json` is for mid-flight journeys inside one workflow.

It answers:

- which agent triggers the MFJ
- what the child spawn mode is
- which context fields must be present
- how fan-in resumes the parent
- where merged child results are injected

It does not contain business prose. It only contains executable runtime config.

### 3. Task Graph / DAG

This is optional and separate from MFJ.

A DAG only exists when a planner emits explicit dependency edges such as `depends_on`.

That means:

- a `DecompositionAgent` output is not automatically a DAG
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
  "version": 2,
  "workflows": [
    { "id": "GreenRoom" },
    { "id": "WritersRoom" },
    { "id": "MainStage" }
  ],
  "journeys": [
    {
      "id": "backstage_showcase",
      "steps": ["GreenRoom", "WritersRoom", "MainStage"]
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

The authored form should usually look like this:

```json
{
  "version": 3,
  "mid_flight_journeys": [
    {
      "id": "writers_room_cycle",
      "trigger_agent": "DecompositionAgent",
      "fan_out": {
        "spawn_mode": "workflow"
      },
      "fan_in": {
        "resume_agent": "WritersHostAgent",
        "resume_entry_agent": "ResumeRouterAgent",
        "aggregation_strategy": "collect_all",
        "inject_as": "mfj_writers_room_results"
      }
    }
  ]
}
```

Meaning:

- `DecompositionAgent` must emit the child specs in its structured output
- runtime fans out deterministically
- runtime fans in deterministically
- parent resumes at the configured agent

Advanced fields like `trigger_on`, `input_contract`, `output_contract`, `child_context_seed`, and timeout settings are optional override knobs. They exist for stricter validation or special cases, but they should not be the default authored experience.

## Decomposition Contract

If a workflow needs productive fan-out, a dedicated decomposition step should prepare it.

That means:

- do not put reasoning in `workflow_graph.json`
- do put reasoning in a `DecompositionAgent`
- require structured outputs from that agent

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

## Current Runtime Constraint

Today, the runtime emits `chat.structured_output_ready` for any agent with a registered structured-output model.

Only agents with `auto_tool_mode: true` will trigger the auto-tool executor.

That means:

- MFJ trigger agents do not need fake auto-tool bindings
- UI tool or side-effect automation should still use `auto_tool_mode: true` only when deterministic tool execution is desired

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

1. `ValueEngine` owns the canonical `AppSpec`
2. `BuildApp` consumes that spec and writes only declarative app-bundle files
3. major changes emit a typed `ChangeIntent`
4. the universal orchestrator routes deterministically from that object

Do not route from raw prose.

Use:

- typed `AppSpec`
- typed `ChangeIntent`
- typed `BuildPlan`
- typed `FeatureTask`

## Summary

- Global pack graphs sequence workflows.
- Workflow pack graphs handle MFJ inside a workflow.
- Decomposition belongs to agents, not runtime graph prose.
- DAG scheduling is optional and separate from MFJ.
- Cross-workflow carry is explicit persistence plus lifecycle loading.
- The runtime executes compiled contracts, not natural-language logic.

