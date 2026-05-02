# Generated Frontend Surface Contract

This document defines the canonical frontend scope for generated Mozaiks apps.

The key rule is simple:

- share one primitive and theme foundation
- keep separate authoring contracts for persistent app UI, workflow UI, transition UI, and bounded custom UI

Mozaiks should not flatten those surfaces into one generic AG-UI-style contract.

## Canonical surfaces

| Surface | Owner | Contract | Runtime path |
| --- | --- | --- | --- |
| Persistent App UI | AppGenerator | `app.json` + `ui/pages/*.yaml` | `SchemaPage` -> `PageRenderer` -> page primitives |
| Workflow UI | AgentGenerator / handwritten workflows | Python tool + workflow-local React | `use_ui_tool(...)` -> `chat.tool_call` -> `WorkflowUIRouter` |
| Transition UI | workflow pack author / shell author | `extension_registry.json` + transition component | `RouteRenderer` / `TransitionScreen` |
| Bounded custom UI | AppGenerator custom route bundle, module/admin JS stubs | `ui/route_manifest.json`, `ui/pages/custom/*.jsx`, explicit stub contracts | route/component registry |

## Shared primitive foundation

These surfaces should share one design foundation, not one authoring contract.

Shared foundations:

- semantic tokens from `brand/theme_config.json`
- shared shell chrome from `config/shell.json`
- shared media inventory from `config/asset_manifest.json`
- shipped page primitives from `chat-ui/src/ui/page-renderer/PrimitiveRegistry.js`
- shipped workflow/component primitives from `chat-ui/src/ui/primitives/index.js`
- generator-side primitive guidance from `mozaiksai/core/workflow/ui_primitives.py`

Use the same primitives where possible.
Do not collapse the producers that use them.

## Persistent app UI

Persistent app UI owns:

- durable routes
- dashboards
- lists
- detail pages
- forms
- boards
- product workspaces

Rules:

- default to declarative page schemas
- prefer primitive composition over raw React
- use `custom_route_bundle` only when the shipped page primitive system cannot express a durable route cleanly
- durable state changes should go through module actions and API surfaces

Persistent pages may launch workflows, but they do not render workflow-local React directly.

Canonical workflow launch seam from a page:

```yaml
actions:
  - id: review-with-ai
    label: Review With AI
    action_type: workflow
    workflow_id: CustomerSupport
    context_variables:
      customer_id: "{id}"
      source_page: customer-detail
```

Rules:

- `workflow_id` is a runtime workflow registry id, not a workflow capability id
- `context_variables` must stay deterministic and match the target workflow context contract
- do not fake workflow launch with `navigate` or `ui.*` events

## Workflow UI

Workflow UI owns:

- human approval cards
- agent-driven forms
- artifact panels
- diagrams
- transient review and planning surfaces
- response-required checkpoint UI

Rules:

- response-bearing workflow UI uses `use_ui_tool(...)`
- fire-and-forget workflow UI uses `emit_ui_surface(...)`
- wire contract is `chat.tool_call`
- response contract is `tool_call_response`
- React components are workflow-local and mounted by `WorkflowUIRouter`

This is the surface that should keep borrowing ideas from AG-UI and CopilotKit:

- explicit tool lifecycle
- clearer interrupt/resume semantics
- simpler renderer ergonomics

It is not the contract for persistent app pages.

## Transition UI

Transition UI owns:

- pre-workflow routing
- between-workflow checkpoints
- deterministic user choices
- seeded launch context

Rules:

- author routing in `extension_registry.json`
- keep workflow sequences runtime-oriented
- keep branded visuals in transition React components, not in routing metadata

## Bounded custom UI

Use bounded custom UI only where a strict contract already declares it:

- app-level custom durable routes via `custom_route_bundle`
- admin/module custom components via explicit JS stub contracts
- workflow-local React through workflow UI contracts

Do not create a second freeform page-generation lane.

## Workflow integration rules

There are two different identifiers in play. Keep them separate.

Direct page launch:

- uses workflow registry id
- example: `CustomerSupport`

Event-driven workflow routing:

- uses workflow capability id
- example: `customer-support`

Rules:

- page actions use workflow registry ids
- `event_flows` and `subscriptions.yaml` use workflow capability ids
- startup mode shapes user expectations only; the runtime decides who speaks first after session launch

## What to borrow from AG-UI and CopilotKit

Borrow:

- cleaner workflow UI protocol semantics
- explicit tool lifecycle state
- explicit interrupt/resume modeling
- shared-state discipline when a real protocol-level state stream is needed

Do not borrow:

- the idea that every interactive frontend surface should become a tool renderer
- the collapse of persistent app pages into chat-only UI

## Production direction

The production-ready target is:

1. one primitive/design foundation
2. one canonical workflow UI transport lane
3. one canonical persistent page schema lane
4. one explicit page-to-workflow launch seam
5. no overlap between page primitives and workflow-local React ownership

That gives Mozaiks the protocol discipline of AG-UI where it matters, while preserving the broader generated-app architecture that CopilotKit alone does not cover.
