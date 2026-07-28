# App Intelligence Plane

The App Intelligence Plane is the Mozaiks substrate that understands an app
before agents change it.

It is shared by greenfield apps, existing-app adoption, generated app revision,
and ongoing refinement. Existing-app discovery is one intake path into this
plane, not a separate code-context system.

## Canonical Contract

Every source-backed app context build produces the same artifact stack:

1. `SourceContextBundle`
2. `AppContextGraph`
3. `AppIntelligenceSnapshot`
4. `AppContextVersion`

`SourceContextBundle` carries selected, redacted source files, chunks, symbols,
imports, parser status, scan health, and retrieval-safe file contents.

`AppContextGraph` carries deterministic relationships over those files and
contracts: files, symbols, routes, modules, pages, workflows, integrations,
data surfaces, ownership, risks, and dependency edges.

`AppIntelligenceSnapshot` carries compact app understanding for agents and
Studio: architecture summary, capability map, ownership summary, integration
surfaces, data surfaces, risk hints, context coverage, and workflow/checkpoint
context policy. It never replaces exact source retrieval.

`AppContextVersion` points to the current artifact set and records mode,
source refs, artifact refs, graph snapshot ref, ownership boundaries, surface
indexes, stale status, validation summary, review state, and promotion state.

## Source Of Truth

The App Intelligence Plane is rebuildable from canonical records:

- source refs
- accepted, staged, or generated `ArtifactVersion` records
- app workspace files
- generated app bundle files
- discovery snapshots
- validation evidence
- ownership boundaries
- refinement and promotion state

Graph databases and search indexes are mirrors. They are allowed to accelerate
queries and Studio visualization, but they must not become authority.

## Parser And Graph Backend

Tree-sitter is baseline parser infrastructure for source-backed code context.
Supported parser packages ship with the core Mozaiks install. Deterministic
fallback parsers exist only for resilience when a parser cannot initialize.

FalkorDB is the recommended production graph backend for larger repos,
cross-version traversal, and Studio graph inspection. It mirrors canonical
`AppContextGraph` and `AppIntelligenceSnapshot` facts. It must preserve
artifact ids, source refs, checksums, stale status, indexed timestamps, and
provenance. It must not store raw secrets or become required for runtime app
execution.

## Agent Context Policy

Agents do not receive full repositories in prompt context. They receive compact
summaries plus retrieval tools.

| Surface | Context |
| --- | --- |
| `ExistingAppDiscovery` | `app_intelligence_catalog`, compact graph pack, source catalog, source retrieval tools, and repo/API/runtime evidence as fallback diagnostics |
| `AppGenerator` | current or generated AppContext summary, App Intelligence catalog, and selected source or artifact context for the generation surface |
| `AgentGenerator` | workflow/module/service/page context from App Intelligence plus exact files only when selected |
| Refinement classifier | revision context, artifact summary, stale families, and App Intelligence freshness |
| Scope selection | App Intelligence catalog, graph catalog, workspace catalog, and bounded source search |
| Contract surface selection | contract surface context, App Intelligence catalog, and source search |
| Coding refinement | explicit scoped files, graph scope, exact source reads, and related source files |
| Validation and review | impacted files, modules, routes, configs, tests, graph relationships, and risk hints |

The rule is simple: use `AppIntelligenceSnapshot` for app understanding,
`AppContextGraph` for relationships, and `SourceContextBundle` tools for exact
code.

## Workflow Journey

### Existing App

1. User connects a repo or local source root.
2. Mozaiks scans source through the shared scan policy.
3. Tree-sitter extracts code facts.
4. Mozaiks builds `SourceContextBundle`, `AppContextGraph`, and
   `AppIntelligenceSnapshot`.
5. The preload registers a current source-backed `AppContextVersion` before
   the first discovery agent turn.
6. `ExistingAppDiscovery` uses those artifacts to produce adoption evidence.
7. The saved discovery may enrich the context with adoption, ownership, and
   inventory artifacts.
8. Later factory workflows and refinement checkpoints retrieve context from the
   current AppContext instead of rerunning discovery by default.

### Greenfield App

1. Factory workflows generate an app bundle or workflow bundle.
2. The artifact workspace is indexed through the same shared indexer.
3. Mozaiks persists source context, graph, and App Intelligence artifacts.
4. `AppContextVersion` becomes the current managed context for future edits.
5. Refinement uses that current context to scope changes and read exact files.

### Continuous Refinement

After a staged edit, promotion, or source refresh, Mozaiks should:

- rescan changed source roots or selected artifacts
- rebuild the source bundle, graph, and intelligence snapshot
- register a new `AppContextVersion`
- mark stale context when source refs, artifact refs, or validation evidence no
  longer match
- use stale status to decide whether to patch locally, ask for review, or rerun
  the relevant workflow sequence

## Ownership Boundaries

The App Intelligence Plane records ownership instead of assuming every file is
safe to mutate.

Ownership classes:

- `read_only_discovered`
- `generated_overlay`
- `staged_patch`
- `migrated_owned`
- `external_system`

Allowed operations:

- `inspect`
- `index`
- `explain`
- `stage_patch`
- `generate_overlay`
- `generate_adapter`
- `propose_migration`
- `promote`
- `open_pr`
- `ignore`

Existing source remains read-only until a user approves a staged patch,
migration, generated overlay, or PR. Generated app bundles can be owned by
Mozaiks, but still move through review and promotion.

## What The Plane Is Not

It is not:

- a replacement for AG2 execution
- a workflow router
- a permission or entitlement authority
- a module action executor
- a secret store
- a database migration engine
- a reason to dump whole repositories into prompts
- a hosted-product-only feature

The App Intelligence Plane informs agents and Studio. Canonical runtime
contracts still execute the app.

## Implementation Map

| Concern | File |
| --- | --- |
| Source scan policy | `mozaiksai/core/app_context/scan_policy.py` |
| Source corpus and retrieval helpers | `mozaiksai/core/app_context/source_corpus.py` |
| Context Graph builder and Tree-sitter extraction | `mozaiksai/core/app_context/context_graph.py` |
| App Intelligence synthesis | `mozaiksai/core/app_context/intelligence.py` |
| Shared source-backed indexer | `mozaiksai/core/app_context/indexer.py` |
| Context health | `mozaiksai/core/app_context/health.py` |
| AppContextVersion persistence | `mozaiksai/core/app_context/store.py` |
| Control-plane context loading | `mozaiksai/control_plane/app_context.py` |
| Refinement App Intelligence tool | `factory_app/refinement_harness/tools/app_intelligence.py` |
| Refinement source retrieval tools | `factory_app/refinement_harness/tools/source_context.py` |
| Existing-app preload | `factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py` |

## Production Posture

For local and small repos, artifact-backed snapshots are sufficient.

For hosted or enterprise deployments, configure FalkorDB as the graph mirror and
keep object/artifact storage as the authority. The product should present this
as "App Intelligence" or "Context Graph indexing", not as a database choice
users need to understand.
