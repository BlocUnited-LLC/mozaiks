# Artifact Staleness and Routing

## Purpose

This document explains how the control plane tracks artifact family staleness
and uses it to route refinement requests to the minimal necessary workflow
sequence rather than always triggering a full rebuild.

Related documents:

- [Refinement Control Plane](../workflows/refinement-control-plane.md)
- [End-to-End Build Lifecycle](end-to-end-build-lifecycle.md)
- [Control-Plane Harness Architecture](../workflows/control-plane-harness-architecture.md)

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
DesignDocs → ExperienceSpec + surface_map + database_intent_bundle (typed)
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

The build pipeline produces five artifact families. Each family depends on
upstream families:

```
concept
  ├── brand          (depends on concept)
  └── design_docs    (depends on concept)
        └── workflow_bundle  (depends on design_docs)
              └── app_bundle (depends on design_docs, workflow_bundle, brand)
```

This dependency order is declared as `artifact_dependency_graph` in
`factory_app/workflows/extended_orchestration/extension_registry.json` and
loaded into `GlobalPackGraph` at runtime. It is the single source of truth for
downstream propagation.

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
computes its downstream set (`brand`, `design_docs`, `workflow_bundle`,
`app_bundle`) and marks all of them stale. The next refinement request will see
all of them as needing resolution.

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
| 4 | `workflow_bundle` | `workflow_revision` | AgentGenerator |
| 5 | `app_bundle` | `app_revision` | AppGenerator |

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
  "workflow_bundle": ["design_docs"],
  "app_bundle":      ["design_docs", "workflow_bundle", "brand"]
}
```

This field is part of `GlobalPackGraph` (`mozaiksai/core/workflow/pack/schema.py`).
It must stay in sync with the actual workflow sequence ownership declared in
`affected_declarative_families` on each sequence.

---

## Key Code Locations

| Concern | File |
|---------|------|
| Dependency graph schema | `mozaiksai/core/workflow/pack/schema.py` — `GlobalPackGraph.artifact_dependency_graph` |
| Dependency graph data | `factory_app/workflows/extended_orchestration/extension_registry.json` |
| BFS propagation + direct invalidation | `mozaiksai/control_plane/invalidation.py` — `ArtifactInvalidationService` |
| Stale families query | `mozaiksai/core/artifacts/store.py` — `get_stale_artifact_families()` |
| Deterministic pre-check (Tier 1) | `mozaiksai/control_plane/implementations/refinement_router.py` — `_stale_route()` |
| Stale priority + sequence map | `mozaiksai/control_plane/implementations/refinement_router.py` — `_STALE_PRIORITY`, `_STALE_SEQUENCE_MAP` |
| Control plane tool (Tier 2) | `factory_app/control_plane/tools/get_stale_artifact_families.py` |
| Tool registration | `factory_app/control_plane/config/tools.yaml` |
| Classifier prompt rules | `factory_app/control_plane/prompts/change_classifier_system.yaml` |
| Lifecycle status enum | `mozaiksai/core/artifacts/models.py` — `ArtifactLifecycleStatus` |

---

## Contributor Rules

- `affected_declarative_families` on each workflow sequence must be accurate.
  It drives both direct invalidation and the BFS starting set.
- When adding a new artifact family, update `artifact_dependency_graph` in
  `extension_registry.json`, add the family to `_STALE_PRIORITY` and
  `_STALE_SEQUENCE_MAP` in `refinement_router.py`, and update
  `GlobalPackGraph` in `schema.py` if needed.
- Do not add staleness routing logic to `control_plane.yaml`. The deterministic
  pre-check in `refinement_router.py` and `ArtifactInvalidationService` own
  that responsibility.
- Do not add staleness logic to tools. Tools return data; the router and LLM reason.
- Tests for BFS propagation: `tests/test_control_plane_invalidation.py`
- Tests for staleness query: `tests/test_artifact_store.py`
- Tests for deterministic stale routing: `tests/test_refinement_router.py`
