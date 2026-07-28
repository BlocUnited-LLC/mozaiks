# Graph Authority Boundaries

Mozaiks treats the Context Graph as the relationship artifact inside the App
Intelligence Plane. This is the context graph intelligence layer, not a graph
database as the runtime source of truth.

The in-repo canonical graph snapshot is `AppContextGraph`. It is built from
source refs, accepted or staged `ArtifactVersion` records, generated bundle
files, workflow bundle files, discovery evidence, ownership boundaries,
validation evidence, and refinement history. Refinement Engine tools use it to
select context, explain impact, rank candidate files, and prepare compact LLM
context packs.

When source files are available, graph retrieval is backed by a
`SourceContextBundle`: a bounded, redacted source corpus with file contents,
chunks, symbols, imports, scan health, and parser status. The bundle carries the
actual code evidence; the graph carries relationships over that evidence.
`AppIntelligenceSnapshot` is the primary intelligence layer over that graph. It
summarizes architecture, capabilities, ownership, integrations, data surfaces,
risks, and agent context policy without copying raw source into prompts.

Code indexing uses Tree-sitter as baseline parser infrastructure, with
deterministic language-native fallbacks only for runtime resilience when a
parser package is unavailable. Parser choice changes extraction depth; it does
not change graph authority.

FalkorDB or another graph database may be used as a backend mirror for
large-scale graph querying and Studio visualization. FalkorDB is recommended for
production-scale code-context deployments, but the mirror must carry the same
version, checksum, provenance, and stale-state metadata as the canonical
snapshot.

## Authority Matrix

| Graph | Source of truth | Runtime critical? | FalkorDB role |
| --- | --- | --- | --- |
| `artifact_dependency_graph` | `factory_app/workflows/extended_orchestration/extension_registry.json` | Yes, for artifact invalidation and refinement impact | Optional backend mirror |
| `workflow_sequence` / handoffs | Workflow pack config, `workflow_sequences[]`, workflow `transition_graph.yaml`, and loaded AG2 handoff objects | Yes, for workflow sequence routing and agent transitions | Optional backend mirror |
| Refinement Engine refinement impact graph | `factory_app/refinement_harness/config/harness.yaml`, artifact metadata, app context policy, and artifact dependency config | Yes, for refinement routing and stale artifact decisions | Optional backend mirror |
| `AppContextGraph` / App Intelligence relationship layer | Source refs, accepted or staged `ArtifactVersion` records, discovery snapshots, generated bundle files, ownership boundaries, validation evidence, and Refinement Engine app context records | No for runtime execution; yes for LLM context quality, scope selection, impact explanation, and Studio inspection | Preferred optional backend mirror |
| Module event/reaction/notification graph | Module contracts plus the runtime module event dispatcher | Yes, for module action side effects, event reactions, and notification dispatch | Optional backend mirror |
| UI route/component graph | `ui/route_manifest.json`, `ui/index.js`, admin registry files, and frontend component registry | Yes, for route rendering and app shell composition | Optional backend mirror |
| Integration readiness graph | `AppBuildPlan`, app-scoped connector store, and connector vault backend | Yes, for build readiness and secret ownership | Redacted optional backend mirror |
| App registry / build lifecycle graph | App registry records, artifact store, and build lifecycle records | Yes, for Studio management state | Optional backend mirror |

## Context Graph Boundary

`AppContextGraph` is the canonical graph contract for factory and Studio
context. It may describe:

- app, artifact, source-ref, file, config, page, component, module, workflow,
  agent, tool, symbol, integration, data entity, risk, and staged patch nodes
- contains, declares, defines, imports, renders, calls, reads, writes,
  references, produces, consumes, triggers, replaces, wraps, and dependency
  edges
- source file paths, artifact version ids, checksums, indexed timestamps,
  ownership class, and stale status for audit

This graph is the relationship layer for:

- code-context retrieval
- prompt injection
- graph-aware coding scope selection
- symbol-level refactor impact analysis
- related file and symbol discovery
- brownfield adoption mapping
- refinement impact explanation
- Studio inspection and build-sequence UX

## App Intelligence Pipeline

The app-aware coding system is built in four layers:

1. Shared AppContext indexing:
   - `mozaiksai.core.app_context.indexer` selects the canonical source scan or
     artifact workspace file map.
   - It builds the redacted `SourceContextBundle`, deterministic
     `AppContextGraph`, parser status, scan health, and persisted context
     artifacts.
   - Workflows and refinement tools consume this indexed context instead of
     assembling separate code-context payloads.
2. Deterministic syntax extraction:
   - Tree-sitter as the baseline parser path for supported languages.
   - Python AST and conservative JavaScript/TypeScript parsing as resilience
     fallbacks when a parser package is unavailable.
   - Outputs file nodes, symbol nodes, imports, references, and call edges.
3. Mozaiks contract mapping:
   - maps files and symbols to module handlers, services, repos, policies,
     schemas, page components, workflow tools, agents, and declared module
     actions
   - links declared module actions to their implementing handler symbols with
     `implements_capability`
4. App Intelligence synthesis:
   - builds `AppIntelligenceSnapshot` from the source corpus and graph
   - summarizes architecture, capabilities, ownership, integration surfaces,
     data surfaces, risk hints, and agent context policy
   - never stores full source text
5. LLM semantic annotation:
   - receives a bounded request built from deterministic graph facts
   - may tag symbols with purpose, domain concepts, side effects, invariants,
     risk, and likely tests
   - is advisory metadata only and never becomes the authority for execution,
     routing, permissions, persistence, secrets, or promotion

It is not the authority for:

- request routing
- workflow execution
- AG2 handoffs
- module action execution
- event dispatch
- permission or entitlement enforcement
- payment or billing enforcement
- connector secret storage
- generated app database access
- UI route rendering
- artifact current/draft/promotion state

Those decisions continue to read canonical contracts and stores directly.

## Backend Rules

The default OSS implementation can build and query `AppContextGraph` snapshots
without a graph database. FalkorDB should be recommended production
infrastructure for larger graphs and interactive inspection, but it remains a
mirror because canonical context must be rebuildable from source refs,
artifacts, and AppContext records.

Any graph backend must:

- mirror canonical `AppContextGraph` snapshots instead of inventing a separate
  schema
- preserve artifact version ids, source refs, checksums, indexed timestamps, and
  stale status
- never store connector secrets, raw API keys, tokens, passwords, credentials,
  or private payloads in graph storage
- fail as an unavailable context index without changing runtime behavior
- avoid proprietary provider or hosted-product examples in OSS docs, prompts,
  tests, and schemas

## Build-Sequence UX

The build sequence should surface the Context Graph as managed context rather
than asking users to choose an implementation detail.

Recommended UX:

1. During greenfield generation, show "App Intelligence indexing" as an
   automatic post-artifact step after `workflow_bundle` and `app_bundle`
   creation.
2. During existing-app adoption, show discovery evidence flowing into the
   current `AppContextVersion`, graph snapshot, and intelligence snapshot.
3. In review and refinement, show affected pages, modules, workflows, files,
   symbols, and risks before a patch is staged.
4. In settings, show the active graph backend:
   `embedded`/artifact-backed for local and small installs, `FalkorDB` for
   production-scale deployments when configured.

Users should not need to understand FalkorDB to build apps. They should see App
Intelligence and Context Graph indexing. Operators can configure FalkorDB as
the backing graph engine when they want larger-scale graph querying or
visualization.

