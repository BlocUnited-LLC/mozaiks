# Mozaiks OSS Software Design

Status: pre-1.0 north star, grounded in the current implementation.

This document defines the software-design direction for Mozaiks OSS after the
architecture archaeology pass completed in August 2026. It describes what the
system is today, what owns each layer, and which future additions are allowed.
It is not a prompt-to-app determinism claim and it is not a request to build new
frameworks.

## Thesis

Mozaiks OSS exists to make AI-generated applications dynamic in reasoning but
deterministic in application contracts, validation, and runtime behavior.

The durable architecture is:

```text
AG2
  -> committed agentic runtime
Mozaiks OSS
  -> deterministic canonical application framework
App Zero and other products
  -> external proprietary consumers and operator-intelligence layers
```

The operating invariant remains:

```text
different intelligence, same canonical app
```

OSS baseline generation and proprietary enhanced generation may use different
strategy, knowledge, prompts, or eval evidence, but both must produce the same
canonical Mozaiks application contracts and pass the same deterministic
acceptance gates.

## Current Reality

The current OSS implementation already has these canonical paths:

- one AG2-backed workflow execution path;
- one canonical module dispatch path;
- `AppLoader` as the canonical app loader;
- layered host composition: runtime, platform, then Studio;
- AG2 `Agent` and network execution;
- AG2 middleware integration;
- AG2 `KnowledgeStore` injection;
- optional declarative A2A remote agents;
- strict structured output contracts;
- App Intelligence for one-app source and context understanding;
- build-context packs for deterministic build-time context;
- refinement harness and control-plane routing;
- deterministic `AppBuildPlan` materialization;
- functional generated-app acceptance.

The current implementation does not production-adopt AG2 Evaluation, AG2
Aggregation, AG2 Agent Harness, or MCP. Those capabilities may become useful,
but their existence in AG2 is not enough reason to add Mozaiks abstractions.

## Design Decisions

1. AG2 lock-in is intentional and acceptable.
2. Mozaiks must not wrap AG2 merely to create vendor neutrality.
3. Mozaiks wrappers must add genuine application semantics.
4. `AG2NetworkRunner` and the current orchestration architecture are canonical.
5. `KnowledgeStore` remains AG2-owned; Mozaiks exposes injection and
   configuration seams.
6. App Intelligence remains distinct from AG2 workflow memory.
7. The Mozaiks refinement harness remains distinct from AG2 Agent Harness unless
   future evidence proves consolidation is useful.
8. AG2 Evaluation may later provide execution or scoring primitives, but
   deterministic application validation remains Mozaiks-owned.
9. AG2 Aggregation may later assist derived operator knowledge, but App
   Intelligence and source facts remain Mozaiks-owned.
10. A2A is already supported declaratively.
11. MCP should be added only when a real canonical-app requirement exists.
12. No parallel workflow runner, module dispatch path, knowledge framework, or
    evaluation framework should be invented.

## Current Execution Chain

The canonical Factory runtime path is:

```text
factory_app/workflows/extended_orchestration/extension_registry.json
  -> workflow sequence selects AppGenerator, AgentGenerator, ExistingAppDiscovery, or review/refinement workflow
  -> mozaiksai.core.adapters.ag2_orchestration.AG2OrchestrationAdapter.run()
  -> mozaiksai.core.workflow.orchestration_patterns.run_workflow_orchestration()
  -> UnifiedWorkflowManager loads workflow YAML contracts
  -> context variables, build contexts, structured outputs, middleware, lifecycle hooks, and A2A declarations load
  -> create_agents() constructs AG2 Agent objects with tools, response schemas, observers, and middleware
  -> AG2NetworkRunner opens an AG2 Hub with a MemoryKnowledgeStore or injected KnowledgeStore
  -> AG2 HubClient/channel/transition graph executes the workflow
  -> structured outputs are validated and persisted
  -> deterministic Factory tools materialize canonical artifacts
  -> generated app validation and functional acceptance run
  -> generated bundles are staged for review, download, or promotion
```

For AppGenerator specifically:

```text
AppPlanAgent structured output
  -> app_build_plan tool
  -> AppBuildPlan context variables
  -> task batch items
  -> AG2 task batch worker agents
  -> assemble_app_tasks deterministic post-passes
  -> generate_and_download
  -> generated app acceptance gate
  -> canonical app bundle
```

This is the production path. Tests should use this path or a deterministic seam
immediately after a dynamic reasoning boundary. They should not manually author
final generated bundles when the real materializer can be exercised.

## AG2 Current-State Matrix

| Capability | Current Use | Owner | Future Rule |
| --- | --- | --- | --- |
| Middleware | Workflow prompt middleware, usage middleware, telemetry, media harvest, and metrics attach to AG2 agents. Mozaiks lifecycle hooks remain separate deterministic workflow hooks. | AG2 owns middleware mechanics. Mozaiks owns declarative compilation and app/workflow semantics. | Prefer compiling Mozaiks YAML into AG2 middleware. Do not add another middleware plane unless it enforces application semantics outside AG2's call boundary. |
| Metrics | AG2 `MetricsMiddleware` is wired behind runtime configuration and surfaced through runtime/admin metrics endpoints. | AG2 owns agent/LLM/tool metrics primitives. Mozaiks owns host exposure and product-safe summaries. | Reuse AG2 metrics directly. Simplify wrappers when AG2 APIs stabilize, but keep tenant/app projection in Mozaiks. |
| Evaluation | AG2 Evaluation is not used in production. Mozaiks owns generated-app validation, AppReview, quality gates, ValueEngine scoring, and CI acceptance. | Mozaiks owns deterministic canonical-app validation. AG2 may own generic execution/eval primitives later. | Do not replace Mozaiks acceptance gates with model/eval scoring. Add AG2 Evaluation only for execution/scoring evidence that feeds deterministic Mozaiks decisions. |
| KnowledgeStore | `AG2NetworkRunnerRequest.knowledge_store` accepts an AG2-compatible store. Default is a fresh AG2 `MemoryKnowledgeStore`. | AG2 owns workflow memory storage. Mozaiks owns injection, lifecycle boundaries, and tenant/session policy. | Keep KnowledgeStore AG2-native. Do not create a parallel workflow memory database. |
| Aggregation | AG2 Aggregation is not used. Mozaiks has App Intelligence snapshots, context graph merging, task-batch assembly, telemetry summaries, and refinement signals. | Mozaiks owns source-fact and canonical-app aggregation. AG2 may own generic agent-output aggregation later. | Use AG2 Aggregation only when it reduces custom agent-output aggregation without moving source/app authority out of Mozaiks. |
| Agent Harness | AG2 Agent Harness is not used. Mozaiks has a refinement harness, which is builder-session policy over artifacts. | AG2 owns generic agent harness mechanics. Mozaiks owns artifact-aware refinement policy. | Do not rename or collapse the Mozaiks refinement harness into AG2 Harness unless the concrete AG2 primitive fits the artifact policy use case. |
| A2A | Optional `a2a.yaml` declarations load remote AG2 A2A agents through AG2 A2A config. | AG2 owns A2A protocol/client behavior. Mozaiks owns declarative workflow binding. | Keep A2A declarative and optional. Do not require A2A for local OSS workflows. |
| MCP | No production MCP mechanism is adopted. | No current owner in Mozaiks runtime. AG2 or app services may own future use depending on requirement. | Add MCP only for a real canonical-app or workflow-tool requirement. Do not add MCP as a speculative integration layer. |

## Intelligence Boundary

Mozaiks OSS owns generic intelligence mechanisms for one application:

- source and application intelligence for the current app;
- deterministic validation and functional acceptance;
- canonical app, module, page, workflow, persistence, secret, capability, and
  route semantics;
- baseline generation and refinement strategy;
- public AG2 composition mechanisms;
- self-hostable provider contracts and fake/local-compatible test providers.

Mozaiks OSS does not own BlocUnited proprietary intelligence artifacts:

- correction corpus;
- production outcomes;
- eval corpus or eval results;
- learned routing;
- cross-app priors;
- operator knowledge;
- hosted Build Intelligence;
- private provider operations;
- production authority.

The distinction is mechanism versus artifact. A public contract or seam can be
OSS. BlocUnited data that fills the seam is private by default.

## Knowledge Boundary

AG2 `KnowledgeStore` and Mozaiks App Intelligence must remain separate.

AG2 workflow memory answers:

- what happened inside this agent/network execution;
- what memory a Hub or agent should access during a workflow;
- how AG2 persists generic network state.

Mozaiks App Intelligence answers:

- what source files exist;
- what routes, modules, entities, services, and ownership boundaries exist;
- what source facts are authoritative;
- what derived graph or snapshot can guide Factory/refinement;
- whether generated or brownfield application contracts are coherent.

App Intelligence may provide context to AG2 agents. AG2 KnowledgeStore must not
become the source of truth for canonical app facts.

## Refinement Boundary

The Mozaiks refinement harness is not a generic agent runtime.

It owns deterministic artifact-aware policy:

- classify the requested change;
- identify affected canonical surfaces;
- decide whether a workflow sequence, contract-surface plan, or coding worker is
  appropriate;
- preserve artifact lineage;
- start the selected workflow or worker only after typed policy accepts the
  decision.

AG2 executes model-backed pieces behind that harness. Mozaiks validates the
structured outputs and applies deterministic routing, invalidation, promotion,
and acceptance behavior.

## Deterministic Validation Boundary

Mozaiks validation is a framework responsibility. It is not an AG2 Evaluation
replacement and should not become model-scored acceptance.

Canonical validation includes:

- structural app-bundle validation;
- functional generated-app scanning;
- AppLoader loading;
- platform host boot;
- declared route resolution;
- module action dispatch;
- workflow registry loading;
- capability facade checks;
- entitlement/auth behavior checks;
- 404, 501, and placeholder protection for declared reachable surfaces.

AG2 Evaluation may later help score traces, compare strategies, or capture
execution evidence. It must not replace deterministic acceptance gates for
canonical application correctness.

## Extension Rule

Before adding any new framework abstraction:

1. Determine whether AG2 already owns the mechanism.
2. Determine whether Mozaiks already has a canonical implementation.
3. Reuse AG2 or the existing Mozaiks implementation directly when possible.
4. Add a Mozaiks seam only when canonical application semantics require it.

Allowed Mozaiks seams are narrow and semantic. Examples include:

- compiling workflow YAML into AG2 objects;
- injecting an operator-owned AG2 KnowledgeStore;
- projecting AG2 events into Mozaiks transport and artifact history;
- validating structured outputs against canonical app contracts;
- mapping App Intelligence facts into prompt/context inputs;
- enforcing tenant, auth, entitlement, persistence, and promotion boundaries.

Disallowed seams include:

- vendor-neutral agent wrappers with no semantic value;
- alternate workflow runners;
- alternate module dispatch paths;
- parallel knowledge frameworks for workflow memory;
- parallel evaluation frameworks that duplicate deterministic acceptance;
- speculative MCP or A2A layers with no concrete app requirement.

## Public Strategy Injection

The current public strategy seams are enough for OSS and external products to
compose different intelligence without forking canonical app behavior:

- build-context packs under `build_context/{context_name}/`;
- workflow registry overlays;
- workflow `context_variables.yaml`;
- workflow `middleware.yaml`;
- structured-output models;
- AG2 KnowledgeStore injection;
- refinement harness overlays;
- capability packs and app-owned facades;
- platform extension bundles;
- app config such as auth, subscriptions, and AI startup contracts.

Private products should use those seams. They should not duplicate Factory,
runtime, AppLoader, module dispatch, or generated-app acceptance behavior.

## Pre-1.0 Consolidation Items

These are documentation-level consolidation targets. They are not implemented
by this document.

| Item | Current State | Rule |
| --- | --- | --- |
| Workflow UI state backfill retirement | Runtime still contains startup backfill for pre-migration workflow UI fields. | Retire only after deployment migration is confirmed. Do not remove reader safety prematurely. |
| AG2 telemetry wrapper simplification | Mozaiks wraps AG2 telemetry/metrics to add env config and host exposure. | Simplify only if AG2 stable APIs cover the same operational needs without losing Mozaiks projection. |
| `legacy_trusted` naming cleanup | Module dispatch still exposes explicit compatibility terminology for trusted internal bypass. | Rename only through a focused compatibility/contract pass. Do not hide trusted bypass semantics. |
| `oss_reuse_contract` docs mismatch | App Zero declares OSS seam consumption in its own repo; OSS docs must remain aligned with public seam names. | Fix documentation drift without moving private product logic into OSS. |
| Mobile contract freeze coverage | Public API freeze exists, but mobile/client contract coverage should be confirmed before 1.0. | Add coverage in a focused contract-freeze pass, not as part of AG2 architecture work. |

## Things Explicitly Not Being Built

This north star does not call for:

- a Mozaiks-owned AG2 replacement;
- a vendor-neutral agent runtime wrapper;
- a second workflow runner;
- a second module dispatch path;
- a Mozaiks workflow-memory database parallel to AG2 KnowledgeStore;
- a model-scored substitute for deterministic app validation;
- AG2 Harness adoption without concrete consolidation evidence;
- AG2 Evaluation adoption without a deterministic decision boundary;
- AG2 Aggregation adoption without a real derived-knowledge use case;
- MCP adoption without a canonical app/workflow requirement;
- private App Zero intelligence in OSS.

## Success Criteria

Mozaiks OSS is on track when:

- a captured canonical plan can materialize deterministically into a runnable app;
- generated apps pass representative Level 2 functional runtime acceptance;
- brownfield and AgentGenerator handoffs preserve upstream structured intent;
- App Zero can consume OSS public seams without framework forks;
- private operator intelligence can improve strategy without changing canonical
  app contracts;
- every new abstraction either compiles to AG2 or enforces Mozaiks application
  semantics.
