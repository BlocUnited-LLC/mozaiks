# Workflow Architecture

This document defines what workflows are in Mozaiks.

## Core Rule

Workflows are for agentic work.

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

## What Workflows Should Not Own

Workflows should not be the default place for:

- navigation
- normal pages
- simple CRUD saves
- basic deterministic backend actions

If something is mostly a screen, make it a page.

If something is mostly support logic, make it a module.

If something is mostly optional operator tooling, make it an adapter.

## Workflow Files

Workflow files live under:

- `app/workflows/*` — workflows owned by one app workspace
- shared factory workflows — owned by the builder system, not by individual app workspaces
- `mozaiks-platform/app/workflows/*` — current App Zero product-owned workflow implementations during transition
- `factory_app/app/workflows/extended_orchestration/` — shared build launcher, journeys, and transition UI
- `mozaiks-platform/app/workflows/extended_orchestration/extension_registry.json` — App Zero product overlay on top of that shared routing layer

Workflow resolution is multi-root:

- the active app root's `workflows/` directory is searched first
- the shared generation-core workflow root is searched second
- `MOZAIKS_WORKFLOW_ROOTS` may override that order explicitly

This lets App Zero keep product workflows locally while still consuming shared
factory workflows through the same runtime and launcher graph.

That is the current composition contract between `mozaiks-platform` and
`factory_app`: App Zero owns its product workflows and overlay registry, while
`factory_app/app/workflows/` remains the shared builder layer loaded alongside
the active app root.

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
- `extended_orchestration/mfj_extension.json` — required when the workflow uses mid-flight journeys (MFJ)
- `tools/*.py`
- `ui/*`

## Mid-Flight Journeys

When a workflow needs to decompose work into parallel child runs and then
fan-in, it declares a mid-flight journey in `extended_orchestration/mfj_extension.json`.

The runtime handles:

- fan-out: spawning N child workflow runs from the trigger agent's output
- fan-in: waiting for all children, merging results, resuming the parent
- resume override: forcing the parent back to the declared `resume_agent`
- context injection: writing merged child results under the `inject_as` key
- auto-synthesis: registering context variables for MFJ keys so agents can
  read them without manual declarations in `context_variables.yaml`

## Practical Rule

Mozaiks should feel like:

- pages and adapters are the app surface
- events connect normal app behavior to automation
- workflows do the agentic work behind or alongside those surfaces

## Cross References

- [overview.md](overview.md)
- [event-system.md](event-system.md)
- [surface-model.md](surface-model.md)
