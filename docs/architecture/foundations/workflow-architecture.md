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
decision. They are not the classifier.

See [Refinement Control Plane](../specs/REFINEMENT_CONTROL_PLANE_SPEC.md).

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

- `platform/workflows/*` — product and showcase workflows
- `mozaiks-platform/app/workflows/*` — platform-builder workflows (AgentGenerator etc.)

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
