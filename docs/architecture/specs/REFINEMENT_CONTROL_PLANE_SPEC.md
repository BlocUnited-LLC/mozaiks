---
title: Refinement Control Plane
status: Authoritative - Pre-Production, No Backward Compat
created: 2026-04-13
depends_on: workflow-routing-gates.md, WORKFLOW_TRIGGERS_SPEC.md, ../foundations/event-system.md, ../../reference/deep-dives/universal-orchestrator.md
---

# Refinement Control Plane

This document defines how Mozaiks handles post-generation changes without forcing users back through full generation workflows for every adjustment.

The goal is simple:

- initial generation workflows create the first canonical shape
- refinement workflows adjust that shape safely and quickly
- the control plane decides when a change is small, scoped, design-only, or concept-breaking

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

**Location:** `mozaiksai/core/refinement/router.py`

The module exposes a platform-agnostic router. Nothing in this module knows about specific products, workflows, or UI surfaces.

```python
from mozaiksai.core.refinement import (
    RefinementRouter,
    ChangeClass,     # Enum: patch | design | feature | core
    ArtifactKind,    # Enum: app_bundle | workflow_bundle | design_docs | concept
    ChangeRequest,   # Input dataclass
    RoutingDecision, # Output dataclass
)

router = get_refinement_router()  # module-level singleton

decision = router.route(ChangeRequest(
    change_class=ChangeClass.PATCH,
    artifact_kind=ArtifactKind.APP_BUNDLE,
    artifact_version_id="v3",
    raw_user_request="Fix the login redirect",
    app_id="abc123",
))

# decision.workflow_id      → "AppGenerator"
# decision.context_seed     → {"refinement_mode": True, "change_class": "patch", ...}
# decision.explanation      → "Applying a targeted patch to scoped app files."
# decision.is_full_restart  → False
```

**`ChangeRequest` fields:**

| Field | Type | Required | Notes |
|---|---|---|---|
| `change_class` | `ChangeClass` | Yes | patch / design / feature / core |
| `artifact_kind` | `ArtifactKind` | Yes | app_bundle / workflow_bundle / design_docs / concept |
| `artifact_version_id` | `str` | No | Version to load in re-entry workflow |
| `raw_user_request` | `str` | No | Passed as `refinement_request` context variable |
| `app_id` | `str` | No | For logging and audit record |

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

The runtime derives routes generically:

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
  "change_class": "patch",
  "artifact_kind": "app_bundle",
  "artifact_version_id": "v3",
  "raw_user_request": "Fix the login redirect",
  "app_id": "abc123"
}
```

`workflow_id` is optional — the router resolves it. The response includes `routing_explanation` so the caller can surface the routing decision to the user.

### Generator Prompt Contract

Generators respect the routing decision via two context variables:

```yaml
refinement_mode: true         # tells agents to skip re-interview, load artifact
change_class: "patch"         # tells agents how much to change
artifact_version_id: "v3"     # tells agents which version to load
refinement_request: "..."     # the raw user request, passed through from ChangeRequest
```

Both `AppGenerator` and `AgentGenerator` declare these in `context_variables.yaml` and their agents respond accordingly. Agents check `refinement_mode` as the highest-priority instruction before any other prompt logic runs.
