---
title: Refinement Control Plane
status: Authoritative - Pre-Production, No Backward Compat
created: 2026-04-13
depends_on: workflow-routing-transitions.md, event-system.md, ../../architecture/mozaiksai/universal-orchestrator.md, ../builder/database-intent-and-revision-contract.md
---

# Refinement Control Plane

This document defines how Mozaiks handles post-generation changes without forcing users back through full generation workflows for every adjustment.

In the canonical orchestration model, this document covers the builder session
loop and the refinement worker loop. It does not redefine workflow-local AG2
handoffs.

The goal is simple:

- initial generation workflows create the first canonical shape
- refinement workflows adjust that shape safely and quickly
- the control plane decides when a change is small, scoped, design-only, or concept-breaking

The refinement control plane is enabled and configured at the app level through
`app/config/ai.json -> control_plane`. The classifier and coding worker do not
read workflow-local AG2 config for this.

This is a pre-production design. There is no backward-compatibility requirement.

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
- No backward-compat shims for the current pre-production loops.

---

## Current Leverage In The Codebase

The existing platform already has the right raw ingredients:

- `ValueEngine` persists canonical concept state via `value_manifest`.
- `DesignDocs` persists draft design documents.
- `AppGenerator` already emits `build_tasks` with `owned_paths`, `depends_on`, and `acceptance_criteria`.
- App validation and preview already run in E2B-backed tooling.

That means refinement does **not** need a brand-new reasoning model. It needs a control plane and durable state model around the artifacts the generators already produce.

Database refinements should follow the companion contract in
[database-intent-and-revision-contract.md](../builder/database-intent-and-revision-contract.md):

- compare previous and target `database_intent` artifacts
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

---

## Routing Rule

Natural-language refinement requests should not go straight into `InterviewAgent`, `PatternAgent`, or `AppPlanAgent`.

They should go through a control-plane classifier:

```text
user request
  -> refinement classifier
  -> emit control-plane event
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
  `control_plane.yaml`
- the first-party harness binds `request_submitted`, `route_requested`,
  `decision_requested`, `scope_requested`, and `coding_requested` through that
  runtime
- this keeps the checkpoint taxonomy declarative while still allowing the
  harness to compose checkpoint handlers deterministically
- refinement re-entry routing is no longer hardcoded to builder workflows in
  Python; the selected control-plane pack declares app-owned artifact kinds and
  `route_to` targets per change class inside `control_plane.yaml`
- that keeps the harness runtime-owned while letting future apps declare their
  own artifact/output topology without becoming `factory_app` clones

Current simplified pack taxonomy:

- `config/control_plane.yaml`
  - top-level `harness`
  - top-level `routing`
  - inline `checkpoints[]`
- `prompts/*.yaml`
- `config/tools.yaml`
- optional `config/policies.yaml`

Each checkpoint declares:

- `event`
- `entrypoint`
- optional `prompt_id`
- optional `tool_ids`

There is no extra user-facing control-plane `components` layer. The pack
declares what should run at each checkpoint.

The `routing` section is the canonical app-local declaration for harness
ownership:

- `default_artifact_kind`
- `artifacts[]`
  - `artifact_kind`
  - optional `label`
  - `routes.patch|design|feature|core.route_to`
  - affected workflows / declarative families / replanning flags

This keeps the runtime generic:

- `factory_app` can declare app-build artifacts like `concept`,
  `design_docs`, `workflow_bundle`, and `app_bundle`
- a future memo/planning app can declare artifacts like `market_research`,
  `financial_model`, or `executive_summary`
- the harness logic stays in `mozaiksai/control_plane`, while artifact
  ownership stays in the selected pack

Current app-level config contract:

```json
{
  "control_plane": {
    "enabled": true,
    "classifier": {
      "enabled": true,
      "llm_config": {
        "model": "gpt-4o-mini",
        "temperature": 0.0
      }
    },
    "coding": {
      "enabled": false,
      "llm_config": {
        "model": "gpt-5.2-codex",
        "temperature": 0.1
      }
    }
  }
}
```

Rules:

- `control_plane.enabled` gates the harness as a whole
- `classifier.llm_config` selects the authoritative refinement-classification model
- `coding.llm_config` is reserved for the refinement worker loop, not for workflow-local AG2 execution

Current first-party pack paths:

- `factory_app/control_plane/config/control_plane.yaml`
- `factory_app/control_plane/config/tools.yaml`
- `factory_app/control_plane/config/policies.yaml`
- `factory_app/control_plane/prompts/*.yaml`
- `factory_app/control_plane/tools/*.py`

Current default classifier grounding:

- the selected control-plane pack declares a `request_submitted` checkpoint
  with its own prompt and tool ids inline in `control_plane.yaml`
- the runtime now provides a generic `get_revision_context` tool that assembles:
  - persisted `SessionState`
  - app-declared routing metadata from the selected control-plane pack
  - tracked artifact refs and latest artifact lineage
  - active change-request lineage when present
  - persisted summary payloads for runtime-owned summary artifacts such as
    `concept`, `build_plan`, `design_docs`, and `theme_capture`
  - one-level resolved `canonical_inputs_version` lineage so downstream bundle
    artifacts can expose the upstream artifacts they were built from
- when a `ChangeRequest` is persisted, the control plane now also marks the
  affected persisted artifact versions `stale` using the change-request id as
  the invalidation reason
- the first-party factory pack pairs that runtime context with
  `get_artifact_summary`
- app-specific packs may add extra tools, but the harness backbone should start
  from runtime-owned revision context rather than builder-only persistence
- that context is gathered before the model call and passed into the
  classifier prompt as canonical persisted runtime state
- this keeps refinement classification above workflow-local AG2 logic and off
  the individual workflow prompt surfaces

Current first-party coding worker path:

- `control_plane.coding.enabled` gates the worker independently from the
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
  validates the merged workspace snapshot, and can persist a child artifact
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
- the worker stays subordinate to control-plane routing and does not masquerade
  as an AG2 workflow
- explicit file scope can now come from persisted artifact-bundle workbenches,
  not only from an in-flight workflow surface
- when explicit file scope is missing, the default scope selector uses
  `get_artifact_workspace_catalog` plus routing metadata to choose the
  narrowest safe files before the worker runs
- the selected control-plane pack now also declares `policies.yaml`, which
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
- the default control-plane pack now declares this behavior as a dedicated
  `decision_requested` checkpoint in `control_plane.yaml`

Current first-party artifact workbench bridge:

- `AppGenerator` now registers `app_bundle` artifact versions for generated
  bundles
- Studio exposes `GET /api/studio/build/artifacts/{artifact_version_id}/bundle` to
  reopen a persisted bundle as a text-file workbench payload
- the Build history surface can open that bundle in `AppWorkbench`
- `AppWorkbench` can launch a scoped refinement request with
  `coding_request.files` sourced from the selected file editor state
- when no file is selected in `AppWorkbench`, the control-plane harness can
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
  - `refinement_request`
  - `refinement_request_meta`
  - `screen`
  - `change_intent`
  - `impact_set`
  - `sequence_status`
  - `revision_origin_workflow`
- this is what lets a confirmed `core_restart` into `ValueEngine` preserve the
  request and typed control-plane rationale without tripping
  `SESSION_LAUNCH_CONTEXT_KEY_REJECTED`
- important: a reroute into `ValueEngine` for `core` does **not** mean "treat
  this like a blank greenfield intake again." It is still a revision entry with
  prior concept, design, and bundle history available through the revision
  context.

This is intentionally different from the older `code_context` subsystem under
`AppGenerator/tools/code_context/`:

- `code_context` is a workflow-local, persisted semantic index for generator
  agents
- `get_artifact_workspace_catalog` is a control-plane tool for harness-time
  file-scope proposal against persisted artifact workspaces
- `get_artifact_workspace_scope` is a control-plane tool for harness-time
  artifact inspection around an explicit scoped refinement request

---

## Current Typed Control-Plane Contracts

The control plane should operate on five typed artifacts:

| Contract | Purpose |
|---|---|
| `RefinementRequest` | Canonical incoming refinement payload: optional route hint, artifact lineage, raw request text, source surface |
| `ChangeIntent` | Typed classification result used for routing and later review/persistence |
| `ImpactSet` | Typed downstream scope summary: affected workflows, declarative families, restart point, replanning/rebuild flags |
| `RefinementRoutingDecision` | Deterministic re-entry decision plus workflow seed context |
| `HarnessDecision` | Typed builder-surface continuation result: auto-run, clarify, fallback, or confirm before launch |

These contracts live above workflow-local AG2 handoffs.

The runtime may still seed workflow context with convenience fields such as:

- `change_class`
- `artifact_kind`
- `artifact_version_id`
- `refinement_request`

But the control plane itself should reason from the typed contracts, not from a
loose bundle of free-form strings.

`HarnessDecision` is the bridge between control-plane reasoning and builder UX.
It is what lets the platform render a structured decision card instead of
falling back to:

- a dropdown for `patch | design | feature | core`
- a generic popup
- or silent rerouting with no explanation

`declared_change_class` inside `RefinementRequest` is advisory only.

It may be supplied by UI as a route hint, but the authoritative classification
comes from the backend control-plane model call.

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
- transitions do **not** decide rebuild scope
- AG2 handoffs do **not** own control-plane routing

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
- units should cover files like `agents.yaml`, `handoffs.yaml`, `tools.yaml`, `context_variables.yaml`, `ui/*`, and workflow-local orchestration files

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

The control plane needs durable records.

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

Workflow sequences still matter, but only after the control plane decides re-entry.

Correct split:

- workflow sequences define major phase order
- transitions define entry or inter-phase user decisions
- refinement control plane decides what phase to re-enter

Example:

- `core` change -> start a fresh `ValueEngine` revision, then downstream phases become stale
- `design` change -> resume at `DesignDocs` or a design refinement workflow, then rebuild affected app artifacts
- `feature` change -> resume at a scoped planner step, then run partial MFJ
- an in-progress build and a completed build both use the same routing matrix;
  the difference is whether the session's `sequence_status` is already
  `completed` or still `in_progress`

So the answer to "does this belong in journey sequencing?" is:

- no for classification
- yes only after classification, as a consumer of the routing decision

---

## Required Cleanups In The Current Platform Flows

These current behaviors should be removed when the refinement control plane lands:

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
    ArtifactKind,    # Enum: app_bundle | workflow_bundle | design_docs | concept
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
| `artifact_kind` | `ArtifactKind` | Yes | app_bundle / workflow_bundle / design_docs / concept |
| `artifact_key` | `str` | No | Defaults to the artifact kind when omitted |
| `artifact_version_id` | `str` | No | Version to load in re-entry workflow |
| `raw_user_request` | `str` | No | Passed as `refinement_request` context variable |
| `app_id` | `str` | No | For logging and audit record |
| `declared_change_class` | `ChangeClass` | No | Optional UI hint only; not authoritative |

**`RoutingDecision` fields:**

| Field | Type | Notes |
|---|---|---|
| `workflow_id` | `str` | Workflow to invoke |
| `context_seed` | `dict` | Merged into `context_variables` for the new session |
| `explanation` | `str` | Human-readable reason for the routing choice |
| `is_full_restart` | `bool` | True for `core` changes — restarts from ValueEngine |

### Default Routing Table

The built-in defaults cover all four change classes × four artifact kinds:

| Change class | Artifact kind | Re-entry workflow | Full restart |
|---|---|---|---|
| `patch` | `app_bundle` | `AppGenerator` | No |
| `design` | `app_bundle` | `DesignDocs` | No |
| `feature` | `app_bundle` | `AppGenerator` | No |
| `core` | `app_bundle` | `ValueEngine` | Yes |
| `patch` | `workflow_bundle` | `AgentGenerator` | No |
| `design` | `workflow_bundle` | `AgentGenerator` | No |
| `feature` | `workflow_bundle` | `AgentGenerator` | No |
| `core` | `workflow_bundle` | `ValueEngine` | Yes |
| `patch/design/feature` | `design_docs` | `DesignDocs` | No |
| `core` | `design_docs` | `ValueEngine` | Yes |
| `patch/design/feature` | `concept` | `ValueEngine` | No |
| `core` | `concept` | `ValueEngine` | Yes |

### Declaration Model

The re-entry policy is declared in code, not in a separate YAML routing file.

The only thing declared explicitly is **artifact ownership**:

| Artifact kind | Owner workflow | Design owner | Root owner |
|---|---|---|---|
| `app_bundle` | `AppGenerator` | `DesignDocs` | `ValueEngine` |
| `workflow_bundle` | `AgentGenerator` | `AgentGenerator` | `ValueEngine` |
| `design_docs` | `DesignDocs` | `DesignDocs` | `ValueEngine` |
| `concept` | `ValueEngine` | `ValueEngine` | `ValueEngine` |

The shared generation-core resolver derives routes generically and hands the
result back to SessionRouter:

- `patch` -> owner workflow
- `feature` -> owner workflow
- `design` -> design owner
- `core` -> root owner with full restart

That keeps the model simpler than a separate 16-entry routing table and avoids
turning refinement routing into another config system beside workflow sequences,
transitions, and MFJ.

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
