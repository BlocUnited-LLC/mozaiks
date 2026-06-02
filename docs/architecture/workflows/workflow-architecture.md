# Workflow Architecture

This document defines what workflows are in Mozaiks.

## Core Rule

Workflows are for agentic work.

Builder-session harness behavior is not authored in workflow packs. It is
driven by app startup in `app/config/ai.json` plus optional app-local
control-plane policy and manifest files under `control_plane/config/`, then
layered above workflow execution by the host/harness.

Use a workflow when the value comes from:

- reasoning
- orchestration
- multi-step generation
- review loops
- HITL
- agent tools

Do not use workflows as the default answer for normal app screens or ordinary
backend actions.

## What Starts A Workflow

A workflow should usually start in one of two ways:

### 1. User action

Examples:

- user starts from chat
- user clicks a page action
- user resumes an active run

### 2. App event automation

Examples:

- an app event happens
- a workflow `triggers:` rule matches it
- the workflow runs or resumes

## What Workflows Produce

Workflows can produce:

- live chat output
- progress updates
- artifacts
- saved results for pages
- follow-up app events

## Initial Generation vs Refinement

Workflows may be entered in two very different ways:

- initial generation that creates the first canonical artifact set
- refinement re-entry that modifies an existing artifact version

Those are not the same responsibility.

Post-generation changes should not automatically route back through intake or
planning agents. A control plane should first classify whether the request is a
`patch`, `design`, `feature`, or `core` change, then choose the smallest valid
re-entry point.

Journey sequencing and ordinary AG2 handoffs are downstream consumers of that
decision. They are not the classifier. The detailed refinement-routing plan is
internal; the public contract is that refinement uses a classifier before
choosing a workflow re-entry point.

That distinction exists because workflows are only one of Mozaiks' three
control loops. Workflow-local AG2 orchestration is separate from the
builder-session loop that chooses re-entry points and from the refinement worker
loop that performs scoped repair.

## What Workflows Should Not Own

Workflows should not be the default place for:

- navigation
- normal pages
- simple CRUD saves
- basic deterministic backend actions

If something is mostly a screen, make it a page.

If something is mostly support logic, make it a module.

If something is mostly optional operator tooling, make it an adapter.

Generated workflow bundles must keep their helpers workflow-local. Do not emit
or depend on `workflows/_shared` or `app.workflows._shared` inside generated
workflow output. Shared factory builder infrastructure belongs under
`factory_app/workflows/_shared/` and is consumed by the factory workflows
themselves, not by generated bundles.

## Workflow Files

Workflow files live under:

- `workflows/*` — workflows owned by one app workspace
- `factory_app/workflows/*` — shared factory workflows owned by the builder system, not by individual app workspaces
- `factory_app/workflows/_shared/*.py` — factory-owned shared Python infrastructure consumed by multiple factory workflows; not part of generated workflow bundles
- `factory_app/workflows/extended_orchestration/` — shared build launcher, journeys, and transition UI
- `workspace-root workflows/extended_orchestration/extension_registry.json` — optional app-local registry used when that app workflow root is selected

For the factory dogfood workspace specifically, `factory_app/workflows/*`
is only an app-local overlay path. It is not the canonical location for shared
generation-core workflows, and the directory should stay absent until the
factory app actually owns a workflow that is not part of the shared builder
layer.

Workflow resolution is single-root by default.

- Studio binds to `factory_app/workflows/` as the shared builder root
- app/product hosts bind to the active app root's `workflows/` directory when
  the app owns workflows there
- `MOZAIKS_WORKFLOWS_PATH` may override the selected root explicitly

The runtime does not auto-merge app and factory workflow roots in normal
platform/studio execution. That is the composition contract between an active
app workspace and `factory_app`: a given host/session executes one selected
workflow root, and `factory_app/workflows/` remains the shared builder layer
for Studio/builder execution.

Builder workflows may generate new workflow bundles, but generated output is
staged under `MOZAIKS_GENERATED_ARTIFACTS_PATH` and is not runtime-loaded until
explicitly promoted into an active app root's `workflows/` directory.

The current file contract:

- `orchestrator.yaml`
- `agents.yaml`
- `handoffs.yaml`
- `context_variables.yaml`
- `structured_outputs.yaml`
- `tools.yaml`
- `ui_config.yaml`
- `hooks.yaml`
- `extended_orchestration/task_batches.yaml` — optional workflow-local AG2 task batch contract
- `tools/*.py`
- `ui/*`

## Task Batches

When a workflow needs to process many typed work items in parallel, it declares
a task batch in `extended_orchestration/task_batches.yaml`.

The runtime handles:

- task source resolution from context variables or structured output
- bounded AG2 agent calls for each task item
- dependency, retry, timeout, and failure policy enforcement
- context injection through the declared `result.context_key`
- downstream synthesis by normal workflow agents

## Practical Rule

Mozaiks should feel like:

- pages and adapters are the app surface
- events connect normal app behavior to automation
- workflows do the agentic work behind or alongside those surfaces

## Cross References

- [Foundations Overview](../foundations/overview.md)
- [../foundations/events-and-data/event-system.md](../foundations/events-and-data/event-system.md)
- [../app/surface-model.md](../app/surface-model.md)
- [orchestration-control-loops.md](orchestration-control-loops.md)


