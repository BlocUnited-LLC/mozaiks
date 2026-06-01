# Platform Intelligence

Platform Intelligence is how Mozaiks understands both the request and the app
before it decides what to do next.

It gives Mozaiks two capabilities before any workflow or coding step runs:

- it decides what kind of request this is and what path to take
- it decides which code, contracts, and files actually matter for that request

That is what makes refinement feel deliberate instead of improvised.

## The Control-Plane Harness

When a user sends a message like "fix this dashboard" or "add export controls",
Mozaiks routes that through its structured refinement path instead of treating
it as ordinary chat. The request needs:

- context about what was already built
- a classification of how big the change is
- a deterministic choice about what to do next

That is the job of the **control-plane harness**. It sits above workflow-local
AG2 execution and processes checkpoint events before any workflow runs.

In one sentence: the harness decides what should happen next.

That means it classifies the request, chooses the right workflow sequence or
coding path, and decides whether Mozaiks should auto-patch, ask for
clarification, or restart from a higher-level planning step.

```text
User request
  → request_submitted   (classify: patch / design / feature / core)
  → route_requested     (select workflow sequence)
  → decision_requested  (decide: auto-patch / re-entry / clarify / restart)
  → scope_requested     (propose affected files when scope is missing)
  → coding_requested    (run scoped coding worker for eligible patches)
```

The harness does not run a workflow. It decides which workflow sequence to
resume or launch, and under what conditions.

The harness is configured at the app level through `app/config/ai.json` and a
declarative pack (`factory_app/control_plane/config/control_plane.yaml`). Most
generated apps use the first-party pack without modification.

## The Context Graph

Scope selection and coding decisions require knowing which files matter. Mozaiks
builds a deterministic **context graph** — a snapshot of the workspace that maps
files, modules, pages, workflows, agents, tools, symbols, and configs to their
relationships.

In one sentence: the context graph gives the harness and coding worker the
smallest useful slice of the workspace.

```text
deterministic syntax extraction
  → Mozaiks contract mapping
  → bounded LLM semantic annotation
  → graph-aware retrieval
  → scoped refinement and coding context
```

At scope-selection time, the harness queries the graph to rank candidate files
by keyword relevance, contract role, and relationship proximity. The result is a
compact context pack — not a raw file dump — that fits inside a prompt.

This means:

- the coding worker sees only the files most likely to be affected
- the scope proposer can explain why it chose those files
- impact annotations (security-sensitive, contract-boundary, etc.) travel with
  the context pack

The context graph is built from code, not from a graph database. `AppContextGraph`
is the canonical model. Graph backends like FalkorDB or Neo4j may accelerate
retrieval later, but they are not the source of truth.

## How They Work Together

The clean mental model is:

- the harness decides **what kind of change this is**
- the context graph helps decide **which code to look at**, when scoped coding
  is needed

From there, the flow diverges based on classification.

**Feature / design / core changes** — harness routes deterministically:

```text
User: "add export controls to the dashboard"

classify: feature
  → route to app_revision sequence
  → decision: workflow_reentry (no confirmation needed)
  → workflow runs with routing context
```

The context graph is not used at the harness level here. The harness routes
without reading files.

**Patch changes** — this is where the context graph matters:

```text
User: "fix the broken column header in the projects table"

classify: patch
  → route to app_revision sequence
  → decision: needs scope
  → scope_requested: load context graph catalog
      → rank workspace files by "column header" + "projects" proximity
      → propose candidate files
  → coding_requested: load graph-neighborhood context for selected files
      → coding worker runs against scoped files only
  → decision: auto_patch (or clarify_scope if confidence is low)
```

The coding worker never sees the whole workspace. It sees the files the context
graph ranked highest for the request, plus their graph neighbors (imports,
declarations, related modules).

So the end-to-end flow is:

1. harness decides the route
2. context graph scopes the code when patch-level refinement is needed
3. workflow or coding worker executes the chosen path

## Read More

- [Harness Architecture](./02-harness-architecture.md)
- [Context Graph](./03-context-graph.md)
- [Refinement Control Plane](./04-refinement-control-plane.md)

For the full canonical contracts, each guide page links back to the deeper
Architecture documentation.
