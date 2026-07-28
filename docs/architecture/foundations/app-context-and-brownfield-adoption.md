# App Context And Existing-App Adoption

`AppContextVersion` is the durable app-state record used by Studio, Factory
workflows, and the Refinement Engine. It is the versioned handle over the App
Intelligence Plane for greenfield, existing-app, and hybrid apps.

Existing-app adoption is an intake journey into AppContext. It does not own a
separate source-of-truth model.

## Core Objects

`AppContextVersion` records:

- `context_version_id`
- `app_id`
- `mode`: `greenfield`, `brownfield`, or `hybrid`
- `source_refs`
- `artifact_refs`
- `graph_snapshot_ref`
- `ownership_boundaries`
- `surface_indexes`
- `indexed_at`
- `stale_status`
- `stale_reasons`
- `validation_summary`
- `review_state`
- `promotion_state`

Source-backed context versions normally reference:

- `source_context_bundle`
- `app_context_graph`
- `app_intelligence_snapshot`
- `app_context_version`

The source bundle contains exact redacted code context, the graph contains
relationships, and the intelligence snapshot contains compact app
understanding for agents.

## Modes

| Mode | Meaning |
| --- | --- |
| `greenfield` | Mozaiks generated the current app bundle or workflow bundle and owns refinement through generated artifacts. |
| `brownfield` | Mozaiks indexed an existing app source ref and treats it as discovered source until ownership changes. |
| `hybrid` | Mozaiks owns some generated or migrated surfaces while other surfaces remain discovered or external. |

The mode describes context provenance. It does not decide workflow routing by
itself.

## Ownership Classes

`AppContextVersion.ownership_boundaries[]` controls what agents may propose and
what must be reviewed.

- `read_only_discovered`: discovered existing source; inspect and explain only
  unless the user approves a patch or PR.
- `generated_overlay`: Mozaiks-generated overlay code that wraps or augments an
  existing app.
- `staged_patch`: proposed code change awaiting review or promotion.
- `migrated_owned`: source the user has approved for Mozaiks-owned migration or
  continued refinement.
- `external_system`: external API, provider, database, hosted capability, or
  third-party dependency.

Allowed operations are explicit: `inspect`, `index`, `explain`,
`stage_patch`, `generate_overlay`, `generate_adapter`, `propose_migration`,
`promote`, `open_pr`, and `ignore`.

## Existing-App Journey

Canonical existing-app adoption:

```text
user connects repo/source/API evidence
-> shared source scan policy selects safe files
-> Tree-sitter extracts symbols/imports/references
-> build SourceContextBundle
-> build AppContextGraph
-> build AppIntelligenceSnapshot
-> ExistingAppDiscovery produces adoption evidence
-> save app-context artifacts
-> register current AppContextVersion
```

`ExistingAppDiscovery` is the onboarding and indexing workflow for existing
apps. It is not the default greenfield build path, and it should not copy code
into generated apps without an explicit adoption plan.

Discovery snapshots are evidence, not authority. The existing repo remains the
source of truth until explicit transfer through staged patch, generated overlay,
migration approval, or PR approval.

## Greenfield Journey

Canonical greenfield registration:

```text
factory workflow produces app/workflow artifact
-> shared source indexer reads artifact workspace or bundle
-> build SourceContextBundle when source files are available
-> build AppContextGraph
-> build AppIntelligenceSnapshot
-> register current AppContextVersion
```

`AppGenerator` produces generated app context, but AppGenerator is not the
entire build system. `AgentGenerator`, DesignDocs, ValueEngine, build
sequences, validation, promotion, and refinement all consume the same
AppContext state.

## Refinement Boundary

The Refinement Engine selects context version and routing before agents edit.

It uses:

- `current_app_context_version_id`
- current `AppContextVersion`
- artifact lineage
- stale status
- ownership boundaries
- `app_intelligence_snapshot`
- `app_context_graph`
- source retrieval tools

Classifier checkpoints use app-context freshness and artifact lineage. Scope
selection uses App Intelligence, graph catalogs, workspace catalogs, and source
search. Coding checkpoints receive explicit scoped files plus exact source
reads and related-file retrieval.

Agents should not infer authority from source paths alone. They must respect
ownership boundaries and review state.

## Staging And Source Of Truth

Staged patches are proposals. They do not become owned app facts until accepted
or promoted through the artifact lifecycle.

Graph backend mirrors are never source of truth. FalkorDB may mirror graph and
intelligence artifacts for production-scale querying, but `AppContextVersion`
and artifact storage remain canonical.

Runtime execution does not depend on the graph backend. Module actions, routes,
permissions, events, persistence, entitlement checks, and workflow execution
read their own canonical contracts.

## Context Refresh

Context refresh creates a new `AppContextVersion` when source refs, generated
artifacts, or validation evidence change.

Refresh may be triggered by:

- explicit user request
- source ref change
- generated artifact promotion
- stale context policy
- validation evidence that invalidates a prior context

The refresh path rescans source or artifact files, rebuilds source/graph/
intelligence artifacts, and registers the new context version. The previous
version remains useful for diff, review, and rollback context.

## App Intelligence Handoff

Every workflow should consume the smallest useful context:

| Workflow/checkpoint | Context |
| --- | --- |
| `ExistingAppDiscovery` | App Intelligence catalog, compact graph pack, source catalog, repo/API/runtime evidence, retrieval tools |
| `AppGenerator` | current AppContext summary, App Intelligence catalog, selected artifact/source surfaces |
| `AgentGenerator` | workflow/module/service/page context from App Intelligence and exact files only when selected |
| Refinement classifier | revision context, artifact summary, stale families, and AppContext freshness |
| Refinement scope | App Intelligence, graph catalog, workspace catalog, source search |
| Refinement coding | selected files, graph scope, exact source reads, related files |

The durable rule: summaries guide agents, graph relationships scope the work,
and exact source retrieval provides proof.

## Build Journey Placement

App Intelligence is not an `AppPage`. It is workflow and control-plane context:

- transition UI keeps the user on an intake screen while App Intelligence
  indexing runs
- `ExistingAppDiscovery` owns the chat-visible App Intelligence overview as a
  workflow UI surface
- the Refinement Engine consumes App Intelligence through checkpoint tools
  before classifying, scoping, or coding a requested change
- AppPages remain persistent generated app surfaces under `app/ui/`; they do
  not own builder indexing or refinement context

The create journey enters `app_type_selector`, then the brownfield branch enters
`brownfield_repo_input`, launches `ExistingAppDiscovery`, runs pre-chat indexing,
registers a current source-backed `AppContextVersion`, emits
`AppIntelligenceOverviewCard`, and then lets agents use retrieval tools for
exact files.

## Implementation Map

| Concern | File |
| --- | --- |
| AppContext models | `mozaiksai/core/app_context/models.py` |
| App Intelligence snapshot | `mozaiksai/core/app_context/intelligence.py` |
| Source-backed indexer | `mozaiksai/core/app_context/indexer.py` |
| AppContext persistence | `mozaiksai/core/app_context/store.py` |
| Control-plane loading | `mozaiksai/control_plane/app_context.py` |
