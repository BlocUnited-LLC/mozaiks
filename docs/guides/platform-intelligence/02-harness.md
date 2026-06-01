# Harness

The harness is the part of the control plane that wires in the opt-in
primitives that let Mozaiks classify a request, route it to the right path, and
scope the work to the right part of the app.

## What It Does

The control plane is the full intelligence layer. The harness is the part that
runs the control flow.

In practice, that means the harness coordinates:

- request classification
- route selection
- scope selection
- confirmation or clarification decisions

## Route Versus Scope

These are different jobs:

- **route** means choosing what path the request should take next
- **scope** means choosing which files, contracts, or surfaces that path should
  touch

So the harness does not just say "this is a feature." It also helps the system
decide whether that feature should go through a workflow sequence, a scoped
coding path, or a targeted surface regeneration.

## Why It Exists

The harness gives the control plane a place to attach the decision flow without
turning the whole system into one huge router.

That is what makes Mozaiks feel intentional: it does not guess first and clean
up later. It decides the right path up front.
