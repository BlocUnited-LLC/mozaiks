# Context Graph and Code Intelligence

Status: Active Architecture

The Context Graph is Mozaiks' canonical code-context intelligence layer. It
captures app, artifact, file, symbol, contract, provenance, and advisory
semantic relationships so agents can reason about generated and brownfield code
without treating a graph database as runtime authority.

The code-intelligence layer is the higher-value part of the system:

```text
deterministic syntax extraction
-> Mozaiks contract mapping
-> bounded LLM semantic annotation
-> graph-aware Refinement Engine retrieval
-> scoped refinement and coding context
```

Graph backends such as FalkorDB or Neo4j may accelerate or visualize this graph
later. They are not the product contract. `AppContextGraph` is. FalkorDB is the
recommended production backend mirror when teams need shared, large-repo graph
queries or Studio visualization beyond embedded artifact snapshots.

Tree-sitter is part of the baseline Mozaiks code-context runtime. It powers
deeper source extraction during indexing, while deterministic fallbacks remain
available only as resilience when a parser package is unavailable at runtime.

## Goals

- give the Refinement Engine code-level impact context before scope selection,
  coding, validation, and promotion
- map generated and brownfield code back to Mozaiks contracts such as modules,
  actions, pages, workflows, tools, repos, services, policies, schemas, and
  artifacts
- provide compact, auditable context packs for LLMs instead of dumping whole
  workspaces into prompts
- keep deterministic facts separate from advisory LLM annotations
- support both generated app bundles and brownfield repositories
- allow optional graph backends without making them the source of truth

## Non-Goals

The Context Graph does not own:

- workflow routing or AG2 handoff execution
- module action execution
- permission, entitlement, billing, or secret enforcement
- generated app persistence
- UI route rendering
- artifact lifecycle state
- promotion approval

Those decisions continue to read deterministic contracts, runtime stores, and
Refinement Engine policy directly.

## Ownership

| Layer | Owns |
| --- | --- |
| `mozaiksai.core.app_context.models` | `AppContextGraph`, nodes, edges, source refs, stale status, graph validation |
| `mozaiksai.core.app_context.context_graph` | deterministic graph construction, syntax extraction, contract mapping, semantic annotation request/apply helpers |
| `mozaiksai.core.app_context.scan_policy` | deterministic, bounded, secret-safe source selection for graph indexing |
| `mozaiksai.control_plane.context_graph` | compact retrieval packs for scope selection and coding |
| `mozaiksai.control_plane.app_intelligence` | local workspace App Intelligence indexing for dogfooding and scoped refinement |
| `factory_app/refinement_harness/tools` | first-party Refinement Engine tools that load artifact workspaces and current app context graphs |
| `factory_app/workflows/_shared/context_graph` | shared workflow prompt injection for preloaded graph packs |
| Studio UX | inspection, impact visualization, and build-sequence surfacing |

Workflow-local code-context systems should not own this layer. AppGenerator,
AgentGenerator, and ExistingAppDiscovery may produce graph source facts, but
the graph construction and retrieval contract stays shared.

## Canonical Contract

`AppContextGraph` is the canonical graph snapshot.

Each graph contains:

- `source_refs`: where the graph facts came from
- `nodes`: app, artifact, source ref, file, module, page, workflow, agent, tool,
  symbol, config, risk, and unknown nodes
- `edges`: contains, declares, defines, imports, calls, references, produces,
  consumes, triggers, implements, generated-from, replaces, wraps, and related
  graph relationships
- `indexed_at`: when the snapshot was built
- `stale_status`: whether the graph can be trusted for planning
- `graph_hash`: deterministic content hash for audit and cache invalidation

The graph is a derived artifact. It must carry enough provenance to trace nodes
and edges back to source files, accepted artifact versions, discovery evidence,
or generated bundle outputs.

## Source Scan Policy

Graph indexing uses the shared source scan policy in
`mozaiksai/core/app_context/scan_policy.py`.

The policy is intentionally deterministic:

- walk local roots with excluded directories pruned
- filter unsupported extensions, generated output, lockfiles, minified files,
  and secret-sensitive paths
- sort candidates by Mozaiks product-code priority before applying file and
  character limits
- emit `scan_health` metadata with selected file counts, skipped-file counts,
  limits, priority buckets, parser status, and warnings

Priority favors app code before tests and scripts:

```text
app/modules
-> app/services
-> app/ui
-> app/config
-> workflows
-> control_plane
-> app_generator contracts/capability packs
-> runtime/factory sources
-> docs/tests/scripts
```

This prevents large dogfood workspaces from filling the graph with tests,
scripts, or docs before the scanner reaches module handlers, services, repos,
page components, workflow tools, and contracts.

The policy is used by:

- ExistingAppDiscovery local source preloads
- Refinement Engine artifact Context Graph loading
- App Intelligence workspace indexing

`context_graph_health` is the workflow-visible health surface. Agents get the
small prompt-safe scan summary inside `context_graph_pack.summary.scan`; tools
and diagnostics can inspect the richer `context_graph_catalog.scan_health`.

## Code-Intelligence Pipeline

### 1. Deterministic Syntax Extraction

Syntax extraction reads source files and produces deterministic facts:

- language
- checksum
- symbols
- qualified symbol names
- line and end-line ranges
- imports
- references
- call targets

Tree-sitter is the baseline parser path for supported languages. The OSS
runtime still records parser health and falls back deterministically if a parser
package is missing at runtime:

- Python uses `ast` fallback extraction.
- JavaScript and TypeScript use conservative regex extraction only when the
  Tree-sitter parser is unavailable.
- YAML and JSON use structured parsing for declarative contracts.

The extractor should never ask an LLM to parse code. LLMs operate only after
deterministic syntax facts exist.

### 2. Contract Mapping

Contract mapping turns file and symbol facts into Mozaiks-specific meaning.

Examples:

| Path or Symbol | Contract Metadata |
| --- | --- |
| `modules/{module_id}/module.yaml` | `contract_role=module_contract` |
| `modules/{module_id}/backend/handler.py` | `contract_role=module_handler` |
| handler method matching a declared action id | `contract_role=module_action_handler`, `action_id={id}` |
| `modules/{module_id}/backend/service.py` | `contract_role=module_service_symbol` |
| `modules/{module_id}/backend/repo.py` | `contract_role=module_repo_symbol`, `side_effect_surface=persistence` |
| `modules/{module_id}/backend/policy.py` | `contract_role=module_policy_symbol`, `side_effect_surface=authorization_scope` |
| `ui/pages/**` | `contract_role=page_schema` or `page_component` |
| `ui/components/**` | `contract_role=ui_component` |
| `workflows/{workflow}/tools/**` | `contract_role=workflow_tool_code` |

Declared module actions become graph nodes. Matching handler symbols link to
those action nodes through `implements_capability`. That lets the Refinement Engine
distinguish "a function named `complete_task`" from "the deterministic handler
for the `tasks.complete_task` action contract."

### 3. Relationship Extraction

The graph should prefer explicit relationships over text matching.

Examples:

- artifact contains file
- file defines symbol
- file imports file
- symbol calls symbol
- page calls module action or module boundary
- handler symbol implements module action
- workflow declares agent or tool
- config generated from artifact

These relationships are what make refactor impact useful:

```text
change request
-> matched graph nodes
-> nearby symbols and contract roles
-> reverse imports and calls
-> affected files, modules, pages, workflows, and tests
-> scoped patch plan
```

### 4. LLM Semantic Annotation

Semantic annotations are advisory metadata added after deterministic graph
construction.

The LLM receives bounded graph facts such as:

- node id
- path
- qualified symbol name
- symbol kind
- contract role
- module id
- action id
- page id
- inbound and outbound relationships

The LLM may return:

- purpose
- domain concepts
- side effects
- invariants
- risk level
- likely test targets
- confidence

The annotation shape is intentionally narrow:

```json
{
  "node_id": "symbol:...",
  "purpose": "Completes a task action request.",
  "domain_concepts": ["task"],
  "side_effects": ["writes_persistence"],
  "invariants": ["Only completes an existing task."],
  "risk_level": "medium",
  "test_targets": ["modules/tasks/backend/test_handler.py"],
  "confidence": 0.8
}
```

When accepted, annotations are stored as `semantic_annotation` metadata on
symbol nodes. They are not authority. They should improve retrieval, review,
and human explanation, but deterministic graph facts remain the basis for
scoping and validation.

## Refinement Engine Consumption

The Refinement Engine consumes the Context Graph through compact packs:

- `get_context_graph_catalog`: scope-selection context with candidate files,
  matched nodes, edges, and semantic annotation candidates
- `get_context_graph_scope`: coding context around explicitly selected files,
  including symbols, related files, graph edges, and a bounded annotation
  request

The coding worker still edits only explicit scoped files. Graph context informs
what should be included in scope; it does not grant permission to mutate
additional files.

Checkpoint context is intentionally staged:

| Checkpoint or workflow | Context provided | Purpose | What is not provided |
| --- | --- | --- | --- |
| `request_submitted` refinement classifier | revision context, artifact summary, stale artifact families, AppContext freshness and ownership warnings | classify `patch`, `design`, `feature`, or `core` and choose a workflow sequence | raw source snippets or full graph dumps |
| `scope_requested` refinement checkpoint | Context Graph catalog, artifact workspace catalog, bounded source search results | choose the narrowest safe file scope | full file contents and edit permission for related files |
| `contract_surface_requested` refinement checkpoint | contract surface context, Context Graph catalog, bounded source search | map the request to module/page/workflow/config contract surfaces | arbitrary source browsing or code patching |
| `coding_requested` refinement checkpoint | explicit scoped files, Context Graph scope, exact source reads for selected paths, related-file/source search as read-only context | produce a bounded patch | scope widening without reroute or human review |
| `ExistingAppDiscovery` before chat | App Intelligence catalog, compact graph pack, source-context catalog, source retrieval tools, and repo/API/runtime evidence as fallback diagnostics | ground discovery and adoption mapping in code evidence | final authority over adoption or generated output |
| `AppGenerator` and `AgentGenerator` startup | compact `context_graph_pack` from the current AppContext or selected artifact workspace | preserve existing app/workflow context during revision runs | raw `AppContextGraph` payloads or full source repositories |

Scope selection is not allowed to trust LLM-selected paths blindly. The first
party scope proposer normalizes proposed paths, checks them against the current
artifact workspace or tool-provided file evidence, and converts unavailable
paths into a clarification instead of passing them to the coding worker. Artifact
workspace readers also skip dependency directories, minified/lock files, and
secret-sensitive paths before building catalogs or prompt context.

The first dogfood entrypoint is App Intelligence workspace indexing:

```python
await index_workspace_app_intelligence(
    app_id="mozaiks-app",
    workspace_root="C:/path/to/mozaiks-app",
)
```

The local developer CLI exposes the same bootstrap path:

```bash
mozaiks context index --app-id mozaiks-app --workspace C:/path/to/mozaiks-app --json
```

The local App Intelligence acceptance gate exercises the same path end to end
and verifies that refinement tools and the `ExistingAppDiscovery` chat overview
consume the registered context:

```bash
MOZAIKS_APP_WORKSPACE_PATH=C:/path/to/mozaiks-app \
  python -m pytest --no-cov tests/test_workspace_app_intelligence_acceptance.py -q
```

That index operation creates:

- a current `app_bundle` artifact containing the selected source evidence
- an `app_context_graph` artifact built from deterministic AST/contract facts
- a current `hybrid` `AppContextVersion` that points to the graph and snapshot
- a `context_graph_health_report` that marks whether core app surfaces were
  indexed, whether scan limits were hit, and whether parser fallback or
  sensitive-path skips require operator attention

Studio exposes the same operation through:

```text
POST /api/studio/apps/{app_id}/context/app-intelligence/index
```

Both the CLI and Studio response include artifact ids, indexed file count,
`scan_health`, and `health_report`. This gives the Refinement Engine a durable
`artifact_version_id` and current graph before scoped coding/refinement starts,
while giving the operator an explicit signal when the graph is missing core
code coverage.

## Prompt Injection

Workflow prompt injection should consume preloaded graph packs only. Hooks must
not independently query persistence or build their own workflow-local graph.

AppGenerator and AgentGenerator load that pack through a shared `before_chat`
lifecycle tool:

```text
factory_app/workflows/_shared/context_graph/load_context_graph_context.py
```

That loader reads the current app id, optional artifact version id, refinement
request, and current `AppContextGraph` artifact. It writes compact
`context_graph_pack` and rich `context_graph_catalog` values into context
variables before any agent uses the shared prompt hook. These keys are not
aliases:

- `context_graph_pack` is the bounded prompt-injection payload. It carries
  status, reason, source, relevant paths, matched graph nodes/edges, warnings,
  and semantic annotation targets.
- `context_graph_catalog` is the richer machine-readable graph catalog for
  diagnostics and tooling. Prompt hooks must not consume it as a fallback.

Raw `app_context_graph` payloads should not be injected directly into prompts;
prompt hooks consume compact packs only.

Refinement Engine tool outputs are not workflow context by default. In particular,
`get_context_graph_catalog` and `get_context_graph_scope` return context to
Refinement Engine checkpoints such as scope selection and coding refinement. The
workflow prompt hook does not read a Refinement Engine `context_graph_scope`; a
workflow that needs graph prompt context must populate `context_graph_pack`
through its own lifecycle preload.

ExistingAppDiscovery is both a producer and an early consumer:

- during `before_chat`, its deterministic preload builds source-backed
  `SourceContextBundle`, `AppContextGraph`, and `AppIntelligenceSnapshot`
  artifacts when source files are available
- that preload registers a current `AppContextVersion` before the first
  discovery agent turn, so later refinement and generator checkpoints can reuse
  the same durable context handle
- during `brownfield_discovery_refresh`, if local source roots are not available
  but `current_context_version_id` is seeded, the preload loads the previous
  `AppContextGraph` for that exact context version so refresh agents can reason
  against prior known state
- the shared Context Graph hook injects that compact pack into discovery agents
  for mid-discovery grounding
- after `DiscoveryArtifactAssemblerAgent` completes, `save_existing_app_artifacts`
  can enrich the current app context with adoption, ownership, risk, and
  inventory artifacts

The preloaded prompt pack is compact and bounded, but the source-backed
artifacts it points at are canonical App Intelligence records. The saved
discovery artifacts enrich that context; they do not replace source truth.

The shared context hook injects:

- load status, reason, and source when available
- graph id and stale status
- scan health summary
- relevant paths
- relevant graph nodes
- relevant graph relationships
- semantic annotation targets
- warnings

This makes graph context available to AppGenerator, AgentGenerator, and future
workflows without reintroducing workflow-specific code-context subsystems.

## Build-Sequence UX

The build sequence should present Context Graph indexing as an automatic
capability, not a database choice.

Recommended UX:

1. When a local workspace is selected for dogfooding, run App Intelligence
   indexing and show the selected roots, file count, graph id, and scan
   warnings.
2. After generated app/workflow artifacts are created, show "Context Graph
   indexing" as a post-artifact step.
3. During brownfield adoption, show discovered source refs, ownership
   boundaries, and graph confidence.
4. During refinement, show affected pages, modules, actions, symbols, files,
   workflows, and likely tests before patch execution.
5. In advanced settings, allow operators to configure graph backend storage.

Users should see "Mozaiks understands the code relationships." Operators may
run embedded graph snapshots locally and use FalkorDB for production-scale graph
queries or Studio visualization when configured. Backend choice should not be a
normal build-flow question.

## Graph Backend Boundary

The default OSS backend is embedded/artifact-backed:

- build graph snapshots deterministically
- store them as artifacts when needed
- query them in process for scope and coding packs

Graph backend mirrors such as FalkorDB may provide:

- faster multi-hop queries on large brownfield repos
- cross-app analytics
- interactive Studio visualization
- incremental graph update mechanics

They must mirror `AppContextGraph`. They must not invent a competing schema or
become the authority for routing, execution, permissions, secrets, persistence,
or promotion.

## Current Implementation Map

| Concern | File |
| --- | --- |
| graph models | `mozaiksai/core/app_context/models.py` |
| graph builder and code-intelligence pipeline | `mozaiksai/core/app_context/context_graph.py` |
| deterministic source scan policy | `mozaiksai/core/app_context/scan_policy.py` |
| Refinement Engine graph query packs | `mozaiksai/control_plane/context_graph/query.py` |
| context graph health gate | `mozaiksai/core/app_context/health.py` |
| App Intelligence workspace indexing | `mozaiksai/control_plane/app_intelligence.py` |
| CLI dogfood snapshot command | `mozaiks_cli/commands/context.py` |
| factory graph loading tools | `factory_app/refinement_harness/tools/_context_graph.py` |
| scope-selection tool | `factory_app/refinement_harness/tools/get_context_graph_catalog.py` |
| coding-context tool | `factory_app/refinement_harness/tools/get_context_graph_scope.py` |
| shared prompt injection hook | `factory_app/workflows/_shared/context_graph/hook_context_graph.py` |
| boundary doc | `docs/architecture/foundations/graph-authority-boundaries.md` |
| app-context doc | `docs/architecture/foundations/app-context-and-brownfield-adoption.md` |

## Testing Expectations

Changes to this layer should include targeted tests for:

- graph model validation
- deterministic symbol extraction
- call/reference edges
- contract role mapping
- module action to handler linkage
- semantic annotation request/apply behavior
- deterministic scan policy and scan health
- Context Graph health reports and blocked/warning coverage states
- Refinement Engine catalog and scope packs
- App Intelligence workspace indexing
- CLI App Intelligence index command wiring
- shared hook injection behavior

Recommended slices:

```bash
python -m pytest tests/test_context_graph_scan_policy.py tests/test_app_intelligence_index.py -q
python -m pytest tests/test_cli_context_index.py -q
python -m pytest tests/test_context_graph.py -q
python -m pytest tests/test_control_plane_tools.py tests/test_control_plane_loader.py -q
python -m pytest tests/test_declarative_config_fallbacks.py -q
```

For broader impact:

```bash
python -m pytest tests/test_scope_proposer.py tests/test_coding_worker.py -q
python -m pytest tests/test_app_context_models.py tests/test_app_context_architecture_contract.py -q
```

## Post-Generation Contract Integrity

The Context Graph is the right layer for detecting structural issues in generated
code after artifacts are written. This is distinct from the contract completeness
checks that run against `BuildPlan` before generation — those operate on declared
intent. Post-generation integrity operates on what was actually written.

The graph already maps:

- declared module actions → handler symbol implementations (`implements_capability`)
- declared page/view addresses → page component or schema files
- declared events → producer modules and subscriber reactions
- declared integrations → client files under `services/integrations/`

These relationships let the Refinement Engine answer questions that no amount of
prompt engineering can answer reliably:

- Is every declared action actually implemented in a handler?
- Does every view address resolve to a generated file?
- Are there imports in generated code that reference paths which were never written?
- Does every declared event have at least one reaction or subscriber registered?

These checks are deterministic graph queries — no LLM involved. They run against
the `AppContextGraph` built from the generated artifact workspace after the build
completes and before promotion.

When a check fails it surfaces as a graph annotation on the affected node with
`risk_level: high` and a `purpose` describing the gap. The Refinement Engine can
surface this as a targeted revision — "ServiceAgent did not implement
`tasks.archive_task`" — rather than asking the user to discover it at runtime.

This is where the code intelligence layer and the build factory layer connect:
the factory declares contracts, generates to those contracts, and the graph
validates that the generated code actually honors them.

### Current gap

Post-generation integrity checks are not yet implemented as first-class graph
queries. The wiring validation today runs as middleware hooks that inspect
generated files directly. The target state is for those checks to be graph
queries against the post-build `AppContextGraph`, making them:

- domain-agnostic (any domain's generated files can be indexed and checked)
- reusable across Genesis Builds and Refinement Runs
- available to Studio's inspection UX as annotated graph nodes

This is tracked as open work below.

## Open Work

The current layer establishes the contract and default extraction path. Next
work should focus on:

- deeper Tree-sitter coverage for JavaScript, TypeScript, JSX, and TSX
- reverse call/import traversal helpers
- explicit test-target discovery
- event/read/write side-effect extraction from canonical module layers
- richer Studio graph inspection UI
- optional graph backend adapter interfaces
- a governed LLM annotation worker that produces the bounded annotation schema
