# Agentic Generator Salvage Plan

## Goal

Salvage the Mozaiks agentic generator by preserving the parts that are still
architecturally correct while removing the parts that are overfit to legacy AG2
group chat execution.

This plan assumes:

- Mozaiks should remain a runtime and generator platform, not just a chat app
- the runtime should stay engine-agnostic above the execution boundary
- AG2 Beta should become the preferred execution substrate over time
- workflow graph, control-plane, and app-boundary concerns still belong to
  Mozaiks core

## Current Diagnosis

The repo currently mixes four concerns too tightly:

1. workflow authoring declaratives
2. execution-engine behavior
3. streaming and transport behavior
4. product and control-plane orchestration

The cleanest parts of the current architecture are still valid:

- app domain events are separate from workflow runtime events
- control events are separate from app domain facts
- the transport and runtime are separated from the app backend boundary
- `OrchestrationPort` is the right engine boundary

The messy part is that legacy group chat assumptions leaked upward into runtime,
event serialization, persistence, and workflow authoring semantics.

## Core Principle

Use AG2 Beta for turn execution, task delegation, stream handling, context
variables, and structured output.

Keep Mozaiks responsible for:

- workflow routing policy
- control-plane state
- workflow graph semantics
- workflow prerequisites and journeys
- app-backend integration
- transport and frontend projections

That keeps the runtime modern without collapsing the whole system into an agent
demo framework.

## Keep, Shrink, Replace

### Keep as core runtime contracts

- `mozaiksai/core/ports/orchestration.py`
- `mozaiksai/core/control_plane/contracts.py`
- `mozaiksai/core/orchestration/universal_orchestrator.py`
- `mozaiksai/core/workflow/pack/schema.py`
- `mozaiksai/core/workflow/pack/gating.py`
- `mozaiksai/core/workflow/pack/journey_orchestrator.py`

These files encode platform-level behavior, not just AG2 mechanics.

### Shrink aggressively

- `mozaiksai/core/events/event_serialization.py`
- `mozaiksai/core/events/auto_tool_handler.py`
- `mozaiksai/core/transport/simple_transport.py`

These should become projection and bridge layers, not places where execution
semantics are invented.

### Replace as engine-specific implementation

- `mozaiksai/core/workflow/orchestration_patterns.py`
- `mozaiksai/core/adapters/ag2_orchestration.py`

These should stop being the center of the runtime and become one adapter path
among others.

## New Runtime Split

### Layer 1: App and automation facts

Keep the event model defined in:

- `docs/architecture/foundations/event-system.md`
- `docs/architecture/foundations/runtime-state-and-control-events.md`
- `docs/architecture/foundations/process-and-event-map.md`

Rules:

- app/backend events remain immutable facts
- control-plane events remain low-frequency routing facts
- workflow runtime events remain live execution signals

Do not merge these layers again.

### Layer 2: Workflow orchestration policy

Mozaiks still owns:

- which workflow to run
- whether prerequisites are satisfied
- whether a session should resume or reroute
- whether a workflow graph step is sequential or parallel

This is the correct home for the generator and runtime policy.

### Layer 3: Execution adapter

Execution should sit fully behind `OrchestrationPort`.

Target adapters:

- `LegacyGroupChatAdapter`
- `BetaTaskAdapter`

The existing AG2 adapter should be treated as the legacy path, even if it stays
alive during migration.

### Layer 4: Projection and transport

Transport should consume normalized runtime events and project them to:

- frontend websocket payloads
- persistence projections
- observability signals

Transport should not own orchestration logic.

## How to Reinterpret MFJ and Pack

The current `workflow_pack` layer is doing two different jobs.

### Job A: Intra-workflow task delegation

Examples:

- planner decomposes work
- specialists execute subtasks
- coordinator synthesizes results

This is the part that should move toward AG2 Beta task delegation when Beta is
ready enough.

### Job B: Cross-workflow orchestration

Examples:

- prerequisites between workflows
- journey progression across workflows
- workflow-level graph semantics
- durable resume checkpoints between meaningful workflow runs

This part should remain in Mozaiks pack.

## The Key Refactor

Stop treating all decomposition as child workflow spawning.

New rule:

- use Beta task delegation for decomposition inside one workflow run
- use workflow pack only when there are truly separate workflow runs with
  separate lifecycle meaning

This removes a large amount of accidental complexity from MFJ.

## Authoring Model Changes

The current workflow authoring contract over-emphasizes handoff-driven group
chat topology.

It should evolve toward three authoring concepts:

### 1. Workflow intent

Examples:

- single-agent workflow
- delegated-task workflow
- cross-workflow orchestrator

### 2. Agent roles

Examples:

- coordinator
- specialist
- reviewer

### 3. Execution policy

Examples:

- `execution_mode: single_agent`
- `execution_mode: delegated_tasks`
- `execution_mode: workflow_graph`

Handoffs can remain as a compatibility shape, but they should stop being the
primary mental model for all workflow creation.

## Recommended Migration Phases

### Phase 0: Freeze the leak

Do not add more engine-specific behavior above `OrchestrationPort`.

Avoid new runtime features that require deeper group chat coupling.

### Phase 1: Introduce a Beta-first adapter path

Build a second adapter behind `OrchestrationPort` that can run one narrow class
of workflow using AG2 Beta primitives.

Suggested initial scope:

- one direct workflow entrypoint
- one coordinator agent
- one or more delegated specialist tasks
- structured output
- stream-based event observation

Do not try to port MFJ wholesale in the first spike.

### Phase 2: Create a normalized runtime event projection

Define one normalized internal runtime event surface that adapters must emit.

Examples:

- `process.started`
- `process.completed`
- `task.started`
- `task.completed`
- `task.failed`
- `chat.message_appended`
- `ui.tool.requested`
- `artifact.updated`

`event_serialization.py` should become a projection helper, not a place where
engine semantics are reconstructed.

### Phase 3: Split pack responsibilities

Refactor `workflow_pack` into two explicit modules:

- delegated-task orchestration
- workflow-graph orchestration

Then migrate the delegated-task piece toward the Beta adapter.

### Phase 4: Rework persistence around projections, not transcripts

Persist:

- canonical workflow/session state
- normalized runtime events
- artifact state
- optional transcript projections for UI replay

Do not make raw group chat transcript shape the canonical runtime truth.

### Phase 5: Evolve the generator contract

Update workflow generation so new workflows declare execution intent instead of
assuming group chat by default.

The generator should produce workflows that target the runtime contract, not one
specific AG2 topology.

## Concrete Short-Term Decisions

### Decision 1

Keep `OrchestrationPort` as the only execution boundary.

### Decision 2

Treat current AG2 group chat execution as a compatibility engine, not the
future-center of the runtime.

### Decision 3

Preserve control-plane and event-taxonomy docs as the stable architectural
source of truth.

### Decision 4

Move decomposition inside workflows toward Beta task delegation when the AG2
surface is stable enough.

### Decision 5

Keep workflow pack only for cross-workflow lifecycle semantics that still exist
above any one execution engine.

## What Success Looks Like

Mozaiks should end up with:

- a clean app/backend event boundary
- a small durable control plane
- an engine-agnostic workflow runtime above `OrchestrationPort`
- Beta-native task delegation for agentic decomposition
- pack and journey logic only where separate workflow runs actually matter
- workflow generation that targets runtime intent rather than group chat shape

That is how the platform stays salvageable while adopting with the times.