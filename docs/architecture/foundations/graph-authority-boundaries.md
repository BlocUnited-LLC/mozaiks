# Graph Authority Boundaries

Mozaiks has several graph-like systems. They are not interchangeable, and no
knowledge graph is currently part of the runtime authority chain.

FalkorDB is not active infrastructure in this repository today. It is not a
runtime dependency, no service config is shipped, no ingestion command is wired,
and active workflows use the Mongo-backed code context index for code-context
retrieval. Any future FalkorDB work must be an optional derived mirror for
querying, reasoning, audit, and explanation. It must not become the source of
truth for deterministic runtime behavior.

## Authority Matrix

| Graph | Source of truth | Runtime critical? | FalkorDB role |
| --- | --- | --- | --- |
| `artifact_dependency_graph` | `factory_app/workflows/extended_orchestration/extension_registry.json` | Yes, for artifact invalidation and refinement impact | Optional derived mirror only |
| `workflow_sequence` / handoffs | Workflow pack config, `workflow_sequences[]`, workflow `handoffs.yaml`, and loaded AG2 handoff objects | Yes, for workflow sequence routing and agent transitions | Optional derived mirror only |
| Control-plane refinement impact graph | `factory_app/control_plane/config/control_plane.yaml`, artifact metadata, and artifact dependency config | Yes, for refinement routing and stale artifact decisions | Optional derived mirror only |
| `AppContextGraph` | Source refs, accepted `ArtifactVersion` records, discovery snapshots, ownership boundaries, and control-plane app context records | No for runtime; target control-plane context artifact for impact explanation and staleness checks | Optional derived mirror only |
| Module event/reaction/notification graph | Module contracts plus the runtime module event dispatcher | Yes, for module action side effects, event reactions, and notification dispatch | Optional derived mirror only |
| UI route/component graph | `ui/route_manifest.json`, `ui/index.js`, admin registry files, and frontend component registry | Yes, for route rendering and app shell composition | Optional derived mirror only |
| Code context graph | Source code plus the Mongo-backed code context index | No for deterministic routing; useful for agent retrieval quality | Best future mirror candidate |
| Integration readiness graph | `AppBuildPlan`, app-scoped connector store, and connector vault backend | Yes, for build readiness and secret ownership | Redacted optional mirror only |
| App registry / build lifecycle graph | App registry records, artifact store, and build lifecycle records | Yes, for Studio management state | Optional derived mirror only |

## AppContextGraph Boundary

A deterministic `AppContextGraph` may become a Studio/control-plane artifact or
context graph for greenfield, brownfield, and hybrid apps. It is derived from
authoritative source refs, accepted artifacts, discovery snapshots, ownership
boundaries, validation evidence, and control-plane app context records.

`AppContextGraph` is not FalkorDB. It must carry source refs, checksums,
artifact version refs, indexed timestamps, and stale status so every node and
edge can be traced back to canonical source. FalkorDB may mirror it later for
querying, reasoning, audit, and explanation. FalkorDB remains optional mirror
only and non-authoritative.

## FalkorDB Boundaries

FalkorDB must not be used for:

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

Future knowledge graph mirrors must be read-only from the perspective of these
systems. Runtime, control-plane, module, connector, and UI code must continue to
read authoritative config or database records directly.

## Mirror Rules

Any future KG mirror must:

- identify the canonical source file or database record for every node and edge
- carry source version metadata such as git ref, build id, artifact version id,
  checksum, and indexed timestamp
- mark stale snapshots instead of silently acting as current truth
- mirror connector metadata only after sanitization
- never store connector secrets, raw credentials, tokens, or private provider
  payloads
- avoid proprietary provider or hosted-product examples in OSS docs, prompts,
  tests, and schemas
- fail closed as an unavailable index without changing runtime behavior

## First Safe Use

The safest future implementation step is a manual, read-only mirror for code
context and artifact dependency data:

1. mirror the existing Mongo-backed code context index into KG nodes for files,
   symbols, imports, and relationships
2. mirror `artifact_dependency_graph` and artifact lineage metadata for impact
   explanation
3. expose query tools only for audit, reasoning, and developer inspection

Do not wire KG queries into request routing, workflow starts, handoffs,
refinement decisions, event dispatch, permission checks, connector readiness
authority, or generated app persistence.
