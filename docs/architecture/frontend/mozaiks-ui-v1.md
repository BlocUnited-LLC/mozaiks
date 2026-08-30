# Mozaiks UI v1

**Status:** Authoritative target architecture constitution for the native Mozaiks UI framework.
**Contract id:** `mozaiks.ui.v1` (documentation contract only; runtime schema ids are introduced by the roadmap in §12).
**Authority:** Subordinate to [Mozaiks OSS Software Design](../MOZAIKS_OSS_SOFTWARE_DESIGN.md) and the
[Canonical Schema Generation Policy](../CANONICAL_SCHEMA_GENERATION_POLICY.md). Where this document and
current source disagree, §11 says which is truth today; changes to this document follow §10.

This document is documentation-only. It changes no code, schema, registry, or runtime behavior.
Inputs include the merged `mozaiks.app_layout.v1` registry
(`mozaiksai/core/runtime/app/layout_registry.py`, PR #286), the current
[Generated Frontend Surface Contract](ui-system/generated-frontend-surface-contract.md),
[UI Surface Model](chat-ui/ui-surface-model.md), the
[AG-UI / CopilotKit comparison](chat-ui/ag-ui-copilotkit-comparison.md), and the recent
cross-repo UI architecture audits (Codex UI architecture audit; Claude Code UI evidence
inventory), with App Zero usage read as evidence only.

---

## 1. Scope

Mozaiks UI is the **deterministic application UI framework** of the Mozaiks canonical
application model. It is:

- a build-time UI **generation** system: strict structured outputs materialize validated,
  byte-stable UI artifacts into canonical app-layout paths;
- a **closed-registry rendering** system: pages and components resolve only through
  registered primitives and named components;
- a **composition** system: durable application pages, bounded custom routes, live
  workflow surfaces, and transition surfaces share one primitive/design foundation while
  keeping separate authoring contracts;
- an **application** framework: routes, shell, navigation, theme authority, module-action
  data binding, and entitlement-aware error recovery are first-class.

Mozaiks UI is **not**:

- only a chat renderer — persistent application pages are not chat artifacts;
- a generative-UI protocol — no model composes screens at runtime; UI is generated once,
  validated, and promoted;
- a transport specification alone — the wire contract (§5) is one layer of the framework,
  not the framework;
- a design system marketplace — reusable UI enters only through capability-pack and
  registry contracts.

**External protocols.** AG-UI and A2UI are comparison inputs only. They are not
dependencies, not canonical authorities, and no Mozaiks contract may reference their
types for canonical identity. This document makes no adapter roadmap commitment. Any
future interoperability adapter requires a separate ADR and explicit product decision
before implementation work starts.

---

## 2. Canonical UI surfaces

There are exactly **four canonical surface kinds**. They are already a finite generator
taxonomy in `mozaiksai/core/workflow/ui_surface_taxonomy.py` and the
[Generated Frontend Surface Contract](ui-system/generated-frontend-surface-contract.md).

| Surface kind | Responsibility | Authoring contract | Runtime path |
| --- | --- | --- | --- |
| `declarative_page` | Durable application routes: dashboards, lists, detail pages, forms, settings, workspaces. No React authoring. | `ui/pages/{page_id}.yaml` (AppPageSchema) | `SchemaPage` → `PageRenderer` → page primitives (`chat-ui/src/ui/page-renderer/`) |
| `custom_react_page` | Durable full-page routes with a real primitive gap. Bounded escape hatch; shared primitives and semantic tokens only. | `ui/route_manifest.json` + `ui/pages/custom/*.jsx` + `ui/index.js` (all three, together) | `@platform/extensions` → component registry |
| `agent_tool` | Live workflow checkpoints inside a session: approvals, structured input, artifact review. Transient by design. | workflow `tools.yaml` + Python tool + shipped/workflow-local React | `use_ui_tool(...)` → tool-call lane → `WorkflowUIRouter` |
| `transition` | Pre-/between-workflow routing, deterministic user choices, seeded launch context. | `extension_registry.json` transitions + transition components | `LauncherScreen` / `ConfirmScreen` / `TransitionScreen` |

**Admin and profile are registered extension surfaces, not fifth and sixth surface
kinds.** They are contract-declared compositions over the four kinds:

- **Admin/operator extension surface** — two tiers, per the admin two-tier model:
  Tier 1 schema panels declared in `admin/admin_registry.yaml` plus module
  `contracts/admin.yaml` (declarative composition rendered inside the unified `/admin`
  shell); Tier 2 full-page operator routes, which are `custom_react_page` instances
  registered through the same three-file route contract plus `admin/index.js`.
  `admin_registry.yaml` is never a route registry.
- **Profile extension surface** — module `contracts/profile.yaml` tabs/panels
  (`mozaiks.profile.v1`) hydrated by the core `ProfilePage` through
  `/api/me/profile-tabs` / `/api/me/profile-panels`. Modules contribute content;
  the host owns identity and the page itself.

This classification is normative: proposals to add a new top-level surface kind must
show why the behavior cannot be expressed as a registered extension over the four kinds,
and must follow §10.

---

## 3. Layer ownership

One owner per layer. No layer may grow a second owner; the
[Canonical Schema Generation Policy](../CANONICAL_SCHEMA_GENERATION_POLICY.md) alias
rules apply.

| Layer | Single owner | Anchor |
| --- | --- | --- |
| Intent | Refinement Engine routing + `workflow_sequences[]` | `extension_registry.json`, `mozaiksai/control_plane/` |
| Structured output | Per-agent strict models + registry | `factory_app/workflows/*/structured_outputs.yaml` |
| UI artifact | AppGenerator / AgentGenerator materialization into `mozaiks.app_layout.v1` paths | `save_app_schema`, `save_workflow_ui_files_output`; `layout_registry.py` |
| Route | `ui/route_manifest.json` + declarative page ids; shell composition | `build_shell_config()` in `mozaiksai/hosts/platform.py` |
| Page/layout | AppPageSchema `layout` + `PageRenderer` layout classes | `chat-ui/src/ui/page-renderer/PageRenderer.jsx` |
| Primitive/component | `PrimitiveRegistry` (page primitives) and `componentRegistry` (named components) | `chat-ui/src/ui/page-renderer/PrimitiveRegistry.js`, `chat-ui/src/registry/componentRegistry.js` |
| Data binding | Section `api_endpoint` → `POST /api/modules/{module}/{action}`; custom routes via `moduleAction()` only | `usePageData.js`, generated `ui/lib/moduleApi.js` |
| Action | Module `ActionDef` (permissions, `entitlement_gate`, `emits`) | `module.yaml`, `ModuleExecutor` |
| Event | Module event/reaction contracts (domain plane); UI event envelope (§5, interaction plane) | `contracts/events.yaml` family; transport |
| Transport | Runtime transport + unified event dispatcher | `simple_transport.py`, `unified_event_dispatcher.py` |
| State | §6 state-domain table | `uiSurfaceReducer.js`, `ChatUIContext`, runtime persistence |
| Persistence | Runtime chat/session/artifact stores; module persistence via `ctx.persistence` | `mozaiksai/core/` stores |
| Validation | Generated-bundle validators + functional scan + quality gates + Playwright acceptance | `mozaiksai/core/validation/`, `generated_ui_contract.py`, `ui-system-quality-gates.md` |
| Refinement | Refinement Engine change classes over UI artifact families | `contract_surface` plans; `ui/pages/{target_id}.yaml` in `CONTRACT_SURFACE_CANONICAL_PATHS` |

**Hard exclusions.** UI never owns: business authorization (module `permissions[]` +
runtime auth own it — `meta.routeAuth` is a pre-render gate only, never authority);
entitlement grants (`EntitlementPort` adapters own enforcement; UI only reacts to
`ENTITLEMENT_REQUIRED` / `INSUFFICIENT_TOKENS` with declared recovery routes); billing
authority; tenant identity; durable domain state (module actions own all durable
mutation). A UI surface that needs a durable fact calls a module action.

---

## 4. Registry constitution

Two registry classes exist and must never be conflated:

**Immutable contract registries** — versioned, digest-stable, closed vocabularies that
generators emit against and validators enforce. Changing one is a contract change (§10).

| Registry | Current owner (truth today) | v1 target |
| --- | --- | --- |
| Surface kinds | `ui_surface_taxonomy.py` (4 ids) | unchanged; formalized as `mozaiks.ui.surface.v1` |
| Page types | AppPageSchema `page_type` values in `structured_outputs.yaml` | closed enum with scanner validation (exists via graph-closure validation) |
| Layout types | `LAYOUT_CLASSES` in `PageRenderer.jsx` (`grid`, `sidebar`, `full-width`, `split`) | enum mirrored into structured outputs; unknown value fails, never silently maps to `full-width` (§9) |
| Page primitives | `PRIMITIVES` map + `PrimitiveSchemas.js` + exported `primitive_schemas.json` | unchanged mechanism; catalog tiers from `PrimitiveCatalog.js` become part of the versioned projection |
| Workflow primitives | [Workflow UI Primitive Catalog](ui-system/workflow-ui-primitive-catalog.md) + `ui.workflow_primitive` manifest field | closed enum validated at bundle scan |
| Named components | registration *names* accepted by route manifests / admin registry / profile manifests | closure check: every referenced name must be registered (functional scanner already checks `registerComponent` closure) |
| Component scopes | core / studio / app-extension / workflow-local (frontend layer model) | explicit scope field on registrations |
| Display modes | `chat.tool_call` `display` values (`inline`, `artifact`, `view`, `fullscreen`, `composer`) | closed enum in the §5 envelope |
| Renderer capabilities | `ui.realization` (`shipped_component`, `workflow_wrapper`, `generated_component`) | unchanged; enum formalized |
| Extension slots | `ExtensionSlot` in `layout_registry.py` (5 slots) + UI extension contracts (§8) | UI extension slots added to the layout registry pattern, not a parallel mechanism |
| UI event types | today: `chat.*`, `chat.tool_call`, and typed `ui.*` including older `ui.render` consumers (fragmented) | the single closed taxonomy of `mozaiks.ui.event.v1` (§5); `ui.render` removed as a render lane |
| State domains | implicit (reducer + context + runtime stores) | explicit closed list (§6) with one owner each |

**Mutable runtime mounting registries** — process-local mount tables populated at boot
from the immutable contracts: `componentRegistry.js` (a runtime `Map`),
`WorkflowUIRouter` workflow-local component resolution, and shell route mounting. They
may hold instances; they may never introduce names, kinds, or paths that the contract
registries do not declare. A mounting registry entry without a contract-registry
counterpart is a validation failure, not a feature.

Relationship to `mozaiks.app_layout.v1`: the layout registry owns **where** every UI
artifact lives (`app_ui_page_schema`, `app_ui_custom_route`, `app_ui_route_manifest`,
`app_ui_extension_barrel`, `app_ui_module_api`, `module_admin_ui`,
`module_ui_extension_barrel`, `workflow_ui`, `app_admin_registry`, `app_dashboard`,
`app_brand_theme`, `app_shell_config`). Mozaiks UI v1 owns **what those artifacts may
contain and how they behave**. Every registry above must key its artifacts to layout
artifact kinds; neither document may redefine the other's axis.

---

## 5. Native event constitution — `mozaiks.ui.event.v1`

Requirements for the future canonical UI interaction envelope. This section sets the
contract bar; the concrete schema lands via §12. Nothing here changes code in this PR.

The envelope MUST provide:

1. **Closed event taxonomy.** A finite enum of event kinds covering: run lifecycle
   (started, finished, failed), text streaming, tool lifecycle (start, args, end,
   result), approvals and interrupts (request, response, resume), navigation intents,
   state snapshot and state delta, primitive refresh, and recovery/replay markers.
   Unknown kinds fail closed at both producer and consumer.
2. **Versioned envelope.** `schema_version: mozaiks.ui.event.v1` on every event;
   consumers reject unversioned or unknown-version events.
3. **Deterministic identity.** Stable `event_id`; identity derives from content and
   ordering fields, never from wall-clock time.
4. **Sequence and ordering.** Monotonic per-stream sequence numbers; consumers can
   detect gaps and must not render out-of-order tool lifecycles.
5. **Correlation and causation.** `run_id`, `chat_id`, `tool_call_id`, and a
   `caused_by` reference so every render can be traced to the emitting turn.
6. **Idempotency.** Redelivery of the same `event_id`/sequence is a no-op render.
7. **Replay boundaries.** Replay is scoped to one session stream; a replay marker
   separates restored history from live events; replayed events never re-trigger side
   effects (tool futures, navigation).
8. **Bounded payloads.** Enforced maximum payload size; large artifacts travel by
   reference (artifact id), never inline.
9. **Tenant/session scoping.** Every event carries `app_id` and session scope; the
   transport layer enforces that a socket only ever receives its own session's events.
   Cross-tenant replay is structurally impossible, not merely filtered (§9).
10. **Lifecycle, tools, approvals, interrupts, navigation, state, recovery** are all
    first-class kinds in the taxonomy — no side channels.
11. **Non-authoritative timestamps.** Timestamps are display metadata; ordering and
    identity never depend on them.
12. **Payload hygiene.** No secrets, raw exception text, source file content, or model
    chain-of-thought in any event payload. Structured-output JSON from registered
    agents is durable trace data routed to the declared artifact/tool path, never
    projected as chat text (per the AG2 runtime-handoff rule).

**Canonical workflow-render lane (conceptual resolution).** The tool-call lifecycle —
today carried primarily as `chat.tool_call` / `tool_call_response` — **is** the
canonical workflow-render lane and survives. Current source still contains `ui.render`
consumer paths from the earlier typed-UI experiment; those paths are transitional
implementation evidence, not a second canonical lane. The intended migration is:
preserve `chat.tool_call` semantics, express that lane through
`mozaiks.ui.event.v1` typed tool-lifecycle kinds, move any still-needed `ui.render`
behavior into that envelope, then delete `ui.render` as a browser-facing render lane.
After that migration, the typed `ui.*` primitive bus is secondary and refresh-only
(never a renderer of new workflow surfaces). This follows the keep/merge/delete map
already recorded in the [AG-UI / CopilotKit comparison](chat-ui/ag-ui-copilotkit-comparison.md):
one stream, one tool lifecycle, one response lane, a default renderer so tool activity
is never invisible.

---

## 6. State ownership

Closed list of state domains. Each has one owner and an explicit durability class.

| State domain | Owner | Durability |
| --- | --- | --- |
| Authoritative application state | Module persistence (`ctx.persistence`) behind module actions | Durable — never reconstructable from UI |
| Workflow/checkpoint state | Runtime workflow manager + AG2 state progression | Durable (resumable runs) |
| Chat history | Runtime chat session store (MongoDB) | Durable |
| Pending tool state | Runtime pending tool-call futures keyed by `tool_call_id` | Process-lifetime; on reconnect it is re-derived from durable run state, never trusted from the client |
| Render state (artifact/inline surfaces) | `uiSurfaceReducer` + `ChatUIContext` caches | Reconstructable — replay of the event stream must rebuild it |
| Shell state (conversation mode, layout mode, widget) | `uiSurfaceReducer` (`ask`/`workflow`; `full`/`split`/`minimized`/`view`) | Reconstructable |
| Cached client state (page data, artifact cache) | `usePageData` per-section fetches; `ChatUIContext` caches | Disposable — a refetch is always legal |
| Approval state | Durable run/checkpoint records (approval is authority-bearing) | Durable — an approval must survive reconnect and process restart; the rendered card is reconstructable, the decision is not |
| Reconnect/replay state | Transport + §5 replay boundaries | Reconstructable from durable stores |

Rules: anything authority-bearing (approvals, module records, workflow checkpoints,
entitlement assignments) is durable and server-owned. Anything the client holds is
reconstructable or disposable. No state domain may be added without extending this
table (§10), and no client cache may become the only holder of an authority-bearing
fact.

---

## 7. Deterministic generation contract

The one required path for every generated UI artifact. Stages must not be skipped or
reordered; each stage consumes only the previous stage's validated output.

```text
strict structured output          AppSchemaOutput / workflow UI CodeFiles — typed, registry-bound
  → registry validation           surface kinds, primitives, page types, workflow primitives,
                                  named-component references all resolve against §4 registries
  → canonical materialization     save tools write only mozaiks.app_layout.v1 paths
                                  (app_ui_page_schema, app_ui_custom_route, workflow_ui, …)
  → route/component/action closure
                                  route → component → module → action closure; registerComponent
                                  closure; moduleAction() usage; workflow ids resolve
  → security & entitlement checks entitlement_gate → subscriptions closure; api_surface taxonomy;
                                  no secret paths; recovery-route rules for gated actions
  → byte-stable artifacts         deterministic serialization; timestamps excluded from identity
  → bundle scan                   validate_generated_app_bundle + generated_bundle_scanner
  → functional validation         scan_functional_generated_app: no 404/501/NotImplemented,
                                  registered components only, endpoint shape rules
  → real runtime boot             app_runtime_load: AppLoader boots the assembled bundle;
                                  Playwright render acceptance for the UI lane
  → promotion                     the only path from generated/ staging into an active app root
```

The quality gates are runtime state, not prompt prose: `app_ui_quality_status` and
`workflow_ui_quality_status` must equal `passed` before assembly/delivery, and
Playwright findings convert to structured `passed | needs_revision | blocked`
production-readiness output. The LLM never chooses file locations (layout registry),
never invents primitives or components (§4 registries), and never bypasses a gate.

---

## 8. Extension constitution

Every extension type has a mandatory, complete checklist. **An incomplete extension
fails closed** — loaders, scanners, and gates reject it; nothing renders partially.

| Extension | Mandatory steps |
| --- | --- |
| Page primitive | Component in `chat-ui/src/ui/page-renderer/../primitives/` → JSON Schema in `PrimitiveSchemas.js` → `PRIMITIVES` entry → regenerate `primitive_schemas.json` → catalog tier + use/avoid guidance in `PrimitiveCatalog.js` → structured-output config model → generator prompt/catalog update → quality-gate rule review → tests |
| Page/layout type | Enum value in structured outputs → renderer support → scanner validation → docs; no silent mapping of unknown values |
| Workflow component (shipped) | Component in `chat-ui/src/core/ui/` → workflow-primitive catalog entry → `ui.workflow_primitive` + `ui.realization` manifest rules → `WorkflowUIRouter` resolution → gate rules → tests |
| Named app component | Registration in the owning barrel (`ui/index.js` scope) + every referencing manifest entry; closure-checked by the functional scanner |
| Custom route | All three artifacts together: `ui/route_manifest.json` entry + `ui/pages/custom/*.jsx` + `ui/index.js` registration; shared primitives and semantic tokens only; missing any piece is an export/download blocker |
| Admin extension | Tier 1: `admin/admin_registry.yaml` page + module `contracts/admin.yaml` panel. Tier 2: route manifest + `admin/pages/*.jsx` + `admin/index.js`, all three |
| Profile extension | `contracts/profile.yaml` (`mozaiks.profile.v1`) tab/panel bound to a declared module action; component declared in `js_stubs`; no admin-only actions, no secrets |
| UI event kind | Taxonomy addition to `mozaiks.ui.event.v1` (schema + producer + consumer + replay semantics + tests together); no ad hoc event names |
| State domain | §6 table addition with owner + durability class + reconstruction rule, before any code holds the state |
| Proprietary App Zero UI extension | Same contracts via the pinned OSS package: routes through `app/ui/route_manifest.json` + `app/ui/index.js`, operator pages over shared primitives, declaration in the workspace reuse contract; never a fork of chat-ui or Studio surfaces (evidence: App Zero's 67 declared routes and operator pages consume exactly these seams) |

---

## 9. Security invariants

Non-negotiable. Violations are defects regardless of convenience.

1. **No agent-invented component or action authority.** Agents reference primitives,
   components, routes, and actions by registered name only; unresolved references fail
   before promotion (schema-generation policy rule 5).
2. **No caller-selected dynamic imports.** The frontend never imports code from a
   name supplied at runtime; all mounting goes through boot-time registries populated
   from validated contracts.
3. **No UI-side authorization.** `meta.routeAuth` and entitlement-aware helpers are
   UX gates; module `permissions[]`, `entitlement_gate`, and runtime auth are the only
   authority. A UI check is never the last check.
4. **No cross-tenant replay.** §5 scoping: events carry tenant/session identity and
   transport delivers only within scope; replay never crosses sessions.
5. **No unvalidated page serving.** `/api/pages/{name}` serves only pages that passed
   the §7 pipeline; hand-edited artifacts in active roots are a promotion-discipline
   violation.
6. **No unknown taxonomy fallback.** Unknown surface kinds, page types, layout types,
   primitives, display modes, or event kinds are errors — never coerced to a default.
   (Current gap: `PageRenderer` maps unknown layouts to `full-width`; §11.)
7. **No silent layout/shell fallback.** Shell composition guarantees (admin-portal
   injection, `appShell` inference) are explicit runtime rules, not ad hoc defaults;
   generated output must not re-declare them.
8. **No raw exception or secret payloads.** UI events and page data carry typed error
   codes (`error_code`), never raw exception text, credentials, or secret names beyond
   the names-only contracts.
9. **No runtime-generated executable UI.** No surface evaluates model-produced code in
   the browser. Executable UI exists only as build-time artifacts that passed §7 — this
   is the structural difference from generative-UI protocols and it is permanent.

---

## 10. Versioning and maintenance

- **Schema/version ownership.** Each contract family carries its own version id
  (`mozaiks.ui.surface.v1`, `mozaiks.ui.event.v1`, `mozaiks.profile.v1`, …). Version
  ids live with the runtime owner of the contract; this document indexes them.
- **Pre-production posture.** Replace, don't preserve: obsolete shapes, aliases,
  dual-read paths, and retired names are removed in the same change that supersedes
  them, per the repo-wide pre-1.0 policy. No compatibility shims without an explicit
  current external contract.
- **Retirement process.** A concept is retired by: updating the structured-output
  model, prompts, materializer, validators, runtime consumer, docs, fixtures, and tests
  together; deleting the retired shape; and recording the change in `CHANGELOG.md`.
  Half-retired concepts (old name still accepted anywhere) are defects.
- **Registry drift tests.** Every §4 immutable registry gets a deterministic test
  asserting the runtime vocabulary, the structured-output enum, and the generator
  catalog agree (pattern: the layout registry's own drift tests against `paths.py` and
  `file_contracts.yaml`).
- **Required tests and documentation.** No UI contract change merges without: the
  narrowest focused tests, the affected quality-gate rules, and updates to this
  document's §11 table when current truth moves.
- **One concept, one owner.** One runtime concept gets one canonical name and one
  owning registry. Discovering two names for one concept triggers consolidation, not
  documentation of both.
- **OSS/proprietary boundary.** Everything in this document is OSS. Proprietary
  operator UI (App Zero product pages, hosted dashboards) consumes these contracts via
  the pinned package and the workspace reuse contract; no proprietary surface may
  require a private fork of any registry, renderer, or event contract.
- **Prohibited shortcuts.** Registering components outside barrels; serving pages that
  skipped gates; adding event names outside the taxonomy; introducing a second
  workflow-render lane; UI-side authority checks; hardcoded visual values in generated
  React; editing generated artifacts in place without promotion.

---

## 11. Current versus target

Facts about today's implementation versus the v1 target. Nothing below presents
unimplemented behavior as existing.

| Area | Current truth (implemented) | v1 target (not yet implemented) |
| --- | --- | --- |
| Surface kinds | 4-id taxonomy in `ui_surface_taxonomy.py`, injected into prompts | Same ids, formalized as a versioned registry projection |
| Page primitives | Closed `PRIMITIVES` map + JSON Schemas + exported `primitive_schemas.json` + tiered catalog | Unchanged mechanism; drift test binding registry ↔ structured outputs ↔ catalog |
| Named components | `componentRegistry` Map with duplicate-warning; functional scanner checks registration closure | Explicit component-scope field; contract/mounting split named in code |
| Layout types | `LAYOUT_CLASSES` with unknown → `full-width` mapping | Unknown layout fails validation before serve (§9.6) |
| Page slot extensions | **Removed.** `AppPageSchema.extensions` / `AppPageSlotExtension` were a false contract: the generator validated slot names but `PageRenderer` never rendered them, no fixture or workspace used them, and no component-closure authority existed. Scanner and quality audit now reject any page declaring the field. | Any future slot-override category enters through the §8 extension process with a closed build-time component authority, a real renderer, and positive render tests — never as a schema-only promise. |
| Workflow render lane | `chat.tool_call` + `tool_call_response`; older `ui.render` consumer paths still exist in `dynamicUIHandler`, `ChatPage`, `WorkflowChat`, and `uiSurfaceReducer`; typed `ui.*` bus; reducer-driven render state; no AG-UI producer exists (prior attempt removed) | One `mozaiks.ui.event.v1` envelope expressing the `chat.tool_call` lane; `ui.render` removed as a workflow render lane; remaining `ui.*` narrowed to refresh-only; default tool renderer |
| Event envelope | Envelopes built by `UnifiedEventDispatcher`; no unified version id, sequence, or idempotency contract | §5 requirements 1–12 |
| State domains | Reducer + `ChatUIContext` caches + runtime stores; ownership implicit | §6 table explicit and tested |
| Generation pipeline | Fully implemented: structured outputs → save tools → quality gates → scanners → functional scan → runtime boot → Playwright acceptance → promotion | Add §4/§5 registry validations as they land |
| Admin/profile | Two-tier admin model; `mozaiks.profile.v1` tabs/panels — both implemented | Classified as registered extensions (this document); no code change needed |
| Layout paths | `mozaiks.app_layout.v1` merged (PR #286), data-only | UI registries key artifacts to layout kinds as wiring lands |
| Entitlement-aware UI | Generated `moduleApi.js` recovery helpers; runtime `ENTITLEMENT_REQUIRED`; gate→plan compile-time closure | Unchanged; documented here as constitutional |

---

## 12. Dependency-ordered implementation roadmap

Small PRs, each independently green, each with rollback. No PR below changes this
document's authority; each cites it. **No AG-UI adapter phase appears here; any
interoperability adapter requires a prior explicit commercial decision gate recorded as
an ADR before any implementation PR may be opened.**

| # | PR (depends on) | Scope | Acceptance criteria | Rollback |
| --- | --- | --- | --- | --- |
| 1 | UI registry drift tests (—) | Tests only: primitives ↔ `PrimitiveSchemas` ↔ exported JSON ↔ structured-output config models; surface ids ↔ prompts; workflow-primitive catalog ↔ manifest vocabulary | New tests green; zero source changes | Revert tests |
| 2 | Layout-type strictness (1) | Unknown `layout` fails page validation before serve; enum mirrored into structured outputs | Scanner + serve-path tests prove unknown layout is an error; fixtures updated | Revert; prior mapping returns |
| 3 | `mozaiks.ui.event.v1` schema (1) | Typed envelope + closed taxonomy as models and docs; producer/consumer untouched | Schema round-trip + taxonomy tests; no transport change | Revert models |
| 4 | Envelope adoption — outbound (3) | `UnifiedEventDispatcher` emits versioned envelopes carrying the existing lane; frontend accepts both shapes for one PR window only, then the old shape is removed in the same series | Runtime smokes + widget/replay tests green; removal PR merges within the series | Revert adoption PR; dispatcher returns to prior envelopes |
| 5 | Tool-lane consolidation (4) | `ui.render` removed as a workflow render lane; remaining `ui.*` bus narrowed to refresh-only; default tool renderer added; `dynamicUIHandler` reduced per the comparison doc's map | Tool-call lifecycle tests; no second render lane remains | Revert consolidation |
| 6 | State-domain table enforcement (4) | §6 domains named in code; reconnect/replay rebuilds render/shell state from durable stores in tests | Replay test proves reconstruction; approval durability test | Revert |
| 7 | Component-scope registration (1) | Scope field on registrations; scanners check scope legality | Registration-closure tests extended | Revert |
| 8 | Layout-registry keying (1, #286 wiring series) | UI artifact registries reference `mozaiks.app_layout.v1` kinds once layout wiring lands elsewhere | Drift test: every UI artifact kind maps to a layout family | Revert keying |

Each PR: worktree from fresh `origin/main`, focused tests + full offline suite + ruff
locally, auto-merge on green, per the multi-agent coordination rules.

---

## Relationship summary

`mozaiks.app_layout.v1` answers *where a UI artifact may exist*. **Mozaiks UI v1**
answers *what it may contain, how it renders, how it behaves, and how it changes*.
The [Generated Frontend Surface Contract](ui-system/generated-frontend-surface-contract.md)
remains the working-level surface specification; this document is the constitution
above it. Where the two ever disagree, this document decides — and the disagreement is
a defect to fix, not a fork to maintain.
