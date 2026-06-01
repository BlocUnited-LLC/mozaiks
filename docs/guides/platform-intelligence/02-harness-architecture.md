# Harness Architecture

The control-plane harness is the part of Mozaiks that decides what to do with a
build or refinement request before any workflow starts running.

If Platform Intelligence has one job at the front door, this is it.

## What The Harness Does

The harness sits above workflow-local AG2 execution and handles the moment when
Mozaiks has to choose a path.

If a user asks for something like:

- "fix this generated dashboard"
- "add export controls"
- "restart this from concept"

the harness is the layer that determines whether Mozaiks should patch,
re-enter a workflow, ask for clarification, or restart from a higher-level
planning point.

## What It Decides

At a practical level, the harness makes three decisions:

- what class of change this is
- what workflow sequence or coding path should handle it
- whether Mozaiks already has enough confidence to continue automatically

Those decisions are based on:

- persisted artifact and session context
- intent interpretation
- deterministic continuation policy
- optional scoped coding refinement
- clear user-facing decisions

## Why This Matters In Product Terms

The harness is what makes Mozaiks feel intent-aware during build and refinement.
Instead of pushing every request through one giant workflow router, it applies a
deterministic decision layer first and then selects the right workflow sequence
or scoped coding path.

That means Mozaiks can:

- classify how large a change really is
- choose the smallest valid re-entry point
- preserve the existing build state when a full restart is unnecessary

That is the difference between a system that merely responds and one that can
refine a product deliberately.

## Current Runtime Truth

In the current implementation, the harness is configured through:

- `app/config/ai.json`
- the selected `control_plane.yaml` pack

It sits above workflow-local AG2 execution. It is not just another workflow and
it is not a workflow-local handoff graph.

## Where It Lives

At a high level:

- `mozaiksai/control_plane/` owns the canonical runtime subsystem
- `factory_app/control_plane/` owns the first-party pack, prompts, and tools
- `app/config/ai.json` enables and configures the control plane at the app level

The simplest way to think about it is:

1. the harness receives the request
2. the harness classifies and routes it
3. Mozaiks launches the smallest valid next step

## Go Deeper

For the full canonical architecture contract, read:

- [Control-Plane Harness Architecture](../../architecture/workflows/control-plane-harness-architecture.md)