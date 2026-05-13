# App Builder State and Routing

This document defines the thin state-and-routing layer for the first-party app
builder.

The builder should feel like one continuous product session even when it uses
multiple internal workflows.

## Core Rule

The user experiences one builder session.

Internal routing may switch between specialized workflows, but that switching
must be driven by typed state and control events rather than ad hoc prompt
logic.

This document describes the builder-session loop only. It is separate from the
workflow execution loop that runs one AG2 workflow and from the refinement
worker loop that performs scoped repair.

## Builder Session States

Recommended visible states:

- `intake`
- `concept_review`
- `architecture_review`
- `prerequisites_pending`
- `building`
- `validation`
- `preview`
- `iterating`
- `completed`
- `failed`

These states should map onto the builder-session responsibilities defined in
[orchestration-control-loops.md](../../docs/architecture/foundations/orchestration-control-loops.md).

## Internal Routing Targets

The builder may internally route between workflows such as:

- `IntentModeler`
- `ArchitecturePlanner`
- `AutomationPlanner`
- `WorkflowAuthor`
- `BundleCompiler`
- `Validator`

The exact names may change. The separation of responsibilities should not.

Required write boundary:

- `IntentModeler`, `ArchitecturePlanner`, and `AutomationPlanner` route state
  and typed models, but do not write bundle paths
- `WorkflowAuthor` may write only workflow declaratives
- `BundleCompiler` may write non-workflow bundle declaratives
- `Validator` may write reports, but not bundle declaratives

## What This Layer Consumes

This layer should consume:

- `ConceptBlueprint`
- `IntentBrief`
- `CapabilityMap`
- `PlatformProvisionPlan`
- `DecompositionPackage`
- `AppModel`
- `AutomationModel`
- `WorkflowModel`
- `BundlePlan`
- `BuildGraph`
- `BuilderBlueprint`
- `ChangeIntent`
- `ImpactSet`
- generic control events

It should not consume raw AG2 event noise directly when a normalized control
fact exists.

## What This Layer Produces

It should produce:

- visible session state
- internal workflow transfer decisions
- iteration lineage
- preview readiness
- scoped rebuild decisions

## Change Classification

`ChangeIntent` should decide whether a user request is:

- substrate-only
- automation-only
- workflow-only
- foundational and requires architectural replanning

`ImpactSet` should then decide how much of the approved plan must be reopened.

That pair is the routing key that keeps iteration coherent.

## Why This Layer Exists

MFJ or workflow packs alone do not solve:

- when to show the architecture review
- when to collect missing prerequisites
- whether a user change requires replanning
- whether a change touches substrate, automation, or workflows

This layer is the missing product control plane.

## Cross References

- [orchestration-control-loops.md](../../docs/architecture/foundations/orchestration-control-loops.md)
- [builder-orchestration-taxonomy.md](builder-orchestration-taxonomy.md)
- [builder-execution-model.md](builder-execution-model.md)
