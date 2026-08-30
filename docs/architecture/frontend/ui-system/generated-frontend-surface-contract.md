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

### Brand-driven visual identity

`app/brand/theme_config.json` is the canonical visual identity source for an
app workspace. Runtime serves it through `/api/theme-config`; the frontend
theme loader turns it into semantic CSS tokens before generated pages render.

`app/config/shell.json` owns shell behavior: navigation policy, chrome modes,
header/profile/footer behavior, and compact shortcuts. It must not carry raw
visual token values such as colors, font stacks, spacing scales, or page-local
brand palettes.

Generated React and custom routes must use:

- shared primitives
- semantic primitive variants such as `Button variant`, `StatusPill tone`, card
  or panel variants, density, radius, and status tone
- semantic classes and tokens such as `bg-background`, `bg-card`,
  `text-foreground`, `text-muted-foreground`, `text-primary`, `border-border`,
  `font-sans`, and `font-heading`

Generated React and custom routes must not hardcode:

- hex, RGB, or HSL color literals
- literal font-family names or `font-family` declarations
- page-local brand palettes
- one-off local button, card, badge, or status styles when a shared primitive
  exists
- repeated local rounded card shells when `SurfaceCard` or `Panel` already fits

Brand values flow through the compact selector layer:

- `theme.primary`
- `theme.radius`
- `theme.font`
- `theme.font_heading`
- `theme.appearance`
- `theme.density`

They may also flow through the expanded runtime shell token layer:

- `fonts`
- `colors`
- `shadows`
- `ui`
- `primitives`

`theme.*` is the compact selector layer for App UI `--mz-*` tokens. `fonts`,
`colors`, `shadows`, `ui`, and `primitives` are the expanded shell/chat token
layer for `--color-*`, `--font-*`, and `--core-primitive-*`. Both surfaces are
part of the current theme contract until all shell tokens move to `--mz-*`.

Local fonts live only under `app/brand/fonts/` and are referenced from theme
config with `/fonts/...` URLs. Do not copy font binaries into generated
artifacts outside `brand/`. Google Fonts are declared in `theme_config.json`
and injected by the theme loader.

### Custom route primitive contract

Bounded custom UI is still part of the shared design system. A custom React
route may exist because the page needs behavior that YAML cannot express, but
it must not create a parallel visual system.

Custom route files under `ui/pages/custom/*.jsx` should import shared
primitives from `@mozaiks/chat-ui/ui` for the common UI shapes:

- `Button`, `ActionButton`, `IconButton`, `LinkButton` for actions
- `StatusPill`, `Alert`, `AlertBanner` for state and notices
- `SurfaceCard`, `Panel` for cards and panels
- `Metric`, `SummaryStrip`, `SegmentedBar` for compact summaries
- `CollectionToolbar`, `ResourceList`, `ResourceTable`, `DataTable` for search,
  lists, and tabular records
- `InlineEmptyState`, `LoadingState`, `ErrorState`, `Skeleton` for feedback

Thin domain wrappers are allowed when they delegate to shared primitives. For
example, a notifications page can implement `NotificationRow` as a wrapper
around `ResourceList` cells and `StatusPill`, or a managed analytics dashboard
can implement `AnalyticsMetricGroup` as a wrapper around `SummaryStrip`.

Local visual primitive clones are not allowed:

- `function StatusPill(...)`
- `function MetricTile(...)`
- `function StatCard(...)`
- `function Badge(...)`
- raw primary buttons such as `<button className="...bg-primary...">`
- repeated local rounded card shells when `SurfaceCard`, `Panel`,
  `ResourceList`, or `SummaryStrip` already fits

Quality-gate false-positive boundary:

- generated React audits intentionally ignore files under `docs/**` and
  `tests/**` fixture paths, even when they contain JSX snippets, so examples
  and test fixtures do not block real generated app output.

Visual values come from the active app brand, not from custom-page constants.
Generated apps should treat `app/brand/theme_config.json` and the shell/theme
semantic token layer as the authority for colors, radius, spacing, density, and
typography. Custom React may use semantic classes such as `bg-card`,
`text-primary`, `border-border`, `text-success`, and `text-muted-foreground`;
it must not hardcode hex colors, brand color names, or local font choices.

Custom route ownership has three required pieces and they must stay in sync:

- `ui/route_manifest.json` declares the route path and component key
- `ui/pages/custom/*.jsx` provides the full-page React component
- `ui/index.js` registers the same component key with `registerComponent`

Scoped private routes may declare `meta.routeAuth`. Declarative pages put it in
`ui/pages/*.yaml`; custom routes put it in `ui/route_manifest.json`
`pages[].meta.routeAuth`. Use it when a path or query parameter identifies a
resource and access depends on app-owned membership, ownership, tenant,
workspace, or invitation state. The shell resolves placeholders such as
`$route.projectId`, `$query.team`, `$user.id`, and `$path`, calls the declared
app module action, and renders only when the response contains `allowed: true`.
This is a pre-render/deep-link gate; the target module action and all backend
module actions remain responsible for authoritative authorization.

`admin/admin_registry.yaml` is not a route registry. It declares admin page ids,
paths, scope, ordering, and labels for the admin shell. Full-page custom React
routes must use the route manifest and component registry contract above.
Module admin panels reference admin page ids from their module-owned
`contracts/admin.yaml`; they do not add route component keys to
`admin_registry.yaml`.

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
- `factory_app/workflows/_shared/generated_ui_contract.py` audits both declarative page schemas and optional `custom_route_bundle.page_files`
- the generated React portion of that audit excludes `docs/**` and `tests/**`
  fixture paths to reduce false positives in documentation and test examples
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

- canonical `ValidationRun` with the `generated_ui_browser` gate result
- structured `ValidationIssue` entries with code, severity, route, message,
  suggested fix, and repair owner
- one `RepairDecision`: `accept`, `repair`, or `block`

This keeps strictness bounded. Playwright failures should be converted into
specific canonical issues; they should not become an open-ended agent loop. The
shared acceptance controller owns the repair budget and blocks to user/operator
review when the evidence is unchanged or the budget is exhausted. The script
does not maintain a second browser-specific retry state machine.

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
- `factory_app/workflows/_shared/generated_ui_contract.py` audits generated workflow-local React with the same primitive/copy/style rules used by AppGenerator custom React
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
- framework-owned shared workflow primitives live in `chat-ui/src/core/ui/` and are mounted by `WorkflowUIRouter`
- factory-owned reusable workflow components live under `factory_app/workflows/_shared/ui/` and must be imported/re-exported by each consuming workflow's `ui/index.js`; they are not auto-registered
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
- the first deterministic smoke target for this contract is described in the
  live smoke workflows alongside `RuntimeSmoke` and `RuntimeToolCallSmoke`
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


