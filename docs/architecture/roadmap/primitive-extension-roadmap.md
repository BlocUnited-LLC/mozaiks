# Primitive Extension Roadmap

**Status:** Proposed roadmap item
**Created:** 2026-04-19

## Purpose

Capture the path for unlocking advanced persistent app surfaces without reviving a second freeform React lane inside AppGenerator.

This roadmap exists because the current schema-driven page system is intentionally bounded. It is strong for dashboards, tables, forms, cards, stats, modals, alerts, badges, skeleton states, and compositional layout. It is not yet sufficient for advanced charting, whiteboards, drag-and-drop builders, deeply custom dashboards, rich editors, unusual workflow canvases, or highly stateful multi-step UX.

## Hard Constraints In The Current Runtime

The present bottlenecks are architectural, not just prompt quality issues:

1. Page primitives are statically registered in `chat-ui/src/ui/page-renderer/PrimitiveRegistry.js`.
2. App/page schema validation only permits shipped primitives through `mozaiksai/core/workflow/ui_primitives.py` and `save_app_schema`.
3. The page runtime is optimized for bounded section widgets, not for complex local interaction engines.
4. The current page data model is section fetch + refetch, not a rich shared client-state graph.
5. The current event bus is intentionally lightweight and does not replace a canvas/editor/workflow runtime.
6. The frontend bundle does not currently ship charting, rich editor, drag-and-drop, graph, or whiteboard subsystems.

Because of this, "just let the schema invent a new primitive" is not a real solution. A new primitive must exist in code, be registered, be validated, be themed, and be tested.

## Decision Direction

Mozaiks should add a **controlled primitive extension path**, but it should not treat primitive creation as arbitrary prompt output.

The correct direction is a dedicated extension workflow, informally a "PrimitiveAgent" lane, that is:

- opt-in
- approval-gated
- platform-owned
- test-backed
- registry-aware
- validator-aware
- dependency-aware

This keeps the persistent page system declarative while allowing the platform vocabulary to grow.

## What A Primitive Agent Should Actually Do

When explicitly enabled and approved, the primitive-extension workflow should:

1. Propose the primitive contract first.
2. Classify whether the requested surface is:
   - a true reusable primitive
   - a page pattern/archetype
   - a subsystem that should not be modeled as a primitive
3. Generate the primitive implementation only if approved.
4. Update:
   - `chat-ui/src/ui/primitives/*`
   - `chat-ui/src/ui/primitives/index.js`
   - `chat-ui/src/ui/page-renderer/PrimitiveRegistry.js`
   - page/schema validation contracts
   - prompt guidance / structured outputs
   - tests and docs
5. Add or review any required frontend dependencies.
6. Keep the primitive inside the shared platform registry so future apps can use it declaratively.

## Required Safety Gates

Suggested gating model:

- `ALLOW_PLATFORM_PRIMITIVE_EXTENSION=false` by default
- require explicit user approval before primitive generation
- require generated tests before registration
- require dependency review for new frontend libraries
- require validator and renderer alignment in the same change set

The goal is not to block growth. The goal is to prevent false flexibility where prompts describe surfaces the runtime cannot actually render.

## Important Boundary: Not Everything Should Become A Primitive

The platform should separate three classes of work.

### 1. Good Primitive Families

These are strong candidates for platform-owned reusable primitives:

- advanced charts
- richer dashboard widgets
- KPI composites
- timeline variants
- analytics cards
- configurable data visualization surfaces

### 2. Better As Page Patterns Or Archetypes

These may not need brand-new primitives if the system gains better composition patterns:

- deeply custom dashboards
- multi-panel operational views
- stateful multi-step business flows
- entity workbenches

### 3. Should Be Treated As Subsystems, Not Simple Primitives

These usually require dedicated runtime models, not just another widget:

- whiteboards
- drag-and-drop builders
- workflow canvases / node graphs
- rich editors
- collaborative document surfaces

These may still need a platform-owned entry point, but they should likely be modeled as explicit subsystems or custom-slot runtimes rather than pretending they are equivalent to `Card` or `DataTable`.

## Roadmap Phases

### Phase 1: Primitive Extension Infrastructure

- define the primitive-extension workflow contract
- add approval gating and context-variable controls
- define the checklist for registry, validator, tests, docs, and dependency updates
- add architectural rules for when to create a primitive vs a pattern vs a subsystem

### Phase 2: First New Primitive Families

- chart primitive family
- richer analytics/dashboard primitive family
- advanced filter/controls primitives where needed

### Phase 3: Pattern And State Evolution

- better page archetypes for multi-step and workbench-style pages
- stronger shared client-state contracts for schema-driven pages
- richer layout and widget orchestration rules

### Phase 4: Subsystem Contracts

- rich editor subsystem contract
- canvas/graph subsystem contract
- drag-and-drop builder/runtime contract

## Non-Goals

- do not reintroduce arbitrary raw React page generation as the default path
- do not let page schemas reference unshipped primitive names without platform integration
- do not pretend declarative schemas eliminate runtime alignment risk

## Success Criteria

This roadmap item is complete when Mozaiks can safely expand its persistent app UI vocabulary without breaking the schema-first architecture and without creating a second uncontrolled frontend generation lane.
