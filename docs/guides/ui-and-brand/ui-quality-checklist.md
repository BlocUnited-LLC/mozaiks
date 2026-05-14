# UI Quality Checklist

Use this checklist when reviewing first-party console screens and generated UI from
`AppGenerator` or `AgentGenerator`.

Surface ownership is defined in
`docs/architecture/frontend/ui-system/generated-frontend-surface-contract.md` and enforced
by `mozaiksai/core/workflow/ui_surface_taxonomy.py`. Use that taxonomy before
deciding whether a surface should be YAML, custom React, workflow UI, or
transition UI.

Canonical reusable UI primitives live in `chat-ui/src/ui/primitives` and are
exposed to page schemas through `PrimitiveRegistry.js` and
`primitive_schemas.json`. App-specific files may compose those primitives, but
must not become a hidden second primitive catalog. First-party console adapters
such as `ConsoleShared.jsx` are not primitive ownership points.
Generated custom-route React lives under `app/ui/pages/custom/*.jsx` through
the typed `custom_route_bundle.page_files` contract. App-level helper
components should be introduced only through an explicit future contract, not by
inventing ad hoc component directories.

Important distinction:

- shipped/runtime page primitives = everything the renderer can still mount
- generated-page primitives = the narrower AppGenerator-safe subset
- shipped/runtime component primitives = everything workflow/local React can still import
- generated-component primitives = the narrower AgentGenerator-safe subset

`Card`, `Stat`, and `Badge` are removed from the generated UI contract. Use
`Panel`, `SurfaceCard`, `SummaryStrip`, `Metric`, `StatusPill`,
`ResourceTable`, and task-specific primitives instead.

When changing declarative page primitives, update `PrimitiveRegistry.js`,
`PrimitiveSchemas.js`, and `PrimitiveCatalog.js`, then regenerate/check the
Python-facing catalog with `node scripts/export-primitive-schemas.js --check`.
The check fails on registry, schema, or quality-guidance drift.

When changing generated or first-party UI composition, run
`npm --prefix web_shell run test:ui-primitives`. This check keeps page YAML
primitive names aligned with the exported registry, blocks deep imports from
`chat-ui` internals, prevents factory-local primitive catalogs, and verifies the
Apps page continues to use the shared collection primitives instead of raw table
markup.

## Core Principle

One concept, one visual representation.

If the page already communicates scope, status, or purpose once, do not restate it
again in a nearby badge, eyebrow, subtitle, or nested card.

## Screen Review

- Can a user understand the page in 5 seconds?
- Is the primary action obvious?
- Does the page feel like a real product surface, not a wireframe or internal admin stub?
- Does the screen repeat the same label more than once without a clear reason?
- Are there more than 2 badges in one compact area?
- Are there nested cards inside cards?
- Is each section necessary for a real user task?
- Is the main content area visually dominant, or is it surrounded by empty containers?
- Is the list page optimized for scanning rather than explanation?
- On a collection page, are search, filters, and row actions clearer than any surrounding summaries?
- Is deeper detail hidden until the user drills in?
- Does the empty state explain what is missing and give at most one next action?
- Does the page look like a real product surface rather than a placeholder dashboard?
- Would this be acceptable in a premium SaaS product without apology?

## Anti-Patterns

Avoid these defaults unless the product requirement explicitly calls for them:

- generic KPI strips
- fake charts
- decorative activity feeds
- dashboard-style card grids
- repeated scope labels like `Workspace`, `Console`, `App Console`, or `Lifecycle`
- stacked helper subtitles that explain obvious UI
- multiple status pills for the same object
- summary panels that are larger than the data they contain
- placeholder panels added only to fill space
- sci-fi cockpit styling, heavy glow, or game-like language

## Generated Page Rules

- Prefer the minimum number of sections needed to support the user task.
- List pages should show scan-first information: name, status, updated time, primary action.
- Collection pages should usually offer search or filtering before adding extra insight panels.
- Resource/index pages should usually start with `PageHeader` + `ResourceTable`, with `SummaryStrip` only when the quick metrics materially improve scanning.
- Detail pages can carry more context, but should still favor progressive disclosure.
- Use cards only when they improve grouping. Do not wrap every row or every fact in a card.
- Use metrics only when they are genuinely useful. Do not default to KPI rows.
- Use one primary action per surface area whenever possible.
- Do not generate explanatory copy for obvious actions or page scope.

## Factory App Proof Page

Use `factory_app/app/ui/pages/notifications.yaml` as the current first-party
declarative page example. It owns a distinct task: a full notifications inbox
for alerts that do not fit in the header dropdown.

Do not add declarative pages that duplicate an existing first-class Console
surface. `/apps` is the app portfolio and app lifecycle surface; a separate app
registry page creates product confusion.

The notifications page proves the intended primitive pattern:

- one `PageHeader`
- one `ResourceTable`
- live data from the platform notifications endpoint
- shell-owned bell routing declared in `config/shell.json -> notifications.path`
- search, filters, and sorting inside the table primitive when needed
- no helper panels, no duplicate CTAs, no decorative cards

## Generator Validation

`factory_app/workflows/generated_ui_contract.py` is the shared hard gate for
generated UI. It audits both declarative page schemas and generated/custom React
so AppGenerator and AgentGenerator cannot drift into separate UI standards.

`save_app_schema` records `app_ui_quality_warnings` in workflow context for
generated page bundles, including `ui/pages/*.yaml` and optional
`custom_route_bundle.page_files`. `AppUIQualityAgent` then calls
`review_ui_quality` and sets `app_ui_quality_status`:

- `passed`: no warnings remain, so AssemblyAgent may assemble the bundle.
- `needs_revision`: AppSchemaAgent must simplify or remove the flagged UI and
  emit the full AppSchemaOutput again.
- `blocked`: warnings remain after the automated revision budget and require
  user/operator review.

This makes UI quality an AppGenerator handoff gate, not just prompt guidance:
`AppSchemaAgent -> AppUIQualityAgent -> AssemblyAgent` is the only clean page
assembly path.
`assemble_app_tasks` also enforces the gate and refuses assembly unless
`app_ui_quality_status == "passed"`.
For schema-driven app pages, assembly reads the files already persisted under
`generated_app_dir`. It should not ask the LLM to recreate `app.json` or
`ui/pages/*.yaml` from memory, and download/validation tools use the same saved
artifact directory as a fallback when no `code_files` payload exists.

Workflow-local React now follows the same deterministic pattern:

- `UIFileGenerator` emits `UIToolsFilesOutput`
- `save_workflow_ui_files_output` persists the files and computes `workflow_ui_quality_warnings`
- `WorkflowUIQualityAgent` calls `review_workflow_ui_quality`
- AG2 routes by `workflow_ui_quality_status`
- `generate_and_download` refuses bundle delivery unless `workflow_ui_quality_status == "passed"`

For a real-AG2 AppGenerator UI smoke without manual input, seed upstream context
and start at `AppSchemaAgent`:

```powershell
python scripts/run_live_mfj_smoke.py `
  --workflow AppGenerator `
  --workflows-root factory_app/workflows `
  --prompt-file factory_app/workflows/AppGenerator/smoke_prompt_ui.txt `
  --context-file factory_app/workflows/AppGenerator/smoke_context_ui.json `
  --tool-response-file factory_app/workflows/AppGenerator/smoke_responses_ui.json `
  --initial-agent AppSchemaAgent `
  --timeout-seconds 300 `
  --expect-output-contains "Support Operations" `
  --expect-output-contains "/tickets" `
  --expect-output-contains "/settings" `
  --fail-on-needs-revision
```

This uses real AG2 and the normal runtime transport, but mocks the upstream
DesignDocs/AppPlan context so the run focuses on schema generation, primitive
usage, UIQualityAgent, and artifact assembly. It does not promote generated
artifacts into an active app root.

The smoke must fail if AppSchemaAgent ignores seeded context, omits a planned
route, or leaves the UI quality gate in `needs_revision`.

For a browser-level generated UI acceptance check, run the Playwright fixture:

```powershell
npm --prefix web_shell run test:generated-ui
```

This serves a generated-app fixture through the same active app root contract
used by Vite (`PLATFORM_PATH` / `MOZAIKS_APP_WORKSPACE_PATH`) and mocks the
platform APIs for `shell-config`, `pages`, module data, and theme config. The
fixture pages are canonical `ui/pages/*.yaml` files rendered by `SchemaPage`;
the test must pass on desktop and mobile, with no `console.error`, no
`pageerror`, no horizontal overflow, and working declarative form submission.

For a generated app root produced by AppGenerator, run the generic acceptance
smoke:

```powershell
$env:MOZAIKS_GENERATED_UI_APP_ROOT="generated/apps/{app_id}/{build_id}/app"
npm --prefix web_shell run test:generated-ui:app
```

This generic smoke loads `app.json`, every `ui/pages/*.yaml` file, and
`brand/theme_config.json` from the supplied app root. It verifies that each
declarative route renders a visible main region and page heading, has no browser
errors, and does not create horizontal overflow.

For structured production-readiness output, run:

```powershell
python scripts/generated_ui_acceptance.py `
  --app-root "generated/apps/{app_id}/{build_id}/app" `
  --output generated-ui-acceptance.json
```

That script runs the generic Playwright smoke, parses the JSON reporter output,
and writes structured findings with `status`, `findings`, and
`revision_request`.

Keep this separate from `app_ui_quality_status`. The AG2 quality gate is
pre-assembly and contract-oriented. Playwright acceptance is post-assembly and
render-oriented. It is not registered as a live AppGenerator tool or handoff.

Current warning classes include:

- removed primitives (`Card`, `Stat`, `Badge`) in generated pages or generated React
- unknown declarative page primitives
- build-plan lane mismatches between `app_build_plan.pages[*].ui_surface` and the emitted page/custom-route bundle
- dashboard-style page/component naming
- repeated `SummaryStrip`, `StatusPill`, `Metric`, `Panel`, or `SurfaceCard` patterns
- nested `Panel`/`SurfaceCard` wrappers
- placeholder/internal copy such as `placeholder`, `todo`, `posture`, `handoff`, `control room`, or `kpi wall`
- custom React that imports/renders removed primitives (`Card`, `Stat`, `Badge`)
- custom React that uses brittle deep primitive imports instead of the public `@mozaiks/chat-ui/ui` entrypoint
- custom React that hardcodes font families, literal brand fonts, raw color values, or non-semantic color utilities
- custom React that uses `.js` for workflow-local generated React instead of `.jsx`

## Workflow UI Rules

- Prefer plain chat replies before generating custom React.
- Prefer inline UI before artifact-tray UI.
- Generate custom workflow UI only when the interaction is structured enough to require it.
- Keep workflow review surfaces compact: one title, one summary, one action group.
- For new workflow-local React, prefer `Panel`, `SurfaceCard`, `StatusPill`, `InlineEmptyState`, `LoadingState`, and `ErrorState`.
- Workflow-local React should not ship if it uses deep primitive imports, hardcoded brand fonts, raw color values, dashboard-style naming, or repeated primary wrapper surfaces.

## Visual Standard

Generated Mozaiks UI should feel closer to:

- Linear
- Stripe
- Vercel
- Notion
- Raycast

Generated Mozaiks UI should not feel like:

- an admin template
- a KPI wall
- a sci-fi control room
- a placeholder SaaS starter kit
