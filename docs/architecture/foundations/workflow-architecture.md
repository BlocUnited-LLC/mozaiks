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
- `routes.json` matches it
- the workflow runs or resumes

## What Workflows Produce

Workflows can produce:

- live chat output
- progress updates
- artifacts
- saved results for pages
- follow-up app events

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

Workflow files still live under:

- `platform/workflows/*`

The current file contract remains:

- `orchestrator.yaml`
- `agents.yaml`
- `handoffs.yaml`
- `context_variables.yaml`
- `structured_outputs.yaml`
- `tools.yaml`
- `ui_config.yaml`
- `hooks.yaml`
- `tools/*.py`
- `ui/*`

## Practical Rule

Mozaiks should feel like:

- pages and adapters are the app surface
- events connect normal app behavior to automation
- workflows do the agentic work behind or alongside those surfaces

## Cross References

- [overview.md](overview.md)
- [event-system-architecture.md](event-system-architecture.md)
- [surface-taxonomy.md](surface-taxonomy.md)
