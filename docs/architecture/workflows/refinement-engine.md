---
title: Refinement Engine
status: Authoritative - Pre-Production, Canonical Contract
created: 2026-04-13
depends_on: workflow-routing-transitions.md, event-system.md, ../../architecture/mozaiksai/universal-orchestrator.md, ../builder/data-contract-and-revision-contract.md
---

# Refinement Engine

This document defines how Mozaiks handles post-generation changes without forcing users back through full generation workflows for every adjustment.

In the canonical orchestration model, this document covers the builder session
loop and the refinement worker loop. It does not redefine workflow-local AG2
handoffs.

The goal is simple:

- initial generation workflows create the first canonical shape
- refinement workflows adjust that shape safely and quickly
- the Refinement Engine decides when a change is small, scoped, design-only, or concept-breaking

The Refinement Engine uses `app/config/ai.json` for runtime startup,
`app/config/refinement_policy.yaml` for refinement policy, and
`refinement_harness/config/harness.yaml` for checkpoint and routing
declarations. LLM-backed refinement checkpoints do not read workflow-local
AG2 config for this.

The Refinement Engine is not an AG2 Network graph and not a single AG2 agent that
launches workflows by tool call. AG2 owns the model execution inside
LLM-backed checkpoints. Mozaiks owns the deterministic artifact policy around
those checkpoints: loading persisted artifact context, validating structured
checkpoint outputs, deriving route impact, choosing workflow sequence re-entry,
and deciding whether to launch a workflow, run a scoped worker, or return a
harness decision to Studio.

LLM-backed refinement checkpoints should run through the shared AG2 adapter
`mozaiksai.core.adapters.AG2StructuredAgentRunner` so AG2 execution mechanics
stay centralized. Routing and artifact lifecycle logic must remain in
`mozaiksai/control_plane`.

This is a pre-production design. Use the canonical contract instead of retaining
outdated refinement paths.

---

## Core Decision

Mozaiks must treat **initial generation** and **refinement** as separate modes.

Initial generation is the compiler path:

1. `ValueEngine` defines canonical product intent
2. `DesignDocs` defines frontend/backend/database/ui schema intent
3. `AgentGenerator` and `AppGenerator` generate the first concrete artifacts

Refinement is the edit path:

1. load the latest persisted artifact version
2. classify the requested change
3. route to the smallest valid re-entry point
4. run refinement agents against scoped files or scoped plans
5. validate and persist a new artifact version

Do **not** re-run `AgentGenerator` or `AppGenerator` from the top for every tweak.
Do **not** let E2B become the source of truth.
Do **not** treat global refinement routing as just another AG2 handoff graph.

---

## Non-Goals

- No mixed "sometimes re-interview, sometimes patch, sometimes guess" behavior.
- No direct natural-language routing through ordinary AG2 handoffs after delivery.
- No local-browser-only edits as the primary refinement path.
- No obsolete shims for the current pre-production loops.

---

## Current Leverage In The Codebase

The existing platform already has the right raw ingredients:

- `ValueEngine` persists canonical concept state via `value_manifest`.
- `DesignDocs` persists draft design documents.
- `AppGenerator` already emits `build_tasks` with `owned_paths`, `depends_on`, and `acceptance_criteria`.
- App validation and preview already run in E2B-backed tooling.

That means refinement does **not** need a brand-new reasoning model. It needs a Refinement Engine and durable state model around the artifacts the generators already produce.

Database refinements should follow the companion contract in
[data-contract-and-revision-contract.md](../builder/data-contract-and-revision-contract.md):

- compare previous and target `data_contract` artifacts
- generate a typed migration plan
- auto-apply additive-safe changes only
- block destructive changes unless explicitly escalated

---

## Canonical State Layers

Each layer has one owner. Refinement routing must respect that ownership.

| Layer | Owner workflow | Source-of-truth payload |
|---|---|---|
| Concept intent | `ValueEngine` | `concept_overview`, `value_manifest`, approved scope |
| Design intent | `DesignDocs` | `frontend_design_document`, `backend_design_document`, `database_design_document`, `ui_schema` |
| Experience intent | `DesignDocs` | typed `ExperienceSpec` stored with the `ui_schema` document |
| Workflow bundle | `AgentGenerator` | generated workflow files, graph config, agent/tool contracts |
| App bundle | `AppGenerator` | generated app files, app schema, build tasks |
| Sandbox execution | E2B | ephemeral workspace only; never canonical |

Rule:

- upstream layers may invalidate downstream layers
- downstream layers must not silently rewrite upstream truth

---

## Change Classes

Revision routing should use four classes.

| Class | Meaning | Typical route |
|---|---|---|
| `patch` | Small localized change; no architectural impact | direct refinement against current artifact version |
| `design` | Visual, branding, information architecture, or UI schema change that does not alter the underlying concept | design/schema refinement, then selective rebuild |
| `feature` | New or changed capability within the same concept | scoped plan rebuild and scoped artifact regeneration |
| `core` | Change to target user, product identity, core value proposition, major domain model, or monetization premise | restart from `ValueEngine` |

Examples:

- `patch`: fix validation error, rename button label, adjust one endpoint call, change copy in one panel
- `design`: switch brand direction, change theme system, restructure dashboard layout, revise navigation
- `feature`: add reports page, add export capability, add role-based approval flow
- `core`: change from CRM to marketplace, change target user entirely, change business model, replace core data model

Brand changes are **not** automatically `core`.
They are `design` unless they imply a new target market or value proposition.

### Refinement Lanes

`ChangeClass` is the high-level routing superclass used by current
Refinement Engine routes:

- `patch`
- `design`
- `feature`
- `core`

Refinement also needs a second, more precise dimension:
`refinement_lane`. The lane describes the intent inside the superclass without
requiring a new workflow route for every product-specific request.

Recommended lanes:

| Lane | Typical superclass | Meaning |
|---|---|---|
| `ui_patch` | `patch` | Local UI copy, spacing, styling, or small behavior fix with no upstream intent change |
| `experience_design` | `design` | Page, navigation, layout, or `ExperienceSpec` change |
| `feature_addition` | `feature` | New capability within the current app concept |
| `integration` | `feature` | External connector or API readiness change |
| `conceptual_reframe` | `core` | Product purpose, audience, or value proposition change |
| `architecture_replan` | `core` or high-impact `feature` | Module, workflow, data ownership, or tenancy model replan |
| `managed_capability_change` | `feature` | Provider-neutral managed capability pack or façade boundary change |
| `data_model_migration` | `feature` or `core` | Persistence shape change requiring data contract and migration planning |

Examples:

- "Change button spacing" -> `patch` + `ui_patch`
- "Replace dashboard experience" -> `design` + `experience_design`
- "Add external analytics connector" -> `feature` + `integration`
- "Turn a marketplace into a subscription community" -> `core` + `conceptual_reframe`
- "Add managed analytics capability" -> `feature` + `managed_capability_change`
- "Change project schema" -> `feature` or `core` + `data_model_migration`

Early implementation may only document or persist `refinement_lane`; routing can
continue to use `ChangeClass` until lane-aware impact analysis is available.
Lanes must stay provider-neutral and app-agnostic. Do not encode vendor,
payment-provider, or hosted product names in the OSS lane taxonomy.

ExperienceSpec is the first-class experience intent artifact. Conceptual or
experience-level refinements should update ExperienceSpec before page
regeneration. Page YAML, route manifests, custom React routes, and shell
navigation are downstream of ExperienceSpec. Small UI patch requests may bypass
ExperienceSpec only when the request is bounded to existing owned paths and does
not change product or experience intent.

`ImpactSet.affected_bundle_paths` starts as deterministic path hints. The first
supported mapping is ExperienceSpec-driven UI impact:

- page intent maps to `ui/pages/*.yaml`, or concrete `ui/pages/{page}.yaml`
  entries when the current app bundle artifact has a `files_manifest`
- route ownership maps to `ui/route_manifest.json`
- navigation, shell, header, footer, or chrome changes map to
  `config/shell.json` when that file exists in the current manifest, or to the
  same path hint when no manifest exists
- custom route React and `ui/index.js` are included only when current artifact
  metadata already contains custom route files

These are conservative hints for scoping and review, not a replacement for the
artifact-family graph.

The second supported mapping is module/backend impact for `app_bundle`
refinements. When the request contains provider-neutral module or backend
signals such as module, action, API, endpoint, backend, schema, event,
reaction, notification, admin panel, permission, handler, service, repo, or
policy:

- known module ids are read from current artifact manifest paths such as
  `modules/{module_id}/module.yaml`
- if the request mentions one or more known module ids, impact is scoped to the
  canonical files already present under those modules
- module contract files include `module.yaml`, `contracts/*.yaml`,
  `backend/*.py`, and `runtime_extensions.yaml` only when the current manifest
  contains that runtime extension file
- if the request is module/backend-related but exact module ownership is
  unknown, the router emits conservative glob hints:
  `modules/*/module.yaml`, `modules/*/contracts/*.yaml`, and
  `modules/*/backend/*.py`

These module paths are deterministic review and scoping hints. They do not
replace module contract validation, and they do not yet model database
migration, managed capability façade, or integration readiness impact. Future
slices will add those path families separately.

The third supported mapping is managed capability façade impact for
`app_bundle` refinements. Generated apps consume managed capabilities through an
app-owned boundary:

```text
managed_capability
  -> app/services/integrations/{pack_id}_client.py
  -> modules/{facade_module_id}/
  -> ui/pages/*.yaml bound to /api/modules/{facade_module_id}/...
```

When the request refers to a managed capability, external adapter,
integration client, façade module, provider-backed surface,
external service, or neutral provider category such as analytics, reporting,
audit, or notification, the router can add managed capability path hints:

- adapter clients under `app/services/integrations/*_client.py`
- app-owned façade module files under `modules/{facade_module_id}/`
- dependent page YAML files when current manifest metadata shows a page binding
  to `/api/modules/{facade_module_id}/`
- `ui/pages/*.yaml` as a conservative hint only when the request is UI-facing
  and exact page binding is not available

Generated apps must not refine provider-owned internals. Provider
implementation remains outside the generated app artifact bundle. The app-owned
façade module and adapter client are the generated app refinement surfaces.

The fourth supported mapping is external integration impact for `app_bundle`
refinements. External integrations are distinct from managed capabilities:

```text
AppBuildPlan.external_integrations / task integration_needs
  -> IntegrationReadinessAgent
  -> app-scoped connector metadata and secret storage
  -> generated adapter/module code references connector ids and capabilities
```

When the request refers to an integration, connector, external API, external
service, API key, credential, provider, webhook, sync, import/export, or a
neutral provider category such as analytics, reporting, search, email, storage,
or CRM, the router can add integration path hints:

- adapter clients under `app/services/integrations/*_client.py`
- module files that declare or use the integration:
  `modules/{module_id}/module.yaml`, `backend/service.py`,
  `backend/schemas.py`, and `backend/policy.py`
- connector declaration or setup docs such as `config/integrations*.json` and
  `docs/integrations*.md`
- `ui/pages/*.yaml` or concrete page YAML files only for UI-facing setup or
  display requests

If a connector id is known from `app/services/integrations/{connector_id}_client.py`
and the request mentions that id, the router prefers the exact adapter path.
Without a manifest, it emits conservative hints for adapter, module, config, and
docs surfaces.

`ImpactSet` currently has no dedicated `integration_readiness_required` flag.
For this slice, readiness rerun is represented by integration path hints and a
scope summary note saying that integration readiness may need to be rechecked.
This does not change connector storage, readiness behavior, or secret handling.
Refinement scope must never include secret files or secret values; generated
apps reference connector ids/capabilities, not raw secrets.

The fifth supported mapping is data model migration impact for `app_bundle`
refinements. Generated app persistence changes stay intent-first:

```text
data_contract
  -> data/contract.json
  -> optional data/migrations/{migration_id}.json
  -> modules/{module_id}/backend/{schemas.py,repo.py,policy.py}
```

When the request refers to schema, fields, data models, database shape,
migrations, collections, tables, indexes, uniqueness, required or optional
fields, relations, references, foreign keys, tenant/workspace/owner scoping,
record archival, soft deletion, renames, type changes, added or removed columns,
or backfills, the router can add data-model path hints:

- `data/contract.json`
- exact `data/migrations/{migration_id}.json` files when present,
  otherwise the conservative `data/migrations/*.json` hint
- module persistence contract files:
  `modules/{module_id}/module.yaml`, `backend/schemas.py`, `backend/repo.py`,
  and `backend/policy.py`
- `modules/{module_id}/contracts/events.yaml` when emitted payloads may change
  and the file exists
- `modules/{module_id}/contracts/admin.yaml` when admin data-field displays may
  change and the file exists
- page YAML only when the request explicitly mentions UI, display, forms,
  tables, or related surface terms

If a known module id such as `projects`, `orders`, `customers`, `reports`, or
`tasks` is present in the current file manifest and the request names it, impact
is scoped to that module's persistence files. If module ownership is unknown,
the router emits conservative module globs:
`modules/*/backend/schemas.py`, `modules/*/backend/repo.py`,
`modules/*/backend/policy.py`, and `modules/*/module.yaml`.

Potentially destructive requests, including deleting or removing fields,
dropping tables, deleting records, purging data, renaming fields, changing
types, introducing required fields or unique constraints, or otherwise marking
a change irreversible/destructive, add a scope-summary warning:
destructive changes require explicit review. This is a review signal only. This
slice does not implement migration generation, migration execution, a migration
review workflow, runtime persistence changes, or `ctx.persistence` changes.

Data model refinements should trigger data contract validation, migration
plan validation, app validation, and explicit review for destructive migrations
before promotion. This generated-app persistence contract is separate from
hosted platform persistence. Refinement scope must never include secret paths
or secret values.

LLM profile tuning is deliberately out of scope until lanes and graph routing
stabilize. The profile ids below centralize configuration references, but this
document does not change model names, temperatures, or per-agent AG2 settings.

### LLM Profile Indirection

Refinement Engine and refinement agents must reference named LLM profiles instead
of scattering raw AG2 `llm_config` objects across individual capabilities.
`app/config/refinement_policy.yaml` owns the profile registry under
`llm_profiles`.

Allowed profile ids:

| Profile | Purpose |
|---|---|
| `classifier` | Classify refinement requests into stable `patch`, `design`, `feature`, or `core` classes |
| `impact_analyzer` | Support artifact impact and routing analysis |
| `planner_replanner` | Plan or replan higher-scope refinement work after routing |
| `codegen` | Generate scoped code or artifact patches |
| `reviewer_validator` | Review generated changes and validate contract conformance |

Capabilities reference profiles by id:

```yaml
schema_version: mozaiks.refinement.policy.v1
enabled: true
llm_profiles:
  classifier:
    purpose: Classify refinement requests.
    expected_behavior: deterministic structured classification
    llm_config:
      model: <configured-model>
      temperature: 0
classifier:
  enabled: true
  llm_profile: classifier
```

This is an indirection layer only. It does not tune models, change AG2
execution semantics, or introduce a new workflow. Existing raw
`classifier.llm_config` and `coding.llm_config` fallback remains valid for
workspaces that have not moved to profile references, but new Refinement Engine
configuration should prefer `llm_profile`.

Rules:

- unknown profile ids fail configuration validation
- capability references to undeclared profiles fail resolution clearly
- no per-agent hidden model overrides should be added for Refinement Engine or
  refinement lanes
- raw provider/model config may live inside the central profile registry, but
  code should pass the resolved `llm_config` to AG2 checkpoint runners
- profile tuning and lane-specific model assignment are future empirical work

### Refinement Smoke Coverage

The OSS test suite includes a deterministic refinement Refinement Engine smoke that
validates routing, impacted declarative families, `affected_bundle_paths`, and
LLM profile resolution without calling a live LLM and without mutating generated
app files. The smoke uses neutral app bundle manifests and fake classifier
outputs so it exercises the Refinement Engine resolver and config contracts only.

This smoke does not execute AppGenerator, run a refinement worker, create
migration files, or promote artifacts. Live classifier behavior, profile tuning,
and end-to-end refinement execution remain separate validation layers.

---

## Routing Rule

Natural-language refinement requests should not go straight into `InterviewAgent`, `PatternAgent`, or `AppPlanAgent`.

They should go through a Refinement Engine classifier:

```text
user request
  -> refinement classifier
  -> emit Refinement Engine event
  -> router selects re-entry point
  -> refinement or rebuild flow runs
```

Canonical app-build events:

- `app.patch_requested`
- `app.design_change_requested`
- `app.feature_change_requested`
- `app.core_change_requested`

The same shape should be used later for workflow-bundle refinement with a parallel family rather than overloading app events.

Important:

- this is not a single global prompt wrapped around every product request
- this is a builder-session harness step for refinement and other
  build-affecting requests
- the current backend entrypoint is `OrchestrationControlHarness`, which owns
  builder-context interception and delegates to narrower analyzers such as the
  refinement classifier
- the current decision layer is deliberately separate from the classifier so
  confirmation, clarification, and workflow fallback do not get buried inside
  one prompt response

Current runtime binding:

```text
Studio /api/workflows/trigger
  -> OrchestrationControlHarness
  -> request_submitted checkpoint
  -> route_requested checkpoint
  -> decision_requested checkpoint
  -> SessionRouter or coding worker or harness decision response
```

Runtime note:

- core now constructs a generic checkpoint runtime from the selected
  `harness.yaml`
- the first-party harness binds `request_submitted`, `route_requested`,
  `decision_requested`, `scope_requested`, and `coding_requested` through that
  runtime
- this keeps the checkpoint taxonomy declarative while still allowing the
  harness to compose checkpoint handlers deterministically
- refinement re-entry routing is no longer hardcoded to builder workflows in
  Python; the selected refinement harness declares app-owned artifact kinds and
  `workflow_sequence` targets per change class inside `harness.yaml`
- that keeps the harness runtime-owned while letting future apps declare their
  own artifact/output topology without becoming `factory_app` clones

Current simplified pack taxonomy:

- `config/harness.yaml`
  - top-level `harness`
  - top-level `routing`
  - inline `checkpoints[]`
- `prompts/*.yaml`
- `config/tools.yaml`
- optional `config/policies.yaml`

Each checkpoint declares:

- `event`
- `mode`: `ag2_structured_agent` or `deterministic_handler`
- `entrypoint`
- `prompt_id` for AG2 structured-agent checkpoints; null for deterministic handlers
- `output_contract` for AG2 structured-agent checkpoints; null for deterministic handlers
- optional `tool_ids`

There is no extra user-facing Refinement Engine `components` layer. The pack
declares what should run at each checkpoint.

The `routing` section is the canonical app-local declaration for harness
ownership:

- `default_artifact_kind`
- `artifacts[]`
  - `artifact_kind`
  - optional `label`
  - `routes.patch|design|feature|core.workflow_sequence`

The Refinement Engine derives affected workflows and artifact-family impact from
the referenced `workflow_sequence` in `extension_registry.json`. Do not
duplicate downstream workflow lists or artifact-family lists in
`harness.yaml`.

This keeps the runtime generic:

- `factory_app` can declare app-build artifacts like `concept`,
  `design_docs`, `workflow_bundle`, and `app_bundle`
- a future memo/planning app can declare artifacts like `market_research`,
  `financial_model`, or `executive_summary`
- the harness logic stays in `mozaiksai/control_plane`, while artifact
  ownership stays in the selected pack

Current refinement policy contract:

```yaml
schema_version: mozaiks.refinement.policy.v1
enabled: true
llm_profiles:
  classifier:
    llm_config:
      model: gpt-5-nano
      temperature: 0.0
  codegen:
    llm_config:
      model: gpt-5.2-codex
      temperature: 0.1
classifier:
  enabled: true
  llm_profile: classifier
coding:
  enabled: false
  llm_profile: codegen
```

Rules:

- `enabled` gates the Refinement Engine as a whole
- `classifier.llm_profile` selects the authoritative refinement-classification profile
- `coding.llm_profile` is reserved for the refinement worker loop, not for workflow-local AG2 execution

Current first-party pack paths:

- `factory_app/refinement_harness/config/harness.yaml`
- `factory_app/refinement_harness/config/tools.yaml`
- `factory_app/refinement_harness/config/policies.yaml`
- `factory_app/refinement_harness/prompts/*.yaml`
- `factory_app/refinement_harness/tools/*.py`

Current default classifier grounding:

- the selected refinement harness declares a `request_submitted` checkpoint
  with its own prompt and tool ids inline in `harness.yaml`
- the runtime now provides a generic `get_revision_context` tool that assembles:
  - persisted `SessionState`
  - app-declared routing metadata from the selected refinement harness
  - tracked artifact refs and latest artifact lineage
  - active change-request lineage when present
  - persisted summary payloads for runtime-owned summary artifacts such as
    `concept`, `build_plan`, `design_docs`, and `theme_capture`
  - one-level resolved `canonical_inputs_version` lineage so downstream bundle
    artifacts can expose the upstream artifacts they were built from
- when a `ChangeRequest` is persisted, the Refinement Engine marks the affected
  persisted artifact versions `stale` using the change-request id as the
  invalidation reason; it also propagates staleness transitively to all
  downstream artifact families using the declared `artifact_dependency_graph`
  (see [Artifact Staleness and Routing](../builder/artifact-staleness-and-routing.md))
- the `get_stale_artifact_families` Refinement Engine tool surfaces that stale set
  to the classifier at `request_submitted` so routing upgrades to the minimal
  necessary sequence automatically
- the first-party factory pack pairs that runtime context with
  `get_artifact_summary`
- app-specific packs may add extra tools, but the harness backbone should start
  from runtime-owned revision context rather than builder-only persistence
- that context is gathered before the model call and passed into the
  classifier prompt as canonical persisted runtime state
- this keeps refinement classification above workflow-local AG2 logic and off
  the individual workflow prompt surfaces

Current AG2 checkpoint execution boundary:

- `request_submitted`, `scope_requested`, `contract_surface_requested`, and
  `coding_requested` model calls run through
  `mozaiksai.core.adapters.AG2StructuredAgentRunner`
- AG2 owns the agent turn, middleware, observer, retry, and structured response
  mechanics for those checkpoints
- Mozaiks owns checkpoint context assembly, output validation after the AG2
  call, route selection, deterministic scope policies, surface dependency
  ordering, artifact persistence, and workflow launch decisions
- checkpoint prompts and tool ids remain declared in
  `factory_app/refinement_harness/config/harness.yaml`

Current first-party coding worker path:

- `coding.enabled` in `app/config/refinement_policy.yaml` gates the worker independently from the
  classifier
- the first-party factory pack declares a `coding_requested` checkpoint with its own
  prompt and tool access
- the first-party factory pack also declares a `scope_requested` checkpoint with its
  own prompt and tool access
- both now ground on the same runtime `get_revision_context` backbone rather
  than requiring builder-only `concept/design/build_state` tools
- Studio may short-circuit a refinement request into `execution_mode:
  coding_worker` when all of these are true:
  - the refinement is classified as a narrow `patch`
  - the artifact kind is `app_bundle` or `workflow_bundle`
  - `artifact_version_id` is present
  - either the trigger includes an explicit scoped `coding_request.files`
    payload or the `scope_requested` checkpoint can infer a bounded file set from
    artifact workspace context
- when it executes successfully, the worker returns concrete `updated_files`,
  validates the merged staged artifact workspace, and can persist a child artifact
  version for the refined bundle
- persisted child artifact versions enter Studio review as `draft`
- Studio review is now a first-class lifecycle step with:
  - diff preview against the parent artifact version
  - selected scope and coding summary
  - explicit `accept`, `reject`, and `promote` actions
- `accept` marks the validated child as the new `current` artifact version and
  supersedes the prior current version in that artifact family
- `reject` archives the child artifact version without changing the active
  runtime state
- `promote` restores an accepted/current artifact bundle into the runnable app
  root or workflow target and marks the linked refinement session as
  `promoted`
- the worker stays subordinate to Refinement Engine routing and does not masquerade
  as an AG2 workflow
- explicit file scope can now come from persisted artifact-bundle workbenches,
  not only from an in-flight workflow surface
- when explicit file scope is missing, the default scope selector uses
  `get_artifact_workspace_catalog` plus routing metadata to choose the
  narrowest safe files before the worker runs
- the selected refinement harness now also declares `policies.yaml`, which
  currently bounds inferred scope with:
  - `scope.max_selected_paths`
  - `scope.auto_apply_max_paths`
  - `scope.overflow_behavior`
- the default coding toolset now includes `get_artifact_workspace_scope`, which
  gives the worker a safe file tree and related-file previews around the
  selected files without collapsing into a full repo-global agent

Current first-party harness decision layer:

- after routing, the `decision_requested` checkpoint turns the typed route and
  optional scope result into a typed `HarnessDecision`
- Studio may now return `execution_mode: "harness_decision"` instead of
  launching a workflow chat when the correct next step is:
  - confirm a high-impact reroute such as `core_restart`
  - clarify local file scope before patching
  - continue into a recommended workflow fallback
- `clarify_scope` is now actionable when a bounded inferred scope already
  exists:
  - the decision may include `apply_proposed_scope`
  - a follow-up trigger with `harness_action.action_id=apply_proposed_scope`
    can continue into the coding worker without forcing the user to manually
    reselect files
- current first-party `decision_type` values are:
  - `workflow_reentry`
  - `core_restart`
  - `auto_patch`
  - `clarify_scope`
  - `fallback_workflow`
- each decision carries typed `actions[]` instead of ad hoc popup logic
- the current first-party action ids are:
  - `confirm_recommended_workflow`
  - `run_recommended_workflow`
  - `clarify_scope`
  - `review_patch`
- builder surfaces round-trip those actions back through
  `refinement_request.extra.harness_action`
- when launch is deferred, the runtime persists revision intent in
  `SessionRouterState` immediately and reuses the active
  `change_request_id` / `revision_id` for the follow-up action request
- that persisted pending decision now includes the replay contract the shell
  needs after refresh:
  - `trigger_source`
  - `requested_workflow_id`
  - `journey_id`
  - `context_variables`
  - `trigger_payload`
  - `selected_paths`
  - `clarification_question`
- the default refinement harness now declares this behavior as a dedicated
  `decision_requested` checkpoint in `harness.yaml`

Current first-party artifact workbench bridge:

- `AppGenerator` now registers `app_bundle` artifact versions for generated
  bundles
- Studio exposes `GET /api/studio/build/artifacts/{artifact_version_id}/bundle` to
  reopen a persisted bundle as a text-file workbench payload
- the Build history surface can open that bundle in `AppWorkbench`
- `AppWorkbench` can launch a scoped refinement request with
  `coding_request.files` sourced from the selected file editor state
- when no file is selected in `AppWorkbench`, the refinement harness can
  now fall back to scope proposal instead of forcing file selection first

Target workflow revision context contract:

- `ValueEngine`, `DesignDocs`, `AgentGenerator`, and `AppGenerator` should all
  declare one shared revision-context subset they are allowed to receive on
  reroute
- the target common subset is:
  - `build_mode`
  - `revision_scope`
  - `revision_id`
  - `change_request_id`
  - `artifact_kind`
  - `artifact_version_id`
  - `workflow_sequence`
  - `refinement_request`
  - `refinement_request_meta`
  - `screen`
  - `change_intent`
  - `impact_set`
  - `sequence_status`
  - `revision_origin_workflow`
- this is what lets a confirmed `core_restart` into `ValueEngine` preserve the
  request and typed Refinement Engine rationale without tripping
  `SESSION_LAUNCH_CONTEXT_KEY_REJECTED`
- important: a reroute into `ValueEngine` for `core` does **not** mean "treat
  this like a blank greenfield intake again." It is still a revision entry with
  prior concept, design, and bundle history available through the revision
  context.

This now uses the shared Context Graph substrate rather than a workflow-local
code-context subsystem:

- `AppContextGraph` is the canonical graph contract for app, artifact, file,
  symbol, module, page, workflow, agent, tool, risk, and provenance context
- `get_artifact_workspace_catalog` remains the deterministic workspace/file
  listing tool for persisted artifact workspaces
- `get_context_graph_catalog` adds graph-aware candidate files, matched nodes,
  relationship hints, and semantic annotation candidates for scope proposal
- `get_artifact_workspace_scope` remains the explicit scoped-file loader
- `get_context_graph_scope` adds graph-neighborhood context around selected
  files for the coding worker, including symbols, call edges, contract roles,
  and bounded semantic annotation requests

These Refinement Engine tool payloads stay inside the Refinement Engine checkpoint
request. They are not automatically copied into downstream workflow
`context_variables`. When a workflow needs graph-aware prompt context, its own
`before_chat` lifecycle loader rebuilds a compact `context_graph_pack` from the
seeded `artifact_version_id` and current `AppContextGraph`. The workflow
`context_graph_catalog` remains the richer diagnostic/tooling catalog; it is
not a prompt-injection fallback. Unavailable graph context is represented as a
prompt pack with `status: unavailable` and a reason such as `missing_app_id` or
`current_app_context_graph_unavailable`.

---

## Staged Execution Plan

Classification, routing, and impact analysis do not mutate app artifacts. Their
first executable output is a `RefinementExecutionPlan`: a Refinement Engine plan
that names the request, app, artifact kind, `ChangeClass`, `refinement_lane`,
workflow sequence, affected declarative families, affected bundle paths, named
LLM profiles, validation checks, warnings, and review gates.

Initial execution plans are non-mutating by default:

- `execution_mode: dry_run`
- `mutation_allowed: false`
- no workflow execution
- no `AppGenerator` rebuild
- no source app bundle writes
- no promotion of refined output

`execution_mode: staged` may be materialized through the Refinement Engine staging
helper. The helper creates an isolated staging workspace at the plan's
`staging_area`/`output_workspace` path, or `.refinement_staging/{request_id}`
when no explicit path exists. It writes a plan snapshot, affected-path status
manifest, and README. When an explicit `source_bundle_path` is supplied, it may
copy existing affected files into `workspace/{relative_app_path}` for review.

Staging still does not execute workflows, call a live model, apply patches,
rewrite app files, or promote refined output. The future progression is:
dry-run plan -> staged artifact workspace -> validation -> human approval ->
accept/promote. Human approval is required before applying destructive, data
model, or core changes.

Staging safety rules:

- source bundle files are read-only inputs
- writes are confined to the staging workspace
- affected paths must stay relative to the app bundle
- path traversal, absolute paths, and drive-qualified paths are skipped
- glob paths are recorded but not expanded
- secret-sensitive paths are skipped
- `mutation_allowed` remains `false`

### Staged Review Lifecycle

The staged workspace is separate from review state. A staged workspace can have a
`refinement_review.json` metadata record with this shape:

```json
{
  "request_id": "...",
  "status": "review_pending",
  "reviewer": null,
  "reviewed_at": null,
  "decision": null,
  "notes": null,
  "promotion_allowed": false,
  "source_bundle_path": "...",
  "staging_area": "...",
  "affected_bundle_paths": [],
  "mutation_allowed": false
}
```

Allowed review states are:

- `staged`
- `review_pending`
- `approved`
- `rejected`
- `promotion_ready`
- `promoted`

Current implemented transitions:

| From | To | Meaning |
|---|---|---|
| `staged` | `review_pending` | Workspace is ready for human/operator review |
| `review_pending` | `approved` | Reviewer accepted the staged result metadata |
| `review_pending` | `rejected` | Reviewer rejected the staged result metadata |
| `approved` | `promotion_ready` | Future promotion is allowed, but not executed |
| `promotion_ready` | `promoted` | Guarded source promotion applied the staged output |

Approval is not promotion. Rejection does not delete the staged workspace.
`promotion_ready` is the guard signal required for the explicit promotion step.
The current review helpers never copy staged files back to the source bundle,
never run workflows, never call live models, and never set `mutation_allowed`
to true. The `promoted` state is set only by guarded promotion after an
explicit confirmation gate.

The lifecycle is:

```text
dry_run -> staged workspace -> review_pending -> approved | rejected
approved -> promotion_ready -> promoted
```

### Scoped Execution Into Staging

Scoped refinement execution is the first mutation step, but its mutation scope is
only the staging workspace. The executor accepts explicit proposed changes in
the form `path -> new_content`, validates each path against the execution plan,
and writes only under:

```text
{staging_area}/workspace/{relative_app_path}
```

It also writes `execution_result.json` in the staging workspace. The result
records changed files, skipped files, `execution_mode: scoped_staging`,
`source_mutated: false`, and `mutation_scope: staging_only`.

Scoped execution is intentionally narrower than a coding worker:

- no live model call
- no workflow execution
- no full `AppGenerator` run
- no patch application to the source app bundle
- no promotion of staged changes
- no writes outside the staging workspace

For the first slice, scoped execution updates only staged files that already
exist in the workspace mirror and whose relative path is present in
`affected_bundle_paths`. Paths outside the impact set, missing staged files,
secret-sensitive paths, absolute paths, drive-qualified paths, globs, traversal
paths, and symlinks are skipped. Future work may replace the explicit change
provider with a real coding worker, but the same staging-only boundary must
remain.

### Coding Worker Into Staging

The coding worker does not write files directly. It produces structured change
records and those records are converted into `ScopedRefinementChange` objects
before they reach `apply_scoped_refinement_changes(...)`. That means the coding
worker can suggest edits, but the scoped execution helper still owns the only
write path into the staging workspace.

Current first-slice behavior is deterministic/test-only:

- explicit `path -> new_content` worker output is accepted
- the worker result must carry the same `request_id` as the plan and staging
  workspace
- all writes still go through scoped execution
- no source bundle writes are allowed
- no validation, review, acceptance, or artifact promotion happens here

Live/manual worker support is a future follow-up. When it exists, it must still
emit the same structured change records and feed them through the same staging
boundary.

### Guarded Promotion To Source Bundle

Promotion is the first source-mutation step and it stays separate from review
approval. The `promote_refinement_staging(...)` contract only applies when:

- the review record is `promotion_ready`
- `review.promotion_allowed` is `true`
- the caller passes `confirm=true`
- `dry_run=false`

`dry_run=true` validates and reports the files that would be promoted without
mutating the source bundle. When promotion is actually applied, backups are
written under:

```text
{staging_area}/backups/{relative_app_path}
```

Promotion only considers staged files that were copied into the workspace and
then updated by scoped execution. New files remain skipped in this slice, and
secret-sensitive, glob, traversal, absolute, and symlinked paths remain
blocked. Promotion is source mutation, so it must remain separate from review
approval, workflow execution, and runtime routing. The canonical hosted/studio
path now snapshots the staged workspace into a draft `app_bundle`
`ArtifactVersion` before any later accept/promote step. Direct filesystem
promotion remains the local/dev fallback, not the canonical hosted path.

### Draft App Bundle Artifact Version From Staged Refinement

`create_draft_app_bundle_from_staged_refinement(...)` packages the staged
workspace into a bundle zip, stores it as a draft `app_bundle`
`ArtifactVersion`, and threads lineage plus refinement metadata into
`commit_metadata.metadata`. The artifact uses:

- `artifact_kind="app_bundle"`
- `artifact_key="app_bundle"`
- `lifecycle_status="draft"`
- `parent_version_id` pointing at the source/current app bundle version when
  available

Patch-ness stays in metadata, not in a separate artifact kind. The staged
refinement snapshot carries:

- `request_id`
- `change_class`
- `refinement_lane`
- `workflow_sequence`
- `affected_declarative_families`
- `affected_bundle_paths`
- `validation_evidence`
- review metadata
- scoped execution metadata
- promotion policy decisions

The staged bundle is still not current until the staged-refinement acceptance
helper calls `ArtifactStore.accept_artifact_version(...)`. For staged
refinement drafts, the guarded path is
`accept_staged_refinement_artifact_version(...)`, which verifies
`promotion_ready` + `promotion_allowed=true` before calling
`ArtifactStore.accept_artifact_version(...)`. Acceptance marks the draft
`app_bundle` CURRENT and records safe audit metadata, but it still does not
restore the runnable root.

After acceptance, Studio `promote` restores the bundle zip into the runnable
app root. The promote path is app-bundle-only, verifies that the accepted
artifact is still `CURRENT`, requires a non-empty file manifest, and uses safe
bundle extraction that rejects path traversal, absolute paths, drive-qualified
paths, and symlinked entries while skipping staging metadata files and backup
trees (`refinement_plan.json`, `affected_paths.json`, `refinement_review.json`,
`execution_result.json`, and `backups/`). That preserves the split between
review, artifact lineage, and source mutation. `mark_refinement_promoted(...)`
remains reserved for that later source-promotion step.

### Promotion Policy Gate

Filesystem safety is not enough on its own. After a staged file passes path and
workspace checks, `promotion.py` consults a lane-aware promotion policy before
copying anything back into the source bundle. That policy decides whether a
path may be promoted directly, must be regenerated from an upstream artifact,
or is blocked until a replan or validation step happens.

The policy modes are:

- `direct_leaf_patch`
- `staged_generated_artifact`
- `artifact_version_promotion_required`
- `blocked_requires_replan`
- `blocked_requires_validation`
- `blocked_requires_upstream_artifact`

Current lane rules are:

- `ui_patch` can directly promote narrow UI leaf files when the scope stays UI
  only.
- `experience_design` requires ExperienceSpec evidence before page, route, or
  shell changes can be promoted directly.
- `data_model_migration` requires data contract or migration artifact
  evidence before `repo.py`, `schemas.py`, or `policy.py` can be promoted.
- `conceptual_reframe` and `architecture_replan` block direct file promotion
  and require a replan.
- `managed_capability_change` only permits app-owned adapter, facade, or page
  paths; hosted/provider internals are blocked.
- `integration` permits adapter/client and app-owned module files, but never
  secrets or credential material.

`dry_run=true` reports the policy decisions without mutating anything. Actual
promotion copies only the files whose policy decision is `allowed=true`; blocked
files remain listed with a policy reason.

The validation plan is derived from `ImpactSet.affected_bundle_paths` and the
selected lane. Current validation item ids are:

| Validation item | Required when |
|---|---|
| `route_component_validation` | UI pages, route manifest, shell config, or experience surfaces are in scope |
| `ui_theme_primitive_validation` | UI schema, theme, primitive, or design surfaces are in scope |
| `experience_spec_update` | Experience-level changes require upstream ExperienceSpec evidence |
| `app_bundle_validation` | Experience and bundle-level promotion requires bundle validation evidence |
| `module_contract_validation` | Module manifests, contracts, or backend handlers are in scope |
| `integration_readiness_validation` | Connector, adapter, or integration files are in scope |
| `data_contract_validation` | Data contract artifacts are in scope |
| `migration_plan_validation` | Migration plan artifacts or destructive database changes are in scope |
| `managed_facade_boundary_validation` | Managed capability adapter, facade module, or page paths are in scope |

### Validation Evidence

Validation checks run outside the promotion step. Promotion consumes explicit
validation evidence, but it does not execute validators automatically and it
does not treat empty placeholders as proof of success.

The current validation evidence shape is:

- `completed`
- `failed`
- `warnings`
- `artifacts`
- `checked_at`
- `source`

`completed` and `failed` are matched against the lane-aware validation
requirements above. `artifacts` carries the upstream artifact evidence the
policy needs to distinguish direct leaf patching from upstream regeneration.
If required evidence is missing, `dry_run=true` reports the missing validation
set and actual promotion blocks the apply.

### Validation Runner Orchestration

Staged refinement workspaces can be checked by
`run_refinement_validations(plan, staging_result, scoped_result=None, *, selected=None)`.
That runner inspects the staged workspace and emits explicit
`ValidationEvidence` for promotion. It is deterministic and source-level only:
it reads staged files, runs the static contract audits that already exist, and
records skipped validators without pretending they passed.

Key rules:

- validation runs happen before promotion
- skipped validators do not count as completed
- failed validators are recorded explicitly in `failed`
- promotion consumes evidence but does not execute validators
- validators that are not implemented yet remain warnings or skipped results
- the runner does not mutate source files, staged files, workflows, or app-generator state
- this runner belongs to the staged patch promotion lane; conceptual_replan carry-forward preservation stays in the generated-artifact lane and does not feed this validator path

The runner currently emits `experience_spec_validation`; the promotion policy
accepts that as an alias for the older `experience_spec_update` validation name
until the contract is fully renamed across the stack.

### Deterministic Staged Patch Smoke

The staged patch lane can be exercised end to end without live models or
workflow execution:

```text
request
-> RefinementExecutionPlan
-> staging workspace
-> scoped execution into staging
-> validation evidence
-> review_pending / approved / promotion_ready
-> draft app_bundle ArtifactVersion
-> accept CURRENT
-> Studio restore runnable root
```

This smoke stays entirely in the staged patch promotion lane. It does not use
`conceptual_replan`, carry-forward preservation, AppGenerator execution, or
live workflow routing. The source bundle remains the source of truth until the
accepted `CURRENT` `app_bundle` is explicitly restored into the runnable app
root.

The plan is a handoff contract between the Refinement Engine and later execution
layers. It is not runtime authority and does not replace workflow routing,
module dispatch, event routing, permissions, connector storage, or generated app
database access.

---

## Current Typed Refinement Engine Contracts

The Refinement Engine should operate on six typed artifacts:

| Contract | Purpose |
|---|---|
| `RefinementRequest` | Canonical incoming refinement payload: optional route hint, artifact lineage, raw request text, source surface |
| `ChangeIntent` | Typed classification result used for routing and later review/persistence |
| `ImpactSet` | Typed downstream scope summary: affected workflows, declarative families, restart point, replanning/rebuild flags |
| `RefinementRoutingDecision` | Deterministic re-entry decision plus workflow seed context |
| `HarnessDecision` | Typed builder-surface continuation result: auto-run, clarify, fallback, or confirm before launch |
| `RefinementExecutionPlan` | Non-mutating dry-run/staged handoff plan with profiles, affected paths, validation checks, warnings, and review gates |

These contracts live above workflow-local AG2 handoffs.

The runtime may still seed workflow context with convenience fields such as:

- `change_class`
- `artifact_kind`
- `artifact_version_id`
- `refinement_request`

But the Refinement Engine itself should reason from the typed contracts, not from a
loose bundle of free-form strings.

`HarnessDecision` is the bridge between Refinement Engine reasoning and builder UX.
It is what lets the platform render a structured decision card instead of
falling back to:

- a dropdown for `patch | design | feature | core`
- a generic popup
- or silent rerouting with no explanation

`declared_change_class` inside `RefinementRequest` is advisory only.

It may be supplied by UI as a route hint, but the authoritative classification
comes from the backend Refinement Engine model call.

`ChangeIntent.source` should stay explicit:

- `llm` when the backend classifier produced the authoritative class used for routing

The typed contract should stay stable even if the model prompt or provider
changes later.

---

## Revision UX Model

Builder UX should stay simple even though routing remains typed internally.

User-facing distinction:

- revisit the build plan
- make a targeted change

Runtime distinction:

- `patch`
- `design`
- `feature`
- `core`

Rules:

- the harness classifies the request semantically first
- build progress or sequence completion does **not** determine the change class
- session/build state influences the follow-up action, not the semantic class
- a `core` or high-impact `feature` request may require workflow re-entry
  before the overall build sequence is complete
- a `patch` request after bundle delivery should stay local unless validation
  forces scope widening

That means:

- "add blockchain" during `AppGenerator` may still route back to
  `ValueEngine` when the classifier determines the concept, value
  proposition, or build plan changed
- "change the hero title" after bundle delivery stays in local refinement or a
  narrow app-bundle re-entry

The user should never have to pick `patch | design | feature | core`
manually as the main path. That taxonomy is runtime-owned.

---

## Re-Entry Matrix

The router should choose the smallest valid re-entry point.

| Change class | Re-entry point | Outcome |
|---|---|---|
| `patch` | refinement workflow or targeted refinement agent | edit scoped files only |
| `design` | `DesignDocs` or `AppSchemaAgent`-style design refinement flow | regenerate design-owned artifacts, then rebuild affected frontend files |
| `feature` | scoped planner rebuild using current canonical concept + design state | regenerate only affected tasks / units |
| `core` | `ValueEngine` | create new concept revision and mark downstream artifacts stale |

Important:

- workflow sequence metadata does **not** classify the request
- selected workflow sequence metadata supplies declared execution and artifact
  impact after classification
- transitions do **not** decide rebuild scope
- AG2 handoffs do **not** own Refinement Engine routing

Those are all downstream consumers of the routing decision.

---

## E2B Contract

E2B is the execution workspace for refinement, validation, and preview.

It is **not** the canonical artifact store.

E2B responsibilities:

- load a persisted artifact version into a runnable workspace
- let refinement agents inspect and modify scoped files
- run validation, tests, and preview
- expose ephemeral preview URLs and execution logs

Persistence responsibilities:

- store the committed artifact version
- store the change request and classification
- store the accepted patch result
- track which sandbox session was attached to which artifact version

Required rule:

- every committed refinement must persist back out of E2B into a new artifact version

If a sandbox dies, the artifact history must still be intact.

---

## Refinement Units

Refinement must operate on explicit units, not vague prose.

Canonical unit shape:

```yaml
unit_id: str
owner_workflow: str
initial_agent: str
description: str
owned_paths: [str]
depends_on: [str]
acceptance_criteria: [str]
```

This is already close to `AppGenerator`'s `build_tasks`.

Rules:

- refinement agents receive one or more units
- agents must stay inside `owned_paths`
- cross-unit changes require the router to widen scope explicitly
- validation must check the unit's `acceptance_criteria`

`AppGenerator` impact:

- keep `build_tasks` as the canonical refinement unit source
- do not downgrade or remove `owned_paths` / `acceptance_criteria`

`AgentGenerator` impact:

- add an equivalent refinement-unit contract for generated workflow bundle parts
- units should cover files like `agents.yaml`, `transition_graph.yaml`, `tools.yaml`, `context_variables.yaml`, `ui/*`, and workflow-local orchestration files

---

## Prompt And Authoring Implications

This architecture changes authoring expectations.

### Initial generator workflows

`ValueEngine`, `DesignDocs`, `AgentGenerator`, and `AppGenerator` should remain focused on **first-pass compilation**.

Do not overload their prompts so they also become universal revision agents.

Their prompt responsibilities are:

- produce canonical state
- define clean ownership boundaries
- emit structured metadata needed for later refinement

### Refinement workflows

Refinement should use dedicated agents or dedicated workflow modes.

Those agents may still run through AG2, but they run only after the control
plane has already classified the change and chosen the refinement scope.

Their prompts should:

- read the current artifact version, not assume a fresh project
- read the classified change request
- read scoped refinement units
- preserve unaffected files
- explain why scope widening is needed when they cannot stay local

Their outputs should stay structured:

- `PatchPlan`
- `PatchResult`
- `ScopeExpansionRequest`
- `ValidationResult`

### Generated workflow prompts

Generated prompts should be written so they can consume persisted upstream context without re-interviewing the user.

That means:

- prefer context-driven objectives over hardcoded fresh-start assumptions
- keep agents modular around inputs and outputs
- avoid prompt text that assumes "build from scratch" when the task is really "modify owned files"

---

## Persistence Model

The Refinement Engine needs durable records.

Minimum records:

### `ChangeRequest`

- `change_request_id`
- `app_id`
- `artifact_kind`
- `artifact_version_id`
- `raw_user_request`
- `classification`
- `scope`
- `router_decision`
- `created_at`

### `ArtifactVersion`

- `artifact_version_id`
- `app_id`
- `artifact_kind`
- `parent_version_id`
- `source_workflow`
- `canonical_inputs_version`
- `files_manifest`
- `validation_status`
- `created_at`

### `RefinementSession`

- `session_id`
- `artifact_version_id`
- `sandbox_id`
- `change_request_id`
- `status`
- `preview_url`
- `started_at`
- `ended_at`

Downstream invalidation rule:

- a new `core` concept revision marks prior design/build artifact versions stale
- a new `design` revision marks prior design-derived app bundle versions stale

Nothing should rely on transcript scraping to reconstruct this.

---

## Sequence Interaction

Workflow sequences still matter, but only after the Refinement Engine decides re-entry.

Correct split:

- workflow sequences define major phase order
- transitions define entry or inter-phase user decisions
- the Refinement Engine decides what phase to re-enter

Example:

- `core` change -> start a fresh `ValueEngine` revision, then downstream phases become stale
- `design` change -> resume at `DesignDocs` or a design refinement workflow, then rebuild affected app artifacts
- `feature` change -> resume at a scoped planner step, then run affected task batches
- an in-progress build and a completed build both use the same routing matrix;
  the difference is whether the session's `sequence_status` is already
  `completed` or still `in_progress`

So the answer to "does this belong in journey sequencing?" is:

- no for classification
- yes only after classification, as a consumer of the routing decision

---

## Required Cleanups In The Current Platform Flows

These current behaviors should be removed when the Refinement Engine lands:

- user-change loops that send delivered bundles back to `InterviewAgent`
- post-delivery change routing that depends on ordinary string handoff logic
- local-only artifact editor behavior as the main refinement mechanism
- full workflow reruns for small changes

The build UX should become:

1. initial compile
2. review
3. refine in place
4. only escalate upstream when the classifier says the change is wider

---

## Production-Ready Direction

The production-ready path is:

- initial structured generation remains strict and typed
- post-generation refinement becomes its own routed system
- E2B becomes a workspace and validator, not the truth store
- persistence tracks versions, sessions, and invalidation
- generator prompts emit better boundaries, not more mixed responsibilities

That produces a better DX than forcing every change through `AgentGenerator` or `AppGenerator` from scratch, and it avoids hiding architectural resets behind casual chat handoffs.

---

## Implementation Reference

### Python API

**Location:** `mozaiksai/control_plane/implementations/refinement_router.py`

The module exposes a framework-owned refinement resolver. Studio and Mozaiks wire
it into SessionRouter through the runtime trigger-route resolver seam, so the
runtime stays policy-agnostic while the shared generation layer owns create and
refinement routing.

```python
from mozaiksai.control_plane import (
    ChangeClass,     # Enum: patch | design | feature | core
    ArtifactKind,    # Enum: app_bundle | workflow_bundle | design_docs | experience_spec | concept
    RefinementRequest,
    RefinementTriggerRouteResolver,
    get_refinement_trigger_route_resolver,
)

resolver = get_refinement_trigger_route_resolver()  # module-level singleton

decision = await resolver.route(RefinementRequest(
    artifact_kind=ArtifactKind.APP_BUNDLE,
    artifact_key="app_bundle",
    artifact_version_id="v3",
    raw_user_request="Fix the login redirect",
    app_id="abc123",
))

# decision.workflow_id      → "AppGenerator"
# decision.context_seed     → {"build_mode": "revision", "revision_scope": "patch", ...}
# decision.explanation      → "Applying a targeted patch to scoped app files."
# decision.is_full_restart  → False
```

**`RefinementRequest` fields:**

| Field | Type | Required | Notes |
|---|---|---|---|
| `artifact_kind` | `ArtifactKind` | Yes | app_bundle / workflow_bundle / design_docs / experience_spec / concept |
| `artifact_key` | `str` | No | Defaults to the artifact kind when omitted |
| `artifact_version_id` | `str` | No | Version to load in re-entry workflow |
| `raw_user_request` | `str` | No | Passed as `refinement_request` context variable |
| `app_id` | `str` | No | For logging and audit record |
| `declared_change_class` | `ChangeClass` | No | Optional UI hint only; not authoritative |

**`RoutingDecision` fields:**

| Field | Type | Notes |
|---|---|---|
| `workflow_id` | `str` | Workflow to invoke |
| `workflow_sequence` | `str \| null` | Workflow sequence to bind this revision run to |
| `context_seed` | `dict` | Merged into `context_variables` for the new session |
| `explanation` | `str` | Human-readable reason for the routing choice |
| `is_full_restart` | `bool` | True for `core` changes — restarts from ValueEngine |

### Default Routing Table

The built-in factory pack covers all four change classes x four artifact kinds:

| Change class | Artifact kind | Workflow sequence | Entry workflow | Full restart |
|---|---|---|---|---|
| `patch` | `app_bundle` | `app_revision` | `AppGenerator` | No |
| `design` | `app_bundle` | `app_surface_revision` | `DesignDocs` | No |
| `feature` | `app_bundle` | `app_revision` | `AppGenerator` | No |
| `core` | `app_bundle` | `full_rebuild` | `ValueEngine` | Yes |
| `patch` | `workflow_bundle` | `workflow_patch` | `AgentGenerator` | No |
| `design` | `workflow_bundle` | `workflow_revision` | `AgentGenerator` | No |
| `feature` | `workflow_bundle` | `workflow_revision` | `AgentGenerator` | No |
| `core` | `workflow_bundle` | `full_rebuild` | `ValueEngine` | Yes |
| `patch` | `design_docs` | `design_patch` | `DesignDocs` | No |
| `design/feature` | `design_docs` | `design_revision` | `DesignDocs` | No |
| `core` | `design_docs` | `full_rebuild` | `ValueEngine` | Yes |
| `patch` | `concept` | `concept_patch` | `ValueEngine` | No |
| `design/feature` | `concept` | `full_rebuild` | `ValueEngine` | Yes |
| `core` | `concept` | `conceptual_replan` | `ValueEngine` | Yes |

`conceptual_replan` runs the same workflow chain as `full_rebuild`
(ValueEngine → ThemeCapture → DesignDocs → AgentGenerator → AppGenerator)
but is semantically distinct so future work can inject preservation context
(`carry_forward_modules`). Use `full_rebuild` only for a complete reset with
no preservation intent.

#### conceptual_replan Context Seed

When the router resolves to `conceptual_replan`, it injects additional fields
into the context seed passed to the launched workflow chain:

| Field | Source | Default | Description |
|---|---|---|---|
| `pivot_description` | `raw_user_request` | — | User's change request text verbatim. Always present. |
| `preserve_families` | `extra.preserve_families` | `["brand"]` | Artifact families to preserve. ThemeCapture should use the existing brand as a starting point rather than regenerating from scratch. |
| `existing_concept_ref` | `extra.existing_concept_ref` | omitted | Artifact version id of the concept at pivot time. Advisory reference only. |
| `previous_brand_ref` | `extra.previous_brand_ref` | omitted | Artifact version id of the brand/theme_config at pivot time. |
| `previous_app_bundle_ref` | `extra.previous_app_bundle_ref` | omitted | Artifact version id of the app bundle at pivot time. Used by `get_carry_forward_candidates` to inspect the previous workspace. |
| `carry_forward_modules` | `extra.carry_forward_modules` (explicit override) or auto-populated from `previous_app_bundle_ref` | `[]` | Advisory list of module ids from the previous app bundle. Explicit client list always wins. When absent and `previous_app_bundle_ref` is present, the router auto-populates from `get_carry_forward_candidates`. Not a merge instruction — no file copy occurs. |

These fields are declared as state variables in `ValueEngine/context_variables.yaml`,
`DesignDocs/context_variables.yaml`, and `AppGenerator/context_variables.yaml`.
`full_rebuild` and all other sequences do not receive these fields.

`carry_forward_modules` reaches `AppPlanAgent` only.  `AssemblyAgent` does not
receive it directly; carry-forward file preservation is driven instead by
`carry_forward_decisions` on `AppBuildPlan` through the Phase 7A resolver (see
[Phase 7A: Declarative Contract Preservation](#phase-7a-declarative-contract-preservation)).

**AppPlanAgent carry_forward_modules semantics:**

- Treat the list as advisory preservation hints, not an inclusion mandate.
- Preserve a carry-forward module only if it is domain-generic and still fits
  the new concept (e.g. notifications, files/media, audit/activity,
  audit_log when audit history still applies).
- Omit domain-specific modules from the old concept (e.g. old CRM
  contacts/pipeline, old domain pages, concept-specific workflows). Note the
  omission in build plan rationale.
- Do not reference or copy files from `previous_app_bundle_ref` directly in
  the prompt. File preservation for `reuse` decisions is handled automatically
  by the Phase 7A resolver after AssemblyAgent runs.

#### Carry-forward module inventory (Phase 2 + Phase 3)

`get_carry_forward_candidates` is a registered read-only Refinement Engine tool at
`factory_app/refinement_harness/tools/get_carry_forward_candidates.py`.

It is available at the `route_requested` checkpoint — where `conceptual_replan`
routing is determined and `_build_context_seed()` runs.

**What it does:**

1. Reads `context.extra["previous_app_bundle_ref"]` to locate the prior artifact.
2. Calls `load_artifact_workspace()` to read the previous app bundle into a
   `file_map`.
3. Calls `extract_module_inventory(file_map)` to produce a structured
   `ModuleInventoryEntry` list.
4. Returns `{ modules, count, source_artifact_version_id, warnings }`.

**What it does NOT do:**

- No file writes.
- No artifact writes.
- No LLM calls.
- No carry-forward merge or file copy.

**Failure behavior:** returns empty `modules` list plus a diagnostic `warnings`
entry on any failure (missing ref, workspace unavailable, load exception). Never
raises.

**Auto-population behavior (Phase 3):**

`_build_context_seed()` auto-populates `carry_forward_modules` using the
following priority:

1. **Explicit client list wins.** If `extra.carry_forward_modules` is a list
   (including an empty list), use it as-is. The tool is not called.
2. **Auto-extract when ref is present.** If `extra.carry_forward_modules` is
   absent and `extra.previous_app_bundle_ref` is set, the router calls
   `get_carry_forward_candidates` via `_auto_carry_forward_resolution()` and
   populates `carry_forward_modules` with the returned module ids.
3. **Default to empty.** If neither is present, `carry_forward_modules = []`.

When auto-extraction produces warnings (e.g. workspace unavailable),
`carry_forward_warnings` is added to the context seed for diagnostic
traceability. When extraction succeeds without warnings, the key is absent.

`_auto_carry_forward_resolution()` uses the pack executor mechanism
(`resolve_control_plane_tool_entrypoint`) to call the registered tool at runtime
without a direct import from `factory_app`. It never raises — returns
`([], [warning])` on any failure.

**Module id validation (auto-extracted ids only):**

After extraction, every module id returned by `get_carry_forward_candidates` is
validated and sanitized by `_sanitize_carry_forward_module_ids()` before being
placed in `carry_forward_modules`. Explicit override lists (`extra.carry_forward_modules`)
are used as-is and are not validated.

Validation rules applied to each auto-extracted id:

| Rule | Rejected examples |
|---|---|
| Must not be empty | `""` |
| Must not be `"."` or `".."` | `"."`, `".."` |
| Must not contain `"/"` or `"\\"` | `"modules/settings"`, `"modules\\settings"` |
| Must match `^[a-zA-Z0-9_-]+$` | `"my module"`, `"mod@ule"` |
| Must not exceed 80 characters | Any id longer than 80 chars |

Each rejected id:

- is excluded from `carry_forward_modules`
- adds a `"carry_forward_invalid_module_id: ..."` string to `carry_forward_warnings`
- is logged at `WARNING` level

Duplicates are removed while preserving insertion order (first occurrence kept).

**Remaining future work:** Artifact content backfill CLI for artifacts written before Phase D.

#### Per-module carry-forward decisions in AppBuildPlan (Phase 6)

AppPlanAgent records its carry-forward planning intent in a structured field
`carry_forward_decisions: list[CarryForwardDecision]` on `AppBuildPlan`.

**`CarryForwardDecision` fields:**

| Field | Type | Description |
|---|---|---|
| `module_id` | `str` | Module directory name from the previous app bundle. |
| `decision` | `reuse \| adapt \| regenerate \| drop` | Planning intent for this candidate. |
| `reason` | `str` | Non-empty explanation for the decision. |
| `source` | `carry_forward_candidate \| human_override \| planner` | What drove the decision. |
| `affected_build_tasks` | `list[str]` | Task ids from `build_tasks` related to this decision. Empty for `drop`. |

**Decision values:**

| Value | Meaning |
|---|---|
| `reuse` | Preserve the module's role in the new plan. Does not copy old files. |
| `adapt` | Create a new/updated module task inspired by the prior contract. |
| `regenerate` | Capability still needed; generate a fresh implementation from scratch. |
| `drop` | Module no longer fits the new concept. |

**Semantics:**

- `carry_forward_decisions` defaults to `[]` for non-conceptual builds.
- Populated only during `conceptual_replan` or when `carry_forward_modules` is present.
- Modules with `decision == "reuse"` are eligible for Phase 7A declarative
  contract preservation by the `resolve_carry_forward_preservation` resolver
  (see [Phase 7A: Declarative Contract Preservation](#phase-7a-declarative-contract-preservation)).
  Modules with `decision == "adapt"`, `"regenerate"`, or `"drop"` receive no
  file preservation.
- `affected_build_tasks` entries must reference existing `build_tasks` task ids
  when provided. Empty lists and omitted keys are both valid.
- AppPlanAgent emits one entry per carry-forward candidate. `source` should be
  `carry_forward_candidate` when driven by `carry_forward_classification`,
  `planner` when driven by AppPlanAgent's own reasoning, or `human_override`
  when the client explicitly instructed reuse or drop.

**Validation** (`factory_app/workflows/AppGenerator/tools/app_build_plan.py`):

- `module_id` must be non-empty.
- `decision` must be one of `reuse`, `adapt`, `regenerate`, `drop`.
- `reason` must be non-empty.
- `source` must be one of `carry_forward_candidate`, `human_override`, `planner`
  when provided.
- `affected_build_tasks` entries must exist in `build_tasks` task ids.
- Plans with no `carry_forward_decisions` key pass unchanged (field defaults to `[]`).

#### Carry-forward module contract read-back (Phase 4)

`read_carry_forward_module_contract` is an AG2 tool registered in
`factory_app/workflows/AppGenerator/tools.yaml` under AppPlanAgent.

Core implementation lives at
`factory_app/refinement_harness/tools/read_carry_forward_module_contract.py`.
The AppGenerator entry point is a thin wrapper at
`factory_app/workflows/AppGenerator/tools/read_carry_forward_module_contract.py`.

**What it does:**

AppPlanAgent may call this tool during `conceptual_replan` to inspect specific
contract files from a carry-forward candidate in the previous app bundle,
before deciding whether to reuse, adapt, or regenerate a module.

Input parameters (from AppPlanAgent):

| Parameter | Type | Description |
|---|---|---|
| `module_id` | `str` | Module directory name to inspect (e.g. `notifications`, `audit_log`). |
| `files` | `list[str] \| null` | Contract filenames to read. When `null`, all allowed files present in the workspace are returned. |

Context variables read by the tool:

| Variable | Description |
|---|---|
| `app_id` | The app being replanned. |
| `previous_app_bundle_ref` | Artifact version id of the prior bundle. |

Output:

| Field | Description |
|---|---|
| `module_id` | Echoed back. |
| `files` | `{filename: content}` dict for returned files. |
| `available_files` | All allowed contract files present in the workspace for this module. |
| `missing_files` | Requested files not found in the workspace. |
| `warnings` | Diagnostic messages for missing refs, disallowed paths, or load failures. |

**Allowed files** (relative to `modules/{module_id}/`):

- `module.yaml`
- `runtime_extensions.yaml`
- `contracts/events.yaml`
- `contracts/reactions.yaml`
- `contracts/notifications.yaml`
- `contracts/settings.yaml`
- `contracts/admin.yaml`
- `contracts/profile.yaml`

**Disallowed:**

- `backend/*.py` — backend Python source is not exposed in this phase.
- Any path outside `modules/{module_id}/` — path traversal is rejected with a warning.
- Arbitrary filenames not in the allowed set.

**Read-only semantics:**

- No file writes.
- No artifact writes.
- No LLM calls.
- No file copy or merge.
- Contract content returned to AppPlanAgent is advisory context only.

**Registration:** AppPlanAgent only (`auto_tool_call: false`). Not registered
as a Refinement Engine checkpoint tool. Not available to any other workflow agent.

**AppPlanAgent advisory use:**

During `conceptual_replan`, AppPlanAgent receives `carry_forward_modules` as an
advisory list. Each entry includes `carry_forward_classification` and
`carry_forward_reasons`. For modules classified `needs_adaptation`, AppPlanAgent
may call `read_carry_forward_module_contract` to inspect their prior contracts
before deciding. The agent still does not copy prior module files, does not
merge content, and does not inspect `backend/*.py` source.

#### Carry-forward reuse-fit classification (Phase 5)

`ModuleInventoryEntry` includes a deterministic advisory reuse-fit
classification for every module returned by `get_carry_forward_candidates`.

**Fields added to `ModuleInventoryEntry`:**

| Field | Type | Description |
|---|---|---|
| `carry_forward_classification` | `Literal["safe_carry_forward", "needs_adaptation", "regenerate"]` | Deterministic advisory class. |
| `carry_forward_reasons` | `list[str]` | Non-empty list of human-readable reasons for the classification. |

**Classification values:**

| Class | Meaning |
|---|---|
| `safe_carry_forward` | Module is known to be generic/infrastructure (e.g. settings, notifications, audit, auth, users, organizations, audit_log). Advisory: carry forward without modification or with minor schema review. |
| `needs_adaptation` | Module has persistence, admin panels, reactions, runtime extensions, or domain events that may not fit the new concept. Review required before reuse. |
| `regenerate` | Module id contains a domain-specific fragment (e.g. project, task, order, lead, pipeline, invoice, campaign, booking) strongly implying it was built for the old concept. Prefer regeneration. |

**Classification logic** (`classify_module_carry_forward` in
`factory_app/refinement_harness/tools/_module_inventory.py`):

Priority (highest to lowest):

1. **`regenerate`** — `module_id` contains a known domain-specific fragment
   from `_DOMAIN_FRAGMENTS`. Presence of persistence or heavy CRUD actions
   reinforces but does not change the class.
2. **`safe_carry_forward`** — `module_id` matches `_SAFE_MODULE_IDS` (exact
   lower-case match), OR the module has infrastructure-only signals
   (settings/notifications/profile with no persistence, no admin, no reactions,
   no events).
3. **`needs_adaptation`** — default for everything else. Any persistence,
   admin, reactions, runtime extensions, events, or CRUD-heavy action mix
   triggers this class.

**Advisory semantics.** Classification is deterministic and conservative.
AppPlanAgent remains the final planner and may override any advisory class
based on the new concept. No file copy, merge, or automatic preservation occurs.
`carry_forward_classification` is surfaced in the `modules[]` list returned by
`get_carry_forward_candidates`, available to any consumer that calls `model_dump()`
on a `ModuleInventoryEntry`.

#### Architecture-level LLM profile signal

When the router resolves to `conceptual_replan` or `full_rebuild`, it injects
an additional field into the context seed:

| Field | Value | Sequences | Description |
|---|---|---|---|
| `llm_profile` | `"architecture"` | `conceptual_replan`, `full_rebuild` | Advisory signal requesting a stronger reasoning model. All other sequences receive no `llm_profile`. |

The `architecture` profile is declared in
`factory_app/app/config/refinement_policy.yaml` under
`llm_profiles.architecture`. Its `llm_config` names a capable
general reasoning model at low temperature for planning work.

**Advisory semantics.** The runner does not yet consume `llm_profile`
automatically. The field is context plumbing — when a runner consumer is wired
up, it will look up the named profile from `ai.json` and build an appropriate
LLM config for the launched workflow session.

**What does not change:**

- The classifier uses `llm_profile: classifier` (deterministic,
  `gpt-5-nano`). Unchanged.
- The coding worker uses `llm_profile: codegen`. Unchanged.
- Tool permissions do not change — no ShellTool or write tools are added to
  planning agents via this signal.
- The signal does not automatically promote or demote models for any current
  agent. It is plumbing for future runner consumption.

This field is declared as a state variable in `ValueEngine/context_variables.yaml`,
`DesignDocs/context_variables.yaml`, `AgentGenerator/context_variables.yaml`,
and `AppGenerator/context_variables.yaml`.

### Declaration Model

The re-entry policy is declared by artifact kind in the selected
`harness.yaml` pack.

Each route chooses a `workflow_sequence` from
`extended_orchestration/extension_registry.json`. The sequence is the source of
truth for downstream workflow order and artifact-family impact. If a route must
start at a different workflow, define a dedicated sequence with that workflow
first.

Example:

```yaml
routing:
  default_artifact_kind: app_bundle
  artifacts:
    - artifact_kind: app_bundle
      label: app bundle
      routes:
        patch:
          workflow_sequence: app_revision
        design:
          workflow_sequence: app_surface_revision
        feature:
          workflow_sequence: app_revision
        core:
          workflow_sequence: full_rebuild
```

Rules:

- `workflow_sequence` is canonical for Refinement Engine routes.
- Do not declare `affected_workflows` in `harness.yaml`; it is derived
  from the selected sequence.
- Do not declare `affected_declarative_families` in `harness.yaml`; it is
  declared once on the selected sequence in `extension_registry.json`.
- Do not declare `requires_replanning`; it is derived from the typed change
  class: `patch=false`, `design|feature|core=true`.
- Do not declare `requires_rebuild`; Refinement Engine rebuild decisions are
  runtime decision outputs, not route manifest inputs.

### Backend Intake

Refinement is triggered via the unified trigger endpoint:

```http
POST /api/workflows/trigger
{
  "trigger_source": "refinement",
  "trigger_payload": {
    "refinement_request": {
      "artifact_kind": "app_bundle",
      "artifact_key": "app_bundle",
      "artifact_version_id": "v3",
      "raw_user_request": "Fix the login redirect"
    }
  },
  "app_id": "abc123"
}
```

`workflow_id` is optional — the router resolves it. The response includes `routing_explanation` so the caller can surface the routing decision to the user.

If the harness decides a workflow should not launch immediately, the same
endpoint may return:

```json
{
  "execution_mode": "harness_decision",
  "workflow_id": "ValueEngine",
  "harness_decision": {
    "decision_type": "core_restart",
    "requires_confirmation": true,
    "actions": [
      {
        "action_id": "confirm_recommended_workflow",
        "label": "Run ValueEngine"
      }
    ]
  }
}
```

Builder surfaces can then confirm or continue by resubmitting the refinement
request with:

```json
{
  "trigger_payload": {
    "refinement_request": {
      "...": "...",
      "extra": {
        "harness_action": {
          "action_id": "confirm_recommended_workflow"
        }
      }
    }
  }
}
```

### Generator Prompt Contract

Generators should respect the routing decision through a revision context,
not a single boolean:

```yaml
build_mode: revision                # initial | revision
revision_scope: patch               # patch | design | feature | core
change_request_id: "cr_123"         # stable lineage handle
artifact_version_id: "v3"           # version to load or compare against
refinement_request: "..."           # raw user request passed through from ChangeRequest
change_intent: {...}                # typed classification, rationale, touched layers
impact_set: {...}                   # restart point, rebuild flags, affected workflows
sequence_status: completed          # in_progress | completed | stale | revising
```

Rules:

- `build_mode=revision` means "do not re-interview by default; start from
  persisted builder context and artifact lineage"
- `revision_scope=core` rerouting into `ValueEngine` is still a revision entry,
  not a blank first-pass interview
- workflow prompts should use `change_intent` and `impact_set` to decide how
  much prior work to preserve and which downstream layers may become stale
- `sequence_status` tells the workflow whether the full build sequence had
  already completed before this revision was requested

`ValueEngine`, `DesignDocs`, `AgentGenerator`, and `AppGenerator` should all
consume this same revision contract so re-entry behavior stays consistent
across the build flow.

---

## Artifact Workspace Durability (Phase D)

### Problem

Before Phase D, artifact workspace content was stored only on the local
filesystem.  `load_artifact_workspace()` would return `present: False` for any
artifact version whose `workspace_dir` or `artifact_path` was no longer
accessible — e.g. after a container replacement, host restart, or distributed
worker deployment.  This made carry-forward reads and AssemblyAgent preservation
unreliable in non-trivial deployments.

### Solution: Pluggable `ArtifactContentStore`

`mozaiksai/core/artifacts/content_store.py` introduces a Protocol-based content
backend.  All existing local-filesystem behaviour is preserved as the default
backend.

#### `ArtifactContentStore` Protocol

```python
class ArtifactContentStore(Protocol):
    backend_name: str

    async def put_bundle(self, data: bytes, *, app_id: str, artifact_version_id: str) -> str: ...
    async def get_bundle(self, content_ref: str) -> bytes: ...
    async def exists(self, content_ref: str) -> bool: ...
    async def verify_checksum(self, content_ref: str, sha256: str) -> bool: ...
    async def delete(self, content_ref: str) -> bool: ...
```

`content_ref` is an opaque string whose format is backend-specific.  Callers
must not parse it.

#### Implementations

| Class | `backend_name` | `content_ref` format |
|---|---|---|
| `LocalArtifactContentStore` | `"local"` | Absolute path string of the stored zip. |
| `GridFSArtifactContentStore` | `"gridfs"` | MongoDB GridFS `_id` rendered as a string. |

`GridFSArtifactContentStore` defers the motor import to its first `_ensure_fs()`
call.  The class can be instantiated without motor installed.

#### Factory

```python
from mozaiksai.core.artifacts.content_store import get_artifact_content_store
store = get_artifact_content_store()
```

Backend is selected by the `MOZAIKS_ARTIFACT_CONTENT_BACKEND` environment
variable:

| Value | Backend |
|---|---|
| `"local"` (default) | `LocalArtifactContentStore` |
| `"gridfs"` | `GridFSArtifactContentStore` |
| *(any other)* | `LocalArtifactContentStore` (fallback) |

The factory returns a process-wide singleton.

### Metadata contract

`ArtifactVersionDoc.commit_metadata.metadata` is a `dict[str, Any]`.  Phase D
adds two optional keys when a non-local content backend is used:

| Key | Type | Set by | Description |
|---|---|---|---|
| `content_ref` | `str` | `generate_and_download`, `coding_worker`, `create_draft_app_bundle_from_staged_refinement` | Opaque backend reference. Absent for `"local"` backend. |
| `content_backend` | `str` | Same | Backend name (`"gridfs"` etc.). Absent for `"local"` backend. |
| `artifact_path` | `str` | AppGenerator, AgentGenerator, coding worker, staged refinement draft helper | Absolute local path (always written, unchanged). |
| `workspace_dir` | `str` | `coding_worker`, staged refinement draft helper | Absolute workspace dir (always written when present). |

The local backend skips writing `content_ref` and `content_backend` because
`artifact_path` already covers local content retrieval.

No Pydantic schema migration is required — `metadata` is already `dict[str, Any]`.

### Three-branch lookup in `load_artifact_workspace()`

`factory_app/refinement_harness/tools/_artifact_workspace.py` resolves workspace
content in this priority order:

1. **`workspace_dir`** — if path exists on disk, read files directly.
2. **`artifact_path`** — if path exists on disk, read zip from filesystem.
3. **`content_ref`** via `ArtifactContentStore` — if a `content_ref` key is
   present in metadata and a `content_store` argument is provided, call
   `content_store.get_bundle(content_ref)` and read the returned bytes as a zip.

`load_artifact_workspace()` now accepts an optional `content_store` parameter:

```python
result = await load_artifact_workspace(
    artifact_store=store,
    app_id=app_id,
    artifact_version_id=version_id,
    content_store=get_artifact_content_store(),  # pass to enable third branch
)
```

**`present: False` reasons introduced by Phase D:**

| `reason` | Condition |
|---|---|
| `content_store_unavailable` | `content_ref` present in metadata but `content_store=None` passed. |
| `content_ref_not_found` | `content_store.get_bundle()` raised `ContentNotFoundError`. |
| `content_store_error` | `content_store.get_bundle()` raised any other exception. |

Existing reasons (`artifact_not_found`, `workspace_unavailable`) are unchanged.

### Where content is written

**`generate_and_download._register_app_bundle_artifact_version()`**

After creating the zip: if `content_store.backend_name != "local"`, calls
`put_bundle()` and writes `content_ref` + `content_backend` into
`commit_metadata.metadata`.  Falls back to local path on content store errors.

**`ScopedRefinementCodingWorker._persist_validated_artifact()`**

Same pattern after writing `artifact.zip`.

**`create_draft_app_bundle_from_staged_refinement()`**

Packages the staged workspace into a draft `app_bundle` artifact version and
writes the bundle path into `artifact_path`. When a non-local content backend
is configured, it also stores the bundle bytes through the content store and
records `content_ref` / `content_backend`. This is the canonical hosted/studio
path for staged patch promotion. Direct filesystem promotion remains a local
development fallback.

The `local` backend is intentionally skipped in both writers because
`artifact_path` is always written and already covers local retrieval.

### Phase 7A relationship

Phase 7A (AssemblyAgent declarative contract preservation) proceeds with
graceful degradation when workspace content is unavailable (`present: False`).
Phase D makes non-local deployments production-reliable by ensuring workspace
content survives container or host replacement.  Phase D is not a hard
prerequisite for Phase 7A functionality.

**Remaining future work:** Object-storage backend (S3/GCS). CLI backfill for
artifacts written before Phase D.

---

## Phase 7A: Declarative Contract Preservation

### Problem

Before Phase 7A, `carry_forward_decisions` was planning metadata only — no
file copy or preservation behavior existed.  Modules with `decision == "reuse"`
generated fresh contracts that discarded stable, hand-tuned declarations from
the prior bundle.

### Solution: `resolve_carry_forward_preservation`

After `AssemblyAgent` calls `assemble_app_tasks`, a second tool
(`resolve_carry_forward_preservation`) runs automatically (`auto_tool_call:
true`) and copies only allowlisted declarative module contract files from the
prior app bundle workspace into the assembled output.

**Generated output always wins.** The resolver only fills paths not already
produced by generation.  It is baseline file injection, not semantic merging.

#### Allowlisted paths (Phase 7A)

For each module with `decision == "reuse"` in `carry_forward_decisions`, the
resolver may copy exactly these relative paths (under `modules/{module_id}/`):

| Path | Description |
|---|---|
| `module.yaml` | Module identity, actions, and capabilities |
| `contracts/events.yaml` | Domain events emitted by the module |
| `contracts/reactions.yaml` | Event reactions owned by the module |
| `contracts/notifications.yaml` | Notification rules per event |
| `contracts/settings.yaml` | Module-owned settings schema for module/admin/config surfaces |
| `contracts/admin.yaml` | Admin panel declarations |
| `contracts/profile.yaml` | User profile panel declarations |

#### Denylist (never copied regardless of allowlist)

- `runtime_extensions.yaml` — runtime wiring is always regenerated
- `.env.example`, `requirements.txt`
- `ui/route_manifest.json`, `ui/index.js` — custom React routing
- `data/contract.json` and `data/migrations/*` — persistence intent
- `ui/pages/custom/*` — custom React pages
- `app/services/integrations/*`, `app/services/routes/*` — integration clients and API routes
- Any path containing `secret`, `credential`, or `password`
- All `backend/*.py` — backend Python source is never preserved

#### Conflict resolution

When the prior bundle contains an allowlisted path that was also produced by
generation, the generated output wins and the path is recorded in
`carry_forward_report.conflicts` with reason `"generated_output_wins"`.

#### Context variables

| Variable | Written by | Description |
|---|---|---|
| `generated_files` | `assemble_app_tasks` | Flat `{filename: content}` map of all generated output. Read by the resolver for conflict detection. |
| `carry_forward_additions` | `resolve_carry_forward_preservation` | Preserved files not already in `generated_files`. Merged by `generate_and_download` with generated-wins semantics. |
| `carry_forward_report` | `resolve_carry_forward_preservation` | Structured report of preserved paths, conflicts, skipped paths, warnings. Saved into `ArtifactVersionDoc.commit_metadata.metadata` by `generate_and_download`. |

#### `carry_forward_report` shape

```python
{
    "previous_app_bundle_ref": str | None,
    "workspace_available": bool,
    "workspace_source": str | None,   # "workspace_dir" | "artifact_path" | "content_ref"
    "preserved_paths": list[str],
    "skipped_paths": dict[str, str],  # path → denylist reason
    "conflicts": dict[str, str],      # path → "generated_output_wins"
    "decisions_processed": list[dict],
    "reused_modules": list[str],
    "adapted_modules": list[str],
    "regenerated_modules": list[str],
    "dropped_modules": list[str],
    "warnings": list[str],
}
```

#### Graceful degradation

The resolver never raises.  When the prior workspace is unavailable
(`present: False`) or `carry_forward_decisions` is absent, it no-ops and
emits a `carry_forward_report` with `workspace_available: False`.  Builds
that have no `conceptual_replan` carry-forward context are unaffected.

#### Implementation

| File | Role |
|---|---|
| `factory_app/refinement_harness/tools/resolve_carry_forward_preservation.py` | Core resolver |
| `factory_app/workflows/AppGenerator/tools/resolve_carry_forward_preservation.py` | Thin AG2 wrapper with `Annotated[..., Field(...)]` DI |
| `factory_app/workflows/AppGenerator/tools.yaml` | `AssemblyAgent` entry, `auto_tool_call: true` |
| `factory_app/workflows/AppGenerator/tools/assemble_app_tasks.py` | Writes `generated_files` to context on both schema and task batch paths |
| `factory_app/workflows/AppGenerator/tools/generate_and_download.py` | Merges `carry_forward_additions`; saves `carry_forward_report` to artifact metadata |
| `factory_app/app/admin/pages/CarryForwardReportPanel.jsx` | Studio app overview — full audit panel from `commit_metadata.metadata.carry_forward_report` |
| `factory_app/app/admin/pages/AppOverviewPage.jsx` | Mounts `CarryForwardReportPanel` when the latest artifact contains a `carry_forward_report` |
| `factory_app/app/admin/pages/CarryForwardReportSummary.jsx` | Compact collapsible carry-forward summary for build history entries |
| `factory_app/app/admin/pages/AppBuildHistoryPage.jsx` | Build History (Activity) page — artifact version list with inline `CarryForwardReportSummary` per entry |

**Tests:**

| Test file | Coverage |
|---|---|
| `tests/test_carry_forward_preservation.py` | 28 tests — Phase 7A resolver logic, allowlist enforcement, context key writes |
| `tests/test_carry_forward_report_persistence.py` | 12 tests — full persistence path from context to `ArtifactVersionDoc.commit_metadata.metadata` and Studio build history `model_dump()` |

Run:
```bash
python -m pytest tests/test_carry_forward_preservation.py tests/test_carry_forward_report_persistence.py -v
```

#### Studio operator display

`carry_forward_report` is surfaced in two Studio locations:

**App Overview (`AppOverviewPage`):** Full audit panel (`CarryForwardReportPanel`) below the
build and runtime panels. Hidden when no report is present.

**Build History (`AppBuildHistoryPage`, `/apps/:appId/activity`):** Compact collapsible
`CarryForwardReportSummary` inline in each artifact version entry. All entries in the
history list that carry a report show it; entries without a report show a
"No carry-forward preservation" notice. Both components read
`artifact.commit_metadata.metadata.carry_forward_report` — no schema or API change required.

Both surfaces display:

- Reused, adapted, regenerated, and dropped module lists
- Preserved declarative contract count
- Conflict count (paths where generated output overwrote a preserved candidate)
- Workspace availability (warning when `workspace_available: false`)
- Diagnostic warnings

**Operator-facing guarantees:**

- The report is an audit trail only — it does not affect build behavior.
- Backend Python source is never carried forward in Phase 7A.  Both components
  explicitly state this.
- Paths containing "secret", "credential", or "password" are redacted in
  the display (belt-and-suspenders; the resolver already filters them out).
- Both components return null when no report is present; callers do not need
  to guard before rendering.

**Remaining future work:** Phase 7B — semantic adaptation merging for modules
with `decision == "adapt"`.

---

## Smoke Runbook: conceptual_replan carry-forward

The following script validates the full CRM → Marketplace carry-forward
pipeline without a live LLM or MongoDB instance.

```bash
# Run both variants and display results
python scripts/smoke_conceptual_replan_carry_forward.py --mode both

# Run only the explicit override variant
python scripts/smoke_conceptual_replan_carry_forward.py --mode explicit

# Run only the auto-populated variant
python scripts/smoke_conceptual_replan_carry_forward.py --mode auto

# Save fixture for pytest replay (runs both variants)
python scripts/smoke_conceptual_replan_carry_forward.py --mode both --save-fixture

# Run all fixture-replay tests (requires saved fixture)
python -m pytest tests/test_live_conceptual_replan_carry_forward.py -q

# Run always-on safety tests only (no fixture needed)
python -m pytest tests/test_live_conceptual_replan_carry_forward.py -q -k TestPipelineSafety
```

**Two smoke variants:**

| Variant | `carry_forward_modules` source | `get_carry_forward_candidates` called |
|---|---|---|
| `explicit` | `request.extra.carry_forward_modules` override | No |
| `auto` | `get_carry_forward_candidates` auto-extraction | Yes |

The auto variant returns all module IDs from the prior workspace at route time
(unfiltered). Reuse/drop filtering is AppPlanAgent's job at plan time, not the
router's. This is current truthful behavior, documented by the smoke.

**What is live (real code exercised):**

- `RefinementTriggerRouteResolver` with the real refinement harness
- `get_carry_forward_candidates` Refinement Engine tool (auto variant only)
- `load_artifact_workspace` with a real temp dir
- `resolve_carry_forward_preservation` Phase 7A tool

**What is stubbed:**

- Change classifier — `_DeterministicChangeClassifier(change_class="core")`
- `ArtifactStore` — returns a synthetic CRM artifact doc pointing to the temp dir
- `carry_forward_decisions` — fixture (settings/notifications=reuse, contacts/pipeline=drop)

**Distinction from full live LLM benchmark:** this smoke does not call any
language model. It validates the routing, context seeding, auto-extraction, and
preservation pipeline deterministically. For live AppPlanAgent quality
validation, see the live benchmark below.

---

## Live LLM Conceptual Replan Benchmark

A single-agent live benchmark that validates end-to-end AppPlanAgent reasoning
for a conceptual_replan scenario using a real OpenAI model.

**What is live:**

- `RefinementTriggerRouteResolver` with real refinement harness (routing deterministic, no LLM)
- `AppPlanAgent` called directly via OpenAI with conceptual_replan context injected
- `resolve_carry_forward_preservation` Phase 7A with the LLM's actual `carry_forward_decisions`

**What is stubbed:**

- Change classifier — `_DeterministicChangeClassifier(change_class="core")`
- `ArtifactStore.get_artifact_version` — synthetic CRM artifact + temp dir

This is a single-agent benchmark, not a full `ValueEngine → AppGenerator` workflow sequence run.

**Scenario:** CRM → Marketplace pivot. Prior modules: contacts, pipeline, settings, notifications.
The LLM must not blindly reuse all candidates — domain-generic modules (settings, notifications)
should be reused; CRM-specific modules (contacts, pipeline) should be dropped or regenerated.

**17 required assertions:**

| # | Assertion |
|---|-----------|
| 1 | route == `conceptual_replan` |
| 2 | `pivot_description` present in context_seed |
| 3 | `previous_app_bundle_ref` present in context_seed |
| 4 | `carry_forward_modules` non-empty in context_seed |
| 5 | `carry_forward_decisions` is non-empty list |
| 6 | All decision values valid: `reuse/adapt/regenerate/drop` |
| 7 | `settings` → `reuse` or `adapt` (domain-generic) |
| 8 | `notifications` → `reuse` or `adapt` (domain-generic) |
| 9 | `contacts` → `drop` or `regenerate` (CRM-specific) |
| 10 | `pipeline` → `drop` or `regenerate` (CRM-specific) |
| 11 | Marketplace-oriented modules appear in capability_packs |
| 12 | `carry_forward_report` exists (Phase 7A ran) |
| 13 | `preserved_paths` only Phase 7A allowlisted files |
| 14 | No backend Python in `preserved_paths` |
| 15 | No `runtime_extensions.yaml` in `preserved_paths` |
| 16 | No custom React in `preserved_paths` |
| 17 | `carry_forward_report` shape valid |

**Running the live benchmark:**

```bash
# Requires OPENAI_API_KEY (funded account)
python scripts/smoke_live_conceptual_replan.py
python scripts/smoke_live_conceptual_replan.py --save-fixture
python scripts/smoke_live_conceptual_replan.py --model gpt-5-nano
```

**Running the fixture replay tests (no API key required):**

```bash
python -m pytest tests/test_live_conceptual_replan_benchmark.py -v
```

**Common failure modes:**

- `carry_forward_decisions is NoneType` — model ignored the requirement; check prompt injection
- `settings not in carry_forward_decisions` — model renamed the module_id; prompt must list exact IDs
- `contacts decision=reuse` — model blindly reused; check carry_forward_classification hints in prompt
- `preserved_paths contains backend Python` — Phase 7A allowlist is too permissive; inspect `_PHASE_7A_MODULE_ALLOWLIST`
- `OPENAI_API_KEY quota exceeded` — fund the account or use a different API key
