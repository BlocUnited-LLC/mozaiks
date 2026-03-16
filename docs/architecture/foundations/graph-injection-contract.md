# Graph Injection Contract

This document defines how graph-shaped declaratives should be treated in
Mozaiks.

The important rule is that graphs are compiled runtime inputs, not freeform
places to hide business logic.

## Graph Families

### 1. Global workflow journey graph

Path:

- `platform/workflows/_pack/workflow_graph.json`

Purpose:

- cross-workflow ordering
- journey-level sequencing

### 2. Workflow-local execution graph

Path:

- `platform/workflows/{workflow_name}/_pack/workflow_graph.json`

Purpose:

- MFJ
- child workflow fan-out and fan-in
- explicit resume locations

### 3. Builder build graph

Purpose:

- compile-time task scheduling for the first-party builder

This graph belongs to the product layer and should not be shipped as core app
runtime behavior unless explicitly compiled into bundle assets.

## Injection Rules

### Graphs are explicit

A graph should define:

- nodes
- edges
- types
- resume or completion behavior

It should not rely on long prose fields called `logic`.

### Graphs are compiled

Graphs should be generated from structured planning or workflow declaratives.
They are not the first place where meaning is invented.

### Graphs are scoped

Workflow graphs own workflow execution structure.

They do not own:

- navigation
- domain event policy
- shell layout
- module registration

### Automation routes are not graph nodes

A domain event to workflow mapping belongs in automation declaratives, not in
`workflow_graph.json`.

The route may select a workflow entry or resume point, but it should not mutate
the graph contract itself.

## Why This Matters

When graph files absorb business policy, the system becomes unreadable:

- app policy hides in graph nodes
- workflow structure hides in prose
- generators stop knowing which layer they are editing

The graph contract should stay narrow enough that a compiler, runtime, and
human can all reason about it.

## Cross References

- [workflow-architecture.md](workflow-architecture.md)
- [builder-execution-model.md](builder-execution-model.md)
- [app-planning-contracts.md](app-planning-contracts.md)
