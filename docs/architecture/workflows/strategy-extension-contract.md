# Strategy Extension Contract

Mozaiks keeps the OSS baseline complete while allowing applications and
operators to supply better strategy through stable seams.

The invariant is:

```text
different intelligence -> same canonical app contracts
```

An app generated or refined with OSS baseline strategy and an app generated or
refined with operator-augmented strategy must still produce the same public
canonical artifact families and pass the same deterministic validators.

## Complete Baseline

The OSS baseline must remain able to:

- inspect source and build App Intelligence locally
- run baseline `ExistingAppDiscovery`
- run baseline `AppGenerator`
- run baseline `AgentGenerator`
- run baseline refinement
- validate generated app bundles
- host canonical apps through the public runtime/platform/Studio contracts

Operator strategy may improve quality, routing, context selection, repair, or
planning. It must not become required for correctness of the public app model.

## Current Extension Seams

Use existing seams before adding new abstractions:

- `build_context/{context_name}/context.yaml` for declared build-time packs,
  catalogs, contracts, templates, and projected context variables
- workflow registry overlays that extend `mozaiks.default_workflow_registry`
  without copying default Factory workflows
- refinement harness overlays for app-specific harness deltas
- workflow `middleware.yaml` compiled into AG2 middleware for prompt/context
  injection
- workflow `context_variables.yaml` and AG2 conversation variables
- `PlatformExtensionBundle` for host/application hooks such as scope,
  permission resolution, dispatch policy, and sanitized audit callbacks
- app-local modules, services, integration clients, and provider-neutral
  facades
- AG2 structured output and middleware exposed through Mozaiks workflow
  contracts

These seams are public where they are documented and validated as framework
contracts. Internal helper imports are not extension seams.

## Proposed Pre-1.0 Seams

The following are candidates only. They are not implemented public APIs yet.

- Refinement routing policy/provider: a small seam for app/operator-specific
  refinement route hints if current harness overlays prove insufficient.
- AG2 `KnowledgeStore` provider/factory: a way to select an AG2 knowledge store
  implementation without hardcoding `MemoryKnowledgeStore` where persistent or
  operator-supplied stores are needed.

Do not publish code that depends on these proposed seams until the contracts are
designed, versioned where needed, and covered by tests.

## Publication Boundary

Generic strategy mechanisms may stay open. Learned strategy assets require
publication review:

- eval-driven generator optimization
- proprietary eval datasets or results
- customer-derived repair/migration heuristics
- learned model routing
- cross-app quality or failure correlations
- production outcome ranking

## Compatibility Rule

An extension may add hints, rankings, policies, or context. It must not change
canonical output schemas silently. If an extension requires a new public
artifact shape, treat that shape as a public contract and follow the ADR and
versioning process.
