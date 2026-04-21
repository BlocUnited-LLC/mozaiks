# Next-Gen App Platform Roadmap

**Status:** Active — Source of Truth for UI System Build
**Created:** 2026-04-09
**Depends on:** UI_SYSTEM_SPEC.md, DESIGN_SYSTEM_SPEC.md, PLATFORM_FRONTEND_STRATEGY.md

> Note: the canonical builder architecture now lives in
> [agentic-app-generation-strategy.md](./agentic-app-generation-strategy.md) and
> [agentic-app-generation-checklist.md](./agentic-app-generation-checklist.md).
> This roadmap should be read as a UI-system implementation roadmap inside that
> broader app-generation strategy, not as the source of truth for overall
> decomposition.

---

## The Moat

### What Competitors Do

| Competitor | Model | Limitation |
|---|---|---|
| Lovable / Bolt / v0 | Generate raw React/HTML | App is frozen after generation. Agent cannot update live UI. Design system breaks the moment code is touched. |
| CopilotKit / AG-UI | Bolt AI onto existing apps | Developer still builds the app. AI is a sidebar or overlay, not a first-class citizen. |
| Retool / AppSmith | Drag-drop CRUD builder | No AI generation. No agent-driven reactivity. Template-locked. |
| GPT-4o Actions | Function calling | No persistent UI. No event contract. Output is text or a one-shot tool call. |

### What Mozaiks Does Differently

**Three properties together — none of our competitors have all three:**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         THE MOZAIKS MOAT                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. SCHEMA-DRIVEN GENERATION                                                 │
│     Agent outputs a structured schema (app.yaml + pages/*.yaml)              │
│     Runtime renders it using pre-built primitives                            │
│     → Design system survives generation. No frozen raw code.                 │
│                                                                              │
│  2. EVENT-REACTIVE PRIMITIVES                                                │
│     Primitives (DataTable, Form, Stat) subscribe to the agent event bus      │
│     Agent emits ui.datatable.refresh → live table reloads                   │
│     Agent emits ui.form.set_field → live form pre-fills                     │
│     → Generated apps stay connected to agent runtime after creation.         │
│                                                                              │
│  3. BIDIRECTIONAL AGENT ↔ UI CONTRACT                                       │
│     Same WebSocket bus used for chat also drives App UI                      │
│     Typed event schemas — not arbitrary callbacks                            │
│     App UI actions (button clicks, form submits) can trigger agent workflows │
│     → UI and agents are first-class peers, not bolt-ons.                     │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Competitor Matrix

| Capability | Lovable/Bolt | CopilotKit | Retool | Mozaiks |
|---|---|---|---|---|
| AI generates app UI | ✅ raw code | ❌ | ❌ | ✅ schema |
| Design system survives generation | ❌ | N/A | ✅ (locked) | ✅ |
| Agent pushes live UI updates | ❌ | partial | ❌ | ✅ typed events |
| Non-AI CRUD pages | ❌ | N/A | ✅ | ✅ page renderer |
| Bidirectional agent ↔ UI | ❌ | partial | ❌ | ✅ event contract |
| Page schema → multiple renderers | ❌ | N/A | ❌ | ✅ (web, mobile, E2B) |
| AI updates live app post-generation | ❌ | ❌ | ❌ | ✅ |

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     FULL GENERATION + RENDERING PIPELINE                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  PHASE 1: IDEATION                                                           │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  ValueEngine workflow                                                  │   │
│  │  ValueInterviewAgent → ResearchAgent → GapAnalysisAgent              │   │
│  │  Output: ConceptBlueprint (value_manifest, concept_overview,          │   │
│  │          capability_pack_hints, agentic_capabilities,                │   │
│  │          app_ui_requirements)                                        │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                              │                                               │
│                              ▼                                               │
│  PHASE 2: DESIGN DOCUMENTS                                                   │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  DesignDocs workflow                                                   │   │
│  │  DesignDocsAgent                                                      │   │
│  │  Output: frontend.md, backend.md, database.md, ui_schema.yaml        │   │
│  │          ↑ NEW: primitive-based page definitions for the app          │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                              │                                               │
│                              ▼                                               │
│  PHASE 3A: APP GENERATION (non-AI CRUD apps)                                │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  AppGenerator workflow                                                 │   │
│  │  InterviewAgent → AppPlanAgent → AppSchemaAgent → AssemblyAgent      │   │
│  │  Output: app.yaml + pages/*.yaml (primitive schemas) + modules/       │   │
│  │  NOT raw React code — declarative schemas only                        │   │
│  │  AppSchemaAgent outputs AppSchemaOutput (manifest + pages + theme)    │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  PHASE 3B: WORKFLOW GENERATION (AI-driven features)                         │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  AgentGenerator workflow                                               │   │
│  │  InterviewAgent → PatternAgent → WorkflowStrategyAgent → ...         │   │
│  │  Output: workflows/{name}/*.yaml (existing, already correct)          │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                              │                                               │
│                              ▼                                               │
│  PHASE 4: RUNTIME RENDERING                                                  │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  Page Renderer reads pages/*.yaml                                      │   │
│  │  Resolves data bindings (module:contacts:list → API call → data)      │   │
│  │  Maps primitive types → pre-built React components                    │   │
│  │  Applies theme tokens from app.yaml                                   │   │
│  │  Primitives subscribe to agent event bus                              │   │
│  │                                                                        │   │
│  │  ui.datatable.refresh → DataTable re-fetches data                    │   │
│  │  ui.form.set_field    → Form pre-fills field value                   │   │
│  │  ui.stat.update       → Stat shows new value                         │   │
│  │  ui.modal.open        → Modal appears                                 │   │
│  │  ui.navigate          → SPA route changes                             │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Workflow Alignment

### ValueEngine → adds `app_ui_requirements`

ConceptBlueprint currently outputs: value_manifest, concept_overview, capability_pack_hints, and agentic_capabilities.

**Needs to add:** `app_ui_requirements` — structured description of what pages, data entities, and UI patterns the app needs. This feeds DesignDocs and AppGenerator with the right context to generate correct primitive schemas.

```yaml
# Addition to ConceptBlueprint structured output
app_ui_requirements:
  page_types:          # e.g. ["dashboard", "list", "detail", "form"]
  primary_entities:    # e.g. ["Contact", "Deal", "Task"]
  key_actions:         # e.g. ["create_contact", "close_deal", "assign_task"]
  data_density:        # "low" | "medium" | "high" (drives layout choices)
  realtime_needed:     # bool (drives whether primitives need live subscriptions)
```

### DesignDocs → adds `ui_schema.yaml` as 4th document

Currently outputs: frontend.md, backend.md, database.md.

**Needs to add:** `ui_schema.yaml` — page definitions using Mozaiks primitives. DesignDocsAgent reads the design documents it just generated and the app_ui_requirements from ConceptBlueprint, then outputs valid page schemas.

```yaml
# Example ui_schema.yaml output from DesignDocsAgent
pages:
  - name: contacts_page
    title: Contacts
    nav: { label: Contacts, icon: users, order: 10 }
    layout: sidebar
    data:
      contacts: { source: "module:contacts:list" }
      stats: { source: "module:contacts:get_stats" }
    content:
      - type: Grid
        columns: 4
        children:
          - type: Stat
            label: Total Contacts
            value_source: "data.stats.total"
      - type: DataTable
        data_source: "data.contacts"
        columns:
          - { key: name, label: Name, type: text, sortable: true }
          - { key: status, label: Status, type: badge }
        actions:
          - { id: create, label: New Contact, trigger: { type: modal, target: create_modal } }
          - { id: analyze, label: Analyze with AI, trigger: { type: workflow, target: ContactAnalyzer }, requires_selection: true }
```

**Key constraint for DesignDocsAgent:** Only use primitives from the controlled vocabulary. No raw JSX, no Tailwind classes, no component imports.

### AppGenerator → outputs schemas, not raw code

Currently: gathers CRUD/page/auth/integration requirements, then generates… presumably raw app code.

**Needs to become:** outputs `app.yaml` + `pages/*.yaml` using primitive schemas. The E2B sandbox then validates the schema renders correctly without needing npm install.

Agent roster change needed:
- `InterviewAgent` — stays: gathers requirements
- Add `SchemaAgent` — translates requirements into `app.yaml` (theme, navigation, module declarations)
- Add `PageDefinitionAgent` — translates page requirements into `pages/*.yaml` primitive schemas
- `ModuleAgent` — stays: generates Python module actions (CRUD handlers)

### AgentGenerator → no schema changes needed

Already outputs the correct declarative YAML format (orchestrator.yaml, agents.yaml, handoffs.yaml, etc.). The only future addition is generating a companion `pages/*.yaml` if the workflow has an associated dashboard/status page.

---

## Master Build Checklist

### Layer 0 — Base Foundation (blocks everything else)

- [ ] Install shadcn/ui base components into `chat-ui/src/ui/base/` — the internal layer AI never sees
- [ ] Install and configure Tailwind CSS in `chat-ui/`
- [ ] Create CSS token system (`chat-ui/src/ui/theme/tokens.js`) — maps theme config to CSS variables
- [ ] Create `mozaiks-platform/brand/` folder with `theme_config.json` and base CSS tokens
- [ ] Wire `mozaiks-platform/brand/` into the existing `themeProvider.js` pipeline
- [ ] Create `chat-ui/src/ui/primitives/` directory — home for all Mozaiks primitives

### Layer 1 — Core Primitives (the moat, build in this order)

- [ ] **Card** — container with title, subtitle, actions, variant (default/elevated/outlined)
- [ ] **Grid** — responsive column layout, gap control
- [ ] **Section** — titled content block, collapsible option
- [ ] **Stat** — label + value + trend + icon, subscribes to `ui.stat.update` event
- [ ] **DataTable** — columns, pagination, search, row selection, subscribes to `ui.datatable.refresh`
- [ ] **Button** — variant (primary/secondary/danger/ghost), size, icon, trigger types
- [ ] **Form** — fields array, layout, submit_action binding, subscribes to `ui.form.set_field`
- [ ] **Modal** — declarative open/close, content slot, subscribes to `ui.modal.open` / `ui.modal.close`
- [ ] **Alert** — type (info/success/warning/error), dismissible, subscribes to `ui.alert.show`
- [ ] **Badge** — status colors, size variants
- [ ] **Skeleton** — loading placeholder matching shape of target primitive
- [ ] **Empty** — empty state with icon, title, description, action button

### Layer 2 — Agent-UI Event Contract (the real differentiator)

- [ ] Define typed event schema in `chat-ui/src/events/ui-events.js`:
  ```
  ui.datatable.refresh   { component_id, filters? }
  ui.datatable.set_data  { component_id, rows }
  ui.form.set_field      { component_id, field, value }
  ui.form.submit         { component_id }
  ui.form.reset          { component_id }
  ui.stat.update         { component_id, value, trend? }
  ui.modal.open          { modal_id, props? }
  ui.modal.close         { modal_id }
  ui.alert.show          { message, type, duration? }
  ui.navigate            { path }
  ui.toast               { message, type }
  ```
- [ ] Wire event listener into each primitive (subscribe on mount, unsubscribe on unmount)
- [ ] Add `send_ui_event(event_type, payload)` helper to the Python transport layer
- [ ] Document the contract in `docs/architecture/foundations/agent-ui-event-contract.md`

### Layer 3 — Page Renderer

- [ ] Create `chat-ui/src/ui/page-renderer/` module
- [ ] `SchemaValidator` — validates page YAML/JSON against primitive registry
- [ ] `BindingResolver` — resolves `module:name:action` → API call → data injection
- [ ] `ComponentCompositor` — maps `type: DataTable` → `<MozaiksDataTable />` with resolved props
- [ ] `PageRenderer` React component — renders a full `PageDefinition` schema
- [ ] Register `PageRenderer` in `componentRegistry` so navigation can mount it
- [ ] Add `/api/pages/{name}` endpoint in `shared_app.py` — serves page schemas from `platform/pages/`

### Layer 4 — Workflow Updates

**ValueEngine**
- [x] Add `app_ui_requirements` field to `ConceptBlueprint` structured output model (`structured_outputs.yaml`)
- [x] Update `GapAnalysisAgent` prompt to populate `app_ui_requirements`
- [x] `app_ui_requirements` flows downstream as natural-language UI capability statements (e.g. "needs a sortable DataTable for managing users")

**DesignDocs**
- [x] Add `ui_schema` as a 4th document kind in `save_design_doc` tool (`DesignDocKinds.UI_SCHEMA`)
- [x] Add UI schema generation step to `DesignDocsAgent` process (reads frontend.md + concept_overview → outputs YAML page primitive schemas)
- [x] Primitive vocabulary reference added to `DesignDocsAgent` prompt
- [x] `ui_schema_content` in context variables; `max_consecutive_auto_reply` raised to 12

**AppGenerator**
- [x] Add `AppSchemaAgent` to `agents.yaml` — single agent that translates plan + concept + ui_schema into typed `AppSchemaOutput` (manifest + pages + theme_config_patch + shell_config + asset_manifest)
  - Note: implemented as one agent (`AppSchemaAgent`), not two separate agents as originally planned
- [x] Add `AppPageSection`, `AppPageSchema`, `AppNavItem`, `AppManifest`, `AppSchemaOutput` models to `structured_outputs.yaml`
- [x] Add `theme_preferences` (raw text) to `AppBuildPlan`; `AppSchemaAgent` infers `theme_config_patch` directly — no intermediate enum
- [x] Add `ui_layout` + `sections_hint` to `AppBuildPage` for downstream schema hints
- [x] Add `save_app_schema` auto-invoke tool to `tools.yaml` + `tools/save_app_schema.py`
- [x] Update `handoffs.yaml` — `AppPlanAgent → AppSchemaAgent → AssemblyAgent → DownloadAgent`
- [x] Update `context_variables.yaml` — `app_manifest`, `app_pages`, `app_theme_config_patch`, `app_shell_config`, `app_asset_manifest`, `app_schema_ready`
- [x] AssemblyAgent reads `app_manifest`/`app_pages` from context for the schema bundle and may still fan-in backend task outputs, but AppGenerator no longer carries a raw frontend page/component path

**AgentGenerator**
- [ ] Add optional `DashboardPageAgent` step for workflows that need a status/progress page
- [ ] Wire `DashboardPageAgent` output → `pages/{WorkflowName}Dashboard.yaml`
- [ ] Add `WorkflowGateConfig` structured output model for multi-path workflow entry
- [ ] Add `save_gate_config` tool — appends gate to `extension_registry.json`
- [ ] Teach `ToolPlanningAgent` semantic rules for when to generate a gate vs use handoffs

### Layer 5 — mozaiks-platform Migration

- [ ] Create `mozaiks-platform/brand/theme_config.json` with platform theme tokens
- [ ] Create `mozaiks-platform/app/pages/dashboard.yaml` using DataTable + Card + Stat primitives (replaces `Dashboard.jsx` hand-written code)
- [ ] Keep `CreateApp.jsx` as-is — it's a workflow launcher, not a CRUD page, intentionally imperative
- [ ] Add `AppCard` usage as a `Card` primitive instance in `dashboard.yaml`
- [ ] Verify `list_apps` module endpoint wired and returning data

### Layer 5b — Workflow Routing Transitions

**See full spec:** [workflow-routing-gates.md](./workflow-routing-gates.md)

**Schema & data model (built)**
- [x] `TransitionOption`, `ConditionRoute`, `WorkflowTransition` Pydantic models in `schema.py`
- [x] `TransitionUIBinding` keeps transition renderer selection in `ui.component`; route options stay semantic
- [x] `GlobalPackGraph` v3 with `workflows[]`, `transitions[]`, and workflow sequence metadata
- [x] Transition helpers in `config.py`: `get_transition`, `list_transitions`
- [x] `extension_registry.json` v3 — `coding_journey_selector` + `app_type_selector` transitions
- [x] `LauncherScreen.jsx` + `LauncherCard.jsx` registered as core shell components

**Shell wire-up**
- [x] `/api/transitions/{id}` endpoint in `shared_app.py`
- [x] `/api/transitions/resolve` endpoint in `shared_app.py`
- [x] Shell router detects `transition:` nav entries and renders `TransitionScreen`
- [x] Transition chaining: `route_to` transition id -> render next transition
- [x] Workflow target resolution creates chat session with validated context variables

**Additional transition types**
- [x] `ConfirmScreen.jsx` fallback for `transition_type: confirm`
- [x] `condition` transition: auto-route based on context variable value

### Layer 6 — E2B Integration

- [ ] Build E2B template with all base components + all primitives pre-installed
- [ ] Add `AppGenerator` E2B validation step — renders generated page schemas in sandbox before delivering to user
- [ ] Add `mozaiks e2b:preview` CLI command

### Layer 7 — CLI

- [ ] `mozaiks add page <name>` — scaffolds `pages/<name>.yaml` with starter schema
- [ ] `mozaiks validate` — validates `app.yaml` + all `pages/*.yaml` against primitive schemas
- [ ] `mozaiks build` — packages `app.yaml` + `pages/` + `modules/` → deployable bundle
- [ ] `mozaiks doctor` — checks Python/Node versions, DB, primitives installed

---

## Build Order (Sequenced for Fastest Value Delivery)

```
Week 1 — Foundation + 3 primitives (unblocks testing)
  Layer 0 complete
  Card, Grid, Stat primitives
  mozaiks-platform/brand/ wired

Week 2 — Core table + form primitives (unblocks AppGenerator output)
  DataTable primitive with ui.datatable.refresh
  Form primitive with ui.form.set_field
  Button, Badge, Skeleton, Empty

Week 3 — Event contract + page renderer (the moat becomes real)
  Full agent-UI event schema
  All primitives wired to event bus
  Page renderer v1 (schema → React tree)
  BindingResolver (module: bindings → API calls)

Week 4 — Workflow updates (closes the loop end to end)
  ValueEngine: app_ui_requirements
  DesignDocs: ui_schema 4th document
  AppGenerator: schema output (not raw code)
  mozaiks-platform dashboard.yaml migration

Week 5 — E2B + polish
  E2B template prepared
  AppGenerator E2B validation step
  CLI add/validate/build commands
```

---

## Key Constraints (Non-Negotiable)

1. **AI generates schemas, never raw React/CSS** — enforced by structured output models
2. **Primitives are the only UI vocabulary agents speak** — DesignDocsAgent and PageDefinitionAgent prompts include the controlled list
3. **Event contract is typed** — no arbitrary event names, all events validated against schema
4. **Base components are internal** — never referenced in any agent prompt, workflow YAML, or page schema
5. **Fonts and themes are tokens** — agents select from predefined enums, never write CSS
6. **Page schemas are validated before rendering** — SchemaValidator runs before ComponentCompositor

---

## Open Questions (Decide Before Building)

1. **Page renderer location**: `chat-ui/src/ui/page-renderer/` vs a separate `mozaiks-ui` package?
   - Recommendation: start in `chat-ui/`, extract to package when OSS CLI needs it

2. **Data binding protocol**: does `module:contacts:list` call the Python module directly via API, or go through a GraphQL/REST adapter?
   - Recommendation: HTTP to `/api/modules/{name}/{action}` — already the pattern in `shared_app.py`

3. **AppGenerator output format**: page schemas as `.yaml` files or inline in `app.yaml`?
   - Recommendation: separate `pages/*.yaml` files per spec — easier for agents to output incrementally

4. **Event bus transport**: do App UI events share the workflow WebSocket or get a separate connection?
   - Recommendation: share the workflow WebSocket — same bus, different event namespace (`ui.*` vs workflow events)
