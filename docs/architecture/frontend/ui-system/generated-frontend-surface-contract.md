# Generated Frontend Surface Contract

This document defines the canonical frontend scope for generated Mozaiks apps.
For the validation model, see
[UI System Quality Gates](ui-system-quality-gates.md).

The key rule is simple:

- share one primitive and theme foundation
- keep separate authoring contracts for persistent app UI, workflow UI, transition UI, and bounded custom UI

Mozaiks should not flatten those surfaces into one generic AG-UI-style contract.

## Canonical surfaces

Finite surface IDs are defined in
`mozaiksai/core/workflow/ui_surface_taxonomy.py` and injected into generator
prompts. Agents must emit only these IDs:

- `declarative_page`
- `custom_react_page`
- `agent_tool`
- `transition`

| Surface | Owner | Contract | Runtime path |
| --- | --- | --- | --- |
| Persistent App UI | AppGenerator | `app.json` + `ui/pages/*.yaml` | `SchemaPage` -> `PageRenderer` -> page primitives |
| Workflow UI | AgentGenerator / handwritten workflows | Python tool + shipped shared component or workflow-local React | `use_ui_tool(...)` -> `chat.tool_call` -> `WorkflowUIRouter` |
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

## App UI Quality Gate

Persistent AppGenerator pages have a deterministic gate before assembly:

- `AppSchemaAgent` emits `AppSchemaOutput`
- `save_app_schema` persists `app.json`, `ui/pages/*.yaml`, optional custom route artifacts, and `app_ui_quality_warnings`
- `factory_app/workflows/generated_ui_contract.py` audits both declarative page schemas and optional `custom_route_bundle.page_files`
- `AppUIQualityAgent` calls `review_ui_quality`
- `app_ui_quality_status == "passed"` is required before `AssemblyAgent`
- `needs_revision` routes back to `AppSchemaAgent`; `blocked` routes to user/operator review

This is the production bar for schema-driven UI. The gate is runtime state, not
prompt prose, so live AG2 runs cannot assemble generated app UI until the
quality status passes.

After the gate passes, assembly must collect files from the persisted
`generated_app_dir` app workspace. The schema-driven path does not rely on the
LLM to reconstruct `code_files` from chat history. Downstream assembly,
validation, and download tools read the canonical saved artifacts:

- `app.json`
- `ui/pages/*.yaml`
- optional `ui/route_manifest.json`
- optional `ui/pages/custom/*`
- optional `ui/index.js`
- optional `brand/theme_config.json`
- optional `config/*.json`

The first browser-level acceptance target for this lane is:

```powershell
npm --prefix web_shell run test:generated-ui
```

That Playwright suite serves a generated app fixture as the active app root,
loads canonical `ui/pages/*.yaml` through `SchemaPage`, and verifies primitive
rendering across desktop and mobile. It complements the AG2 quality gate: the
gate decides whether agents may assemble artifacts, while Playwright proves that
the approved artifacts render cleanly in the shell.

Playwright is intentionally a second-stage render acceptance check, not the
same thing as the AG2 quality gate. The AG2 gate is cheap, deterministic, and
agent-readable before assembly. Playwright runs against rendered browser output
after artifacts exist and catches issues the schema audit cannot see: missing
routes, browser errors, invisible headings, horizontal overflow, broken
declarative form submission, and responsive layout regressions.

Browser findings flow through `scripts/generated_ui_acceptance.py` when they
need to become structured production-readiness feedback:

- output status: `passed`, `needs_revision`, or `blocked`
- structured findings: severity, route, category, message, suggested fix
- revision text: concrete rendered issue, not generic prompt advice

This keeps strictness bounded. Playwright failures should be converted into
specific findings such as route, severity, category, message, and suggested fix;
they should not become an open-ended agent loop. The acceptance script has its
own revision-budget vocabulary and can block to user/operator review when the
generated UI keeps failing browser checks.

To run the generic rendered-app smoke against a generated app root instead of
the checked-in fixture:

```powershell
$env:MOZAIKS_GENERATED_UI_APP_ROOT="generated/apps/{app_id}/{build_id}/app"
npm --prefix web_shell run test:generated-ui:app
```

For structured production-readiness output:

```powershell
python scripts/generated_ui_acceptance.py `
  --app-root "generated/apps/{app_id}/{build_id}/app" `
  --output generated-ui-acceptance.json
```

## Workflow UI

Workflow UI owns:

- human approval cards
- agent-driven forms
- artifact panels
- diagrams
- transient review and planning surfaces
- response-required checkpoint UI

Workflow-local React now has a deterministic gate:

- `UIFileGenerator` persists its emitted UI files through `save_workflow_ui_files_output`
- `factory_app/workflows/generated_ui_contract.py` audits generated workflow-local React with the same primitive/copy/style rules used by AppGenerator custom React
- `WorkflowUIQualityAgent` calls `review_workflow_ui_quality`
- `workflow_ui_quality_status == "passed"` is required before backend tool generation and bundle delivery
- `needs_revision` routes back to `UIFileGenerator`; `blocked` routes to user/operator review

Rules:

- response-bearing workflow UI uses `use_ui_tool(...)`
- fire-and-forget workflow UI uses `emit_ui_surface(...)`
- wire contract is `chat.tool_call`
- response contract is `tool_call_response`
- generic text checkpoints in `chat-ui` should use the standard composer lane
  (`interaction_type=input_request`, `display=composer`)
- inline React components are for structured workflow interaction, not default
  free-text reply
- shared workflow components live in `chat-ui/src/core/ui/` and are mounted by `WorkflowUIRouter`
- runtime-enriched workflow payloads should carry manifest-owned `workflow_primitive`, `ui_realization`, and `ui_contract`
- shipped shared workflow components should derive their actions from `ui_contract.actions_schema`
- workflow-local React is only for genuine customization or primitive gaps
- workflow-local React must import shared primitives from `@mozaiks/chat-ui/ui`; deep `chat-ui/src` imports and removed primitives (`Card`, `Stat`, `Badge`) fail the gate
- `UIFileGenerator` should emit canonical `CodeFile` entries under the workflow's
  `tools/` and `ui/` tree, not ad hoc frontend payload shapes
- workflow assembly synthesizes `ui/index.js` deterministically from the
  workflow-local component files that remain after contract validation
- workflow interaction planning should use the canonical
  [Workflow UI Primitive Catalog](workflow-ui-primitive-catalog.md)
- shell-owned workflow status surfaces such as progress, run status, and agent
  activity are not workflow-local React generation targets
- every real workflow manifest UI entry must declare `ui.workflow_primitive`
- every real workflow manifest UI entry must declare `ui.realization`
- `composer_reply` remains shell-owned and must not generate a `UI_Tool` or `UI_Surface` manifest entry
- shipped workflow primitives should prefer the canonical shared component names directly
- `ui.realization=shipped_component` means no workflow-local React file should exist for that checkpoint
- `ui.realization=workflow_wrapper` means the workflow owns only a thin wrapper/re-export around a shipped primitive
- `ui.realization=generated_component` means the workflow owns the full custom React surface
- workflow-local wrappers around shipped components should stay thin and intentional
- if `ui.component` already equals the canonical shipped component name, no
  workflow-local React file should be generated or saved for that checkpoint
- the first deterministic regression target for this contract is
  `factory_app/workflows/WorkflowPrimitiveAcceptance`
- the stable real-AG2 regression target is
  `factory_app/workflows/AgentGenerator` with the workflow-owned smoke pair:
  `factory_app/workflows/AgentGenerator/smoke_prompt.txt` and
  `factory_app/workflows/AgentGenerator/smoke_responses.json`

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
- `event_flows` and runtime trigger/reaction contracts use workflow capability ids
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
