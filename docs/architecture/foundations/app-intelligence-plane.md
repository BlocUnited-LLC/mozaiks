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

## Source Import And Indexing Jobs

Source-backed context is created by an explicit App Intelligence index job.
Studio uses that job for both local workspace indexing and repository import.

The job carries:

- source kind: `local_workspace` or `git_repository`
- repo URL and branch for public Git imports
- monorepo path
- ignored paths merged into the source scan policy
- durable phase state: clone, scan, source index, symbol parse, graph build,
  intelligence synthesis, ready
- public readiness metadata for Studio

Public job payloads redact absolute workspace roots and auth connector ids.
OSS Git import supports public HTTP(S) repositories only. Private repository
auth must be supplied by a connector-backed credential resolver in the hosted
product or operator deployment.

## Parser And Graph Backend

Tree-sitter is baseline parser infrastructure for source-backed code context.
Supported parser packages ship with the core Mozaiks install. Deterministic
fallback parsers exist only for resilience when a parser cannot initialize.

Tree-sitter is one layer of App Intelligence. It provides deterministic source
facts such as symbols, imports, routes, components, functions, and classes. It
does not by itself understand product intent, database semantics, API behavior,
runtime behavior, or deployment readiness. Mozaiks combines AST facts with
source search, manifests, data contracts, validation evidence, runtime
artifacts, and user/workflow intent.

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

## Source Validation

Framework detection emits candidate validation commands such as lint, test,
typecheck, and build. The control plane owns the canonical source validation
runner for those commands.

Validation runs only from a current App Intelligence source root or an explicit
backend workspace root. Studio does not accept arbitrary browser-supplied local
paths for validation. The runner creates an isolated workspace copy by default,
overlays staged file changes when supplied, and executes only parsed argv
commands with allowed executables and contained working directories. Long-lived
dev/start commands are not validation commands.

Install commands are detected but not run by default. They require an explicit
`include_install` request because dependency installation can be slow, networked,
and mutating. When runnable commands are unavailable, Mozaiks falls back to
deterministic checks such as Python syntax compilation and JSON/YAML manifest
parsing.

Validation result payloads are redacted: they report command status, exit code,
timing, bounded output tails, fallback checks, selected command kinds, and
warnings without exposing absolute workspace roots.

Scoped coding refinement uses this same runner after the worker has produced
staged file content. The worker overlays the proposed files into an isolated
copy of the current App Intelligence source root and persists the resulting
source-validation payload on the draft artifact. Model-suggested validation
commands are retained only as plan hints; executable commands come from
framework detection and the App Intelligence index.

Promotion gates use the persisted artifact validation status. `passed` is the
normal accept/promote path. `failed` blocks acceptance and promotion. `skipped`
or `pending` evidence may be accepted or promoted only through an explicit
operator validation override that is recorded in artifact metadata.

## Studio Surface

Studio must show a visible App Intelligence panel before source-editing work:

- readiness: missing, queued, indexing, ready, stale, degraded, or failed
- current indexing phase and progress
- detected primary framework
- indexed file count
- graph node and edge counts
- detected frameworks
- validation commands available to the refinement harness
- warnings from scan, parser, graph, health, and stale-context checks

This makes indexing a visible transition state instead of a silent chat delay.

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

## Indexing Job State Machine

Each App Intelligence index request is tracked as an `AppIntelligenceIndexJob`
document. The job progresses through a fixed phase sequence:

| Phase ID | Label | local_workspace | git_repository |
| --- | --- | --- | --- |
| `repo_clone` | Clone repo | `skipped` | `pending → running → complete` |
| `workspace_scan` | Scan files | `pending → running → complete` | `pending → running → complete` |
| `source_index` | Source index | `pending → running → complete` | `pending → running → complete` |
| `symbol_parse` | Parse symbols | `pending → running → complete` | `pending → running → complete` |
| `context_graph` | Build graph | `pending → running → complete` | `pending → running → complete` |
| `app_intelligence` | App intelligence | `pending → running → complete` | `pending → running → complete` |
| `ready` | Ready | terminal | terminal |

`repo_clone` is always created but immediately set to `skipped` for
`local_workspace` imports — the phase list stays the same regardless of source
kind so UI progress bars never need to know the import kind.

After the `app_intelligence` phase completes, all phases are transitioned to
`complete` (or `skipped`) and the job `current_phase` is set to `ready`.
Completed jobs store `import_result` (public payload, redacted), `app_intelligence`
(framework detection and intelligence snapshot), and `context_readiness`.

## Studio API Surface

Studio exposes four endpoints for App Intelligence management:

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/studio/apps/{app_id}/context/app-intelligence/index` | Start a `local_workspace` index job |
| `POST` | `/api/studio/apps/{app_id}/context/source-import` | Start a `git_repository` or `local_workspace` import + index job |
| `GET` | `/api/studio/apps/{app_id}/context/app-intelligence/index/latest` | Poll the latest job (public payload) |
| `GET` | `/api/studio/apps/{app_id}/context/app-intelligence/index/{job_id}` | Fetch a specific job by ID |
| `POST` | `/api/studio/apps/{app_id}/context/validation/run` | Run source validation against the current App Intelligence context |

The `source-import` endpoint accepts a `SourceImportRequest` body (source kind,
workspace root or repo URL, branch, monorepo path, ignored paths). The
`app-intelligence/index` endpoint is a shorthand for local workspace indexing
that does not require a body. Both start a background job and return `202
Accepted` with the job document.

Public job payloads produced by these endpoints redact `workspace_root` and
`selected_root` (replaced with `workspace_root_present` / `selected_root_present`
booleans) and strip `import_root` from metadata.

## Implementation Map

| Concern | File |
| --- | --- |
| Source scan policy | `mozaiksai/core/app_context/scan_policy.py` |
| Framework detection | `mozaiksai/core/app_context/framework_detection.py` |
| Source corpus and retrieval helpers | `mozaiksai/core/app_context/source_corpus.py` |
| Context Graph builder and Tree-sitter extraction | `mozaiksai/core/app_context/context_graph.py` |
| App Intelligence synthesis | `mozaiksai/core/app_context/intelligence.py` |
| Shared source-backed indexer | `mozaiksai/core/app_context/indexer.py` |
| Context health | `mozaiksai/core/app_context/health.py` |
| AppContextVersion persistence | `mozaiksai/core/app_context/store.py` |
| Source import resolver | `mozaiksai/control_plane/source_import.py` |
| Studio-visible indexing jobs | `mozaiksai/control_plane/app_intelligence_jobs.py` |
| Source validation runner | `mozaiksai/control_plane/app_validation.py` |
| Control-plane context loading | `mozaiksai/control_plane/app_context.py` |
| Refinement App Intelligence tool | `factory_app/refinement_harness/tools/app_intelligence.py` |
| Refinement source retrieval tools | `factory_app/refinement_harness/tools/source_context.py` |
| Refinement source validation tool | `factory_app/refinement_harness/tools/app_validation.py` |
| Existing-app preload | `factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py` |
| Studio overview panel | `factory_app/app/admin/pages/AppOverviewPage.jsx` |
| Studio import surface | `factory_app/app/admin/pages/AppsPage.jsx` |

## Production Posture

For local and small repos, artifact-backed snapshots are sufficient.

For hosted or enterprise deployments, configure FalkorDB as the graph mirror and
keep object/artifact storage as the authority. The product should present this
as "App Intelligence" or "Context Graph indexing", not as a database choice
users need to understand.
