# Artifact Staleness and Routing

## Purpose

This document explains how the control plane tracks artifact family staleness
and uses it to route refinement requests to the minimal necessary workflow
sequence rather than always triggering a full rebuild.

Related documents:

- [Refinement Engine](../workflows/refinement-engine.md)
- [End-to-End Build Lifecycle](end-to-end-build-lifecycle.md)
- [Refinement Harness Architecture](../workflows/refinement-harness-architecture.md)

---

## Typed Artifact Chain

Each build stage outputs a typed artifact that the next stage consumes as an
authoritative input — not a suggestion to reinterpret from prose:

```
ValueEngine → ConceptBlueprint (typed)
    ↓  surface_candidate_hints[]  → DesignDocs surface_map (authoritative)
    ↓  brand_intent               → experience_spec.brand_direction
ThemeCapture → CapturedThemeConfig (typed)
    ↓  theme.variant / appearance → experience_spec.brand_direction
    ↓  identity.tagline           → brand posture
DesignDocs → ExperienceSpec + surface_map + data_contract (typed)
    ↓  experience_spec.pages[]   → AppPlanAgent page list (authoritative)
    ↓  surface_map               → AppGenerator module generation
AgentGenerator → workflow_bundle
AppGenerator → app_bundle (DRAFT → reviewed → CURRENT)
```

This chain is only as strong as its weakest typed handoff. Every typed artifact
variable in a downstream workflow's `context_variables.yaml` must declare a
`data_reference` source pointing at the upstream collection and the specific
typed field — not at a prose string.

---

## Artifact Families and Their Dependencies

The build pipeline produces six artifact families. Each family depends on
upstream families:

```
concept
  ├── brand          (depends on concept)
  ├── design_docs    (depends on concept)
  └── experience_spec (depends on concept and design_docs)
        └── workflow_bundle  (depends on design_docs)
              └── app_bundle (depends on design_docs, experience_spec, workflow_bundle, brand)
```

This dependency order is declared as `artifact_dependency_graph` in
`factory_app/workflows/extended_orchestration/extension_registry.json` and
loaded into `GlobalPackGraph` at runtime. It is the single source of truth for
downstream propagation.

`experience_spec` is the first-class experience intent family produced by
`DesignDocs`. It remains separate from the broader `design_docs` family so a
page, navigation, or shell-experience refinement can invalidate experience
intent before app-bundle regeneration without necessarily treating all design
documentation as stale.

---

## When a Family Becomes Stale

Every `ArtifactVersionDoc` carries a `lifecycle_status`:

| Status | Meaning |
|--------|---------|
| `draft` | Generated, not yet reviewed |
| `current` | Accepted and active |
| `stale` | Invalidated by a later change; superseded in intent |
| `superseded` | Replaced by a newer current version |
| `archived` | Rejected or explicitly retired |
| `deleted` | Hard-removed |

After a change request is accepted and routed, the `ArtifactInvalidationService`
does two things:

1. **Direct invalidation** — marks the specific artifact version IDs in the
   session's tracked lineage as `stale` using the `change_request_id` as the
   reason. This covers the families explicitly written by the selected workflow
   sequence (declared in `affected_declarative_families` on that sequence).

2. **Downstream propagation** — performs a BFS traversal over
   `artifact_dependency_graph`, finds all families that transitively depend on
   the written families, and marks all their non-archived/non-deleted versions
   `stale` via `invalidate_artifact_family()`. This does not require the
   classifier or the session to know those families' specific version IDs.

Example: a `concept_patch` writes only `concept`. The invalidation service
computes its downstream set (`brand`, `design_docs`, `experience_spec`,
`workflow_bundle`, `app_bundle`) and marks all of them stale. The next
refinement request will see all of them as needing resolution.

---

## When Staleness Clears

A family is only considered stale if it has at least one `stale` version **and
no `current` version**. Once a workflow run writes a new accepted `current`
version for that family, the family drops off the stale list automatically —
no explicit cleanup step is required.

```
get_stale_artifact_families()
  = families with any stale version
  - families with any current version
```

This means the signal is always accurate: a family that was stale but then
rebuilt correctly will not show as stale on the next request.

---

## Two-Tier Staleness Routing

Staleness is handled in two tiers, applied in order:

### Tier 1 — Deterministic Pre-Check (RefinementTriggerRouteResolver)

Before the LLM classifier is invoked, `RefinementTriggerRouteResolver._stale_route()`
calls `get_stale_artifact_families()` for the request's `app_id`. If stale
families are found, the router returns a deterministic decision immediately —
**the LLM is not called at all**.

Priority order (earliest dependency wins):

| Priority | Stale family | Sequence used | Entry workflow |
|----------|-------------|---------------|----------------|
| 1 | `concept` | `full_rebuild` | ValueEngine |
| 2 | `brand` | `theme_revision` | ThemeCapture |
| 3 | `design_docs` | `design_revision` | DesignDocs |
| 4 | `experience_spec` | `app_surface_revision` | DesignDocs |
| 5 | `workflow_bundle` | `workflow_revision` | AgentGenerator |
| 6 | `app_bundle` | `app_revision` | AppGenerator |

The `ChangeIntent` produced by the pre-check has `source="stale_upstream"` and
`confidence=1.0`. The `signals` list carries the full set of detected stale
families so the context seed is accurate for the restart workflow.

### Tier 2 — LLM Classifier Advisory (LLMChangeClassifier)

When no stale families are detected, the flow proceeds to the LLM classifier at
the `request_submitted` checkpoint. The classifier has access to the
`get_stale_artifact_families` tool, which returns:

```json
{
  "stale_families": [],
  "all_current": true
}
```

The classifier uses the result as an advisory signal alongside the user's
request text to produce a `patch | design | feature | core` classification.
The LLM tier only runs when Tier 1 finds nothing to act on.

---

## Where the Dependency Graph Lives

```
factory_app/workflows/extended_orchestration/extension_registry.json
```

```json
"artifact_dependency_graph": {
  "concept":         [],
  "brand":           ["concept"],
  "design_docs":     ["concept"],
  "experience_spec": ["concept", "design_docs"],
  "workflow_bundle": ["design_docs"],
  "app_bundle":      ["design_docs", "experience_spec", "workflow_bundle", "brand"]
}
```

This field is part of `GlobalPackGraph` (`mozaiksai/core/workflow/pack/schema.py`).
It must stay in sync with the actual workflow sequence ownership declared in
`affected_declarative_families` on each sequence.

Staged refinement promotion also snapshots into draft `app_bundle`
`ArtifactVersion` records using the same `app_bundle` family and lifecycle.
Patch-ness stays in metadata; it is not a separate artifact kind or family.
Those drafts become `CURRENT` only through the staged refinement acceptance
helper, not by introducing a new artifact family.
After acceptance, Studio promotes the accepted `CURRENT` `app_bundle` back
into the runnable app root using safe extraction that skips staging metadata
files and backup trees and rejects unsafe archive paths. The accepted bundle
must carry a non-empty `files_manifest` before Studio restore can proceed.

---

## Artifact Persistence Per Workflow

Every workflow that produces a canonical artifact family must call
`persist_summary_artifact()` so that `ArtifactVersionDoc` records exist in the
DB with accurate lifecycle status. The stale-routing chain depends on these
records being present.

| Workflow | Artifact family | Persistence call site |
|---|---|---|
| ValueEngine | `concept` | `factory_app/workflows/ValueEngine/tools/manifest.py` |
| ThemeCapture | `brand` | `factory_app/workflows/ThemeCapture/tools/save_captured_theme.py` |
| DesignDocs | `design_docs` | `factory_app/workflows/DesignDocs/tools/save_design_doc.py` |
| AgentGenerator | `workflow_bundle` | `factory_app/workflows/AgentGenerator/tools/platform/build_lifecycle.py` — `emit_build_completed` override |
| AppGenerator | `app_bundle` | `factory_app/workflows/AppGenerator/tools/platform/build_lifecycle.py` — `emit_build_completed` override |

AppGenerator and AgentGenerator persist their artifacts via the
`trigger: on_complete` lifecycle hook, which fires after the workflow run
finishes. The local `build_lifecycle.py` in each workflow overrides
`emit_build_completed` from the shared module to call
`persist_summary_artifact()` after the platform notification, reading
`build_mode` from the persisted chat session context to correctly set
`revision_mode`.

---

## Artifact Version Refs — How Session State Stays Current

`ArtifactInvalidationService.invalidate_for_change_request()` reads
`SessionState.artifact_version_refs` to mark specific version IDs stale (direct
invalidation). Those refs must be accurate for every refinement request, including
the very first one after an initial build.

After each workflow run completes, `SessionRouter.advance_journey_after_run_complete()`
calls `ArtifactStore.get_current_artifact_version_refs()` to fetch all CURRENT
artifact version IDs for the app, and merges them into `state.artifact_version_refs`
before upserting. This means:

- All artifact-producing workflows (ValueEngine, ThemeCapture, DesignDocs,
  AgentGenerator, AppGenerator) populate the refs automatically at completion —
  no tool-level bookkeeping required.
- Revision routing via `_apply_revision_state()` still overlays the specific
  requested artifact version ID, which takes precedence over the synced value.
- The sync is best-effort: a failure is logged at DEBUG level and skipped.

### Canonical Inputs — CURRENT-only Filter

`resolve_latest_artifact_version_refs()` (called inside `persist_summary_artifact()`)
only resolves **CURRENT** artifact versions as canonical inputs. DRAFT versions
created during an in-flight revision are never included.

- On first run, when no CURRENT version exists for a requested kind, that kind
  is simply absent from the returned dict. A DEBUG log line is emitted.
- DRAFT versions that have not yet been promoted to CURRENT do not appear in
  any downstream artifact's `canonical_inputs_version`, so spurious staleness
  comparisons against in-flight draft IDs cannot occur.
- The parent-version lookup in `persist_summary_artifact()` also filters by
  CURRENT, ensuring revision DRAFTs always link to the last accepted CURRENT
  rather than another DRAFT.

---

## Key Code Locations

| Concern | File |
|---------|------|
| Dependency graph schema | `mozaiksai/core/workflow/pack/schema.py` — `GlobalPackGraph.artifact_dependency_graph` |
| Dependency graph data | `factory_app/workflows/extended_orchestration/extension_registry.json` |
| BFS propagation + direct invalidation | `mozaiksai/control_plane/invalidation.py` — `ArtifactInvalidationService` |
| Stale families query | `mozaiksai/core/artifacts/store.py` — `get_stale_artifact_families()` |
| Current version refs query | `mozaiksai/core/artifacts/store.py` — `get_current_artifact_version_refs()` |
| Lifecycle-status filter on `list_artifact_versions` | `mozaiksai/core/artifacts/store.py` — `lifecycle_status` parameter |
| Canonical input resolution (CURRENT-only) | `mozaiksai/core/artifacts/summary_artifacts.py` — `resolve_latest_artifact_version_refs()` |
| Version refs sync after run | `mozaiksai/core/session/router.py` — `advance_journey_after_run_complete()` |
| Deterministic pre-check (Tier 1) | `mozaiksai/control_plane/implementations/refinement_router.py` — `_stale_route()` |
| Stale priority + sequence map | `mozaiksai/control_plane/implementations/refinement_router.py` — `_STALE_PRIORITY`, `_STALE_SEQUENCE_MAP` |
| Control plane tool (Tier 2) | `factory_app/refinement_harness/tools/get_stale_artifact_families.py` |
| Tool registration | `factory_app/refinement_harness/config/tools.yaml` |
| Classifier prompt rules | `factory_app/refinement_harness/prompts/change_classifier_system.yaml` |
| Lifecycle status enum | `mozaiksai/core/artifacts/models.py` — `ArtifactLifecycleStatus` |

---

## Contributor Rules

- `affected_declarative_families` on each workflow sequence must be accurate.
  It drives both direct invalidation and the BFS starting set.
- When adding a new artifact family, update `artifact_dependency_graph` in
  `extension_registry.json`, add the family to `_STALE_PRIORITY` and
  `_STALE_SEQUENCE_MAP` in `refinement_router.py`, and update
  `GlobalPackGraph` in `schema.py` if needed.
- Do not add staleness routing logic to `harness.yaml`. The deterministic
  pre-check in `refinement_router.py` and `ArtifactInvalidationService` own
  that responsibility.
- Do not add staleness logic to tools. Tools return data; the router and LLM reason.
- Tests for BFS propagation: `tests/test_control_plane_invalidation.py`
- Tests for staleness query: `tests/test_artifact_store.py`
- Tests for deterministic stale routing: `tests/test_refinement_router.py`


