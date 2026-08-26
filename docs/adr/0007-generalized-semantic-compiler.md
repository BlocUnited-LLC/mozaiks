# ADR 0007: Generalized Semantic Compiler

Date: 2026-08-26

Status: Proposed

## Context

Mozaiks generates applications through a sequence of factory workflows, but no
single artifact owns what the generated application *means*. The same
application structure is represented at least four times with no round-trip
test between representations:

- generator-side YAML structured-output models — the `AppBuildPlan` and
  `AppSchemaOutput` families in
  `factory_app/workflows/AppGenerator/structured_outputs.yaml`, compiled to
  Pydantic at runtime by `mozaiksai/core/workflow/outputs/structured.py`;
- normalized in-memory dictionaries inside generator tools such as
  `factory_app/workflows/AppGenerator/tools/app_build_plan.py` and
  `factory_app/workflows/AppGenerator/tools/save_app_schema.py`;
- on-disk bundle artifacts under the generated roots; and
- the runtime Pydantic contracts in `mozaiksai/core/runtime/app/`
  (`module_loader.py`, `page_schema.py`, `subscriptions_loader.py`,
  `loader.py`).

Studio re-normalizes the same shapes again, and the control plane derives its
own work-contract projections (`mozaiksai/core/workflow/work_contracts.py`,
`mozaiksai/core/workflow/plan_assignment_compiler.py`).

Because that semantic authority is missing, safety became path-shaped:
`mozaiksai/control_plane/implementations/refinement_router.py`,
`mozaiksai/control_plane/dry_run.py`,
`mozaiksai/control_plane/promotion_policy.py`, and
`mozaiksai/control_plane/validation_runner.py` each maintain an independent
path→artifact-family glob taxonomy, while the canonical typed answer —
`mozaiksai/core/runtime/app/layout_registry.py`, the repo's only
machine-readable, self-digesting artifact-family registry
(`mozaiks.app_layout.v1`) — is not consumed by any control-plane module.
Naming is likewise fragmented:

- events are declared in at least five places with different enforcement:
  `MozaiksEventType` in `mozaiksai/core/transport/event_contract.py`,
  fail-closed `CANONICAL_EVENT_PREFIXES` in
  `mozaiksai/core/runtime/app/module_loader.py`, constants in
  `mozaiksai/core/events/runtime_events.py`, build lifecycle literals in
  `factory_app/workflows/_shared/platform/build_lifecycle.py`, and ad hoc
  literals elsewhere; the dispatcher
  (`mozaiksai/core/events/unified_event_dispatcher.py`) accepts any string and
  silently no-ops unknown emits;
- `capability_id` has no namespace rule at module load but a regex in
  `mozaiksai/core/runtime/app/subscriptions_loader.py` — one identifier space,
  two validators;
- the theme artifact family is named three ways (`theme_capture`, `brand`,
  `theme_config`), so staleness propagation through
  `mozaiksai/control_plane/invalidation.py` matches zero theme documents.

A Phase 0 architecture audit (read-only, five parallel code-analysis passes
over the entry→design, generation→export, contract-inventory,
build-context/prompt/AG2/event, and refinement/control-plane/eval lanes)
verified these ground-truth defects in the current pipeline, each cited where
it is classified in this document: a dead `save_build_plan` branch in
ValueEngine whose `build_plan` dependency is still declared in
`factory_app/workflows/extended_orchestration/extension_registry.json`; an
`AppBuildPlan` that exists only as a YAML-defined structured output, is never
persisted, and is lossy-normalized before use; a production-dead normalization
layer in `factory_app/workflows/AgentGenerator/tools/workflow_converter.py`;
inconsistent generated artifact roots between writers; persistence failures
swallowed while success is still reported; an artifact query using
non-existent fields; runtime-compiled structured-output models that are
permissive even though their provider JSON-schema projection is strict; and an
`AppLoader` that downgrades a subscriptions-load failure to a warning, leaving
entitlement enforcement silently permissive.

The audit's overall verdict, which this ADR adopts, is **consolidate, do not
rebuild**. Every deterministic capability a semantic compiler needs already
exists in proven form somewhere in the repository: strict versioned runtime
contracts (module family, `AppPageSchema`, `SubscriptionsConfig`), the
self-digesting `layout_registry`, deterministic renderers (deployment
contracts, capability-pack Jinja templates with the `mozaiks.pack_digest.v1`
digest in
`factory_app/workflows/AppGenerator/tools/resolve_managed_capability_templates.py`,
schema emitters, assembly post-passes), fail-closed loaders, an
artifact/lineage store (`mozaiksai/core/artifacts/store.py` plus
`mozaiksai/control_plane/artifact_promotion.py`), deterministic validation and
acceptance gates (`factory_app/workflows/AppGenerator/tools/app_validation.py`,
`mozaiksai/control_plane/validation_runner.py`), a deterministic evaluation
harness (`factory_app/eval/bundle_eval.py`, `factory_app/eval/bundle_scorers.py`),
and a typed application graph model
(`AppContextGraph`/`AppContextVersion` in `mozaiksai/core/app_context/models.py`).
What is missing is the semantic spine between agent intent and file bytes.

ADR 0006 (Accepted) requires this decision: production
`JourneyExecutionPort.start` and production capability advertisement are
blocked until the semantic-compiler ADR is accepted and its typed reference
contracts exist. ADR 0005 (the OSS/proprietary build-intelligence boundary,
reserved in PR #394) constrains how proprietary intelligence may interact with
the compiler.

Mozaiks is pre-1.0 and not in production. There are no supported customers,
production deployments, or meaningful third-party consumers; existing forks
are experimental friends' forks that carry no compatibility guarantee.

## Decision

Introduce one canonical semantic authority for Mozaiks application
generation:

```text
ApplicationManifest
  → immutable SemanticGraph
  → derived CompilationPlan
  → deterministic renderer registry
  → YAML / JSON / Jinja / contracted Python and JavaScript artifacts
  → deterministic validation
  → ArtifactRevision and promotion
```

Agents own semantic reasoning and candidate content: what the application
means — surfaces, modules, actions, events, pages and their intent, data
shapes, plans and pricing structure, workflow designs, stub bodies within
declared contracts, and repair choices among validated options. Deterministic
code owns serialization, digests, reference closure, path layout, rendering,
validation, promotion, and retention. Unsupported customization fails closed
at compile time: out-of-namespace names, undeclared stub kinds, unresolvable
references, and unknown fields are rejected, never silently accepted.

`ArtifactRevision` is immutable artifact lineage and promotion evidence only.
It records which graph and derived plan produced which bytes; it cannot author
or reconcile semantic facts. `CompilationPlan` likewise contains only derived
execution detail. The only generation-time semantic author is the pinned
`SemanticGraph`.

This ADR approves the contract direction only. It implements no interface,
changes no workflow, migrates no data, and authorizes no model spend.

## ApplicationManifest

`ApplicationManifest` is the minimal root identity and reference document,
versioned as `mozaiks.app_manifest.v1`. It contains:

- application identity, including a pre-app form that does not invent a final
  `app_id` (matching ADR 0006's `ExecutionAccessScope` requirement that a raw
  build not require an `app_id` that does not yet exist);
- an immutable `ExecutionAccessScopeRef` identifying the owning tenant,
  workspace, or pre-app creation scope. A pre-app manifest uses a creation-scope
  identifier, never a fabricated `app_id`; assignment of a real `app_id`
  creates a new manifest version rather than mutating that scope;
- mode — `greenfield`, `brownfield`, or `hybrid` — reusing the
  `AppContextVersion` vocabulary in `mozaiksai/core/app_context/models.py`;
- the current `SemanticGraphRef`;
- pinned `TaxonomyNamespaceRef` versions;
- the `BuildContextBindingRef` for the active build;
- the current `ArtifactRevisionRef` where one exists; and
- the compiler, renderer-registry, and schema versions in effect.

The manifest references; it does not duplicate. It is explicitly not a giant
application-specific root schema: modules, pages, data, events, plans, and
workflows live as graph nodes and rendered child contracts, never as inline
manifest content. Growth happens by adding node types and artifact families to
their registries, not by widening the manifest. Manifest versions are immutable,
and every manifest/graph/revision read or write is checked against the pinned
execution-access scope before content is returned or changed.

## SemanticGraph

The `SemanticGraph` is the single generation-time source of truth for
application semantics. Each version is immutable, content-digested, and
tenant- or pre-app-scoped; change produces a new version, never an in-place
mutation.

Required properties:

- **Deterministic canonical serialization.** One canonical byte serialization
  (stable key order, normalized scalars, closed input set) defined once and
  reused for digesting, following the proven patterns of
  `layout_registry`'s stable digest and `work_contracts.stable_digest`.
  Serializing the same graph twice is byte-identical. The serialization covers
  every semantically relevant schema field; no field may be omitted from the
  digest by convention. The exact scalar normalization, Unicode, number,
  collection-order, and excluded non-semantic metadata rules are delegated to
  the versioned slice-2 serialization contract and its golden vectors, not to
  individual callers.
- **Stable digest rules.** The graph digest is computed over the canonical
  serialization; digests are stable across process restarts and key-order
  permutations, and every ref that pins a graph pins it by digest.
- **Stable node and edge identity.** Every node has a namespace-qualified
  semantic identifier that remains stable across graph versions and is
  independent of list order or renderer layout. An identifier is reused only
  for the same semantic concept. Edge identity is derived deterministically
  from its versioned edge kind, source node, target node, and any typed
  discriminator; duplicate identities and collisions fail closed. Slice 2
  owns the exact identity algorithm and golden vectors. A later graph-
  granularity change therefore requires an explicit schema/identity migration;
  the deferred granularity choice cannot reinterpret an existing identifier.
- **Typed nodes and edges.** Node and edge kinds are a closed, versioned set —
  seeded from the existing `GraphNodeType` and `GraphEdgeType` enums of
  `AppContextGraph` (`mozaiksai/core/app_context/models.py`) — covering
  surfaces, modules,
  actions, capabilities, permissions, events, reactions, notifications, pages
  and sections, data collections and aliases, workflows and triggers,
  plans/products, meters/limits, deployment targets, and stub declarations,
  with edges such as declares/emits/consumes/renders/binds/depends_on/gates/owns.
- **Reference closure.** Every reference a node makes — to a taxonomy entry,
  another node, a capability, an event, a data alias — must resolve inside the
  pinned graph and taxonomy versions. No dangling references; validation is
  deterministic and fail-closed.
- **Namespaced extension nodes.** Extensions add nodes only inside granted
  namespaces; they cannot redefine core node kinds or another namespace's
  entries.
- **Strict unknown-field handling.** Graph documents reject unknown fields.
- **Ownership scope.** Each graph version records its owning tenant/workspace
  or pre-app creation scope, aligned with ADR 0006's immutable creation-scope
  rule. Its scope must equal the manifest's `ExecutionAccessScopeRef`; scope
  mismatch is a validation error, not a reconciliation case.

### Relationship to AppContextGraph

`SemanticGraph` and `AppContextGraph` are distinct authorities that must not
be merged:

- `SemanticGraph` is **authored intent** — the upstream document the compiler
  compiles *from*.
- `AppContextGraph`/`AppContextVersion` remain the **observed/indexed view**
  of actual artifacts and source, derived downstream by scanning — as the
  canonical statement in
  [App Context And Brownfield Adoption](../architecture/foundations/app-context-and-brownfield-adoption.md)
  already requires: graph mirrors are never source of truth, and the runtime
  reads canonical contracts, not the graph.

`AppContextVersion.graph_snapshot_ref` gains a sibling `semantic_graph_ref`
so an observed snapshot can record which authored graph version produced the
artifacts it indexed. Indexing is one-way: it cannot mutate, promote, or
replace that semantic graph. Brownfield discovery becomes authored intent only
through an explicit, ownership-checked graph patch. Brownfield and hybrid
applications compile only surfaces
the ownership boundaries declare as owned; `AppContextVersion.ownership_boundaries`
is the guard, and its current uniform directory-level population by
`mozaiksai/control_plane/app_intelligence.py` must become per-surface before
hybrid compilation relies on it.

## Taxonomy

One namespaced, versioned taxonomy registry (`mozaiks.taxonomy.v1`) owns the
semantic identifier spaces:

- modules; pages and surfaces; actions; capabilities; events; reactions;
  workflows; agents; tools; data entities; integrations;
  subscription/entitlement references; artifact families; and permitted
  customization-stub kinds.

Rules:

- Namespaces are versioned independently (the pattern `mozaiks.events.v1`
  already declared by the module event lane in
  `mozaiksai/core/runtime/app/module_loader.py` and enforced through
  `mozaiksai/core/runtime/composition/module_event_router.py` is the seed).
- Extensions — capability packs and product overlays — may add namespaced
  entries under declared grants (pack identity is already regex-validated in
  `mozaiksai/core/session/build_context_schema.py`), but may not weaken core
  invariants or redefine another namespace. Core namespaces cannot be
  redefined. Every namespace reference pins both version and digest;
  namespace/version collisions fail closed.
- The compiler validates complete reference closure deterministically: every
  emitted event type belongs to a declared namespace version; every reaction
  resolves to a declared producer or canonical family; every capability,
  action, page, and data reference resolves in-graph. Unknown names fail
  closed at compile time instead of silently no-oping at dispatch.

The taxonomy owns artifact-family **identifiers and reference grammar only**.
`layout_registry` remains the exclusive owner of layout metadata: canonical
paths, renderers, validators/loaders, dependency families, security and
ownership classes, allowed stub kinds, and output-digest behavior. Taxonomy
entries refer to layout-registry rows; they do not duplicate those rows. The
registry is implemented as independently versioned namespace tables behind a
single resolver, so adding a namespace or family does not require widening one
giant Pydantic schema.

This registry is the direct answer to the fragmentation named in Context: the
five event registries converge on declared event families
(`chat.`, `ui.`, `runtime.`, `artifact.`, `domain.`, `platform.`, `hosted.`,
`build.`, `notification.`, `workflow.`, plus granted provider namespaces such
as `mozaikspay.`); the two capability-ID validators
(`module_loader.py` free-string vs `subscriptions_loader.py` regex) become one
grammar; the three theme family names collapse to one registered family so
invalidation actually matches; and artifact families fold into the existing
`layout_registry` family table rather than a new list. The module event
lane's fail-closed prefix check and payload-schema validation are the
implementation seed — extended, not replaced.

## Child Contracts

Existing strict runtime contracts remain the normative artifact formats their
loaders consume: the module contract family loaded by
`mozaiksai/core/runtime/app/module_loader.py`, `AppPageSchema` in
`mozaiksai/core/runtime/app/page_schema.py`, `SubscriptionsConfig` in
`mozaiksai/core/runtime/app/subscriptions_loader.py`, data contracts under
`app/data/`, workflow bundle files, and module `contracts/events.yaml` and
`contracts/reactions.yaml`. The deployment manifest remains the normative
deployment handoff format for its hosted-product consumer; it has no OSS
runtime consumer today. Nothing in this ADR changes what each current consumer
loads or how it validates.

What changes is generation-time authority: the semantic content of those
artifacts is **derived from the SemanticGraph** during compilation. Child
contracts are rendered views; they are not competing generation-time semantic
authorities. The generator-side YAML mirrors of runtime contracts (the module
and page families inside `factory_app/workflows/AppGenerator/structured_outputs.yaml`,
and AgentGenerator's per-file output models) are retired once agents emit
graph-node payloads validated directly against the runtime models — a
mechanism already proven by the companion-model validation in
`mozaiksai/core/workflow/module_contract_executor.py`.

These authorities operate at different times. For compiler-managed surfaces,
the `SemanticGraph` is authoritative when semantic intent disagrees with a
rendered file; the runtime loader is authoritative for deciding whether the
actual on-disk artifact is executable. A loader-valid file with the wrong
recorded digest is still drifted and cannot be promoted. Hand-authored or
brownfield surfaces remain runtime-artifact-authoritative until an explicit
ownership decision brings that surface into the graph; the compiler must not
silently import or overwrite an unowned surface.

### Round-trip and drift rules

Rendered artifacts cannot silently diverge from their semantic source:

- every rendered artifact records provenance — the `SemanticGraphRef`,
  renderer-registry version, and its own content digest — in the
  `ArtifactRevision` manifest;
- a direct edit to a derived artifact is detected by digest mismatch during
  validation and refinement; it is either rejected or must re-enter through a
  typed `RefinementPatch` (or a declared customization-stub region, the only
  place free-form content is legal);
- rendered contracts are not round-trip authoring sources. Re-extraction is an
  offline equivalence/drift test only. Regeneration from an unchanged graph
  deterministically restores the registered output and never imports a manual
  edit into semantic intent; preserving such an edit requires a validated graph
  patch or an owned stub-region update that creates new graph/revision lineage;
- the current substitute — the hardcoded three-glob derived-file deny list in
  `mozaiksai/control_plane/promotion_policy.py` — is replaced by
  registry-declared derived families, so "this file is compiled output" is a
  registry fact, not a pattern tuple.

## CompilationPlan Replaces AppBuildPlan As Authority

`AppBuildPlan` is replaced as canonical planning authority by a
**derived** `CompilationPlan`. `AppBuildPlan` is the active AppGenerator's
agent-authored operational plan today, but it is neither a durable semantic
authority nor a clean execution-only plan: it exists only as a YAML-defined
structured output compiled at runtime, has no Python class, and the active path
keeps it in a context variable only. A generic persistence helper does exist at
`mozaiksai/core/data/persistence/artifact_store.py` as
`BuilderArtifactStore.save_build_plan`, but its only production call site is
the non-auto, transition-unreachable ValueEngine `save_build_plan` tool; that
tool persists ValueEngine's different upstream `BuildPlan`, not AppGenerator's
`AppBuildPlan`. AppGenerator's normalization in
`factory_app/workflows/AppGenerator/tools/app_build_plan.py` silently drops
declared fields, including `surface_map` and `workflow_touchpoints`. The
remaining object mixes semantic candidates (pages, entities, roles,
capability packs, monetization, and data contract) with execution concerns
(tasks, dependencies, agents, and owned paths), which is precisely the
authority blend this ADR separates.

`CompilationPlan` is deterministically derived from:

- the pinned `SemanticGraphRef`;
- resolved taxonomy versions;
- the `BuildContextBindingRef`;
- the renderer-registry version;
- the target runtime/deployment profile; and
- the selected capability packs.

It may contain tasks, dependency ordering (from graph edges), owned paths
(from registry family path templates), renderer selections, validation
requirements, and artifact destinations. It must not introduce new semantic
meaning: any fact a renderer needs must be a graph node or a registry
declaration, never a plan-only invention. Task-level owned-path enforcement
continues to use the existing mechanism in
`mozaiksai/core/workflow/task_batches.py`.

Plan derivation must prove coverage of every execution concern consumed today:
task-batch dependencies and owned paths; page/surface bindings; capability-pack
selection; workflow touchpoints; monetization/subscription outputs; data
contracts; and deployment tasks and profiles. Missing coverage is a derivation
error, never permission for `CompilationPlan` to invent a semantic default.

Before cutover, the current agent-produced `AppBuildPlan` remains the sole
active operational plan. Offline projection captures it only as a comparison
fixture: equivalence tests prove derived-plan versus agent-produced-plan
agreement on the archetype corpus. The derived candidate never drives a live
build alongside it. At cutover `CompilationPlan` replaces it atomically and the
old plan is retired; there is no live dual-authority period and no dual-write.

## Renderer Registry

The renderer registry extends the existing `layout_registry` authority
(`mozaiksai/core/runtime/app/layout_registry.py`, `mozaiks.app_layout.v1`,
with its self-verified registry digest) rather than introducing a second
registry. Each registered artifact family declares:

- family identifier (taxonomy-registered);
- semantic input node/reference types;
- renderer identifier;
- canonical paths (the single source for generated roots);
- artifact schema/loader binding;
- validator;
- security/ownership class;
- allowed customization-stub kinds;
- dependency families; and
- output digest behavior.

Adding an artifact family is a registry extension — a new registered row with
its renderer, validator, security class, and taxonomy grants — never an edit
to one monolithic application schema. The registry digest makes additions
tamper-evident. Dependency families must form an acyclic graph; rendering uses
a stable topological order with a registry-defined identifier tie-break. Every
renderer writes only the canonical paths owned by its row, with resolved paths
confined beneath the registered output root. Extensions cannot claim a core or
another extension's path. Every output passes the row's validator/loader before
it can enter an `ArtifactRevision`; no renderer or extension has a validation-
bypass path.

Deterministic rendering per format:

- **JSON** through canonical structured serialization;
- **YAML** through canonical structured serialization;
- **Jinja** only for declared template packs — the capability-pack `.j2`
  mechanism with `mozaiks.pack_digest.v1` integrity verification in
  `factory_app/workflows/AppGenerator/tools/resolve_managed_capability_templates.py`
  is the sanctioned engine and the pattern to generalize;
- **Python/JavaScript** only through typed, bounded customization-stub
  contracts (next section).

This consolidates today's fragmented rendering — deployment string renderers
in `factory_app/workflows/AppGenerator/tools/deployment_contract.py`,
schema emitters in `save_app_schema.py`, assembly post-passes in
`factory_app/workflows/AppGenerator/tools/assemble_app_tasks.py`, pack Jinja,
and AgentGenerator's production-dead converter **normalizers** — behind one
registry. AgentGenerator regains a real deterministic renderer layer. The
normalization/rendering helpers in `workflow_converter.py` show part of the
intended shape but have no production caller and are deleted; its
`promote_generated_workflow` copy helper is currently called by
`generate_and_download.py` and is not evidence of a dead promotion path.

## Contracted Python And JavaScript Stubs

Agents may propose stub bodies only within declared stub kinds and ownership
boundaries. The existing `ModulePythonStub`/`ModuleJsStub` declarations in
`factory_app/workflows/AppGenerator/structured_outputs.yaml` and the module
contract lane's entrypoint validation are the boundary to keep — today
enforced for handler and runtime-extension entrypoints but not for
admin/profile/settings hooks; this ADR adds the missing enforcement. Every
stub declaration specifies:

- stable stub identity;
- language;
- target path (from the registry family path template, never model-chosen);
- declared imports/dependencies;
- referenced contracts (`contract_refs` must resolve — today they are never
  resolved);
- owned symbols/regions;
- forbidden APIs and paths;
- validation commands;
- deterministic wrapper/template generation around the stub body;
- content digest; and
- security scanning.

Undeclared free-form files are rejected. An agent never receives general
filesystem authority merely because a schema field allows a string containing
code: whole-file model output survives only inside contracted customization
regions the graph declares.

## Structured-Output Strictness

The audit found that `structured_outputs.yaml` declarations appear strict
while the dynamically compiled runtime Pydantic models are permissive: the
compiler in `mozaiksai/core/workflow/outputs/structured.py` builds models via
`create_model` with no `model_config`, so unknown fields are ignored at
runtime. `_patch_model_schema` makes the provider JSON-schema projection look
strict, but provider/runtime validation therefore disagree. Open-ended dict
fields have two current behaviors: the older `get_llm_for_workflow` helper
logs a warning and falls back to a plain LLM configuration, while the active
agent factory rejects an unsupported dict-bearing schema when
`structured_outputs_required: true` and omits provider response-schema
enforcement for an optional structured agent. Runtime validation failure in
`mozaiksai/core/workflow/outputs/runtime_events.py` is logged and returned as
`None`, not persisted as a typed failure. No dynamically compiled model
carries a schema version.

This ADR requires, for the compiler's structured-output surface:

- compiled models are **strict by default** (`extra="forbid"`), with explicit
  per-model opt-out only where a contract genuinely allows open content.
  Extensibility is represented by explicitly typed, namespace-keyed extension
  models and grants, never by a global `extra="allow"` or untyped dict escape;
- every compiled model carries an explicit schema version;
- unknown fields fail closed at runtime validation, matching the provider
  projection;
- provider projection is deterministic — the same declaration always produces
  the same provider schema, and silent strictness downgrades are removed;
- runtime validation is equivalent to provider-schema validation, so a payload
  the provider would reject cannot pass the runtime;
- validation failure is observable — a typed, recorded failure, never
  warning-and-continue (the current silent no-op on structured-output
  validation failure in `mozaiksai/core/workflow/outputs/runtime_events.py`
  is exactly what this forbids); and
- before the strictness flip, an **offline archetype-corpus compatibility
  report** measures breakage across all factory workflows and recorded
  transcripts, so the flip is a measured change, not a hopeful one.

This ADR does not implement the flip; it is rollout work (slice 5) gated on
the compatibility report.

## BuildContextBindingRef

Build contexts are immutable, declared compiler inputs — not hidden prompt
authority. A `BuildContextBindingRef` identifies and digests every consumed
non-graph input, including:

- catalog;
- capability pack;
- template;
- prompt projection;
- knowledge/corpus projection;
- context-variable projection;
- schema asset;
- workflow/agent/prompt configuration revision;
- selected strategy/configuration and model/provider selection parameters; and
- public or private extension input.

The existing `mozaiks.pack_digest.v1` mechanism is extended from packs to all
consumed build-context assets, and the binding is recorded in the build
record. The binding provides provenance for candidate reasoning; it does not
claim that a stochastic model will recreate the same candidate graph from
prompts alone. Deterministic compilation is reproducible from the closed tuple
of immutable `ApplicationManifestRef`, `SemanticGraphRef`, pinned taxonomy
refs, `BuildContextBindingRef`, compiler/renderer-registry versions, and target
profile. The graph records the accepted result of any agent reasoning.

Prompts, context variables, and knowledge stores may influence candidate
reasoning only through declared, versioned inputs; they cannot bypass graph or
schema validation. The canonical path removes the current bypass channels
found by the audit:

- the prompt-projection loader in
  `mozaiksai/core/workflow/context/projection.py` resolves a `cwd`-relative
  `build_context` root ahead of `factory_app/build_context` and allows
  wildcard recipients — `cwd`-dependent and arbitrary-import precedence is
  removed from the canonical path;
- the context-variable lane in `mozaiksai/core/session/build_context.py`
  (`merge_build_context`) is wired only through the
  `MOZAIKS_LAUNCH_CONTEXT_PROVIDER` arbitrary-import environment seam in
  `mozaiksai/core/session/launcher.py` — the two lanes are unified into one
  typed loader with declared inputs;
- the launcher's authority validation currently degrades open when workflow
  config loading fails — it becomes fail-closed; and
- `projections`/`values` payloads, today mapping-only checks in
  `mozaiksai/core/session/build_context_schema.py`, become schema-validated
  with observable failure instead of DEBUG-level swallows.

The typed context-variable surface — `ContextVariablesPlan`, the writer
authority policy in `mozaiksai/core/workflow/context/authority.py`, and the
repo-wide compile guard `tests/test_workflow_context_authority_compile.py` —
is the strongest existing input discipline and is the pattern the unified
loader follows.

## OSS And Proprietary Intelligence

Per the boundary ADR 0005 reserves (PR #394) and
[Eval And Build Intelligence Boundary](../architecture/foundations/eval-and-build-intelligence-boundary.md):

**OSS owns**: the public schemas and references
(`ApplicationManifest`, `SemanticGraph`, `CompilationPlan`, all refs);
semantic-graph mechanics, canonical serialization, and digest rules; the
taxonomy registry and extension rules; compilation planning; the renderer
registry and reference renderers; deterministic validators and acceptance
gates; contracted stub enforcement; refinement patch mechanics; evaluation
integration points; and full local operation without Cloud. The entire golden
path already runs offline in CI with zero hosted calls
(`tests/test_factory_regression_suite.py`,
`tests/test_e2e_deterministic_acceptance_gate.py`); the compiler strengthens
that property.

**Mozaiks Cloud may privately own**: build/evaluation corpora; learned
strategies; prompt/catalog variants; repair rankings; model routing; quality
thresholds beyond the OSS reference gates; historical build intelligence; and
hosted analytics.

Cloud intelligence enters only through the same public
`BuildContextBindingRef` and semantic contracts — better catalogs, packs, and
declared configuration delivered as digested build-context assets, and policy
that calls the same public contracts. It cannot require an OSS fork, a hidden
service call, or a hidden Cloud dependency.

## RefinementPatchRef

Refinement becomes a typed patch against a pinned base, replacing whole-file
mutation of derived artifacts. A `RefinementPatch` declares:

- the exact `SemanticGraphRef` base;
- the exact `ArtifactRevisionRef` base;
- the affected semantic nodes;
- expected base digests; and
- the permitted ownership regions it may touch.

Patch application compares both expected base digests immediately before
write. A mismatch is a typed conflict: the patch is rejected and must be
replanned or explicitly rebased against new refs; it is never merged by last
writer wins. Patch validation also proves that every affected node and stub
region belongs to the declared ownership boundary.

The flow:

```text
classify request
  → identify affected graph nodes
  → propose typed RefinementPatch
  → validate patch
  → create new SemanticGraph version
  → deterministically recompile affected families
  → validate generated artifacts
  → promote a new ArtifactRevision
```

The refinement harness (`mozaiksai/control_plane/` plus
`factory_app/refinement_harness/config/harness.yaml`) remains the policy and
control surface — classification, routing, checkpoints, staging, review, and
promotion policy. It must not become a second semantic compiler. The current
five LLM checkpoints survive with re-typed output schemas: typed edits
replace the whole-file `FileUpdate{path, content}` bodies the coding worker
emits today (`mozaiksai/control_plane/implementations/coding_worker.py`,
`mozaiksai/control_plane/contracts.py`). The four control-plane glob
taxonomies are replaced by graph-region queries over the renderer registry.
Staleness-as-substitute — BFS invalidation in
`mozaiksai/control_plane/invalidation.py` standing in for recompilation — is
subsumed by deterministic recompilation of affected families. The `patch`
fast lane becomes a cost optimization (recompile one leaf family), not a
safety class. Whole-file model output survives only inside contracted
customization regions. Promotion advances the new graph ref and artifact
revision together from the caller's perspective. Rollback selects a previously
consistent graph/revision pair; it never rolls artifacts back while leaving a
newer graph current. Existing draft/review/acceptance primitives may be reused,
but their current staging workspace is not semantic replay functionality and
no replay capability is claimed to exist today.

## WorkflowSequenceRef And ADR 0006

`WorkflowSequenceRef` remains owned by
`factory_app/workflows/extended_orchestration/extension_registry.json` and the
workflow pack schema/config (`mozaiksai/core/workflow/pack/schema.py` and
`mozaiksai/core/workflow/pack/config.py`). The semantic
compiler may reference a workflow sequence but must not redefine build journey
steps, route choices, transition UX, journey advancement, or execution
lifecycle. `JourneyExecutionPort` may carry compiler references opaquely but
cannot interpret or rewrite them — exactly as ADR 0006 states from its side.

**Which implemented contracts satisfy ADR 0006's production prerequisite:**
ADR 0006 blocks production `JourneyExecutionPort.start`, production capability
advertisement, and any public live-model journey entrypoint until the
semantic-compiler ADR is accepted **and** the typed references it names exist.
That prerequisite is satisfied only when all hold:

1. this ADR is Accepted; and
2. rollout slices 1 and 2 below are implemented and proven — the taxonomy
   registry plus the typed reference contracts
   (`ApplicationManifestRef`, `SemanticGraphRef`, `TaxonomyNamespaceRef`,
   `CompilationPlanRef`, `BuildContextBindingRef`, `RefinementPatchRef`,
   `ArtifactRevisionRef`, and the typed child-contract references) with
   canonical serialization, stable digests, closure validation, versioning,
   and passing contract tests; and
3. the runtime advertises explicit capability identifiers for those implemented
   contracts and ADR 0006's journey `required_capabilities` pins them. A class
   existing in source without capability advertisement does not unlock start.

Acceptance of ADR 0007 alone does **not** unlock production
`JourneyExecutionPort.start`, a bounded live-model journey, or public journey
entrypoints. The canonical digest for the fully resolved
sequence/transition/dependency view remains defined by the registry schema
owner (`mozaiksai/core/workflow/pack/schema.py` and
`mozaiksai/core/workflow/pack/config.py`), per ADR 0006 slice 0.

## Pre-1.0 Migration Posture

Explicitly:

- No external compatibility guarantee exists before 1.0.
- No customer-data migration is required; there are no customers.
- Friends' experimental forks do not justify compatibility code.
- Breaking changes are allowed with a changelog entry and concise migration
  notes.
- No permanent aliases, dual writes, or parallel semantic authorities.
- Temporary comparison adapters may exist only in offline tests.
- Feature flags may be used temporarily for development proof and are removed
  during the authority flip.
- Dead code is removed once its replacement passes deterministic proof.
- No retired or transitional path becomes a public supported mode.

Staged slices and rollback points below exist to protect the large codebase
during the transition — not external users, because there are none.

This work also intersects the active verification-priority freeze
(`.claude/rules/verification-priority.md`): slice 0 consists of defect
repairs, which the freeze allows; slices 1 and beyond add framework surface
and proceed only after the end-to-end traversal is proven or the freeze is
explicitly lifted.

## Current Authority And Migration Boundary

One disposition per current subsystem. "Separate authority" means the
compiler may reference or consume it but does not become its source of truth.

| Current component | Current responsibility | Disposition | Migration action |
|---|---|---|---|
| `extension_registry.json` + `mozaiksai/core/workflow/pack/schema.py` and `config.py` | Workflow sequences, entrypoints, transitions | separate authority, retained unchanged | Compiler pins `WorkflowSequenceRef`; never redefines sequence content. |
| Runtime loaders (`module_loader.py`, `page_schema.py`, `subscriptions_loader.py`, `loader.py`) | Normative artifact validation at load | retained as child-contract authority | Rendered views must pass them unchanged; generation-time mirrors retire. |
| `layout_registry.py` | Artifact families, paths, validators, security classes | **extended** into the renderer registry | Gains renderer/stub/dependency declarations; becomes the single path→family authority repo-wide. |
| `AppBuildPlan` (structured output + `app_build_plan.py`) | Agent-authored build plan | **replaced** by derived `CompilationPlan` | Offline equivalence fixture during migration; retired at cutover. |
| Generator YAML mirrors in `structured_outputs.yaml` (module/page families, AgentGenerator per-file models) | Generation-time re-declaration of runtime contracts | **replaced** | Agents emit graph-node payloads validated against runtime models (per `module_contract_executor.py` pattern). |
| `save_app_schema.py` hand validators | Parallel page/manifest validation | **replaced** | Collapse onto runtime models once the renderer path lands. |
| Control-plane glob taxonomies (`refinement_router.py`, `dry_run.py`, `promotion_policy.py`, `validation_runner.py`) | Path→family inference | **replaced** | Graph-region queries over the renderer registry. |
| `AppContextGraph` / `AppContextVersion` | Observed/indexed artifact view | separate authority, retained | Gains `semantic_graph_ref` sibling; stays downstream. |
| `mozaiksai/core/artifacts/store.py` (`BuildRecordStore`) + `mozaiksai/control_plane/artifact_promotion.py` | Build records, lineage, promotion evidence | retained, extended | `ArtifactRevision` binds to existing lineage; revisions record graph/plan/binding digests. |
| `mozaiksai/core/data/persistence/artifact_store.py` (`BuilderArtifactStore`) | Typed builder artifact collections | direction decided at slice 5 | Becomes a projection of graph/artifact records or a typed view; no dual authority retained. |
| Validation and acceptance gates (`app_validation.py`, `validation_runner.py`, bundle scanner) | Deterministic artifact validation | retained | Consume registry/graph instead of private path predicates. |
| `factory_app/eval/` | Deterministic bundle scoring | separate authority, retained | Scores rendered output; gains archetype-corpus equivalence fixtures. |
| Refinement harness + control plane | Classification, routing, staging, review, promotion policy | retained as policy surface | Checkpoint schemas re-typed to graph edits; no second compiler. |
| Capability-pack Jinja + `mozaiks.pack_digest.v1` | Template materialization with integrity | retained, generalized | Digest mechanism extends to all build-context assets (`BuildContextBindingRef`). |
| Context-variable authority (`ContextVariablesPlan`, `authority.py`, compile guard test) | Typed workflow state discipline | retained | Pattern for the unified build-context loader. |
| AG2 1.0.2 integration (`mozaiksai/core/workflow/agents/factory.py`, network/task runners) | Agent execution primitives | separate authority, retained unchanged | Compiler is contract enforcement around agent outputs; no AG2-parallel machinery. |
| ADR 0006 journey execution | Execution lifecycle, budgets, cancellation | separate authority | Carries compiler refs opaquely; slices interlock as stated above. |
| Subscriptions/entitlements runtime (`ConfiguredEntitlementAdapter`, `EntitlementPort`) | Runtime entitlement enforcement | separate authority, retained | Compiler renders `subscriptions.yaml`; enforcement unchanged. |

## Ground-Truth Findings And Their Disposition

The Phase 0 findings are classified into four lanes rather than becoming
separate ADR decisions:

**Prerequisite defect repair (slice 0 — allowed under the verification
freeze as defect fixes):**

| Finding | Evidence |
|---|---|
| Inconsistent generated artifact roots: AgentGenerator's live writer omits `build_id`; AppGenerator's final bundle root is CWD-relative, keyed by chat not build, name model-chosen, absent from `layout_registry` | `factory_app/workflows/AgentGenerator/tools/generate_and_download.py`, `factory_app/workflows/AppGenerator/tools/generate_and_download.py` |
| Swallowed persistence failures reporting success: bundle write, artifact-record registration, design-doc status writes | `save_app_schema.py`, `generate_and_download.py` (both generators), `factory_app/workflows/DesignDocs/` save tools |
| Dead artifact query: `subscription_contract_artifact` fallback queries non-existent fields (`artifact_kind`/`artifact_key` vs `build_family`/`build_key`) | `factory_app/workflows/AppGenerator/context_variables.yaml` |
| Dead ValueEngine `save_build_plan` branch with `build_plan` still declared as a dependency — lineage incomplete on every real build | `factory_app/workflows/ValueEngine/`, `factory_app/workflows/extended_orchestration/extension_registry.json` |
| Theme family named three ways so invalidation matches zero documents; phantom `experience_spec` family | `mozaiksai/control_plane/invalidation.py`, front-half save tools |
| Launcher authority validation fails open when workflow config load throws | `mozaiksai/core/session/launcher.py` |
| Dead event-envelope guard script (missing directory, wired into no CI) | `scripts/check_event_envelope_protocol_guard.py` |
| Dangling `ContractSurfaceClassification` contract name in the checkpoint schema map | `mozaiksai/control_plane/schema.py` |

**Compiler migration (slices 1–7):** duplicated concept/theme/page/module/data
representations; data-contract validator disagreement between DesignDocs and
AppGenerator; production-dead AgentGenerator converter normalizers
(`workflow_converter.py`); lossy, unpersisted `AppBuildPlan` planning path;
the four control-plane glob taxonomies; `BuildRecordStore`/`ArtifactStore`
alias duplication in `mozaiksai/core/artifacts/store.py`; multiple event and
taxonomy registries; structured-output permissiveness; incomplete
provenance/ownership use in refinement (the `refinement` provenance mode and
`last_refined_with` field in `mozaiksai/core/runtime/app/provenance.py` are
declared but never read or written by any control-plane code); lack of refinement replay; dual builder persistence
(`BuilderArtifactStore` vs build records).

**Separate product decision (not resolved by this ADR):** the `AppLoader`
subscriptions fail-open behavior — a subscriptions-load failure is downgraded
to a warning in `mozaiksai/core/runtime/app/loader.py`, leaving entitlements
silently permissive. SaaS apps should likely fail closed; deciding that
default is a product call recorded here as an open question.

**Documented debt (tracked, not blocking):** the scripts/tests-only
refinement staging/execution/promotion-policy pipeline
(`mozaiksai/control_plane/staging.py`, `scoped_execution.py`, `promotion.py`
are reachable only from scripts and tests today). Studio does expose review
and acceptance of an already-created staged draft through
`mozaiksai/control_plane/artifact_promotion.py`; it does not call those three
pipeline modules to create, execute, validate, or promote the staged work; the
dormant AG2 `KnowledgeStore` seam (zero producers); and
`deployment.manifest.json` having no OSS consumer (consumed by the hosted
product).

## Rollout Slices

Slices are sequential gates. Slices 0–4 run no live models. Slice 5 and later
may use live models only after their offline proof gates and only through an
ADR 0006 bounded journey whose required compiler capabilities are explicitly
advertised; this ADR never authorizes an unbounded or standalone live call.
Each slice lists affected components, authority before/after, tests and proof
gate, deletion targets, rollback boundary, and ADR 0006 interaction.
Equivalence comparison happens in an offline archetype corpus; there is no live
dual-run.

Slice 0 is a gate composed of small, independently reviewable defect-repair
PRs, not one cleanup mega-PR. Each repair must prove the cited defect and may
delete only its own obsolete path.

| Slice | Components; authority before → after | Tests and proof gate | Deletions; rollback; live models; ADR 0006 |
|---|---|---|---|
| **0. Ground-truth repair** — generator path writers, save tools, launcher, `context_variables.yaml` query, invalidation family names, dead branches/scripts | Defect repair only; no authority change | Path-contract tests; failure-injection tests proving fail-closed persistence; lineage resolution on the archetype corpus; per-family staleness-propagation tests | In separate PRs, delete the dead ValueEngine `save_build_plan` branch, dead guard script, and only helpers individually proven unreachable. Retain active `promote_generated_workflow` and Studio staged-draft acceptance. Rollback: revert the individual repair PR. No live models. No ADR 0006 dependency; allowed under the verification freeze as defect fixes. |
| **1. Taxonomy and artifact-family registry** — new `mozaiks.taxonomy.v1`; consumers: module loader, subscriptions loader, `layout_registry`, event dispatcher | Five event registries and two capability grammars → one versioned registry (existing names grandfathered by explicit entries) | Closure property tests; unknown-name fail-closed tests behind a test/development flag; envelope schema-version guard revived as a real check | No deletions yet. Rollback: remove the test/development advisory mode; it is never a supported runtime mode and is deleted at cutover. No live models. No ADR 0006 dependency. |
| **2. Manifest/graph/reference contracts** — `ApplicationManifest`, `SemanticGraph`, all refs, canonical serialization + digests, **test-only seams** per ADR 0006's non-production-prototype rule | No production authority; contracts exist behind test seams | Byte-identical double-serialization; digest stability across key order; no-dangling-edge; unknown-field rejection; cross-tenant scoping tests | No deletions. Rollback: delete the seam. No live models. **This slice (with slice 1) is the implementation half of ADR 0006's slice-0 prerequisite.** |
| **3. Offline projection adapters** — deterministic builders projecting current stage outputs into candidate graph nodes, run against the archetype corpus and recorded builds | No authority change; comparison only | Closure of every page action/capability/event across the corpus; re-extraction equivalence | Adapters are offline-test-only and deleted at cutover. Rollback: delete builders. No live models. No ADR 0006 interaction. |
| **4. Derived CompilationPlan + renderer registry** — plan derivation from graph; `layout_registry` extended as the single path/family/renderer authority; AgentGenerator regains a renderer layer | No production authority change: agent-produced `AppBuildPlan` remains current while a derived `CompilationPlan` is selected only in offline tests/development after equivalence proof | Derived-vs-produced equivalence on the corpus; registry-extension invariants (digest, no monolith edit); stable renderer-order and path-confinement tests; generated-root path contracts | Begin identifying generator plan mirrors and dead converter normalizers for cutover; retain active promotion copying. Rollback: delete the candidate path/flag. No live models. No ADR 0006 dependency. |
| **5. Authority cutover, strict outputs, persistence unification** — compiled models `extra="forbid"` by default; agents emit graph-node payloads validated against runtime models; graph version + build record become the persistence spine; `BuilderArtifactStore` becomes a projection or typed view | Agent-produced plan and four representations → one authored graph, derived plan, and rendered views in a single cutover | Offline corpus regeneration equivalence; the strictness compatibility report published **before** the flip; data-reference consumer tests through a test/development-only comparison window that closes inside the slice | Retire generator YAML mirrors, `AppBuildPlan`, and `save_app_schema` parallel validators on proof. Rollback: per-workflow test/development flag only until cutover completes, then removed. No production dual-read/dual-authority mode. Live-model builds only after offline proof and only under ADR 0006 bounded journeys. |
| **6. Refinement on the graph** — typed `RefinementPatch`; checkpoint output schemas re-typed; affected set = graph query; recompile → validate → promote | Whole-file patching + glob safety → typed patches + registry regions | Patch property tests (apply+recompile == direct compile); promotion parity matrix against recorded cases; rollback rehearsal to prior graph version and revision | Retire the four glob taxonomies and `_stale_route` staleness substitution after parity proof. Rollback: prior graph/revision restore. No live models beyond slice 5 policy. Uses ADR 0006 counters for repair/refinement starts when bounded. |
| **7. Retirement** — remove obsolete schemas, glob taxonomies, aliases, converter paths, transitional adapters, comparison fixtures, and development flags | One semantic authority; one registry per concern | Repository hygiene guard extended to ban retired names (pattern: `scripts/production_readiness_gate.py`); full suite; generated-app acceptance | Deletions complete. Rollback: deployment rollback before deletion only; no dual-read shim reintroduced. No live-model change. ADR 0006 slice interleaving agreed before this point. |

## Acceptance Criteria For Implementation

- Canonical serialization is byte-identical across repeated serialization of
  the same graph, manifest, plan, or registry document.
- Content digests are stable across process restarts, key order, and
  platforms; every ref pins by digest.
- Node identifiers remain stable across graph versions; edge identities are
  deterministic and independent of document order; collisions fail closed.
- Reference closure is validated deterministically; a graph with any dangling
  reference fails compilation.
- Unknown fields are rejected by every compiler-surface model.
- Namespace isolation holds: an extension cannot redefine or weaken another
  namespace's entries or core invariants.
- Recompiling an unchanged graph yields byte-identical artifacts; recompiling
  after a patch changes only the affected families.
- Adding an artifact family requires only a registry extension and cannot
  bypass validator, security-class, or digest declarations.
- Every rendered child contract passes its existing runtime loader unchanged
  (module, page, subscriptions, data, workflow bundle, deployment manifest).
- Event and reaction references close over the taxonomy: every emitted type
  and consumed reaction resolves; unknown names fail closed at compile.
- Stub content exists only inside declared stub regions; undeclared files and
  out-of-region code are rejected; `contract_refs` resolve.
- Compilation is reproducible from the complete pinned input tuple (manifest,
  semantic graph, taxonomy refs, build-context binding, compiler/renderer
  registry, and target profile): identical closed inputs and digests yield
  byte-identical outputs. `BuildContextBindingRef` alone is provenance for
  candidate reasoning, not a promise to replay stochastic model output.
- Graph stores, manifests, and revisions are tenant-scoped; cross-tenant
  reads and writes fail closed.
- Failed or cancelled builds never promote artifacts (composing with ADR
  0006's quarantine rules).
- Each `ArtifactRevision` records consistent
  (`semantic_graph_ref`, `compilation_plan_digest`, `build_context_binding`,
  files manifest); a revision cannot cite a graph it was not compiled from.
- A refinement updates the semantic graph version and the artifact revision
  together, atomically from the caller's perspective; artifact mutation
  without a semantic update is impossible outside declared stub regions.
- After cutover, the old canonical path is removed: no generator YAML mirror,
  glob taxonomy, or transitional adapter remains, enforced by the hygiene
  guard.
- OSS operates fully without Cloud: no hidden service dependency exists on
  any compiler path.
- No second orchestration system exists: the compiler adds no scheduler,
  queue, or journey machinery beyond what ADR 0006 owns.

## Non-Goals

This ADR explicitly does not:

- implement `JourneyExecutionPort` (ADR 0006 owns that contract and its
  rollout);
- change AG2 execution primitives or introduce AG2-parallel machinery;
- define proprietary Cloud strategy, corpora, or thresholds;
- authorize live-model evaluation or any model spend;
- rewrite the repository — runtime, workflow engine, artifact store,
  routing registry, acceptance gates, and evaluation harness survive intact;
- preserve unused pre-1.0 APIs for compatibility's sake;
- replace `extension_registry.json` sequence authority;
- make every generated application structurally identical — the graph
  constrains references and rendering, not product shape; or
- grant agents unrestricted filesystem or code-generation authority.

## Affected Invariants

- **#1 Public Framework Contracts Stay Provider-Neutral.** All compiler
  contracts are provider-neutral; Jinja packs and renderers carry no provider
  execution code.
- **#3 Agents Produce Candidates; Deterministic Code Validates and Promotes.**
  This ADR is that invariant's structural enforcement.
- **#4 Public Schemas and Contracts Are Classified and Versioned.** Manifest,
  graph, taxonomy, plan, registry, binding, patch, and revision schemas are
  versioned public surfaces.
- **#5 Generic App Intelligence Can Be OSS; Multi-App Learned Intelligence
  Requires Review.** Mechanism is OSS; learned content enters through
  declared, digested inputs only.
- **#6 Authority Bypass Semantics Must Not Expand Casually.** The audit's
  bypass channels (cwd precedence, arbitrary-import seams, fail-open
  validation) are closed on the canonical path.

## Open Questions Deferred To Implementation

- Graph granularity: family/child-contract level is the recommendation; finer
  granularity only where refinement targeting proves it necessary.
- `BuilderArtifactStore` end state: projection of graph records versus merge
  into the build-record store — a data-migration decision at slice 5.
- `AppLoader` subscriptions fail-open default for SaaS apps — separate
  product decision.
- Joint sequencing of ADR 0006 journey slices and these compiler slices —
  agreed explicitly before implementation begins.
- Performance budget for graph validation/digesting on every save-tool call —
  corpus benchmarks in slice 3.
- Per-surface ownership-boundary population for brownfield/hybrid apps.

## Validation

For this Proposed ADR draft:

- repository source-hygiene scan (`scripts/production_readiness_gate.py`
  offline gates and `tests/test_production_readiness_gate.py`)
- `python -m mkdocs build --strict`
- verification that every repository path cited in this document exists at
  the authoring commit
- local Markdown-link verification
- `git diff --check`
- exact changed-file review (this document and its `mkdocs.yml` nav entry
  only)

Independent architecture, contract, and boundary review is required before
accepting this ADR or beginning implementation. Acceptance of this document
alone changes no runtime behavior and unlocks no production execution.
