# ADR 0005: Reference Factory and Proprietary Build Intelligence Boundary

Date: 2026-08-24

Status: Accepted

Supersedes: ADR 0002 in part; ADR 0003 in part

ADR 0002 and ADR 0003 remain accepted historical decisions and are partially
superseded only as described below.

## Decision

Mozaiks OSS remains a complete, independently useful AI app factory with a
capable reference generation and refinement strategy. BlocUnited keeps
customer-derived, fleet-derived, learned, internally optimized, or
otherwise commercially differentiating build intelligence private by default.

The governing rule is:

> Publish the canonical contracts, generic engines, and a capable reference
> strategy. Deliberately review optimized strategy and keep learned operational
> intelligence private by default.

This classification concerns provenance and information content, not value or
sophistication alone. Generic framework authority remains OSS even when it is
commercially valuable. Private implementation policy cannot move canonical
contracts, provider-neutral engines, deterministic validation, or generic
extension authority into a hosted product.

Prompts, workflow YAML, scorer policy, repair heuristics, routing tables, context
packs, and agent instructions are executable product intelligence. Their
location in an OSS family does not by itself approve publication.

## Context

ADR 0002 established that AppGenerator baseline strategy belongs in OSS. ADR
0003 froze the source boundary and concluded that the repository had no current
extraction candidates. Those decisions correctly protected a useful,
self-hostable framework, but their directory-based "OSS by default" language is
too broad for future improvements.

Mozaiks now needs a durable boundary that permits a strong public reference
factory without automatically publishing every optimization BlocUnited learns
from operating builds, refinements, deployments, and commercial applications.

This ADR does not determine that any current file must be removed. It establishes
the classification and review rule for a separate exact-file inventory.

Accepted ADR 0007 subsequently defined the generalized semantic compiler's
taxonomy, manifest, graph, reference, binding, plan, renderer-registry, patch,
revision, validation, and promotion contracts as versioned public framework
surfaces. Completed ADR 0007 Slice 0 repaired prerequisite defects without
implementing later compiler slices or changing this public/proprietary boundary.
This decision must be read consistently with both facts.

## Classification

| Classification | Default destination |
| --- | --- |
| Canonical app contracts, schemas, validators, ports, and generic engines | OSS |
| ADR 0007 taxonomy, semantic-reference, graph, binding, compilation, rendering, validation, patch, revision, and promotion contracts and generic engines | OSS |
| Capable provider-neutral reference generation and refinement strategy | OSS |
| Material BlocUnited-authored optimization with uncertain provenance or sensitive information content | Publication review |
| Customer-derived corrections, learned policy, fleet-level intelligence, proprietary eval evidence, and commercial operating strategy | Private |
| Provider-specific production execution, credentials, commercial policy, settlement, ranking, and payout economics | Private |

"Reference strategy" means enough implementation for an independent user to
build, inspect, validate, and refine a real application without a hosted Mozaiks
dependency. It does not require publication of BlocUnited's best-performing
policy, private benchmarks, learned variants, or managed operating playbooks.

## Public Surface

The following remain OSS:

- the canonical application model and module/page/action/data contracts;
- ADR 0007's public taxonomy and semantic-compiler contract families and their
  generic deterministic implementations;
- the runtime, workflow engine, deterministic validators, and extension ports;
- the refinement mechanism and local/self-hosted execution path;
- provider-neutral deployment, payment, telemetry, and capability contracts;
- a capable AppGenerator reference strategy and transparent deterministic gates;
- explicit seams for optional operator-supplied policy and context.

OSS operation must not depend on a hidden call to BlocUnited services or private
intelligence.

## Relationship To Accepted ADR 0007

ADR 0007's public contracts are mechanisms, not proprietary intelligence
artifacts. Their participation in generation does not reclassify them. The
public boundary includes canonical taxonomy ownership, immutable semantic
references, graph and binding contracts, deterministic compilation and rendering,
reference closure, validation, revision and promotion authority, and the generic
refinement contract.

Private intelligence may supply declared inputs or strategy through public seams.
It may not become the only implementation of generic compiler behavior, redefine
canonical names, bypass deterministic validation, or create a private framework
authority that the OSS reference factory requires for correctness.

## Private-by-Default Build Intelligence

The following require an ADR explicitly approving publication before entering
the public repository:

- corrections or heuristics derived from customer or fleet outcomes;
- learned prompt variants, routing weights, rankings, and model-selection policy;
- proprietary eval corpora, scored outputs, benchmarks, and threshold tuning;
- failure classification and repair-selection policy derived from operations;
- cross-app architectural priors and outcome correlations;
- hosted managed-refinement, production-operations, or commercial playbooks;
- marketplace ranking, fraud, settlement, pricing, and payout policy;
- provider-specific hosted production executors and credential topology.

Generic interfaces for these capabilities may be public. Private artifacts and
optimized implementations must cross the boundary only through explicit,
inspectable inputs or declared ports.

## Publication Test for Strategy Content

Before publishing a material prompt, workflow, scorer, heuristic, routing rule,
or build-context pack, reviewers must record:

1. provenance: whether it is generic, authored from public knowledge, or derived
   from BlocUnited/customer operations;
2. ecosystem value: why publication improves adoption, trust, portability,
   interoperability, or contribution;
3. competitive exposure: what hard-to-retract knowledge becomes public;
4. OSS sufficiency: whether withholding it would leave the reference factory
   independently useful;
5. contract impact: whether it creates a new public schema, protocol, authority,
   or compatibility commitment.

Material uncertainty routes to publication review, not automatic publication.

## Later Exact-File Inventory

A follow-up exact-file audit must classify content under:

- `factory_app/workflows/`;
- `factory_app/build_context/`;
- `factory_app/refinement_harness/`;
- `factory_app/eval/`;
- generator/refinement agent guidance and strategy documentation.

That later audit must identify exact files, provenance, replacement requirements,
compatibility impact, license implications, and whether any action is
forward-only. This ADR and its six-file documentation changes do not perform that
inventory and do not claim that source extraction, repository cleansing, package
removal, tag or release deletion, or history rewriting has occurred. None of
those actions is authorized merely by this ADR.

## History and Release Decision

Existing ADRs remain in the repository as decision history.

ADR 0002 is partially superseded only where "baseline strategy" could be read to
approve every future strategy optimization. A capable reference AppGenerator
remains OSS.

ADR 0003 is partially superseded in these areas:

- the historical "Current OSS extraction candidates: NONE" finding will not be
  treated as prospective approval for future content;
- additions inside DO-NOT-MOVE directories are not automatically approved for
  publication;
- prompt catalogs and refinement strategy require information-content and
  provenance review, not directory-only classification.

ADR 0003 remains accepted and continues to govern the canonical-model,
generic-mechanism, App Zero no-fork, hosted-provider, commercial-policy, and
evidence-based history-retention decisions.

This ADR does not authorize rewriting Git history. Public MIT-licensed material
is treated as a one-way door. Historical remediation requires a separate,
evidence-based decision explaining the benefit, disruption, and limitations.

## Enforcement

- Update `OSS_PUBLICATION_POLICY.md` to make provenance and information content
  authoritative over directory placement.
- Update the OSS boundary family registry so DO-NOT-MOVE protects generic
  families while requiring review for private-by-default intelligence placed
  within them.
- Extend governance and package-content checks in a follow-up implementation PR
  where deterministic enforcement is feasible.
- Require an ADR before publishing a new material strategy surface, learned
  artifact, provider executor, commercial policy, authority path, or dataset.

## Alternatives Considered

### Keep ADR 0003 unchanged

Rejected. It protects a useful OSS product but can be interpreted as automatic
approval for every future optimization placed in an OSS directory.

### Make the entire AppGenerator proprietary

Rejected. That would weaken adoption, self-hosting, contributor trust, and the
open canonical application ecosystem.

### Publish all strategy and compete only on hosting

Rejected. Hosting is valuable, but publishing accumulated build intelligence by
default would give away a stronger and more compounding potential moat.

### Rewrite history immediately

Rejected. No exact-file materiality review has established that the disruption
would produce meaningful protection, and previously published copies cannot be
recalled.

## Consequences

- Mozaiks OSS remains a real AI app factory rather than a hollow SDK.
- BlocUnited can compound a private moat from build, repair, deployment, and
  commercial outcomes.
- Strategy changes require more deliberate provenance and publication review.
- Directory ownership remains useful for architecture but is no longer a
  substitute for IP-boundary classification.
- Any future extraction must preserve a capable OSS replacement and follow
  semantic-versioning and community-communication requirements.

## Reversibility

The policy can be relaxed later by an ADR approving publication. Accidental MIT
publication cannot be reliably reversed, so uncertain proprietary-intelligence
artifacts default to private review.

## Affected Invariants

- OSS Mozaiks Is Independently Useful
- Public Framework Contracts Stay Provider-Neutral
- Different Intelligence, Same Canonical App
- App Zero Dogfoods Public Framework Contracts
- Operator Intelligence Is Explicitly Separated
- Public MIT Publication Is a One-Way Door

## OSS Boundary

Keep OSS: canonical contracts, generic engines, deterministic validation,
extension seams, ADR 0007's public semantic-compiler surfaces, and a capable
reference factory.

Keep private by default: learned, customer-derived, fleet-derived, optimized
build intelligence whose provenance or information content is sensitive, and
hosted commercial or provider-specific production operations. This private
boundary does not transfer generic framework authority out of OSS.
