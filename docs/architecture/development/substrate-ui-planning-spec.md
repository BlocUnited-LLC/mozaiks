# Substrate UI Planning Spec

**Status**: Development  
**Scope**: non-AI CRUD, UI pages, and event-to-automation seams

This spec defines how the builder should plan and compile the non-AI side of an
app without blurring runtime boundaries.

## Core Rule

Plan substrate logic first, then automation policy, then workflows.

Use this chain:

1. CRUD/action plan
2. post-commit event emission plan
3. automation route policy
4. workflow execution plan

## Contract Order (Non-AI First)

For non-AI and page surfaces, the builder should materialize these in order:

1. `IntentBrief`
2. `CapabilityMap`
3. `DecompositionPackage` (substrate subset first)
4. `BundlePlan`
5. `BuildGraph`

Within `DecompositionPackage`, derive in this order:

1. `EntitySpec`
2. `ViewSpec`
3. `ActionSpec`
4. `PolicySpec`
5. `ModuleSpec`
6. `DomainEventSpec`
7. `AutomationRouteSpec`
8. `WorkflowSpec`

## Typed Specs Used Today

Canonical classes live in
`mozaiksai/core/orchestration/planning_contracts.py`.

For substrate and UI page planning, these are the primary types:

- `EntitySpec`
- `ViewSpec`
- `ActionSpec`
- `PolicySpec`
- `ModuleSpec`
- `DomainEventSpec`
- `AutomationRouteSpec`
- `BundlePlan`

## Compile Targets

Use this mapping when compiling non-AI and UI outputs:

| Planning type | Runtime/declarative output |
| --- | --- |
| `ModuleSpec` | `platform/modules/<name>/module.json`, `handler.py`, `ui/index.js` |
| shell/page projection | `platform/config/navigation_config.json` |
| module registry projection | `platform/config/module_registry.json` |
| admin portal navigation projection | `platform/config/admin.json` |
| `DomainEventSpec` | `platform/automations/event_catalog.json` |
| `AutomationRouteSpec` | `platform/automations/routes.json` |
| settings/notifications/subscriptions provision config | `platform/config/settings_config.json`, `notifications_config.json`, `subscription_config.json` |

## Event Boundary Rules

1. `mozaikscore` emits domain facts using internal source names (for example
   `module_executed`, `settings_updated`).
2. `event_catalog.json` maps source names to canonical `event_type`.
3. `routes.json` maps `event_type` + `when` predicates to effects.
4. `mozaikscore` must not emit workflow names.
5. `mozaiksai` owns route matching and workflow dispatch.

## `post_commit_only` Rule

Default to `post_commit_only: true`.

Set `false` only for pre-commit or no-commit intent signals, such as validation
or advisory checks.

## Strict Stub Contracts

Generated stubs must obey strict signatures.

### Python module stub

```python
async def execute(data: dict) -> dict:
    ...
```

Rules:

- never require positional runtime-only parameters
- use `data["_context"]` and `data["app_id"]`/`data["user_id"]` when needed
- return JSON-serializable objects

### UI module registry stub

```javascript
import MyPage from './MyPage';

const MyComponents = {
  MyPage,
};

export default MyComponents;
```

Rules:

- default export must be an object map
- component keys must match names used by route/module declaratives

## Minimal Starter Mode

When bootstrapping a new app, keep only:

- 1-2 modules
- 1 default shell page (`AdminPortal` at `/admin`)
- 2 domain events
- 1-2 automation routes using only `workflow.run` or `workflow.resume`

In starter mode, module surfaces should come from module declaratives and
`module_registry` projection. Avoid duplicating module routes in `pages[]`.

Avoid advanced effect kinds in starter mode.

## Build-Time Validation Checklist

Before compile succeeds, validate:

1. every `ActionSpec` write has an expected event emission or explicit reason for none
2. every route `event_type` exists in `event_catalog`
3. every workflow target in enabled routes exists in global workflow registry
4. every module route has a module stub (`module.json`, `handler.py`, `ui/index.js`)
5. no app-specific workflow names exist in substrate runtime internals

## What This Spec Is Not

This spec is not workflow runtime orchestration.

Workflow-local fan-out/fan-in behavior and global journey sequencing are still
owned by workflow pack graphs under `platform/workflows/*/_pack/workflow_graph.json`.
