# Refinement Control Plane

The refinement control plane is the part of Mozaiks that turns a post-generation
change request into the smallest safe next step.

It is the operating layer that keeps refinement fast without making it vague.

## What Refinement Means

Mozaiks treats **initial generation** and **refinement** as separate modes.

Initial generation builds the first canonical shape of the app. Refinement is
the edit path that classifies a requested change, chooses the smallest valid
re-entry point, and updates the artifact safely.

That is what allows Mozaiks to improve an existing app without pretending every
change is a brand-new build.

## The Core Idea

The control plane decides whether a request is:

- `patch`
- `design`
- `feature`
- `core`

That classification determines whether Mozaiks should:

- run a scoped coding path
- selectively re-enter a workflow sequence
- rebuild part of the plan
- restart from concept-level intent

The key idea is not just classification for its own sake. The point is to avoid
doing more work than the change actually requires.

## Why This Is Better Than Re-Running Everything

Without the refinement control plane, every change request risks becoming a full
rebuild. With it, Mozaiks can preserve the canonical app state, route only the
necessary work, and validate a new artifact version against the right scope.

This is what makes post-generation changes feel fast without giving up
determinism.

## How To Think About The Four Classes

- `patch`: a small, localized fix
- `design`: a visual or information-architecture change without changing the core product
- `feature`: a new capability within the same product direction
- `core`: a concept-level shift that changes what the app fundamentally is

## Current Runtime Truth

In the current implementation, refinement is driven by:

- `app/config/ai.json`
- the selected `control_plane.yaml` pack
- checkpoint routing and control-plane re-entry

It is not a separate dedicated `RefinementWorkflow`.

So the clean flow is:

1. load the current artifact state
2. classify the requested change
3. route to the smallest valid re-entry point
4. validate and persist the updated result

That is the core promise of the refinement control plane: Mozaiks changes the
app at the right level instead of defaulting to a full rebuild.

## Go Deeper

For the full canonical architecture contract, read:

- [Refinement Control Plane](../../architecture/workflows/refinement-control-plane.md)