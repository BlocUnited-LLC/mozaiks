# Artifact Terminology

The word "artifact" has more than one meaning in this codebase. This document
maps each distinct concept to its canonical name and explains the three
collision points where an LLM or contributor will most likely confuse them.

---

## Canonical Names

| Canonical name | What it is | Primary location |
|---|---|---|
| **Build record** | A versioned, lifecycle-tracked record of one build output (app_bundle, workflow_bundle, design_docs, etc.) stored in MongoDB. Primary identifier format: `av_…`. | `mozaiksai/core/artifacts/models.py` — `BuildRecord` |
| **Build family** | The semantic category of a build record: `app_bundle`, `workflow_bundle`, `design_docs`, `experience_spec`, `concept`, `brand`. The key used by the dependency graph and staleness routing. | `BuildRecord.build_family`; harness routing table |
| **Build key** | The variant namespace within a build family, e.g. `"primary"`. Defaults to the family name when omitted. | `BuildRecord.build_key` |
| **Build record ID** | The stable identifier for one build record instance (`av_…`). Used for lineage, direct invalidation, and re-entry seeding. | `BuildRecord.build_record_id` (stored as `_id` in MongoDB) |
| **Build record store** | The MongoDB-backed store for build records. Methods: `create_build_record`, `get_build_record`, `list_build_records`, `invalidate_artifact_version_refs`, etc. | `mozaiksai/core/artifacts/store.py` — `BuildRecordStore` |
| **Blob / object storage port** | Protocol for storing and retrieving large binary blobs (bundle ZIPs, generated files) on disk or cloud. Methods: `write`, `read`, `delete`, `exists`, `url_for`. Completely separate from build records. | `mozaiksai/core/ports/artifact_store.py` — `ArtifactStore` Protocol |
| **Bundle content store** | Protocol for storing and retrieving generated app bundle ZIPs specifically. Returns a `content_ref` (opaque backend handle). | `mozaiksai/core/artifacts/content_store.py` — `ArtifactContentStore` |
| **Builder metadata store** | MongoDB store for factory pipeline intermediate artifacts: concepts, build plans, design docs, theme captures. NOT versioned build output. | `mozaiksai/core/data/persistence/artifact_store.py` — `BuilderArtifactStore` |
| **UI artifact** | A transient display surface emitted by a workflow tool during a chat session. Rendered in the right-side panel (`display="artifact"`) or inline in chat (`display="inline"`). No persistent identity in MongoDB; lifecycle is session-scoped. | `chat-ui/src/pages/hooks/useArtifacts.js`; `ArtifactPanel.jsx` |
| **Media generated asset** | An AI-generated image or graphic. Can be promoted to a brand/app/page asset or kept as an ephemeral chat surface. Not a build record. | `mozaiksai/core/media/artifacts.py` — `artifact_type: "core.media.generated_asset"` |
| **Workflow review artifact** | A durable proposal record saved by a workflow for human review before acceptance. Stored in `workflow_review_artifacts` collection, not in build records. | `mozaiksai/core/workflow/artifacts/review_store.py` |
| **Stale artifact family** | A build family (not a single record) that has at least one `stale` version and no `current` version. Computed dynamically; not a stored entity. | `mozaiksai/core/artifacts/store.py` — `get_stale_build_families()` |

---

## Three Collision Points

### 1. Build record store vs. blob storage port — same class name

**`ArtifactStore` appears twice with completely different semantics:**

| Location | What it is | Methods |
|---|---|---|
| `mozaiksai/core/ports/artifact_store.py` | Protocol for binary blob I/O (disk/S3) | `write`, `read`, `delete`, `exists`, `url_for` |
| `mozaiksai/core/artifacts/store.py:1167` | Legacy alias for `BuildRecordStore` | `create_build_record`, `get_build_record`, `list_build_records`, … |

These have no overlapping methods. Importing `ArtifactStore` without the full
module path produces code that calls the wrong object.

**Rules:**
- Use `BuildRecordStore` for build record operations. Do not use the `ArtifactStore` alias.
- Use the `ArtifactStore` Protocol from `core/ports/artifact_store.py` for blob/object storage injection only.
- When reading existing code, always resolve the import before assuming what the object does.

---

### 2. Build family (routing key) vs. storage `artifact_kind` (blob path component)

The string values are often identical (`"app_bundle"`, `"workflow_bundle"`) but
they serve entirely different purposes:

| Usage | What it means |
|---|---|
| `BuildRecord.build_family` | Semantic category of a version record. Used by the dependency graph, staleness propagation, and routing table dispatch. |
| `ArtifactStore.write(..., artifact_kind=…)` | Directory or path prefix used to organize blobs in local filesystem or S3. An implementation detail of the blob backend, not a semantic routing key. |
| `ControlPlaneArtifactRoutingManifest.build_family` | Configuration entry in `harness.yaml` declaring which workflow_sequences handle this family. Read-only routing config; not a runtime state property. |

Changing which workflow handles `"app_bundle"` is a routing config change.
Looking up stale `"app_bundle"` records is a build record store query.
Storing a bundle ZIP under `app_bundle/` in S3 is a blob storage path.
These are three operations on three separate systems that happen to share a
string value.

---

### 3. UI artifact vs. durable build record

| | UI artifact | Build record |
|---|---|---|
| What it is | Display surface in a chat session | Versioned, persistent build output |
| Stored in | Chat session memory (WebSocket state, React) | MongoDB `ArtifactVersions` collection |
| Identifier | Derived from `tool_call_id` or payload; format varies | `av_…` prefix; stable across sessions |
| Lifecycle | Session-scoped; gone when session ends or UI resets | `draft → current → stale → superseded → archived` |
| How it's created | Tool calls `emit_ui_surface(display="artifact", …)` | Workflow calls `persist_summary_artifact()` |
| `artifact_id` field | UI display handle derived in `actionUtils.js:deriveArtifactId()` | Stored as `_id` / `build_record_id` in MongoDB |

An LLM writing tool code that calls `emit_ui_surface` is creating a UI
artifact. An LLM writing workflow lifecycle code that calls
`persist_summary_artifact` is creating a build record. These are not the same
operation and the results are not interchangeable.

---

## Legacy Field Names Still in the Codebase

The `BuildRecord` model and its callers are partway through a rename. The table
below shows which old names are still present and where they live.

| Old name | New canonical name | Status |
|---|---|---|
| `artifact_kind` | `build_family` | Remapped by `model_validator(mode="before")` in `BuildRecord`, `RefinementRequest`, `CodingWorkerRequest`, `ContractSurfacePlan`. Prior-api `@property` aliases remain for backward compat. |
| `artifact_key` | `build_key` | Same remapping pattern. |
| `artifact_version_id` | `build_record_id` | Same remapping pattern. |
| `ArtifactVersionDoc` | `BuildRecord` | `ArtifactVersionDoc = BuildRecord` alias in `models.py`. These are the same class. Do not write migration code between them. |
| `ArtifactStore` | `BuildRecordStore` | Alias at `store.py:1167`. Prefer `BuildRecordStore` in new code. |
| `get_stale_artifact_families()` | `get_stale_build_families()` | Both exist in `store.py`. Prefer `get_stale_build_families()` in new code. |
| `default_artifact_kind` | `default_build_family` | Harness routing YAML and schema; remapped by `model_validator`. |

The backward-compat remappers mean old field names in incoming payloads and
MongoDB documents still parse correctly. They do not mean old names are the
right choice in new code.

---

## Context-Seed Dual-Writes

When the refinement router seeds a workflow's `context_variables`, it writes
both old and new names so existing workflow YAML that reads `artifact_kind` or
`artifact_version_id` from context keeps working:

```python
context_seed["build_family"] = request.build_family
context_seed["artifact_kind"] = request.build_family       # workflow compat only
context_seed["artifact_version_id"] = request.build_record_id  # workflow compat only
```

These dual-writes are a transition measure, not a permanent API. New workflow
YAML should read `build_family` and `build_record_id`.

---

## What Not To Rename

These uses of "artifact" are intentional and must not be changed:

| Name | Why it stays |
|---|---|
| `display="artifact"` (UI display mode) | Protocol value in the chat tool event system. Changing it breaks the frontend rendering pipeline. |
| `ArtifactStore` Protocol (`core/ports/artifact_store.py`) | This is the blob storage port name. The collision is with the `BuildRecordStore` alias, not with this Protocol. |
| `artifact_type: "core.media.generated_asset"` | Typed event discriminator in the media system. Changing it is a media event contract break. |
| `workflow_review_artifacts` (MongoDB collection name) | Stored collection name. Changing it is a data migration. |
| `artifact_dependency_graph` in `extension_registry.json` | Declared graph field name loaded by `GlobalPackGraph`. Changing it is a breaking schema change. |

---

## Related Docs

- [Artifact Staleness and Routing](../builder/artifact-staleness-and-routing.md)
- [Persistence and Artifact Storage](../foundations/events-and-data/persistence-and-artifact-storage.md)
- [Refinement Engine](../workflows/refinement-engine.md)
- [Refinement Harness Architecture](../workflows/refinement-harness-architecture.md)
- [UI Surface Model](../frontend/chat-ui/ui-surface-model.md)
