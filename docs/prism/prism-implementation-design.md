# Prism Implementation Design

## Domain-Agnostic Build Factory — Stage-by-Stage Technical Specification

**Status:** Pre-production design. This document drives the refactor.  
**Scope:** Full factory-layer refactor. Clean-cut contract changes throughout.  
**Principle:** Determinism first, intelligence second. Modular by contract, not by convention.

---

## How to Read This Document

This document is an implementation specification, not aspirational prose. Each
stage contains:

1. **What to analyze** — the exact files to read and questions to answer before
   touching anything
2. **What to change** — precise refactor instructions
3. **What to remove** — what gets deleted, not shimmed or aliased
4. **What the result looks like** — the canonical post-refactor shape
5. **Validation gate** — how to confirm the stage is complete before moving on

Work in order. Each stage produces artifacts the next stage depends on.

---

## Core Design Principles

These govern every decision in this refactor. If a proposed change violates one,
stop and redesign.

**P1: Contracts before code.**
No agent writes a file before the contract for that file has been validated. The
contract layer is the source of truth. The generation layer is execution against
it.

**P2: Domain knowledge is data, not code.**
Domain-specific behavior (file shapes, language idioms, primitive vocabularies,
framework patterns) lives in build context catalogs — YAML data files. It does not
live in agent code, middleware Python, or hardcoded structured output enums. If a
domain change requires editing Python, the design is wrong.

**P3: Harness stability.**
The execution harness (task batch worker, assembly, packaging, control plane
routing, session management) must not change per domain. If a new domain requires
harness changes, the harness contract is underspecified. Go back and fix the
contract, not the harness.

**P4: Structured outputs define the factory floor.**
Every agent that produces a typed artifact must have a Pydantic model that
validates it. No prose-only outputs in the build pipeline. No "the LLM knows what
to write." If the output cannot be validated, it is not a build artifact — it is a
draft.

**P5: Contract completeness before generation.**
The wiring between every declared surface, action, view, event, and integration
is validated as a structured contract before any code is written. A disconnected
declaration is a build error caught at the contract layer, not in generated code.

---

## Current Architecture Map

Before any changes, understand what exists.

### Factory layer (what we are changing)

```
factory_app/
├── workflows/
│   ├── ValueEngine/          → concept + value decomposition (largely domain-agnostic)
│   ├── ThemeCapture/         → visual identity (web-tinted, can be generalized)
│   ├── DesignDocs/           → design artifacts (PARTIALLY web-specific)
│   ├── AgentGenerator/       → workflow bundle generation (largely domain-agnostic)
│   ├── AppGenerator/         → app bundle generation (HEAVILY web-specific)
│   └── extended_orchestration/
│       └── extension_registry.json  → sequence registry (domain-agnostic structure)
├── build_context/
│   ├── AppGenerator/         → web app catalogs (web-specific content, generic structure)
│   ├── AgentGenerator/       → AG2 patterns (domain-agnostic)
│   └── mozaikspay/           → capability pack example (web-specific content)
└── control_plane/
    └── config/               → harness routing config (web-tinted artifact kinds)
```

### Runtime layer (mostly not changing)

```
mozaiksai/
├── core/workflow/context/    → context variable loading (domain-agnostic, keep)
├── core/session/build_context.py  → build context loader (domain-agnostic, keep)
├── core/workflow/            → execution engine (domain-agnostic, keep)
└── hosts/                   → platform/studio/runtime hosts (not changing)
```

### The seam

```
AppPlanAgent
  → produces AppBuildPlan (web-specific structured output)
    → produces AppBuildTask[] (web-specific task model)
      → task_batches.yaml (harness reads task_model: AppBuildTask ← hardcoded)
        → routes to initial_agent per task (already generic)
          → agents write files to owned_paths (already generic)
```

The seam is `AppBuildPlan → AppBuildTask`. Everything upstream is domain-knowledge.
Everything downstream of `initial_agent` routing is already domain-agnostic.

---

## Stage 0: Baseline Analysis

**Do this before writing a single line of new code.**

### 0.1 Map every hardcoded domain assumption

Read each file below. For each file, annotate every field, enum value, agent
prompt section, or catalog entry that assumes web app specifically. Tag each as:

- `[WEB]` — tied to web: pages, routes, Python, React, REST, HTTP, module.yaml
- `[ABSTRACT]` — already domain-agnostic: surface_kind, events, entities, contracts
- `[INJECTABLE]` — web-specific today but extractable to build context catalog

**Files to analyze:**

```
factory_app/workflows/DesignDocs/structured_outputs.yaml
factory_app/workflows/DesignDocs/agents.yaml
factory_app/workflows/AppGenerator/structured_outputs.yaml
factory_app/workflows/AppGenerator/agents.yaml
factory_app/workflows/AppGenerator/middleware.yaml
factory_app/workflows/AppGenerator/context_variables.yaml
factory_app/workflows/AppGenerator/transition_graph.yaml
factory_app/workflows/AppGenerator/extended_orchestration/task_batches.yaml
factory_app/build_context/AppGenerator/capability_directory.yaml
factory_app/build_context/AppGenerator/module_archetypes.yaml
factory_app/build_context/AppGenerator/file_contracts.yaml
factory_app/build_context/AppGenerator/shell_presets.yaml
factory_app/control_plane/config/control_plane.yaml
```

### 0.2 Current domain-assumption inventory (findings from analysis)

The following is the result of the baseline analysis. This is the map the refactor
works from.

**`[ABSTRACT]` — Keep as-is or rename only:**

- `DesignSurface` model: `surface_id`, `surface_kind`, `owner`, `primary_entities`,
  `owned_mutations`, `events_emitted`, `workflow_triggers`, `integrations` — these
  are genuinely domain-neutral
- `DataContractBundle` + all child models — collection ownership, fields, indexes,
  lifecycle — domain-neutral
- `transition_graph.yaml` — pure state machine, zero domain vocabulary
- `extension_registry.json` — sequence registry structure, domain-agnostic
- `AgentGenerator` workflow — generates workflow YAML bundles, already domain-neutral
- `ValueEngine` workflow — concept decomposition, domain-neutral
- Context variable loading, build context loader, harness execution loop

**`[WEB]` — Replace or remove entirely:**

- `ExperienceSpec`, `UIPage`, `UIPageSection` — web page + route + UI primitive
  model. Cannot be generalized by injection. Must be redesigned.
- `UIPageSection.primitive` literal enum — `DataTable | Form | Grid | Button | ...`
  — web React component names baked as enum values
- `AppBuildTask.task_type` literal enum — `module_contract | business_services |
  page_bundle | ...` — web app file structure baked as enum values
- `AppBuildTask.execution_target` — hardcoded Mozaiks agent names
- `AppBuildPage.route` — HTTP URL path
- `AppBuildPage.ui_layout` — `grid | sidebar | full-width | split` — web layout modes
- `AppBuildPage.page_type_hint` — `record_list | record_detail | analytics_dashboard
  | ...` — web CRUD page archetypes
- `file_contracts.yaml` — `module.yaml`, `handler.py`, `service.py`, `repo.py`,
  `app/ui/pages/*.yaml`, `config/shell.json` — web file structure encoded as
  canonical contracts
- `module_archetypes.yaml` — `backend/handler.py`, `backend/service.py`,
  `backend/repo.py` — Python backend stack encoded as archetype templates
- `shell_presets.yaml` — web shell chrome, navigation slots, mobile layout modes
- `capability_directory.yaml` — product domains (billing, subscriptions, usage,
  messaging) encoded as web SaaS capability vocabulary
- `DesignDocsBundle.frontend_markdown` / `backend_markdown` / `database_markdown`
  — the document partition (frontend/backend/database) is a web stack partition

**`[INJECTABLE]` — Keep the structure, replace the content with build context:**

- `AppPlanAgent` prompt sections: the planning logic is sound; the domain vocabulary
  (pages, routes, modules, Python) is injectable
- `AppBuildTask.domain_context` (new field, replaces `task_type`): pass-through dict
  the domain's execution agents read; harness ignores it
- Module archetype patterns: the archetype concept (standard, messaging, admin) is
  reusable; the file shapes are injectable
- Capability pack domains: the pack selection and routing logic is reusable; the
  domain vocabulary (billing, marketplace) is injectable
- Quality gate hooks: the gate concept is reusable; the web-specific checks
  (module.yaml wiring, primitive validation) are injectable per domain pack

---

## Stage 1: Define the Stable Harness Contract

**Goal:** Formalize what the harness needs from any build task, regardless of
domain. This is the ABI between the planning layer and the execution layer.

**Duration estimate:** 1–2 days.

### 1.1 Define `BuildTaskBase`

`BuildTaskBase` is the stable harness-visible contract. Every domain's task model
extends this. The harness only reads these fields.

```yaml
# New: shared contract definition (docs/contracts/build-task-base.yaml)
BuildTaskBase:
  task_id: str              # Stable unique id for this build unit
  initial_agent: str        # Agent that executes this task
  initial_message: str      # Seed prompt for the executing agent
  owned_paths: str[]        # Files this task exclusively owns
  depends_on: str[]         # task_ids that must complete first
  acceptance_criteria: str[] # Completion checks the agent must satisfy
  context_variables: ContextVariable[]  # Domain context seeded into the agent
  integration_needs: IntegrationNeed[]  # Third-party deps this task requires
  domain_context: dict | null  # Opaque pass-through; harness ignores; agent reads
```

`domain_context` is the key addition. It carries everything domain-specific
(`task_type`, `capability_pack_id`, `surface_kind`, `execution_target`, and any
new domain fields) as a typed dict the harness passes through to the executing
agent without interpreting it.

### 1.2 Refactor `AppBuildTask` to extend `BuildTaskBase`

**File:** `factory_app/workflows/AppGenerator/structured_outputs.yaml`

Move `task_type`, `capability_pack_id`, `surface_id`, `surface_kind`,
`execution_target` into `domain_context`. They remain available to AppGenerator's
execution agents unchanged. The harness sees only `BuildTaskBase` fields.

No behavior change. This is pure extraction.

### 1.3 Make `task_model` injectable in `task_batches.yaml`

**File:** `factory_app/workflows/AppGenerator/extended_orchestration/task_batches.yaml`

Change `task_model: AppBuildTask` to `task_model: ${build_task_model}`.

Add `build_task_model` as a `state` context variable to `AppGenerator/context_variables.yaml`
with default `AppBuildTask`. Webapp dev pack projects `AppBuildTask`. A future
domain pack projects its own model name.

**Runtime change:** The task batch loader resolves `${build_task_model}` from
context at launch time. This is a small addition to
`mozaiksai/core/workflow/` task batch loader.

### 1.4 Make validation hooks pack-declared

**Files:** `factory_app/workflows/AppGenerator/middleware.yaml` and
`factory_app/build_context/AppGenerator/context.yaml`

Today `middleware.yaml` always runs web-specific quality gates:
- `hook_module_contract_quality_gate.py`
- `hook_module_runtime_quality_gate.py`
- `hook_primitive_catalog.py`

Move these to be declared by the webapp dev pack in `context.yaml` under
`validation_hooks[]`. The middleware loader runs hooks declared by the active dev
pack, not hooks hardcoded in `middleware.yaml`.

`middleware.yaml` keeps only domain-agnostic hooks (context graph, build context
projections).

### 1.5 Validation gate for Stage 1

- All existing AppGenerator tests pass with zero behavior change
- `task_batches.yaml` resolves `AppBuildTask` from the default context variable
- Webapp dev pack's `context.yaml` explicitly declares its validation hooks
- `BuildTaskBase` is documented as the formal harness contract

---

## Stage 2: Redesign the Design Layer

**Goal:** Replace web-specific design artifacts (`ExperienceSpec`, `UIPage`,
`UIPageSection`) with domain-injectable equivalents. The design layer produces
a `DesignBundle` that works for any domain.

**Duration estimate:** 1–2 weeks. This is the most intellectually intensive stage.

### 2.1 Analyze what `DesignDocs` actually needs to produce

Read `factory_app/workflows/DesignDocs/structured_outputs.yaml` and
`factory_app/workflows/DesignDocs/agents.yaml` with this question:

> What does `AppGenerator` consume from `DesignDocs`, and can the field be expressed
> without web-specific vocabulary?

Results from analysis:

| DesignDocs output | Consumed by | Web-specific? | Redesign |
|---|---|---|---|
| `surface_map` | AppPlanAgent | No — already abstract | Keep as-is |
| `data_contract` | AppPlanAgent, DatabaseAgent | No — abstract | Keep as-is |
| `experience_spec.pages[]` with `UIPage` | AppPlanAgent, AppSchemaAgent | Yes — routes, layouts, primitives | Replace with `ViewSpec` |
| `experience_spec.navigation_model` | AppSchemaAgent | Partially — web nav metaphor | Generalize to `navigation_intent` |
| `frontend_markdown` | AppPlanAgent context | Partially — frontend = web | Rename to `presentation_markdown` |
| `backend_markdown` | Multiple agents | Partially — backend = Python | Rename to `logic_markdown` |
| `database_markdown` | DatabaseAgent | No — abstract | Keep, rename `persistence_markdown` |

### 2.2 Redesign `ExperienceSpec` → `ViewSpec`

**Remove:** `ExperienceSpec`, `UIPage`, `UIPageSection` with their literal enums.

**Replace with:**

```yaml
ViewElement:
  type: model
  fields:
    element_id: str
    element_type: str          # domain_injectable — valid values from dev pack catalog
    intent: str
    config_hint: dict | null   # domain_injectable — valid keys from dev pack catalog

ViewSurface:
  type: model
  fields:
    surface_id: str
    address: str               # web: route path | mobile: screen name | game: scene path
    address_scheme: str        # domain_injectable — "http_route" | "screen" | "scene"
    layout_hint: str | null    # domain_injectable — valid values from dev pack catalog
    intent: str
    elements: ViewElement[]

ViewSpec:
  type: model
  fields:
    navigation_intent: str     # How primary destinations are navigated (plain language)
    brand_direction: str       # Visual/experience posture
    views: ViewSurface[]       # Ordered list of persistent views
```

`element_type`, `address_scheme`, and `layout_hint` are `str` fields. Their
valid values are not Pydantic literals. They are injected at agent initialization
time from the active dev pack's `ui_primitives.yaml` catalog via dynamic Pydantic
`Field(description=...)` construction.

### 2.3 Dynamic Pydantic model construction

This is the runtime mechanism that makes `element_type` domain-aware without
baking values into `structured_outputs.yaml`.

**How it works:**

1. When the workflow loader initializes `DesignDocsAgent`, it checks for
   `domain_injectable: true` fields in its structured output model definition.
2. For each such field, it reads the active dev pack catalog key declared in
   `domain_key`.
3. It builds the Pydantic model dynamically using `pydantic.create_model()` with
   `Field(description=f"Valid values: {catalog_values}")`.
4. This model is set as `response_format` on the agent.

In `structured_outputs.yaml`, injectable fields are declared:

```yaml
ViewElement:
  type: model
  fields:
    element_type:
      type: str
      domain_injectable: true
      domain_key: view_element_types
      description: "Element type for this domain view"
    layout_hint:
      type: str
      domain_injectable: true
      domain_key: view_layout_types
      description: "Layout mode for this view surface"
```

In the webapp dev pack catalog (`build_context/webapp_builder/view_primitives.yaml`):

```yaml
view_element_types: "DataTable | Form | Grid | Button | Modal | Alert | Skeleton | Empty | PageHeader | ResourceTable | SummaryStrip | StatusPill | Metric | Timeline | CodeBlock | ProgressTracker | AlertBanner | ActionButton | FileList"
view_layout_types: "grid | sidebar | full-width | split"
```

In a future game dev pack catalog (`build_context/game_builder/view_primitives.yaml`):

```yaml
view_element_types: "Sprite | RigidBody | Camera | AudioSource | ParticleSystem | TileMap | AnimationPlayer | Control | Label | Button"
view_layout_types: "scene | canvas | viewport | overlay"
```

Same model. Different domain. Deterministic validation by the Field description
rather than a closed enum.

**Runtime implementation location:** `mozaiksai/core/workflow/structured_outputs.py`
(new function `build_domain_injectable_model(model_def, build_context)`).

### 2.4 Update `DesignDocsAgent` prompt to be domain-aware

**File:** `factory_app/workflows/DesignDocs/agents.yaml`

Replace every web-specific term in the agent prompt with domain-neutral equivalents
injected via middleware. The agent prompt template uses markers that the dev pack
catalog fills:

Remove: "pages", "routes", "layouts", "frontend/backend/database split"
Replace with: "views", "addresses", "presentation surfaces", "logic/persistence/presentation"

The dev pack provides a `design_docs_domain_guidance.yaml` catalog asset with:
- what "views" means in the domain
- what valid "addresses" look like
- what the logic/persistence/presentation split means for the domain
- file shape examples for the domain

The `DesignDocsAgent` reads this as a prompt injection via the existing middleware
system.

### 2.5 Rename `DesignDocsBundle` document kinds

Current: `frontend_markdown`, `backend_markdown`, `database_markdown`, `ui_schema`
New: `presentation_markdown`, `logic_markdown`, `persistence_markdown`, `view_spec`

These new kinds are registered in `extension_registry.json` under
`artifact_dependency_graph`. Old kinds are removed — no aliases, no compatibility
shims.

Update context variable declarations across AppGenerator and AgentGenerator to
reference new field names.

### 2.6 Validation gate for Stage 2

- `DesignDocsAgent` runs against a webapp concept and produces `ViewSpec` with
  `ViewSurface[]` containing web-appropriate `element_type` values
- `ViewSpec.views[].elements[].element_type` values match webapp dev pack catalog
- `DataContractBundle` and `DesignSurfaceMap` are unchanged and passing
- `AppPlanAgent` consumes `ViewSpec` instead of `ExperienceSpec` and produces
  equivalent planning output
- All existing integration tests pass

---

## Stage 3: Redesign the Planning Layer

**Goal:** Make the planning agent domain-aware without domain-specific structured
output enums. Replace `AppBuildPlan` with `BuildPlan` — a domain-injectable
planning contract.

**Duration estimate:** 1–2 weeks.

### 3.1 Analyze `AppBuildPlan`

Read `factory_app/workflows/AppGenerator/structured_outputs.yaml` models:
`AppBuildPlan`, `AppBuildPage`, `AppBuildCapabilityPack`, `AppBuildModule`,
`AppBuildEntity`, `AppBuildIntegration`, `AppEventFlow`.

Apply the same tagging from Stage 0: `[ABSTRACT]`, `[WEB]`, `[INJECTABLE]`.

Results:

| Model / Field | Tag | Redesign |
|---|---|---|
| `AppBuildEntity` (name, operations, notes) | `[ABSTRACT]` | Keep, rename `BuildEntity` |
| `AppBuildIntegration` (name, kind, service, purpose) | `[ABSTRACT]` | Keep, rename `BuildIntegration` |
| `AppEventFlow` (event_type, producer, subscriber_intents) | `[ABSTRACT]` | Keep, rename `BuildEventFlow` |
| `AppBuildPage` (name, route, ui_layout, page_type_hint, sections_hint) | `[WEB]` | Replace with `BuildView` |
| `AppBuildModule` (module_id, capability_pack_id, implementation_mode) | `[INJECTABLE]` | Rename `BuildSurface` |
| `AppBuildCapabilityPack.pack_type` literal enum | `[INJECTABLE]` | Make `domain_injectable` |
| `AppBuildTask.task_type` literal enum | `[WEB]` | Move to `domain_context` |

### 3.2 Define `BuildPlan` — the domain-agnostic planning contract

```yaml
BuildPlan:
  type: model
  fields:
    app_id: str | null
    title: str
    domain: str                    # Injected: "webapp" | "mobile" | "game"
    concept_summary: str
    surfaces: BuildSurface[]       # Replaces capability_packs[]
    views: BuildView[]             # Replaces pages[]
    entities: BuildEntity[]
    integrations: BuildIntegration[]
    event_flows: BuildEventFlow[]
    build_tasks: BuildTaskBase[]   # Planning agent emits harness-ready tasks
    data_contract: DataContractBundle
    has_agent_backend: bool
    context_variables: AppContextVariable[]

BuildSurface:
  type: model
  fields:
    surface_id: str
    surface_kind: str              # module | workflow | control_plane | external_integration | ui_only
    label: str
    summary: str
    implementation_mode: str       # domain_injectable — valid modes from dev pack
    primary_entities: str[]
    primary_views: str[]           # Replaces primary_pages
    operations: str[]
    required_integrations: BuildRequiredIntegration[]
    capability_source: str         # generated | hosted_pack | framework | external_adapter

BuildRequiredIntegration:
  type: model
  fields:
    service: str                   # App-scoped connector service id
    provider: str | null
    display_name: str | null
    kind: str                      # api_key | oauth | webhook | hosted_pack | internal_service
    purpose: str
    required_at: str               # build_time | validation_time | runtime | deployment | optional
    required_fields: IntegrationRequiredField[]

IntegrationRequiredField:
  type: model
  fields:
    name: str
    label: str
    type: str                      # secret | text | url | select | number | json
    required: bool
    frontend_safe: bool            # false for secrets and write-only fields

BuildView:
  type: model
  fields:
    name: str
    address: str                   # domain_injectable format (route, screen name, scene path)
    intent: str
    surface_id: str                # Which BuildSurface owns this view
    design_intent: str | null
    layout_hint: str | null        # domain_injectable
    view_type_hint: str | null     # domain_injectable — record_list/scene/screen/etc.
    elements_hint: ViewElement[]   # From ViewSpec pass-through
```

`implementation_mode`, `layout_hint`, `view_type_hint`, and `address` format
guidance are all injected from the dev pack catalog via `domain_injectable` field
mechanism from Stage 2.

### 3.3 Update `AppPlanAgent` prompt to consume `BuildPlan`

The `AppPlanAgent` planning prompt is long and web-vocabulary-heavy. Refactor it
in this order:

1. Replace all instances of "page" with "view"
2. Replace "route" with "address"
3. Replace "module" with "surface" or "logic surface" depending on context
4. Replace "Python handler/service/repo" with domain-injected file shape guidance
5. Replace `page_type_hint` vocabulary with domain-injected view type vocabulary
6. Move all capability pack domain vocabulary (billing, subscriptions, messaging)
   into the webapp dev pack's `capability_directory.yaml` catalog

The `AppPlanAgent` prompt template retains its planning logic (capability
decomposition, surface realization, event wiring, integration discovery) but
sources its vocabulary from build context injection rather than embedded strings.

### 3.4 Validation gate for Stage 3

- `AppPlanAgent` running under webapp dev pack produces `BuildPlan` equivalent in
  content to the previous `AppBuildPlan`
- `BuildPlan.build_tasks[]` items satisfy `BuildTaskBase` contract
- `BuildPlan.views[]` items use webapp-appropriate `layout_hint` and `view_type_hint`
  values from the webapp dev pack catalog
- Planning round-trip test: concept in → `BuildPlan` out → `AppBuildTask[]` out →
  harness executes → files written to `owned_paths`

---

## Stage 4: Redesign the Execution Layer

**Goal:** Consolidate execution agents around `surface_kind` rather than
`task_type`. Four domain-injectable agents replace the current five web-specific
agents.

**Duration estimate:** 2–3 weeks. Highest content volume.

### 4.1 Analyze current execution agents

Read `factory_app/workflows/AppGenerator/agents.yaml` for:
- `ServiceAgent` — writes Python handler/service/repo/policy files
- `ModelAgent` — writes Python schemas.py
- `DatabaseAgent` — writes data model files
- `FrontendStubAgent` — writes React JSX stubs
- `ControllerAgent` — writes Python controller/API files

For each agent, answer:

1. What does the agent's prompt know about the domain (Python, React, module.yaml)?
2. What is injected by middleware (file contracts, domain catalog)?
3. What does it read from `current_build_task` / `domain_context`?
4. What does it write to `owned_paths`?

The pattern: agents today are specialized by **language/file-type** (Python service
vs React frontend vs YAML contract). In the redesign, agents are specialized by
**surface kind** (logic, presentation, integration, persistence).

### 4.2 Define four domain-surface agents

These replace all current execution agents:

**`LogicImplementationAgent`** — surface_kind: `module`, `workflow`, `control_plane`
- Reads: `domain_context.surface_kind`, `domain_context.logic_patterns` from
  build context, `owned_paths`
- Writes: Logic unit files — Python for webapp, GDScript for game, Kotlin for mobile
- Domain knowledge: injected from dev pack `logic_patterns.yaml` catalog
- Prompt structure: `[ROLE]` → `[DOMAIN CONTEXT]` (injected) → `[TASK]` →
  `[FILE CONTRACTS]` (injected) → `[OUTPUT]`

**`PresentationImplementationAgent`** — surface_kind: `ui_only`, view surfaces
- Reads: `domain_context.view_primitives` from build context, `BuildView` spec
- Writes: Presentation files — React/YAML for webapp, .tscn for game, Composable
  for mobile
- Domain knowledge: injected from dev pack `view_primitives.yaml` catalog

**`IntegrationImplementationAgent`** — surface_kind: `external_integration`
- Reads: `BuildIntegration` spec, `domain_context.integration_patterns`
- Writes: Client/adapter files — Python client for webapp, GDPlugin for game,
  Swift/Kotlin SDK wrapper for mobile
- Domain knowledge: injected from dev pack `integration_patterns.yaml` catalog

**`PersistenceImplementationAgent`** — `data_models`, `persistence_contract` task types
- Reads: `DataContractBundle`, `domain_context.persistence_patterns`
- Writes: Schema/model files — schemas.py for webapp, SQL migration for mobile,
  save format definition for game
- Domain knowledge: injected from dev pack `persistence_patterns.yaml` catalog

### 4.3 Middleware refactor

**File:** `factory_app/workflows/AppGenerator/middleware.yaml`

Remove all agent-specific hooks that inject web vocabulary:
- `hook_domain_catalog_context.py` — absorb into dev pack catalog injection
- `hook_file_contract_context.py` — move to dev pack `file_contracts.yaml` catalog
- `hook_shell_preset_context.py` — move to webapp dev pack `view_presets.yaml`
- `hook_capability_routing_context.py` — move to dev pack `capability_routing.yaml`
- `hook_hosted_capabilities_context.py` — move to hosted pack context

Keep only:
- `inject_context_graph_context` (all agents) — domain-agnostic
- `inject_build_context_projections` (planning agent) — generic mechanism
- `inject_domain_surface_contracts` (new, all execution agents) — generic mechanism
  that reads dev pack file contract catalogs

`inject_domain_surface_contracts` is the single new middleware function. It reads
the active dev pack's `file_contracts.yaml` catalog and injects the appropriate
file shape constraints for the current task's `surface_kind`. One function, any
domain.

### 4.4 Validation gate for Stage 4

- Webapp dev pack injects Python patterns into `LogicImplementationAgent`; agent
  writes equivalent files to current `ServiceAgent` + `ModelAgent`
- Webapp dev pack injects React/YAML primitives into `PresentationImplementationAgent`;
  agent writes equivalent files to current `FrontendStubAgent`
- Webapp dev pack injects Python client patterns into `IntegrationImplementationAgent`;
  agent writes equivalent to current `ControllerAgent` for external surfaces
- `PersistenceImplementationAgent` writes equivalent to current `DatabaseAgent`
- Full build pipeline produces a valid, wired, deployable webapp
- Module wiring check passes (all declared actions have implementations)
- Page wiring check passes (all declared views have implementation files)

---

## Stage 5: Package the Webapp Dev Pack

**Goal:** Package everything webapp-specific as the canonical first dev pack.
This proves the pattern works and makes the architecture explicit.

**Duration estimate:** 3–5 days.

### 5.1 Create `build_context/webapp_builder/`

```
build_context/webapp_builder/
├── context.yaml                  ← pack registration
├── view_primitives.yaml          ← web UI primitive vocabulary
├── logic_patterns.yaml           ← Python handler/service/repo patterns
├── persistence_patterns.yaml     ← MongoDB schema patterns
├── integration_patterns.yaml     ← Python client/adapter patterns
├── file_contracts.yaml           ← canonical web app file layout
├── capability_directory.yaml     ← web SaaS capability vocabulary
├── capability_routing.yaml       ← web capability routing rules
├── module_archetypes.yaml        ← Python module archetypes
└── design_docs_guidance.yaml     ← webapp design vocabulary for DesignDocsAgent
```

`context.yaml` declares:

```yaml
context_id: webapp_builder
applies_to_workflows:
  - DesignDocs
  - AppGenerator
  - AgentGenerator
pack:
  id: webapp_builder
  status: active
  pack_kind: dev_domain
validation_hooks:
  - hook_module_contract_quality_gate
  - hook_module_runtime_quality_gate
  - hook_view_wiring_check
projections:
  context_variables:
    build_task_model:
      value: AppBuildTask
    factory_domain_id:
      value: webapp
```

All content currently embedded in agent prompts, middleware hooks, and catalog
files that is webapp-specific moves here.

### 5.2 Register the webapp build sequence explicitly

**File:** `factory_app/workflows/extended_orchestration/extension_registry.json`

Add `dev_pack_id` to the build sequence:

```json
{
  "id": "build",
  "dev_pack_id": "webapp_builder",
  "steps": [...]
}
```

The control plane reads `dev_pack_id` to load the correct dev pack at sequence
start.

### 5.3 Validation gate for Stage 5

- `factory_domain_id: webapp` is set in context at build sequence start
- `build_task_model: AppBuildTask` resolves correctly
- All webapp validation hooks run and pass
- A complete build from concept to packaged bundle produces identical output
  to pre-refactor
- No webapp-specific vocabulary remains in shared workflow files

---

## Stage 6: Validate Domain Isolation with a Minimal Second Domain

**Goal:** Build the minimum viable second domain to prove the architecture is
genuinely domain-agnostic. Mobile (React Native) is the recommended choice —
closest to webapp, lowest new-infrastructure risk.

**Duration estimate:** 3–4 weeks.

### 6.1 Build the Mobile Dev Pack

```
build_context/mobile_builder/
├── context.yaml
├── view_primitives.yaml           ← Screen, List, Form, Card, TabBar, Modal, etc.
├── logic_patterns.yaml            ← TypeScript service patterns
├── persistence_patterns.yaml      ← AsyncStorage, SQLite schema patterns
├── integration_patterns.yaml      ← React Native SDK integration patterns
├── file_contracts.yaml            ← React Native file layout
└── design_docs_guidance.yaml      ← Mobile design vocabulary
```

`view_primitives.yaml` declares:
```yaml
view_element_types: "Screen | List | Form | Card | TabBar | BottomSheet | Modal | Toast | Header"
view_layout_types: "stack | tab | drawer | modal"
address_scheme: screen
```

### 6.2 Register the mobile build sequence

```json
{
  "id": "mobile_build",
  "dev_pack_id": "mobile_builder",
  "steps": [
    { "workflows": ["ValueEngine"] },
    { "workflows": ["DesignDocs"] },
    { "workflows": ["AgentGenerator"] },
    { "workflows": ["MobileGenerator"] }
  ]
}
```

### 6.3 Build `MobileGenerator`

`MobileGenerator` is a thin workflow that reuses:
- The shared `BuildPlan` structured output model
- `LogicImplementationAgent` (same agent, different domain context)
- `PresentationImplementationAgent` (same agent, different domain context)
- `IntegrationImplementationAgent` (same agent, different domain context)
- `PersistenceImplementationAgent` (same agent, different domain context)

`MobileGenerator` adds only:
- Its own `AppPlanAgent` variant (`MobilePlanAgent`) with `MobileBuildTask` as
  the domain-specific task model extension — includes `react_native_screen_type`
  in `domain_context` instead of `task_type`
- Mobile-specific acceptance criteria in task items

If the architecture is correct, **no harness changes are needed** to run
`MobileGenerator`. The harness is domain-agnostic. The dev pack supplies the
domain knowledge.

### 6.4 Validation gate for Stage 6

- Mobile concept → `BuildPlan` with `mobile` domain → `MobileBuildTask[]` →
  harness executes → React Native files written to `owned_paths`
- Generated React Native project installs and runs without errors
- `LogicImplementationAgent` writes TypeScript services (not Python)
- `PresentationImplementationAgent` writes React Native screens (not web YAML/JSX)
- Webapp build sequence produces identical output to pre-refactor (no regression)
- Harness files (`task_batches.yaml` loader, assembly agent, packaging agent)
  are untouched

---

## Contract Completeness Checks

These checks run as deterministic Python against `BuildPlan` before any
execution agent writes a file. They do not use LLM reasoning. If any check
fails, the planning pass reruns. The executing agents never see an incomplete
plan.

### C1: Surface completeness
Every surface declared in `BuildPlan.surfaces[]` has at least one `BuildTask` in
`build_tasks[]` with a matching `surface_id` and non-empty `owned_paths`.

### C2: View ownership
Every view declared in `BuildPlan.views[]` has a `surface_id` that matches a
declared `BuildSurface`. No orphan views.

### C3: Operation coverage
Every `operation` declared on a `BuildSurface` has a corresponding implementation
in the surface's `owned_paths`. No declared operation without an implementation stub.

### C4: Event producer resolution
Every `event_type` in `BuildPlan.event_flows[]` has a `producer_pack_id` that
resolves to a declared surface.

### C5: Integration declaration
Every integration referenced in `owned_paths` appears in `BuildPlan.integrations[]`
with `required_at` declared.

### C6: Data contract coverage
Every entity in `BuildPlan.entities[]` appears in `DataContractBundle.surfaces[]`
with at least one collection declared.

---

## What Gets Deleted (Not Shimmed)

This is the pre-production cleanup list. No aliases. No fallback paths. Remove.

| What | Where | When |
|---|---|---|
| `ExperienceSpec`, `UIPage`, `UIPageSection` | `DesignDocs/structured_outputs.yaml` | Stage 2 |
| `UIPageSection.primitive` literal enum | Same | Stage 2 |
| `AppBuildPage`, `AppBuildModule` with web-specific fields | `AppGenerator/structured_outputs.yaml` | Stage 3 |
| `AppBuildTask.task_type` literal enum | Same | Stage 1 (moved to domain_context) |
| `frontend_markdown`, `backend_markdown`, `database_markdown` doc kind names | `extension_registry.json`, context vars | Stage 2 |
| `ServiceAgent`, `ModelAgent`, `DatabaseAgent`, `FrontendStubAgent`, `ControllerAgent` as web-specific agents | `AppGenerator/agents.yaml` | Stage 4 |
| `hook_domain_catalog_context.py`, `hook_file_contract_context.py`, `hook_shell_preset_context.py`, `hook_capability_routing_context.py` as always-on hooks | `middleware.yaml` | Stage 4 |
| `AppSchemaAgent` as a web-specific page schema agent | `AppGenerator/agents.yaml` | Stage 4 (absorbed into PresentationImplementationAgent) |
| Web vocabulary in `DesignDocsAgent` prompt | `DesignDocs/agents.yaml` | Stage 2 |
| Capability directory web SaaS vocabulary from shared catalog | `build_context/AppGenerator/` | Stage 5 (moves to webapp dev pack) |
| `build_context/AppGenerator/` directory | Entire directory | Stage 5 (content moves to `build_context/webapp_builder/`) |

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Dynamic Pydantic model construction introduces validation gaps | Medium | High | Validate catalog injection produces equivalent schema to current literals; snapshot tests |
| Planning agent prompt generalization causes planning quality regression | High | High | Run planning quality eval against 20 webapp concepts before/after; iterate until equivalent |
| Middleware refactor breaks build context projection for existing webapp pack | Low | High | Keep `inject_build_context_projections` unchanged; only remove web-specific hooks |
| Stage ordering dependency (Stage 3 depends on Stage 2 outputs) | Certain | Medium | Do not start Stage 3 until Stage 2 validation gate passes |
| Mobile domain reveals harness assumption not caught in Stage 1 | Medium | Medium | Stage 6 is the canary; if harness changes are needed, fix the contract (Stage 1) not the harness |
| LLM drift in domain-injectable agents (agent ignores injected vocabulary) | Medium | Medium | Acceptance criteria in BuildTaskBase are deterministic checks; if criteria fail, task reruns |

---

## Success Criteria

Prism is complete when:

1. A webapp concept produces a deployable webapp — C1–C6 contract checks pass
2. A mobile concept produces a deployable React Native app — C1–C6 contract checks pass
3. Adding a third domain requires only a new dev pack and sequence registration —
   no harness changes, no workflow ABI changes
4. The time from concept to packaged build artifact is deterministic — the same
   inputs produce the same contract, the same contract drives the same build
