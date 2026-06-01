# Platform Intelligence

Mozaiks has two layers that make the build and refinement UX intent-aware
without turning every workflow into a giant decision tree.

## The Control-Plane Harness

When a user sends a message like "fix this dashboard" or "add export controls",
Mozaiks cannot handle that as normal chat. The request needs:

- context about what was already built
- a classification of how big the change is
- a deterministic choice about what to do next

That is the job of the **control-plane harness**. It sits above workflow-local
AG2 execution and processes checkpoint events before any workflow runs.

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

The two paths diverge at classification.

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

## Read More

- [Control-Plane Harness Architecture](../../architecture/workflows/control-plane-harness-architecture.md)
- [Refinement Control Plane](../../architecture/workflows/refinement-control-plane.md)
- [Context Graph and Code Intelligence](../../architecture/foundations/context-graph-and-code-intelligence.md)
