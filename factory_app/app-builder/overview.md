# App Builder Product Docs

This directory contains first-party product logic for the Mozaiks app builder.

Use these docs for builder-specific behavior such as:

- intent decomposition
- planning contracts
- build graph execution
- builder session routing
- user-facing builder workflow design

Keep runtime and platform architecture concerns in
`docs/architecture/foundations/`.

In particular, these builder docs assume the canonical three-loop model in
`docs/architecture/foundations/orchestration-control-loops.md`.

## Recommended Reading Order

1. [App Builder Architecture](app-builder-architecture.md)
2. [App Planning Contracts](app-planning-contracts.md)
3. [Builder Orchestration Taxonomy](builder-orchestration-taxonomy.md)
4. [Builder Execution Model](builder-execution-model.md)
5. [App Builder State and Routing](app-builder-state-and-routing.md)
6. [App Creation Guide](app-creation-guide.md)

## Boundary Rule

If a document explains how the runtime behaves for any app, it belongs in
`docs/architecture/foundations/`.

If a document explains how Mozaiks' first-party builder product generates or
iterates app bundles, it belongs here.
