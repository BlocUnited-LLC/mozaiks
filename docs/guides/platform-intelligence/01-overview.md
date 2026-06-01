# Code Context

Code Context is the user-facing view of how Mozaiks figures out what a request
means and what part of the app it should touch.

When someone says something like "fix this dashboard" or "add export controls",
Mozaiks does not start by guessing. It first builds enough context to answer two
practical questions:

- what kind of change is this?
- what part of the app does it affect?

That is what makes refinement feel intentional instead of improvised.

## What Mozaiks Figures Out First

Before a workflow runs or a coding worker edits anything, Mozaiks works out:

- whether the request is a small patch, a design change, a feature request, or
  a concept-level shift
- whether it should route back into a workflow, run a scoped coding path, ask
  for clarification, or restart from a higher planning step
- which files, contracts, modules, pages, or workflows are actually relevant to
  the request

Under the hood, two systems do that work together:

- the **control-plane harness** decides the route
- the **context graph** scopes the relevant code and contracts when coding is
  needed

The harness itself is more general than this guide. Here, the focus is on how
Mozaiks uses it in build and refinement flows.

## How The Flow Works

At a high level, the flow looks like this:

```text
User request
  → classify the request
  → choose the next path
  → gather the smallest useful code context
  → run the selected workflow or coding step
```

For larger changes, the route is mostly about workflow re-entry.

Example:

```text
User: "add export controls to the dashboard"

classify: feature
  → route to app_revision sequence
  → workflow runs with the right routing context
```

For patch-level changes, Mozaiks also needs scoped code context.

Example:

```text
User: "fix the broken column header in the projects table"

classify: patch
  → route to app_revision sequence
  → load context graph catalog
  → rank likely files and contracts
  → load graph-neighborhood context
  → run scoped coding worker
```

The important product behavior is that the coding worker never needs the whole
workspace. It gets the smallest relevant slice of the app instead.

## Why This Matters

This gives Mozaiks a cleaner refinement loop:

- change requests are classified before anything runs
- code edits stay scoped instead of expanding across the whole workspace
- the platform can explain why a certain file or contract was selected
- refinement stays fast without giving up deterministic control

The context graph is built from the code and contracts already in the app.
`AppContextGraph` is the canonical model. Optional graph databases may help with
retrieval later, but they are not the source of truth.

## Read More

- [Refinement Control Plane](./04-refinement-control-plane.md)

For the deeper canonical architecture behind these systems, see:

- [Control-Plane Harness Architecture](../../architecture/workflows/control-plane-harness-architecture.md)
- [Context Graph and Code Intelligence](../../architecture/foundations/context-graph-and-code-intelligence.md)
