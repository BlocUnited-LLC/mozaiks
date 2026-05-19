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

## How the Classifier Uses Staleness

At the `request_submitted` checkpoint, the `LLMChangeClassifier` has access to
the `get_stale_artifact_families` tool. It returns:

```json
{
  "stale_families": ["design_docs", "workflow_bundle"],
  "all_current": false
}
```

The classifier applies these routing rules before finalizing its
`patch | design | feature | core` decision:

1. **Upstream stale, target is downstream** — upgrade the classification to
   cover the stale upstream. Examples:
   - User requests an `app_bundle` patch, but `design_docs` is stale → classify
     as `design` so the route runs `DesignDocs` before `AppGenerator`
   - User requests an `app_bundle` patch, but `concept` is stale → classify as
     `core` so the route restarts from `ValueEngine`

2. **Only the target family itself is stale** — the staleness is consistent
   with the user's intent; do not upgrade the classification.

3. **`all_current: true`** — route purely from the request text; no adjustment.

4. **A downstream family is stale** — no upgrade needed; the selected route
   will refresh it as a side effect.

The staleness signal is advisory to the classifier, not a hard override. The
classifier is expected to reason about it along with the request text and
produce a classification that covers the minimum necessary upstream work.

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
| Control plane tool | `factory_app/control_plane/tools/get_stale_artifact_families.py` |
| Tool registration | `factory_app/control_plane/config/tools.yaml` |
| Classifier prompt rules | `factory_app/control_plane/prompts/change_classifier_system.yaml` |
| Lifecycle status enum | `mozaiksai/core/artifacts/models.py` — `ArtifactLifecycleStatus` |

---

## Contributor Rules

- `affected_declarative_families` on each workflow sequence must be accurate.
  It drives both direct invalidation and the BFS starting set.
- Do not add staleness routing logic to `control_plane.yaml`. The classifier
  prompt and `ArtifactInvalidationService` own that responsibility.
- Do not add staleness logic to tools. Tools return data; the LLM reasons.
- When adding a new artifact family, update `artifact_dependency_graph` in
  `extension_registry.json` and add the field to `GlobalPackGraph` if needed.
- Tests for BFS propagation: `tests/test_control_plane_invalidation.py`
- Tests for staleness query: `tests/test_artifact_store.py`
