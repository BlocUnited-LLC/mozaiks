# Add Workflows

This guide is the public starting point for adding a new workflow in the current
Mozaiks architecture.

## Choose The Owning Root

Put the workflow in the root that owns its behavior.

### Shared Builder Workflow

Use `factory_app/workflows/{WorkflowName}/` when the workflow belongs to the
shared generation/control-plane layer.

Examples:

- App generation
- workflow generation
- shared create/refinement journeys

### App-Owned Workflow

Use `app/workflows/{WorkflowName}/` when the workflow belongs to one app
workspace.

Examples:

- app-specific copilots
- product-local review or drafting flows
- event-triggered follow-up workflows for one app

## Canonical File Set

Each workflow should include the canonical YAML contract files:

- `orchestrator.yaml`
- `agents.yaml`
- `handoffs.yaml`
- `context_variables.yaml`
- `structured_outputs.yaml`
- `tools.yaml`
- `ui_config.yaml`
- `hooks.yaml`

Optional workflow-local files:

- `tools/*.py`
- `ui/*`
- `extended_orchestration/mfj_extension.json`

## Core Rules

- Keep tools simple; let agents do the reasoning.
- Put workflow triggers in `orchestrator.yaml`.
- Use structured outputs when the workflow needs typed downstream behavior.
- Keep reusable framework behavior in framework code, not copied into workflow
  folders.

## Read Next

- [Workflow Architecture](../../architecture/foundations/workflow-architecture.md)
- [Workflow Authoring Contracts](../../architecture/foundations/workflow-authoring-contracts.md)
- [Mid-Flight Journeys](../../reference/deep-dives/mid-flight-journeys.md)