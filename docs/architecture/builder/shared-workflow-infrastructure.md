# Shared Workflow Infrastructure

This document defines when shared factory workflow infrastructure belongs under
`factory_app/workflows/_shared/` instead of a workflow-local `tools/` or `ui/`
directory, another workflow folder, or the runtime core.

## Core Rule

`factory_app/workflows/_shared/` is for factory-owned builder infrastructure
consumed by multiple factory workflows.

It is not part of generated workflow bundles.

Do not confuse these three cases:

- `factory_app/workflows/_shared/`:
  factory-owned shared implementation used by AppGenerator, AgentGenerator,
  DesignDocs, ValueEngine, ThemeCapture, or other builder workflows
- `factory_app/workflows/{WorkflowName}/tools/*.py`:
  workflow-local implementation owned by one workflow pack
- `factory_app/workflows/{WorkflowName}/ui/`:
  workflow-local UI registration and workflow-specific React surfaces
- `mozaiksai.core.*`:
  runtime/framework APIs required by runtime instances beyond the builder

## What Belongs In `_shared`

Put a module in `factory_app/workflows/_shared/` only when all of these are
true:

- it is consumed by two or more factory workflows
- it is builder infrastructure, not app-specific or product-specific behavior
- it should run as part of generation/build orchestration, validation, or
  bundle assembly
- it does not belong to every runtime instance as a general framework API

Typical `_shared` examples:

- deterministic quality gates reused by multiple generator workflows
- builder-only lifecycle helpers reused across multiple factory workflows
- static audit/validation helpers shared by AppGenerator and AgentGenerator
- reusable workflow React components shared by multiple factory workflows

Current canonical examples:

- `factory_app/workflows/_shared/generated_ui_contract.py`
- `factory_app/workflows/_shared/platform/build_lifecycle.py`
- `factory_app/workflows/_shared/ui/AgentAPIKeysBundleInput.js`

## Shared Workflow UI

Reusable workflow React components belong under
`factory_app/workflows/_shared/ui/` when they are consumed by multiple factory
workflows and are not app-specific or product-specific.

Shared workflow UI is implementation only. It is not auto-discovered or
registered directly by the workflow UI registry. Each consuming workflow must
import and re-export/register the shared component from its own
`factory_app/workflows/{WorkflowName}/ui/index.js`. That keeps the runtime
component namespace workflow-scoped, for example `AgentGenerator:ComponentName`
or `AppGenerator:ComponentName`.

Do not import React components from a sibling workflow folder. If two workflows
need the same component, move the reusable implementation to `_shared/ui/` and
keep only workflow-specific wrappers or registration barrels inside each
workflow's `ui/` directory.

## What Must Stay Workflow-Local

Keep code inside `factory_app/workflows/{WorkflowName}/tools/` when any of the
following is true:

- the helper is only used by one workflow
- the helper is part of that workflow's authored contract
- the workflow manifest references the file as a workflow-local tool path
- the helper expresses workflow-specific business behavior, prompt semantics,
  or output shaping

Generated workflow bundles must keep their own helpers workflow-local.
Generated output must not emit or depend on:

- `workflows/_shared`
- `app.workflows._shared`
- sibling workflow tool folders

If a workflow manifest needs a workflow-local file path but the implementation
is shared, use this pattern:

1. put the implementation in `factory_app/workflows/_shared/...`
2. keep a thin workflow-local wrapper in `tools/...`
3. let the workflow manifest continue to reference the workflow-local file

That keeps runtime file-path expectations stable while making shared ownership
explicit.

The same ownership rule applies to workflow UI: generated workflow bundles must
own their UI locally. `factory_app/workflows/_shared/ui/` is only for the
factory repo's first-party builder workflows.

## What Belongs In `mozaiksai.core`

Promote code into `mozaiksai.core.*` only when it is required outside the
builder and is part of the runtime/framework contract itself.

Examples:

- runtime orchestration primitives
- workflow pack parsing/loading
- multi-tenant filtering and persistence namespaces
- transport, eventing, or shared framework adapters

Do not move builder-only logic into runtime core just because more than one
factory workflow uses it.

## DesignDocs Boundary

DesignDocs is not the owner of shared builder infrastructure.

DesignDocs may describe:

- surface ownership
- UI intent
- realization boundaries
- persistent app/workflow contracts

But DesignDocs does not decide where shared Python builder helpers live.
That boundary is enforced directly in the builder implementation:

- tool imports
- hooks
- deterministic quality gates
- assembly/persistence tools

If shared code participates in generation or validation, wire it into the
factory workflow tools and hooks directly. Do not rely on DesignDocs prompt
prose to make it part of the build process.

## Practical Decision Order

When adding a helper, decide placement in this order:

1. Is it used by only one workflow? Put it in that workflow's `tools/`.
2. Is it used by multiple factory workflows but only during builder execution?
   Put it in `factory_app/workflows/_shared/`.
3. Is it required as a general runtime/framework API beyond the builder?
   Put it in `mozaiksai.core.*`.

## Cross References

- [app-builder-architecture.md](app-builder-architecture.md)
- [builder-execution-model.md](builder-execution-model.md)
- [workflow-architecture.md](../workflows/workflow-architecture.md)
- [workflow-authoring-contracts.md](../workflows/workflow-authoring-contracts.md)

