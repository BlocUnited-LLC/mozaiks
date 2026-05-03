# Prompt Packs

Use these pages when you want to hand a Mozaiks task to an AI coding agent
without improvising the repo context from scratch.

## Start Here

Before any task-specific prompt, make sure the agent reads:

- `ARCHITECTURE.md`
- `AGENTS.md`
- `CLAUDE.md`

Those three files define the canonical host layering, workspace model, and
repo-specific operating rules.

## Recommended Packs

### 1. Bootstrap An Agent In This Repo

Use [Agent Bootstrap Prompt](../agent-bootstrap-prompt.md) when you want a
clean repo-aware starting point for a coding agent.

### 2. Setup And Local Bring-Up

Use [Getting Started](../getting-started.md) when the task is environment
setup, local startup, or first-run validation.

### 3. Workflow Authoring

Use [Add Workflows](../guides/adding-workflows/01-overview.md) when the task is
creating or modifying workflow bundles under the current shared-generation or
app-owned workflow roots.

### 4. App Shell And Branding

Use [App Shell And Branding](../guides/custom-brand-integration/01-overview.md)
when the task is shell config, theme config, logos, navigation, or auth-facing
chrome.

### 5. Architecture Or Contract Cleanup

Use [Architecture Overview](../architecture/index.md) and the foundations docs
when the task is documentation cleanup, contract validation, or path/ownership
drift.

## Current Repo Guardrails

Any prompt pack for this repo should preserve these boundaries:

- canonical hosts live under `mozaiksai/hosts/`
- shared builder workflows live under `factory_app/workflows/`
- app-owned workflows live under the active app root's `app/workflows/`
- the first-party Studio app bundle in this repo is `factory_app/app/`
- do not reintroduce retired roots such as `platform/` as the canonical app
  workspace contract

## Related

- [Agent Bootstrap Prompt](../agent-bootstrap-prompt.md)
- [Add Workflows](../guides/adding-workflows/01-overview.md)
- [App Shell And Branding](../guides/custom-brand-integration/01-overview.md)
