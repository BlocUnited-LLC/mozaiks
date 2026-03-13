# Prompt Pack: Decompose App Intent

Use this prompt pack when an AI coding agent needs to turn a user idea into a
structured Mozaiks app plan before any file generation starts.

Do not use this prompt pack to directly generate final app files.

## Goal

Given a user request, produce a typed planning output that classifies the app
into:

- entities
- views
- actions
- modules
- workflows
- policies

The output should be a planning artifact, not a loose narrative.

## Read First

Before doing any work, read:

- `docs/architecture/foundations/app-creation-guide.md`
- `docs/architecture/foundations/app-bundle-declaratives.md`
- `docs/architecture/foundations/canonical-app-structure.md`
- `docs/architecture/foundations/workflow-architecture.md`
- `docs/architecture/foundations/app-planning-contracts.md`

Runtime contract implementation:

- `mozaiksai/core/orchestration/planning_contracts.py`

## Working Rules

- Do not start from `backend/` and `frontend/` file trees.
- Do not treat every capability as a workflow.
- Do not jump directly to React or Python implementation.
- Start from user intent, then decompose into app concerns.
- Be opinionated and bounded.
- Prefer typed lists over prose.

## Required Output Shape

Return a plan with these sections:

1. `app_spec`
2. `capability_map`
3. `entities`
4. `views`
5. `actions`
6. `modules`
7. `workflows`
8. `policies`
9. `bundle_plan`

The output should be valid for `DecompositionPackage`.

## Field Guidance

### `app_spec`

Include:

- `name`
- `summary`
- `user_personas`
- `core_jobs`
- `constraints`
- `non_goals`

### `capability_map`

Represent capabilities as verb+noun statements.

Examples:

- `browse listings`
- `create listing`
- `review incident timeline`
- `generate postmortem draft`

### `entities`

For each entity, include:

- `name`
- `purpose`
- `key_fields`
- `relations`

### `views`

For each view, include:

- `name`
- `type` (`list`, `detail`, `create`, `edit`, `dashboard`, `search`)
- `entity`
- `filters`
- `sort`

### `actions`

For each action, include:

- `name`
- `type` (`mutation`, `integration`, `trigger`)
- `target`
- `required_inputs`
- `result`

### `modules`

For each module, include:

- `name`
- `purpose`
- `primary_views`
- `route_shape`

### `workflows`

Only include workflows when the capability requires:

- multi-turn reasoning
- orchestration
- handoffs
- HITL checkpoints
- decomposition or synthesis

For each workflow, include:

- `name`
- `purpose`
- `entry_reason`
- `outputs`

### `policies`

Include only when role, plan, or tenant access matters.

For each policy, include:

- `name`
- `scope`
- `rule`

### `bundle_plan`

List which `platform/` file families must exist.

Examples:

- `platform/config/ai.json`
- `platform/config/module_registry.json`
- `platform/modules/orders/*`
- `platform/workflows/Concierge/*`
- future:
  - `platform/entities/*.json`
  - `platform/views/*.json`
  - `platform/actions/*.json`

## Classification Rules

Use this matrix:

- durable business data -> `entities`
- persistent page/surface -> `modules` + `views`
- deterministic mutation -> `actions`
- conversational or orchestrated intelligence -> `workflows`
- access rules -> `policies`

## What Good Looks Like

A good result:

- is structured
- separates CRUD from workflows
- identifies durable pages
- identifies where AI is actually needed
- gives a realistic app-bundle plan

A bad result:

- turns everything into a workflow
- skips entities and views
- jumps into implementation files too early
- describes “features” without classifying them

## Handoff To Build

After this planning step is accepted, the next AI agent should use the plan to:

1. create the bundle plan
2. decompose the bundle plan into build tasks
3. generate the actual `platform/` files
