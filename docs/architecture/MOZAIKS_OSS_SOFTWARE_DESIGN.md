# Mozaiks OSS Software Design

**Repository:** `BlocUnited-LLC/mozaiks`
**Status:** Pre-1.0 architectural direction
**Purpose:** Define what Mozaiks OSS is trying to become, what value it must provide independently, what belongs in the public framework, and where the boundary with proprietary BlocUnited intelligence begins.

**Authority:** This is the authoritative OSS north-star software-design document. Do not create or maintain a second competing OSS north-star document under `docs/architecture/foundations/`.

**Architecture freeze:** After this reconciliation, future north-star changes should require either a concrete contradiction between this document and current source/runtime behavior or an accepted ADR.

---

# 1. Executive Goal

Mozaiks OSS exists to make AI-generated software **dynamic in reasoning but deterministic in output, validation, and runtime behavior**.

The system should allow a developer to describe or discover an application using agentic reasoning while still producing a canonical application whose:

* structure is predictable;
* contracts are versionable;
* routes resolve;
* module actions exist;
* workflows load;
* authentication behavior is intentional;
* provider integrations use explicit capability contracts;
* validation is deterministic;
* runtime behavior is testable;
* generated applications are portable and self-hostable.

The core invariant is:

> **Dynamic intelligence, deterministic application contracts.**

A second strategic invariant is:

> **Different intelligence, same canonical app.**

The OSS Factory may use baseline strategy.

BlocUnited's hosted product may use more sophisticated proprietary strategy.

Both must ultimately produce the same canonical application model.

---

# 2. Why Mozaiks OSS Exists

The value of Mozaiks OSS is not simply that it wraps an agent framework.

AG2 provides generic agent execution mechanics.

Mozaiks should provide the missing application framework around those mechanics.

AG2 answers questions such as:

* how agents reason;
* how agents collaborate;
* how tools are invoked;
* how context is passed;
* how execution pauses for a human;
* how structured output is returned;
* how agent/network state is persisted.

Mozaiks answers a different set of questions:

* what is an application;
* what is a page;
* what is a module;
* what is an action;
* what is a workflow;
* what is a capability;
* what is a secret contract;
* how does generated source map to runtime behavior;
* how do we know a generated app actually works;
* how do we validate the app deterministically;
* how do applications remain portable between operators.

Therefore:

```text
AG2
│
│ agent mechanics
│
▼
Mozaiks OSS
│
│ deterministic application framework
│
▼
Canonical Applications
```

Mozaiks must not attempt to replace AG2 with a second general-purpose agent framework.

---

# 3. Product Thesis

The long-term OSS product should make this possible:

```text
Natural-language intent
        │
        ▼
Dynamic reasoning
        │
        ▼
Structured AppPlan
        │
        ▼
Deterministic materialization
        │
        ▼
Canonical application
        │
        ▼
Deterministic validation
        │
        ▼
Functional runtime
```

The first part may vary because AI reasoning is probabilistic.

The second part should become increasingly deterministic.

The primary engineering objective is therefore not:

> Make model output deterministic.

It is:

> Establish a deterministic boundary after reasoning so that a valid structured plan reliably becomes a functioning canonical application.

## Structured Boundary Principle

Mozaiks should:

> Reason dynamically; cross important workflow boundaries structurally.

AG2 reasoning may be dynamic.

Important handoffs should be represented as typed, inspectable artifacts that Mozaiks can validate, persist, replay, refine, and materialize.

Representative structured artifacts already present in the source include:

* `ExperienceSpec`;
* `SurfaceMap`;
* `DataContractBundle`;
* workflow bundles;
* `AppSchemaOutput`;
* `AppPlan` / `AppBuildPlan`;
* module manifests;
* event and reaction contracts;
* page schemas;
* data contracts.

This principle constrains dynamic agent reasoning without claiming deterministic LLM output.

## Mozaiks Control Loop

The complete architectural lifecycle is:

```text
INTENT
→ CONTEXT
→ REASON
→ STRUCTURE
→ COMPOSE / MATERIALIZE
→ VALIDATE
→ EVALUATE
→ REFINE / PROMOTE
→ OPERATE
→ OBSERVE
→ LEARN
→ IMPROVE STRATEGY
```

Ownership:

| Phase | Owner |
| --- | --- |
| Intent | Product or application owner. |
| Context | OSS `build_context` plus operator extensions. |
| Reason | AG2. |
| Structure | Mozaiks OSS structured outputs and canonical contracts. |
| Compose / materialize | Mozaiks OSS deterministic materialization. |
| Validate | Mozaiks OSS deterministic contracts and runtime acceptance. |
| Evaluate | Deterministic OSS checks, AG2 semantic evaluation when adopted, and human/operator evidence where appropriate. |
| Refine | Mozaiks OSS refinement harness. |
| Operate | Self-hoster or hosted operator. |
| Observe | Operator. |
| Learn | Operator/private by default for cross-app evidence. |
| Improve strategy | Operator strategy through public OSS seams. |

---

# 4. Canonical Application Model

Mozaiks has one canonical application model.

This applies to:

* apps generated by Factory;
* hand-authored Mozaiks apps;
* `factory_app`;
* proprietary applications such as App Zero;
* brownfield applications adopted into Mozaiks.

Canonical does not mean byte-for-byte generated by Factory.

Canonical means the application conforms to public Mozaiks contracts.

Typical canonical surfaces include:

```text
app/
  app.json

  modules/
    <module>/
      module.yaml
      backend/
      contracts/

  ui/
    route_manifest.json
    pages/
    components/

workflows/
  <workflow>/

build_context/
  <context>/

config/
```

Canonical contracts include:

* application manifest;
* module manifests;
* actions;
* handlers;
* page schemas;
* route/component registration;
* workflows;
* context variables;
* structured outputs;
* auth declarations;
* persistence contracts;
* capabilities;
* provider-facing facades;
* secrets-by-name;
* events and reactions;
* provenance;
* generated-bundle contracts.

The canonical model is the interoperability layer for the ecosystem.

## Build Context Architecture

`build_context` is Mozaiks's versioned context and reusable-asset projection layer between application intent and AG2 reasoning/materialization.

It is not merely prompt storage.

It has two architectural roles:

### Reasoning Projection

`build_context/{context_name}/context.yaml` and its declared assets project reusable knowledge into AG2 reasoning.

Examples:

* catalogs;
* contracts;
* capability directory entries;
* module archetypes;
* workflow patterns;
* shell presets;
* domain knowledge.

### Deterministic Materialization

Capability-pack contracts, templates, and assets project deterministic generated artifacts into canonical app bundles.

The materializer consumes the declared assets and writes bundle files through the existing Factory materialization path.

Future approved operator strategy may influence context or pack selection through public seams.

It must not silently change canonical app contracts.

---

# 5. Event-Driven Composition

Mozaiks applications have two complementary composition surfaces:

* modules and actions are synchronous public capability surfaces;
* events and reactions are the canonical loose-coupling mechanism between generated app modules, workflows, notifications, and app-owned adapters.

The generated app contract is:

```text
module action
  → handler / service
  → ctx.emit(event_type, payload)
  → canonical event envelope
  → ModuleEventRouter
  → reaction target
```

`module.yaml.actions[].emits` declares which facts an action may publish.

`contracts/events.yaml` defines the module-owned event contracts.

`contracts/reactions.yaml` defines how a module reacts to events.

`contracts/notifications.yaml` defines notification records derived from events.

Generated customer apps, `factory_app`, and hosted product workspaces use the same event/reaction contract shape.

This makes events/reactions part of the modularity model, not just a runtime notification feature.

Reusable modules and packs can compose by emitting and reacting to namespaced facts instead of importing each other's implementation.

Example:

```text
orders component
  → domain order-completed event
  → notification component reaction
  → analytics component reaction
  → AG2 workflow capability reaction
```

Actual event names remain contract-defined by the app, module, or pack.

Reaction targets have specific ownership boundaries:

| Target | Rule |
| --- | --- |
| `handler` | Invoke a method on the reacting module's handler. This is for deterministic module-owned reaction behavior. |
| `capability` | Invoke a declared capability. Workflow execution connects through this target; reactions route to capability IDs, not raw workflow names. |
| `notification` | Create a notification from the module's notification contract. |
| `service_adapter` | Call an app-owned service adapter for provider or integration mechanics. This remains an implementation hook, not durable app-fact authority. |

Events transport facts.

They do not grant authority by themselves.

Public authority remains in:

* module permissions;
* auth;
* entitlement gates;
* provider verification;
* production-authority checks;
* trusted runtime boundaries.

Event payload claims must not authorize provider mutation or durable state changes unless the receiving module or adapter verifies the source and applies its own authority rules.

Current enforcement:

* event namespace prefixes are validated;
* `module.yaml.actions[].emits` must reference declared events;
* reaction targets must use canonical target kinds and required fields;
* notification reactions must reference declared notification IDs;
* generated-app validation checks event, reaction, workflow, capability, and handler wiring;
* platform startup wires `ModuleExecutor`, `UnifiedEventDispatcher`, and `ModuleEventRouter` for loaded app modules.

Known pre-1.0 hardening gaps:

| Gap | Current status |
| --- | --- |
| Runtime event payload-schema enforcement | Event `payload_schema` is declared and validated as metadata, but runtime emit-time JSON Schema enforcement is not yet a hard guarantee. |
| Reaction idempotency | `idempotency_key` is part of the reaction contract, but the router does not yet enforce idempotency. |
| Reaction permissions | Reaction `permissions` are declared/provenanced, but reaction dispatch does not re-enter public module permission checks. |
| Cycle detection | No hard deterministic reaction-cycle detector is currently part of runtime validation. |
| Persistent `EventBus` bridging | `NoOpEventBus`, `MongoEventBus`, and `RedisEventBus` exist as ports/adapters, but the canonical in-process module reaction path is `UnifiedEventDispatcher` plus `ModuleEventRouter`; cross-instance bridging is not yet the proven core path. |

These gaps are not a reason to introduce a parallel event system.

---

# 6. Modular Composition

`CapabilityPack` is the existing reusable generation-time unit.

It is how Mozaiks currently collects reusable build context, contracts, templates, facades, provider boundaries, and generated app artifacts.

Do not introduce a parallel `Component` framework.

A Community Component is a distributed, versioned `CapabilityPack`, not a new runtime primitive.

Conceptually:

```text
CapabilityPack
+ provenance
+ dependencies
+ trust / integrity
+ distribution
+ upgrade metadata
= Community Component
```

Community Component Foundation v1 currently provides:

* pack identity and version metadata;
* dependency declarations;
* deterministic pack-content digests over declared assets;
* pack provenance manifest emission;
* catalog structural validation before pack context becomes generation input;
* dependency and exact declared-version validation before template materialization;
* offline local community-pack proof.

It does not yet provide:

* remote registry discovery;
* cryptographic signing;
* trust scoring;
* automatic remote installation;
* remote fetching;
* marketplace behavior;
* upgrade planning or migration execution.

Future community component work should evolve capability packs by adding distribution semantics:

* stronger identity and version provenance;
* trust and integrity metadata;
* local/self-host installability;
* upgradeability and migration semantics;
* validation against canonical app, module, page, workflow, service, event, and capability contracts.

The component model should preserve the current materialization architecture:

```text
discover
  → select
  → compose
  → adapt
  → generate only missing pieces
```

The long-term Factory direction is reuse-first, not regeneration-first.

Factory should increasingly discover existing packs/components, select the best fit, compose them through canonical contracts, adapt only where the app plan requires variation, and generate new code only for missing surfaces.

This is a directional rule, not an implementation-percentage target.

Third-party trust principle:

```text
Discovery != installation.
Installation != trust.
Trust != production authority.
```

Third-party reusable code must not become trusted merely because Factory can discover or materialize it.

OSS owns component contracts, local/self-host installation, validation, provenance, dependency semantics, and trust primitives.

App Zero or another hosted operator may privately own hosted discovery, ranking, reputation, commercial marketplace behavior, and private quality intelligence.

---

# 7. UI Portability Model

Generated and reusable UI has three portability levels:

| Level | Meaning | Reuse rule |
| --- | --- | --- |
| `SCHEMA_NATIVE` | Declarative page schemas rendered by canonical primitives such as `SchemaPage`. | Preferred for reusable community UI and generated app surfaces. |
| `SEMANTIC_TOKEN_REACT` | React components that use Mozaiks primitives, semantic tokens, and stable shell/runtime contracts. | Portable when the target app supports the same primitive and token contract. |
| `ARBITRARY_CUSTOM_REACT` | App-specific React escape hatch for experiences that cannot be represented declaratively. | Supported, but not the preferred reusable component format. |

Schema-native UI is inherently portable because it composes through canonical page primitives and declarative backend/module bindings.

Semantic-token React can be portable when it avoids app-specific imports, hardcoded styling, and private runtime assumptions.

Arbitrary custom React remains supported for product-specific experiences and workflow-local UI, but it should not become the default community component surface.

Reusable community UI should prefer schema-native surfaces first, semantic-token React second, and arbitrary React only when needed.

Schema-native UI is not mandatory for every application.

---

# 8. Factory App

`factory_app` is the OSS reference canonical application.

It is intentionally substantial.

Its purpose is to prove that Mozaiks itself can build a serious application using the same:

* modules;
* pages;
* workflows;
* build contexts;
* AppLoader;
* runtime;
* validation;
* persistence;
* AG2 integration;

that external applications use.

Factory-specific builder behavior is allowed.

Factory should not receive arbitrary private framework shortcuts simply because it is first-party.

The rule is:

> Factory may be special as an application, but it should not require a private version of the Mozaiks framework.

---

# 9. Core Factory Pipeline

The conceptual Factory pipeline is:

```text
Intent
  ↓
Value / product understanding
  ↓
Design
  ↓
Capability selection
  ↓
Agent/workflow planning
  ↓
AppPlan / AppBuildPlan
  ↓
Deterministic materialization
  ↓
Canonical generated bundle
  ↓
Validation
  ↓
Review/refinement
```

Dynamic reasoning may occur before structured artifacts such as AppPlan.

Once a canonical structured plan exists, downstream behavior should be as deterministic as practical.

## Refinement Lifecycle

Generation and refinement are phases of the same canonical application lifecycle.

The current conceptual refinement flow is:

```text
AppReview / user revision
  ↓
classify affected change
  ↓
route patch / design / feature / core changes
  ↓
plan affected surfaces
  ↓
repair or re-enter AppGenerator where required
  ↓
validate
  ↓
review again
```

Current source supports the major change classes `patch`, `design`, `feature`, and `core` in refinement routing and Studio workflow launch paths.

The Mozaiks refinement harness owns application lifecycle semantics: artifact families, affected-surface selection, re-entry decisions, validation, review package creation, and promotion readiness.

Do not replace the Mozaiks refinement harness with AG2 Agent Harness merely because both use the word "harness".

AG2 Agent Harness may later be used inside agent execution if a concrete requirement proves it is the right primitive.

---

# 10. Functional Acceptance

A syntactically valid generated application is not enough.

Mozaiks must prove generated applications actually function.

The validation model should progressively cover:

## Level 1 — Structural Acceptance

Examples:

* valid manifests;
* valid YAML/JSON schemas;
* declared routes;
* action definitions;
* handler declarations;
* workflow references;
* capability declarations.

## Level 2 — Functional Runtime Acceptance

Representative generated applications should:

* boot through the real runtime;
* resolve declared pages;
* expose declared module actions;
* load workflows;
* avoid accidental `404`;
* avoid accidental `501`;
* avoid placeholder implementations;
* satisfy capability facade requirements;
* enforce intentional auth/entitlement behavior.

## Post-Reasoning Deterministic Acceptance

Captured structured planning artifacts should prove:

```text
AppPlan
→ deterministic materialization
→ canonical app
→ functional acceptance
```

This does **not** claim:

```text
prompt
→ identical generated app
```

## Evaluation Architecture

Evaluation complements validation.

It does not replace validation.

### Deterministic Mozaiks Validation

Mozaiks owns deterministic validation for:

* schemas;
* routes;
* actions;
* handlers;
* events and reactions;
* security invariants;
* bundle correctness;
* runtime correctness.

These gates must not be replaced by LLM judges.

### AG2 / Semantic Evaluation

AG2 Evaluation may later provide generic execution/scoring primitives for:

* intent fidelity;
* planning quality;
* workflow effectiveness;
* refinement quality;
* strategy comparison.

Adoption should happen only when source requirements justify it.

### Human Evaluation

Human evaluation includes:

* AppReview acceptance;
* user-requested refinement;
* future structured build satisfaction;
* UX judgment.

### Operator Outcome Evaluation

Operator outcome evaluation includes build, deployment, refinement, runtime, and business-result evidence observed by a self-hoster or hosted operator.

Cross-app outcome learning is private by default unless deliberately published through OSS review.

---

# 11. Representative Application Coverage

The public baseline should maintain regression acceptance across multiple representative archetypes rather than one demo app.

Examples include:

* authenticated CRUD;
* monetized SaaS;
* workflow/agent application;
* admin/operations application;
* community/content application;
* brownfield adoption;
* agent-generated workflow composition.

The objective is to detect cross-feature contract drift.

---

# 12. Brownfield Applications

ExistingAppDiscovery belongs in OSS.

Understanding one existing application is part of the framework baseline.

OSS brownfield functionality may include:

* local source scanning;
* framework detection;
* route discovery;
* service discovery;
* entity discovery;
* module decomposition;
* adoption planning;
* migration planning;
* local App Intelligence;
* deterministic post-discovery materialization.

It must not require BlocUnited-hosted intelligence.

However, future intelligence derived from operating many customer migrations is private by default.

Examples:

* learned migration ranking;
* cross-project retrieval;
* customer-derived repair heuristics;
* migration success prediction;
* production-outcome correlations.

---

# 13. AG2 Ownership Boundary

AG2 owns generic agent mechanics.

Mozaiks should expose and compose AG2 rather than reimplementing it.

Relevant AG2 capabilities include:

* agents;
* networks;
* orchestration;
* context variables;
* middleware;
* structured output;
* human-in-the-loop;
* KnowledgeStore;
* streaming;
* tool execution;
* approval middleware;
* telemetry;
* evaluation primitives.

Mozaiks may create canonical configuration and adapters around these concepts.

Example:

```text
AG2 KnowledgeStore
        │
        ▼
Mozaiks public injection seam
        │
        ├── MemoryKnowledgeStore
        ├── SQLite
        └── Redis
```

The mechanism is public.

The proprietary contents of a BlocUnited KnowledgeStore are not.

## Knowledge Boundary Clarification

AG2 KnowledgeStore is the canonical mechanism for agent/workflow memory.

Mozaiks should expose injection and configuration for AG2-owned knowledge primitives.

App Intelligence remains separate.

It describes the current app, source, contracts, context graph, build artifacts, and validation evidence.

Cross-app Build Intelligence remains an operator concern.

Operator knowledge may use AG2 knowledge primitives where appropriate, but tenant/workflow memory must not be conflated with cross-app Build Intelligence.

---

# 14. Mechanism vs. Intelligence Artifact

This is one of the most important IP rules in the project.

## Public Mechanism

Examples:

* KnowledgeStore interface;
* evaluation framework;
* routing interface;
* refinement framework;
* scoring mechanisms;
* telemetry schema;
* validation logic;
* strategy injection seam.

## Private Artifact By Default

Examples:

* customer correction datasets;
* eval corpora;
* eval results;
* learned routing weights;
* production outcome datasets;
* cross-app repair priors;
* proprietary knowledge contents;
* commercial conversion data.

A mechanism may be extremely sophisticated and still belong in OSS.

The accumulated proprietary evidence driving the mechanism does not automatically belong in OSS.

---

# 15. Framework Intelligence

Framework Intelligence describes the current application.

It should remain independently useful in OSS.

Examples:

* source indexing;
* source retrieval;
* framework detection;
* AppContext;
* AppIntelligence snapshots;
* source graph;
* validation evidence;
* build context;
* generic refinement context;
* generic app scoring.

Framework Intelligence may be advanced.

It remains OSS when it can be derived from the application being analyzed rather than requiring customer history.

---

# 16. Operator / Learned Intelligence

Operator Intelligence is accumulated through operating many applications.

Examples:

* correction corpora;
* build outcome histories;
* eval results;
* learned repair strategies;
* model performance correlations;
* cross-customer failure patterns;
* production success metrics;
* customer behavior;
* commercial performance;
* migration success data.

This information is private by default.

The OSS publication policy must require explicit review before such intelligence is published.

---

# 17. Strategy Architecture

OSS must provide a complete baseline strategy.

It must not require BlocUnited services to generate useful applications.

Operator or application-specific strategy may improve:

* context selection;
* model choice;
* repair ranking;
* planning;
* generation quality;
* refinement;
* migration strategy.

These improvements should enter through stable seams.

Possible seams include:

* build contexts;
* workflow overlays;
* middleware;
* context variables;
* KnowledgeStore;
* refinement configuration;
* model policy;
* capability routing hints where a future generic need is proven.

An extension may improve strategy.

It must not silently create a different application model.

---

# 18. Managed Capabilities

Mozaiks OSS should support managed capabilities through provider-compatible contracts.

Examples:

* payments;
* email;
* media;
* analytics;
* hosting-oriented capabilities.

A capability may have a recommended/default provider.

However:

> Default does not mean mandatory.

For example, MozaiksPay may remain the recommended monetization path while self-hosters can implement the same public provider contract.

OSS should expose:

* generated facade;
* capability contract;
* API shapes;
* entitlement boundary;
* secret-name contracts;
* replacement requirements.

BlocUnited's actual provider implementation remains proprietary.

---

# 19. MozaiksPay Boundary

Public OSS may contain:

* billing portal facade;
* generated client;
* compatible provider API contract;
* subscription capability;
* entitlement/fulfillment contract;
* default/recommended selection.

OSS should not require:

* BlocUnited Stripe implementation;
* wallet implementation;
* payouts;
* settlement;
* proprietary billing persistence;
* hosted marketplace economics.

The generated app targets a provider contract.

App Zero may be the default provider implementation for hosted users.

---

# 20. Self-Hosting

OSS must be genuinely usable without BlocUnited.

A self-hosting developer should be able to:

* install Mozaiks;
* run Factory;
* configure a model provider;
* understand one application;
* generate an application;
* refine it;
* validate it;
* boot it;
* configure compatible providers;
* use local/durable AG2 memory;
* operate without App Zero.

No `mozaiks-app` import or BlocUnited credential may be required for baseline correctness.

---

# 21. Public API Philosophy

Pre-1.0 should maintain a small intentional public surface.

Public APIs should represent stable framework seams.

Examples:

* Studio scope;
* PlatformExtensionBundle;
* module dispatch;
* authority/provenance metadata;
* event/reaction provenance;
* generated app validation;
* AppLoader;
* AG2 workflow orchestration;
* KnowledgeStore injection.

Internal implementation helpers should not become accidental contracts simply because Python allows importing them.

App Zero should consume public seams.

If App Zero repeatedly needs an internal helper, ask:

> Is this a generic framework capability missing from the public API?

If yes, design the smallest public seam.

Do not create a private framework fork.

---

# 22. Community Flywheel

The OSS strategy should create a positive feedback loop.

Community improvements to:

* runtime;
* validators;
* schemas;
* Factory baseline;
* AG2 adapters;
* App Intelligence;
* provider-neutral capability contracts;
* workflows;
* self-hosting;

should benefit:

```text
factory_app
third-party apps
App Zero
generated customer apps
```

App Zero should receive those improvements by advancing its public OSS dependency.

---

# 23. IP Protection Rule

The OSS repository should not be intentionally weakened to create artificial scarcity.

The protected advantage should come from:

```text
Operating apps
      ↓
Collecting private evidence
      ↓
Evaluation
      ↓
Learning
      ↓
Better strategy
      ↓
Better generation / operation
```

The open framework enables adoption.

The private intelligence compounds from operation.

---

# 24. Explicitly Keep OSS

Unless a new ADR overturns this decision, the following families are intentional OSS:

* runtime;
* canonical contracts;
* Factory app;
* AppGenerator baseline;
* AgentGenerator;
* ExistingAppDiscovery;
* AppReview;
* baseline refinement;
* local App Intelligence;
* validation;
* generated-app functional acceptance;
* AG2 adapters;
* provider-neutral capability contracts;
* managed-capability framework;
* baseline build contexts;
* canonical self-host support.

Do not repeatedly propose moving these private solely because they are valuable.

Useful OSS is the strategy.

---

# 25. Private-By-Default Future Additions

Require publication review for future:

* customer datasets;
* eval datasets/results;
* Build Intelligence artifacts;
* learned model routing;
* production outcomes;
* cross-app rankings;
* customer-derived heuristics;
* marketplace performance;
* commercial outcome intelligence;
* operator KnowledgeStore contents.

---

# 26. Non-Goals

Mozaiks OSS should not become:

* a BlocUnited control plane;
* a payment processor;
* an Azure-specific deployment service;
* a marketplace operator;
* an investment marketplace;
* a cross-customer intelligence database;
* a secret distribution platform;
* a proprietary hosted backend.

It should expose generic contracts where those systems interact with canonical applications.

---

# 27. Near-Term Engineering Priorities

The OSS pre-1.0 priorities are now primarily stabilization rather than endless feature expansion.

Priorities:

1. preserve Level-2 functional acceptance and deterministic materialization;
2. stabilize Community Component Foundation v1;
3. harden event/reaction runtime payload-schema enforcement, idempotency, reaction authority/permissions, and cycle detection;
4. strengthen component trust/integrity before remote installation;
5. preserve and improve UI/theme portability contracts;
6. strengthen ThemeCapture/design fidelity validation;
7. preserve refinement lifecycle and diagnostics;
8. strengthen brownfield deterministic adoption;
9. add AG2-native evaluation/composition seams only when demonstrated requirements exist;
10. finish self-host, package, and release readiness without weakening IP/distribution guardrails.

This list is not a claim that every item must be complete before 1.0 unless a separate release policy says so.

---

# 28. OSS Success Definition

Mozaiks OSS succeeds when an unrelated developer can:

```text
install
→ run Factory
→ describe or import an app
→ generate/refine
→ receive canonical source
→ validate
→ boot
→ extend
→ self-host
```

without BlocUnited assistance.

And when BlocUnited can simultaneously use the exact same public framework to operate a substantially more intelligent proprietary platform.

That is the intended equilibrium.

---

# 29. Final Architectural Principle

Mozaiks OSS is not the moat because it hides how applications are built.

Mozaiks OSS is the ecosystem.

The moat is what BlocUnited learns and operationalizes on top of that ecosystem.

> **Open the capability to build one excellent application. Protect the accumulated intelligence gained from building and operating thousands.**
