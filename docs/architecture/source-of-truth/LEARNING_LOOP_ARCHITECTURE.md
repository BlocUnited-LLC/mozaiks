# Learning Loop Architecture

**Status:** Source of truth  
**Last updated:** 2026-02-26

## Purpose

This document defines how mozaiks supports continuous workflow quality improvement through runtime telemetry and feedback loops.

It does not prescribe private automation pipelines. It defines OSS runtime contracts and integration points.

## Core Model

Learning is a loop, not a runtime layer.

1. Execute workflow
2. Capture telemetry
3. Persist telemetry
4. Compute quality signals
5. Feed signals into future workflow design and runtime decisions

## Ownership Boundary

### Mozaiks runtime (`core` + `orchestration`)

- emits runtime events
- persists run/event history
- exposes queryable artifacts/events
- supports graph injection hooks

### Consuming app/tooling

- computes custom quality scores
- tracks generation-time metadata (if applicable)
- applies policy for prompt/workflow improvements

## Telemetry Contract

Reserved namespace: `telemetry.*`.

Recommended baseline events:

- `telemetry.run.started`
- `telemetry.run.completed`
- `telemetry.run.failed`
- `telemetry.run.summary`
- `telemetry.tool.outcome`
- `telemetry.hitl.requested`
- `telemetry.hitl.resolved`

`telemetry.run.summary` is the preferred aggregation anchor for scoring pipelines.

## Data Pipeline

### Step 1: Runtime execution

Workflows execute in orchestration runtime and emit lifecycle events.

### Step 2: Telemetry emission

Runtime emits telemetry facts tied to `run_id` and workflow metadata.

### Step 3: Persistence

Telemetry is persisted alongside event history in the event store.

### Step 4: Scoring

Deployment-specific jobs compute quality metrics (pattern quality, tool reliability, prompt efficiency, etc.).

### Step 5: Feedback

Scores are injected back into workflow selection/design paths (for example via graph injection queries).

## What Can Improve

| Target | Signals |
|---|---|
| workflow decomposition quality | completion/failure, retry, abandonment |
| prompt and agent config quality | turn counts, error concentration, HITL rate |
| tool wiring quality | tool success/failure and latency |
| artifact quality and usability | post-run edits, follow-up actions |
| routing/gating quality | branch success and dead-end rates |

## Minimal Runtime Hooks to Preserve

1. stable `telemetry.*` namespace in taxonomy
2. `run_id` correlation across lifecycle and telemetry events
3. post-run summary emission path
4. durable event persistence and replay
5. graph injection extension points for score reads

## Risk Controls

- Keep telemetry schema versioned and explicit.
- Avoid coupling runtime correctness to scoring availability.
- Treat scoring pipelines as optional extensions, not core runtime dependencies.

## Open Questions

1. Which score calculations should remain app-defined vs standardized?
2. What minimum sample size is required before acting on quality signals?
3. How should score decay and recency weighting be handled?
4. Which feedback actions require human review?

## Cross References

- [EVENT_TAXONOMY.md](EVENT_TAXONOMY.md)
- [PROCESS_AND_EVENT_MAP.md](PROCESS_AND_EVENT_MAP.md)
- [GRAPH_INJECTION_CONTRACT.md](GRAPH_INJECTION_CONTRACT.md)
- [WORKFLOW_ARCHITECTURE.md](WORKFLOW_ARCHITECTURE.md)
