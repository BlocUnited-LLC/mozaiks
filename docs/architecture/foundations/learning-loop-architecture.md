# Learning Loop Architecture

This document defines how learning and feedback loops should fit into Mozaiks
without blurring runtime boundaries.

## Core Rule

Learning loops are downstream improvement systems.

They should observe:

- domain events
- control events
- runtime events
- user feedback

They should not silently mutate live app behavior inside the runtime without an
explicit review or compilation step.

## Inputs

Useful learning inputs include:

- automation route outcomes
- workflow completion and failure patterns
- user approvals and rejections
- artifact quality feedback
- substrate usage and operational telemetry

## Outputs

Learning systems may produce:

- prompt improvements
- workflow design suggestions
- automation route suggestions
- template upgrades
- policy recommendations
- generator heuristics

These outputs should usually flow back into:

- product templates
- bundle revisions
- reviewed config changes

Not directly into opaque live self-modification.

## Why This Boundary Matters

If the learning loop writes directly into the running architecture without a
clear contract:

- workflow behavior drifts invisibly
- app policy becomes hard to audit
- generator outputs become non-deterministic

The platform should learn, but it should learn through explicit artifacts and
reviewable changes.

## Recommended Pattern

```text
runtime and substrate facts
  -> telemetry and analysis
  -> recommendation artifact
  -> review
  -> bundle or template update
```

This keeps the architecture observable and debuggable.

## Cross References

- [event-system-architecture.md](event-system-architecture.md)
- [runtime-state-and-control-events.md](runtime-state-and-control-events.md)
- [app-builder-architecture.md](app-builder-architecture.md)
